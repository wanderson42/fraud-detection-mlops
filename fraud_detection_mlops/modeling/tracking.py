"""Publish a verified historical baseline to local MLflow, without refitting."""

from datetime import UTC, datetime
from importlib.metadata import version
import json
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from typing import Annotated

from filelock import FileLock
import joblib
import mlflow
from mlflow import MlflowClient
from mlflow.models import infer_signature
import mlflow.pyfunc
import mlflow.sklearn
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from sklearn.pipeline import Pipeline
import skops.io as sio
from threadpoolctl import threadpool_limits
import typer

from fraud_detection_mlops.bronze import DEFAULT_INVENTORY, _write_json
from fraud_detection_mlops.features import FEATURE_COLUMNS, METADATA_DTYPES
from fraud_detection_mlops.gold import DEFAULT_GOLD, DEFAULT_GOLD_CONTRACT
from fraud_detection_mlops.modeling import train
from fraud_detection_mlops.profiling import PROJECT_ROOT
from fraud_detection_mlops.silver import DEFAULT_CONTRACT
from fraud_detection_mlops.temporal import DEFAULT_PROTOCOL

DEFAULT_TRACKING = PROJECT_ROOT / "data/tracking"
EXPERIMENT = "fraud-temporal-baseline-v1"
TRACKING_VERSION = "mlflow_skops_v1"
# Reviewed sklearn objects used by the pinned HistGradientBoosting pipeline.
# Unknown types are rejected, never accepted from get_untrusted_types automatically.
TRUSTED_TYPES = {
    "sklearn._loss.link.LogitLink",
    "sklearn._loss.loss.HalfBinomialLoss",
    "sklearn.ensemble._hist_gradient_boosting.binning._BinMapper",
    "sklearn.ensemble._hist_gradient_boosting.predictor.TreePredictor",
}
app = typer.Typer(no_args_is_help=True)


class TrackingError(ValueError):
    """Tracking or persistence does not reconcile with the baseline."""


def tracking_uri(root: Path) -> str:
    return "sqlite:///" + str(root.resolve() / "mlflow.db")


def _context(directory, gold_root, inventory, silver_contract, protocol, gold_contract, config):
    result = train.verify_baseline(
        directory,
        inventory_path=inventory,
        silver_contract=silver_contract,
        protocol_path=protocol,
        gold_contract=gold_contract,
        config_path=config,
    )
    manifest = json.loads((directory / "manifest.json").read_text())
    source_dir = gold_root / manifest["source"]["commit"] / "gold_v1"
    source_manifest = json.loads((source_dir / "manifest.json").read_text())
    if train._sha256(source_dir / "manifest.json") != manifest["gold_manifest_sha256"]:
        raise TrackingError("Gold manifest differs from the baseline input")
    partitions = [f for f in source_manifest["files"] if f["split"] == "validation"]
    recorded = [f for f in manifest["input_partitions"] if f["split"] == "validation"]
    if not partitions or partitions != recorded:
        raise TrackingError("Validation partitions differ from the baseline input")
    # A temporary validation snapshot binds the scored rows to the checked bytes.
    with TemporaryDirectory(prefix="fraud-validation-") as temporary:
        snapshot = Path(temporary)
        for item in partitions:
            relative = Path(item["path"])
            if relative.is_absolute() or ".." in relative.parts:
                raise TrackingError("Invalid Gold partition path")
            target = snapshot / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_dir / relative, target)
            if (
                target.stat().st_size != item["size_bytes"]
                or train._sha256(target) != item["sha256"]
            ):
                raise TrackingError("Validation partition integrity mismatch")
        X, _, metadata = train._read_split(snapshot, {"files": partitions}, "validation")
    scores = {}
    for name in train.CLASSIFIERS:
        predictions = pq.read_table(
            directory / "models" / name / "validation_predictions.parquet"
        ).to_pandas()
        pd.testing.assert_frame_equal(
            metadata.reset_index(drop=True),
            predictions[list(METADATA_DTYPES)].reset_index(drop=True),
            check_exact=True,
        )
        scores[name] = predictions.SCORE.to_numpy()
    return result, manifest, X, scores


def _check_environment(manifest):
    current = {
        "python": sys.version.split()[0],
        **{
            name: version(package)
            for name, package in {
                "sklearn": "scikit-learn",
                "numpy": "numpy",
                "pandas": "pandas",
                "joblib": "joblib",
                "pyarrow": "pyarrow",
                "duckdb": "duckdb",
            }.items()
        },
    }
    if manifest["environment"] != current:
        raise TrackingError("Use the baseline's exact Python and package versions for migration")


def _check_pipeline(model, name, configuration):
    expected_steps = ["scale", "classifier"] if name == "logistic_regression" else ["classifier"]
    if (
        type(model) is not Pipeline
        or list(model.named_steps) != expected_steps
        or type(model.named_steps["classifier"]) is not train.CLASSIFIERS[name]
        or list(model.classes_) != [0, 1]
        or list(model.feature_names_in_) != FEATURE_COLUMNS
        or any(
            model.named_steps["classifier"].get_params()[key] != value
            for key, value in configuration["parameters"].items()
        )
    ):
        raise TrackingError("Persisted pipeline differs from the baseline configuration")
    if (
        name == "logistic_regression"
        and type(model.named_steps["scale"]) is not train.StandardScaler
    ):
        raise TrackingError("Unexpected preprocessing pipeline")


def _check_scores(model, X, expected):
    probabilities = model.predict_proba(X)
    if probabilities.shape != (len(X), 2) or not np.allclose(
        probabilities[:, 1], expected, rtol=1e-12, atol=1e-12
    ):
        raise TrackingError("Persisted model changed validation scores")
    return probabilities


def _check_export(path, name, configuration, X, scores):
    metadata = mlflow.models.Model.load(path / "MLmodel")
    flavor = metadata.flavors["sklearn"]
    if flavor.get("serialization_format") != "skops":
        raise TrackingError("Expected skops serialization")
    model_file = path / flavor["pickled_model"]
    if model_file.resolve().parent != path.resolve():
        raise TrackingError("Unexpected model artifact path")
    unknown = set(sio.get_untrusted_types(file=model_file))
    if not unknown.issubset(TRUSTED_TYPES):
        raise TrackingError(f"Unreviewed skops types: {sorted(unknown - TRUSTED_TYPES)}")
    model = sio.load(model_file, trusted=sorted(TRUSTED_TYPES))
    _check_pipeline(model, name, configuration)
    expected = _check_scores(model, X, scores)
    pyfunc = metadata.flavors["python_function"]
    if (
        pyfunc.get("loader_module") != "mlflow.sklearn"
        or pyfunc.get("predict_fn") != "predict_proba"
        or not set(flavor.get("skops_trusted_types", [])).issubset(TRUSTED_TYPES)
    ):
        raise TrackingError("Unexpected pyfunc serving contract")
    output = np.asarray(mlflow.pyfunc.load_model(str(path)).predict(X))
    if not np.allclose(output, expected, rtol=1e-12, atol=1e-12):
        raise TrackingError("MLflow pyfunc changed class probabilities")
    return model


def _parameters(manifest, name):
    configuration = manifest["config"]["models"][name]
    return {
        "model_id": name,
        "preprocessor": str(configuration["preprocessor"]),
        "feature_count": len(FEATURE_COLUMNS),
        "fit_rows": manifest["split_rows"]["train"],
        "validation_rows": manifest["split_rows"]["validation"],
        **configuration["parameters"],
    }


def _find_runs(client, experiment_id, digest, name):
    return client.search_runs(
        [experiment_id],
        filter_string=(
            f"tags.baseline_manifest_sha256 = '{digest}' AND tags.model_id = '{name}' "
            f"AND tags.tracking_version = '{TRACKING_VERSION}' AND attributes.status = 'FINISHED'"
        ),
        max_results=2,
    )


def _verify_run(client, run, manifest, digest, name, X, scores, metrics, *, status="FINISHED"):
    expected_tags = {
        "baseline_manifest_sha256": digest,
        "baseline_run_id": manifest["run_id"],
        "model_id": name,
        "tracking_version": TRACKING_VERSION,
        "gold_manifest_sha256": manifest["gold_manifest_sha256"],
        "test_evaluated": "false",
        "promotion_status": "not_promoted",
        "model_format": "skops",
        "positive_class_index": "1",
        "score_semantics": "uncalibrated_ranking",
        "source_commit": manifest["source"]["commit"],
        "baseline_created_at_utc": manifest["created_at_utc"],
    }
    if (
        run.info.status != status
        or "mlflow.parentRunId" in run.data.tags
        or any(run.data.tags.get(k) != v for k, v in expected_tags.items())
        or run.data.params != {k: str(v) for k, v in _parameters(manifest, name).items()}
        or run.data.metrics != metrics
    ):
        raise TrackingError("MLflow run metadata or metrics mismatch")
    with TemporaryDirectory(prefix="fraud-mlflow-verify-") as temporary:
        path = Path(client.download_artifacts(run.info.run_id, "model", temporary))
        _check_export(path, name, manifest["config"]["models"][name], X, scores)


def _publish_baseline(
    directory: Path,
    *,
    trust_local_models: bool = False,
    tracking_root: Path = DEFAULT_TRACKING,
    gold_root: Path = DEFAULT_GOLD,
    inventory: Path = DEFAULT_INVENTORY,
    silver_contract: Path = DEFAULT_CONTRACT,
    protocol: Path = DEFAULT_PROTOCOL,
    gold_contract: Path = DEFAULT_GOLD_CONTRACT,
    config: Path = train.DEFAULT_CONFIG,
    verify_only: bool = False,
) -> dict:
    if not verify_only and not trust_local_models:
        raise TrackingError("Migration loads your own joblib models; pass --trust-local-models")
    # Copy first: deserialize only the exact bytes verified in this private snapshot.
    with TemporaryDirectory(prefix="fraud-baseline-export-") as temporary:
        staging = Path(temporary)
        baseline = staging / "baseline"
        shutil.copytree(directory, baseline)
        result, manifest, X, scores = _context(
            baseline, gold_root, inventory, silver_contract, protocol, gold_contract, config
        )
        digest = train._sha256(baseline / "manifest.json")
        _check_environment(manifest)
        root = tracking_root.resolve()
        if verify_only and not (root / "mlflow.db").is_file():
            raise TrackingError("No local tracking database; publish the baseline first")
        root.mkdir(parents=True, exist_ok=True)
        client = MlflowClient(tracking_uri=tracking_uri(root))
        experiment = client.get_experiment_by_name(EXPERIMENT)
        if experiment is None:
            if verify_only:
                raise TrackingError("Baseline experiment not found")
            experiment_id = client.create_experiment(
                EXPERIMENT, artifact_location=(root / "artifacts").as_uri()
            )
        else:
            if experiment.lifecycle_stage != "active":
                raise TrackingError("Restore the deleted baseline experiment before publishing")
            experiment_id = experiment.experiment_id
        records = []
        with threadpool_limits(limits=manifest["config"]["thread_limit"]):
            for name in train.CLASSIFIERS:
                measured = json.loads((baseline / "models" / name / "metrics.json").read_text())
                metrics = {
                    "validation_" + k: v
                    for k, v in result["comparison"][name].items()
                    if v is not None
                } | {k: measured[k] for k in ("fit_seconds", "predict_seconds")}
                existing = _find_runs(client, experiment_id, digest, name)
                if len(existing) > 1:
                    raise TrackingError("Duplicate completed runs; resolve before continuing")
                reused = bool(existing)
                if existing:
                    run = existing[0]
                    _verify_run(client, run, manifest, digest, name, X, scores[name], metrics)
                elif verify_only:
                    raise TrackingError(f"No completed tracking run for {name}")
                else:
                    model = joblib.load(baseline / "models" / name / "model.joblib")
                    configuration = manifest["config"]["models"][name]
                    _check_pipeline(model, name, configuration)
                    probabilities = _check_scores(model, X, scores[name])
                    model_path = staging / name
                    mlflow.sklearn.save_model(
                        model,
                        str(model_path),
                        serialization_format="skops",
                        skops_trusted_types=sorted(TRUSTED_TYPES),
                        pyfunc_predict_fn="predict_proba",
                        signature=infer_signature(X.head(5), probabilities[:5]),
                        pip_requirements=[
                            f"{package}=={version(package)}"
                            for package in (
                                "mlflow",
                                "scikit-learn",
                                "skops",
                                "numpy",
                                "pandas",
                                "scipy",
                            )
                        ],
                    )
                    _check_export(model_path, name, configuration, X, scores[name])
                    run = client.create_run(
                        experiment_id,
                        tags={
                            "mlflow.runName": f"{name}-{manifest['run_id'][:8]}",
                            "model_id": name,
                            "tracking_version": TRACKING_VERSION,
                            "baseline_manifest_sha256": digest,
                            "baseline_run_id": manifest["run_id"],
                            "gold_manifest_sha256": manifest["gold_manifest_sha256"],
                            "source_commit": manifest["source"]["commit"],
                            "baseline_created_at_utc": manifest["created_at_utc"],
                            "tracking_operation": "migration_without_fit",
                            "model_format": "skops",
                            "export_poetry_lock_sha256": train._sha256(
                                PROJECT_ROOT / "poetry.lock"
                            ),
                            "export_implementation_sha256": train._sha256(Path(__file__)),
                            "test_evaluated": "false",
                            "promotion_status": "not_promoted",
                            "selected_on_validation": str(
                                name == result["selected_model"]
                            ).lower(),
                            "positive_class_index": "1",
                            "score_semantics": "uncalibrated_ranking",
                        },
                    )
                    run_id = run.info.run_id
                    try:
                        for key, value in _parameters(manifest, name).items():
                            client.log_param(run_id, key, value)
                        for key, value in metrics.items():
                            client.log_metric(run_id, key, value)
                        client.log_artifacts(run_id, str(model_path), "model")
                        for filename in ("manifest.json", "report.json"):
                            client.log_artifact(run_id, str(baseline / filename), "evidence")
                        for filename in ("metrics.json", "validation_predictions.parquet"):
                            client.log_artifact(
                                run_id, str(baseline / "models" / name / filename), "evidence"
                            )
                        client.log_artifact(
                            run_id, str(PROJECT_ROOT / "poetry.lock"), "export_environment"
                        )
                        _verify_run(
                            client,
                            client.get_run(run_id),
                            manifest,
                            digest,
                            name,
                            X,
                            scores[name],
                            metrics,
                            status="RUNNING",
                        )
                        client.set_terminated(run_id, status="FINISHED")
                    except BaseException as exc:
                        client.set_tag(run_id, "export_error_type", type(exc).__name__)
                        client.set_terminated(
                            run_id,
                            status="KILLED" if isinstance(exc, KeyboardInterrupt) else "FAILED",
                        )
                        raise
                    run = client.get_run(run_id)
                records.append(
                    {
                        "model_id": name,
                        "mlflow_run_id": run.info.run_id,
                        "model_uri": f"runs:/{run.info.run_id}/model",
                        "reused": reused,
                    }
                )
                typer.echo(
                    f"{'verified' if verify_only else 'reused' if reused else 'published'}: {name}"
                )
        receipt = {
            "schema_version": 1,
            "version": TRACKING_VERSION,
            "baseline_run_id": manifest["run_id"],
            "baseline_manifest_sha256": digest,
            "tracking_uri": tracking_uri(root),
            "experiment_id": experiment_id,
            "models": records,
            "test_evaluated": False,
            "refit": False,
            "environment": {
                package: version(package)
                for package in ("mlflow", "skops", "scikit-learn", "numpy", "pandas")
            },
            "export_implementation_sha256": train._sha256(Path(__file__)),
            "export_poetry_lock_sha256": train._sha256(PROJECT_ROOT / "poetry.lock"),
            "selected_model": result["selected_model"],
            "status": "success",
            "recorded_at_utc": datetime.now(UTC).isoformat(),
        }
        if not verify_only:
            _write_json(root / "exports" / f"{digest}.json", receipt)
        return receipt


def publish_baseline(directory: Path, *, tracking_root: Path = DEFAULT_TRACKING, **kwargs) -> dict:
    """Serialize publishers so retries cannot create duplicate completed runs."""
    verify_only = kwargs.get("verify_only", False)
    if not verify_only and not kwargs.get("trust_local_models", False):
        raise TrackingError("Migration loads your own joblib models; pass --trust-local-models")
    root = tracking_root.resolve()
    if verify_only and not (root / "mlflow.db").is_file():
        raise TrackingError("No local tracking database; publish the baseline first")
    root.mkdir(parents=True, exist_ok=True)
    with FileLock(str(root / "publish.lock"), timeout=0):
        return _publish_baseline(directory, tracking_root=root, **kwargs)


@app.command("publish")
def publish(
    directory: Annotated[Path, typer.Argument()],
    trust_local_models: Annotated[bool, typer.Option()] = False,
    tracking_root: Annotated[Path, typer.Option()] = DEFAULT_TRACKING,
    gold_root: Annotated[Path, typer.Option()] = DEFAULT_GOLD,
    inventory: Annotated[Path, typer.Option()] = DEFAULT_INVENTORY,
    protocol: Annotated[Path, typer.Option()] = DEFAULT_PROTOCOL,
):
    """Migrate your verified baseline models to MLflow/skops without training."""
    _execute(
        directory,
        trust_local_models=trust_local_models,
        tracking_root=tracking_root,
        gold_root=gold_root,
        inventory=inventory,
        protocol=protocol,
    )


@app.command("verify")
def verify(
    directory: Annotated[Path, typer.Argument()],
    tracking_root: Annotated[Path, typer.Option()] = DEFAULT_TRACKING,
    gold_root: Annotated[Path, typer.Option()] = DEFAULT_GOLD,
    inventory: Annotated[Path, typer.Option()] = DEFAULT_INVENTORY,
    protocol: Annotated[Path, typer.Option()] = DEFAULT_PROTOCOL,
):
    """Verify tracked models and validation scores; never load baseline joblib."""
    _execute(
        directory,
        verify_only=True,
        tracking_root=tracking_root,
        gold_root=gold_root,
        inventory=inventory,
        protocol=protocol,
    )


def _execute(directory, **kwargs):
    try:
        result = publish_baseline(directory, **kwargs)
    except Exception as exc:
        typer.echo(f"Tracking failed: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(json.dumps(result, indent=2))


@app.command("ui")
def ui(
    tracking_root: Annotated[Path, typer.Option()] = DEFAULT_TRACKING,
    port: Annotated[int, typer.Option(min=1024, max=65535)] = 5001,
):
    """Serve the local tracking UI on loopback, independently of training."""
    if not (tracking_root / "mlflow.db").is_file():
        raise typer.BadParameter("Publish a baseline before starting the UI")
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
