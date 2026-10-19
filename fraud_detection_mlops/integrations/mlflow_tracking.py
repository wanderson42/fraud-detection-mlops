"""Native MLflow logging of newly fitted pipelines; no historical conversion."""

from contextlib import contextmanager
from importlib.metadata import version
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
from tempfile import TemporaryDirectory
from typing import Annotated
from urllib.parse import urlparse

import mlflow
from mlflow import MlflowClient
from mlflow.models import infer_signature
import mlflow.pyfunc
import mlflow.sklearn
import numpy as np
import typer

from fraud_detection_mlops.config import PROJECT_ROOT
from fraud_detection_mlops.features.feature_schema import FEATURE_COLUMNS
from fraud_detection_mlops.integrations.skops_persistence import (
    TRUSTED_TYPES,
    check_scores,
    load_pipeline,
)
from fraud_detection_mlops.modeling.contracts.model_interface import (
    VERSION as MODEL_INTERFACE_VERSION,
)
from fraud_detection_mlops.modeling.contracts.model_interface import predict_scores

DEFAULT_TRACKING = PROJECT_ROOT / "data/tracking"
EXPERIMENT = "fraud-temporal-baseline-v2"
app = typer.Typer(no_args_is_help=True)


class TrackingError(ValueError):
    """Tracking does not satisfy the standalone pipeline contract."""


def tracking_uri(root: Path) -> str:
    return "sqlite:///" + str(root.resolve() / "mlflow.db")


@contextmanager
def local_tracking(root: Path):
    previous = mlflow.get_tracking_uri()
    mlflow.set_tracking_uri(tracking_uri(root))
    try:
        yield
    finally:
        mlflow.set_tracking_uri(previous)


def validate_http_uri(uri: str) -> str:
    """Explicit server root only; local paths and credentials are not endpoint URIs."""
    if not isinstance(uri, str) or any(c.isspace() for c in uri):
        raise TrackingError("Use an explicit http:// or https:// tracking server root")
    try:
        parsed = urlparse(uri)
        valid_port = parsed.port is None or 1 <= parsed.port <= 65535
    except ValueError as exc:
        raise TrackingError("Invalid HTTP tracking URI") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.params
        or parsed.query
        or parsed.fragment
        or not valid_port
    ):
        raise TrackingError(
            "Use a tracking server root without credentials, path, query or fragment"
        )
    return uri.rstrip("/")


def require_proxy_artifacts(uri: str):
    parsed = urlparse(uri)
    if (
        parsed.scheme != "mlflow-artifacts"
        or parsed.netloc
        or parsed.query
        or parsed.fragment
        or not parsed.path.startswith("/")
        or ".." in PurePosixPath(parsed.path).parts
        or "%" in parsed.path
    ):
        raise TrackingError("HTTP candidates require server-proxied mlflow-artifacts locations")


@contextmanager
def http_tracking(uri: str):
    """Keep artifact bytes on the tracking proxy and restore settings on failure.

    The fluent MLflow API has process-global state; these contexts are for serial callers.
    """
    uri = validate_http_uri(uri)
    previous = mlflow.get_tracking_uri()
    settings = {
        "MLFLOW_HTTP_REQUEST_TIMEOUT": "10",
        "MLFLOW_HTTP_REQUEST_MAX_RETRIES": "0",
        # S3 capabilities can otherwise redirect clients to Docker-internal signed URLs.
        "MLFLOW_ENABLE_PROXY_MULTIPART_DOWNLOAD": "false",
        "MLFLOW_ENABLE_PROXY_MULTIPART_UPLOAD": "false",
    }
    original = {name: os.environ.get(name) for name in settings}
    try:
        os.environ.update(settings)
        mlflow.set_tracking_uri(uri)
        yield
    finally:
        mlflow.set_tracking_uri(previous)
        for name, value in original.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def download_pipeline(uri: str, destination: Path, *, feature_columns=FEATURE_COLUMNS):
    """Download and review a native sklearn/skops pipeline for prediction or inspection."""
    path = Path(mlflow.artifacts.download_artifacts(artifact_uri=uri, dst_path=str(destination)))
    metadata = mlflow.models.Model.load(path / "MLmodel")
    if feature_columns is None:
        feature_columns = metadata.signature.inputs.input_names() if metadata.signature else []
    sklearn_flavor = metadata.flavors.get("sklearn", {})
    pyfunc = metadata.flavors.get("python_function", {})
    model_file = path / sklearn_flavor.get("pickled_model", "")
    if (
        sklearn_flavor.get("serialization_format") != "skops"
        or not set(sklearn_flavor.get("skops_trusted_types", [])).issubset(TRUSTED_TYPES)
        or model_file.resolve().parent != path.resolve()
        or pyfunc.get("loader_module") != "mlflow.sklearn"
        or pyfunc.get("predict_fn") != "predict_proba"
        or metadata.signature is None
        or metadata.signature.inputs.input_names() != list(feature_columns)
    ):
        raise TrackingError("Unexpected MLflow model format, loader or signature")
    return load_pipeline(model_file, feature_columns=feature_columns), path


def load_tracked_model(uri: str, destination: Path, *, feature_columns=FEATURE_COLUMNS):
    """Review the pipeline before invoking the native pyfunc loader."""
    _, path = download_pipeline(uri, destination, feature_columns=feature_columns)
    return mlflow.pyfunc.load_model(str(path))


def log_candidate(
    model,
    features,
    scores,
    *,
    name,
    parameters,
    metrics,
    tags,
    root=None,
    http_uri=None,
    experiment_name=EXPERIMENT,
    artifacts=(),
):
    """One main run per fitted pipeline; a reload failure makes the run FAILED."""
    if (root is None) == (http_uri is None):
        raise TrackingError("Choose exactly one local root or HTTP tracking URI")
    if http_uri is not None:
        http_uri = validate_http_uri(http_uri)
    if mlflow.active_run() is not None:
        raise TrackingError("Finish the active MLflow run before logging independent candidates")
    source_run_id = tags.get("experiment_run_id") or tags.get("baseline_run_id")
    if not isinstance(source_run_id, str) or not source_run_id:
        raise TrackingError("Expected the source experiment or baseline run identity")
    checked_scores = predict_scores(model, features)
    if np.asarray(scores).shape != checked_scores.shape or not np.allclose(
        checked_scores, scores, rtol=1e-12, atol=1e-12
    ):
        raise TrackingError("Recorded scores differ from the contributed model")
    if root is not None:
        root.mkdir(parents=True, exist_ok=True)
    context = http_tracking(http_uri) if http_uri is not None else local_tracking(root)
    with context:
        client = MlflowClient()
        experiment = client.get_experiment_by_name(experiment_name)
        if experiment is None:
            options = (
                {}
                if http_uri is not None
                else {"artifact_location": (root / "artifacts").resolve().as_uri()}
            )
            experiment_id = client.create_experiment(experiment_name, **options)
            experiment = client.get_experiment(experiment_id)
        else:
            experiment_id = experiment.experiment_id
        if http_uri is not None:
            require_proxy_artifacts(experiment.artifact_location)
        with mlflow.start_run(
            experiment_id=experiment_id,
            run_name=f"{name}-{source_run_id[:8]}",
        ) as run:
            if http_uri is not None:
                require_proxy_artifacts(run.info.artifact_uri)
            mlflow.log_params(parameters)
            mlflow.log_metrics(metrics)
            mlflow.set_tags(
                {
                    **tags,
                    "model_id": name,
                    "model_format": "skops",
                    "test_evaluated": "false",
                    "model_interface_version": MODEL_INTERFACE_VERSION,
                }
            )
            for artifact in artifacts:
                mlflow.log_artifact(str(artifact))
            logged = mlflow.sklearn.log_model(
                model,
                name="model",
                serialization_format="skops",
                skops_trusted_types=sorted(TRUSTED_TYPES),
                pyfunc_predict_fn="predict_proba",
                signature=infer_signature(features.head(5), model.predict_proba(features.head(5))),
                pip_requirements=[
                    f"{p}=={version(p)}"
                    for p in ("mlflow", "scikit-learn", "skops", "numpy", "pandas", "scipy")
                ],
            )
            model_artifact_uri = None
            if http_uri is not None:
                model_artifact_uri = client.get_logged_model(logged.model_id).artifact_location
                require_proxy_artifacts(model_artifact_uri)
            with TemporaryDirectory(prefix="fraud-mlflow-check-") as temporary:
                restored = load_tracked_model(
                    logged.model_uri, Path(temporary), feature_columns=list(features.columns)
                )
                probabilities = restored.predict(features)
                check_scores(probabilities, scores)
            mlflow.set_tag("logged_model_uri", logged.model_uri)
            result = {"run_id": run.info.run_id, "model_uri": logged.model_uri}
            if http_uri is not None:
                result.update(
                    tracking_uri=http_uri,
                    experiment_id=experiment_id,
                    run_artifact_uri=run.info.artifact_uri,
                    model_artifact_uri=model_artifact_uri,
                    model_reload_validated=True,
                    max_absolute_score_error=float(
                        np.max(np.abs(np.asarray(probabilities)[:, 1] - scores))
                    ),
                    parity_rtol=1e-12,
                    parity_atol=1e-12,
                )
            return result


def verify_model(uri: str, root: Path = DEFAULT_TRACKING) -> dict:
    """Load a model from the existing local store; no fit or baseline conversion."""
    if not (root / "mlflow.db").is_file():
        raise TrackingError("No local tracking database")
    if not uri.startswith(("runs:/", "models:/")):
        raise TrackingError("Use a runs:/ or models:/ URI from your local tracking store")
    with local_tracking(root), TemporaryDirectory(prefix="fraud-mlflow-check-") as temporary:
        model, path = download_pipeline(uri, Path(temporary), feature_columns=None)
        mlflow.pyfunc.load_model(str(path))
    return {"model_uri": uri, "feature_count": len(model.feature_names_in_), "status": "success"}


@app.command("verify")
def verify(
    uri: Annotated[str, typer.Argument()],
    tracking_root: Annotated[Path, typer.Option()] = DEFAULT_TRACKING,
):
    """Verify a tracked model URI; score parity is checked when training publishes it."""
    try:
        result = verify_model(uri, tracking_root)
    except Exception as exc:
        typer.echo(f"Tracking failed: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(json.dumps(result, indent=2))


@app.command("ui")
def ui(
    tracking_root: Annotated[Path, typer.Option()] = DEFAULT_TRACKING,
    port: Annotated[int, typer.Option(min=1024, max=65535)] = 5001,
):
    """Open the existing local store, including historical experiments."""
    if not (tracking_root / "mlflow.db").is_file():
        raise typer.BadParameter("Train a tracked candidate before starting the UI")
    subprocess.run(
        [
            sys.executable,
            "-m",
            "mlflow",
            "ui",
            "--backend-store-uri",
            tracking_uri(tracking_root),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--workers",
            "1",
        ],
        check=True,
    )


if __name__ == "__main__":
    app()
