"""Native MLflow logging of newly fitted pipelines; no historical conversion."""

from contextlib import contextmanager
from importlib.metadata import version
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from typing import Annotated

import mlflow
from mlflow import MlflowClient
from mlflow.models import infer_signature
import mlflow.pyfunc
import mlflow.sklearn
import typer

from fraud_detection_mlops.config import PROJECT_ROOT
from fraud_detection_mlops.features import FEATURE_COLUMNS
from fraud_detection_mlops.modeling.persistence import TRUSTED_TYPES, check_scores, load_pipeline

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


def download_pipeline(uri: str, destination: Path):
    """Download and review a native sklearn/skops pipeline for prediction or inspection."""
    path = Path(mlflow.artifacts.download_artifacts(artifact_uri=uri, dst_path=str(destination)))
    metadata = mlflow.models.Model.load(path / "MLmodel")
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
        or metadata.signature.inputs.input_names() != FEATURE_COLUMNS
    ):
        raise TrackingError("Unexpected MLflow model format, loader or signature")
    return load_pipeline(model_file), path


def load_tracked_model(uri: str, destination: Path):
    """Review the pipeline before invoking the native pyfunc loader."""
    _, path = download_pipeline(uri, destination)
    return mlflow.pyfunc.load_model(str(path))


def log_candidate(model, features, scores, *, name, parameters, metrics, tags, root):
    """One main run per fitted pipeline; a reload failure makes the run FAILED."""
    if mlflow.active_run() is not None:
        raise TrackingError("Finish the active MLflow run before logging independent candidates")
    root.mkdir(parents=True, exist_ok=True)
    with local_tracking(root):
        client = MlflowClient()
        experiment = client.get_experiment_by_name(EXPERIMENT)
        experiment_id = (
            experiment.experiment_id
            if experiment is not None
            else client.create_experiment(
                EXPERIMENT, artifact_location=(root / "artifacts").resolve().as_uri()
            )
        )
        with mlflow.start_run(
            experiment_id=experiment_id, run_name=f"{name}-{tags['baseline_run_id'][:8]}"
        ) as run:
            mlflow.log_params(parameters)
            mlflow.log_metrics(metrics)
            mlflow.set_tags(
                {**tags, "model_id": name, "model_format": "skops", "test_evaluated": "false"}
            )
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
            with TemporaryDirectory(prefix="fraud-mlflow-check-") as temporary:
                restored = load_tracked_model(logged.model_uri, Path(temporary))
                check_scores(restored.predict(features), scores)
            return {"run_id": run.info.run_id, "model_uri": logged.model_uri}


def verify_model(uri: str, root: Path = DEFAULT_TRACKING) -> dict:
    """Load a model from the existing local store; no fit or baseline conversion."""
    if not (root / "mlflow.db").is_file():
        raise TrackingError("No local tracking database")
    if not uri.startswith(("runs:/", "models:/")):
        raise TrackingError("Use a runs:/ or models:/ URI from your local tracking store")
    with local_tracking(root), TemporaryDirectory(prefix="fraud-mlflow-check-") as temporary:
        load_tracked_model(uri, Path(temporary))
    return {"model_uri": uri, "feature_count": len(FEATURE_COLUMNS), "status": "success"}


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
