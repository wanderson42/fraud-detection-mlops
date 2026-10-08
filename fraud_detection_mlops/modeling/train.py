"""Train fixed candidates, save skops pipelines, and log native MLflow models."""

from datetime import UTC, datetime
from importlib.metadata import version
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import Annotated
from uuid import uuid4
import warnings

import duckdb
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import sklearn
from sklearn.exceptions import ConvergenceWarning
from threadpoolctl import threadpool_limits
import typer

from fraud_detection_mlops.artifacts import sha256, write_json
from fraud_detection_mlops.bronze import DEFAULT_INVENTORY
from fraud_detection_mlops.config import PROJECT_ROOT
from fraud_detection_mlops.features import FEATURE_COLUMNS, METADATA_DTYPES
from fraud_detection_mlops.gold import DEFAULT_GOLD, DEFAULT_GOLD_CONTRACT, verify_gold
from fraud_detection_mlops.modeling import baseline, persistence, tracking
from fraud_detection_mlops.modeling.baseline import (
    CLASSIFIERS,
    DEFAULT_CONFIG,
    PREDICTION_DTYPES,
    BaselineError,
    make_model,
    verify_baseline,
)
from fraud_detection_mlops.modeling.metrics import evaluate_ranking
from fraud_detection_mlops.silver import DEFAULT_CONTRACT
from fraud_detection_mlops.temporal import DEFAULT_PROTOCOL

DEFAULT_BASELINE = PROJECT_ROOT / "data/processed/handbook"
BASELINE_VERSION = "baseline_v2"
app = typer.Typer(no_args_is_help=True)


def load_split(directory: Path, manifest: dict, split: str) -> tuple:
    """Only train and validation enter the modeling loader."""
    if split not in ("train", "validation"):
        raise BaselineError("Baseline loader permits only train and validation")
    paths = [str(directory / f["path"]) for f in manifest["files"] if f["split"] == split]
    with duckdb.connect() as connection:
        connection.read_parquet(paths, hive_partitioning=False).create_view("selected")
        frame = connection.execute(
            "SELECT "
            + ", ".join(list(METADATA_DTYPES) + FEATURE_COLUMNS)
            + " FROM selected ORDER BY TX_DATETIME, TRANSACTION_ID"
        ).fetchdf()
    metadata = frame[list(METADATA_DTYPES)].astype(METADATA_DTYPES)
    return frame[FEATURE_COLUMNS].astype("float64"), metadata.TX_FRAUD.copy(), metadata


def fit_candidate(name, X_train, y_train, X_val, y_val, metadata):
    model = make_model(name)
    with warnings.catch_warnings():
        warnings.simplefilter("error", ConvergenceWarning)
        start = perf_counter()
        model.fit(X_train, y_train)
        fit_seconds = perf_counter() - start
    if list(model.classes_) != [0, 1]:
        raise BaselineError("Expected binary model classes [0, 1]")
    start = perf_counter()
    scores = model.predict_proba(X_val)[:, 1]
    timings = {"fit_seconds": fit_seconds, "predict_seconds": perf_counter() - start}
    return model, scores, evaluate_ranking(y_val, scores, metadata), timings


def save_candidate(part, model, X_val, scores, metadata, measurement):
    part.mkdir(parents=True)
    persistence.save_pipeline(model, part / "model.skops", X_val, scores)
    predictions = metadata.assign(SCORE=scores).astype(PREDICTION_DTYPES)
    predictions.to_parquet(
        part / "validation_predictions.parquet",
        engine="pyarrow",
        compression="zstd",
        index=False,
        version="2.6",
    )
    pd.testing.assert_frame_equal(
        predictions,
        pq.ParquetFile(part / "validation_predictions.parquet").read().to_pandas(),
        check_exact=True,
    )
    write_json(part / "metrics.json", measurement)


def selection_report(run_id, results, config):
    ranking = baseline.rank_models(results, config)
    return {
        "schema_version": 1,
        "run_id": run_id,
        "feature_columns": FEATURE_COLUMNS,
        "selection_metric": "average_precision",
        "eligible_ranking": ranking,
        "selected_model": ranking[0],
        "test_evaluated": False,
        "refit_before_test": False,
        "decision_threshold": None,
        "comparison": {
            name: {k: v for k, v in m.items() if k != "daily_customer_metrics"}
            for name, m in results.items()
        },
        "selected_beats_dummy_ap": results[ranking[0]]["average_precision"]
        > results["dummy_prior"]["average_precision"],
        "validation_all_genuine_reference": {
            "accuracy": 1 - results["dummy_prior"]["fraud_rate"],
            "fraud_recall": 0.0,
        },
    }


def check_inputs(source_dir, source_manifest, identities, paths, audit):
    changed = (
        sha256(source_dir / "manifest.json") != audit["gold_manifest_sha256"]
        or any(sha256(source_dir / f["path"]) != f["sha256"] for f in source_manifest["files"])
        or any(sha256(path) != identities[name] for name, path in paths.items())
        or sha256(PROJECT_ROOT / "poetry.lock") != audit["poetry_lock_sha256"]
        or any(
            sha256(PROJECT_ROOT / name) != digest
            for name, digest in audit["implementation_sha256"].items()
        )
    )
    if changed:
        raise BaselineError("Inputs or implementation changed during baseline run")


def run_baseline(
    gold_root: Path = DEFAULT_GOLD,
    output_root: Path = DEFAULT_BASELINE,
    *,
    inventory_path: Path = DEFAULT_INVENTORY,
    silver_contract: Path = DEFAULT_CONTRACT,
    protocol_path: Path = DEFAULT_PROTOCOL,
    gold_contract: Path = DEFAULT_GOLD_CONTRACT,
    config_path: Path = DEFAULT_CONFIG,
    tracking_root: Path | None = tracking.DEFAULT_TRACKING,
) -> dict:
    config, inventory, protocol, identities = baseline.load_inputs(
        inventory_path, silver_contract, protocol_path, gold_contract, config_path
    )
    parent = output_root / inventory["source"]["commit"]
    run_id = uuid4().hex
    destination = parent / BASELINE_VERSION / run_id
    audit_path = parent / "runs" / f"baseline_{run_id}.json"
    audit = {
        "schema_version": 1,
        "run_id": run_id,
        "status": "running",
        "started_at_utc": datetime.now(UTC).isoformat(),
        "source": inventory["source"],
        **identities,
        "test_evaluated": False,
        "tracking": {},
        "implementation_sha256": {
            str(p.relative_to(PROJECT_ROOT)): sha256(p)
            for p in [
                *Path(__file__).parent.glob("*.py"),
                PROJECT_ROOT / "fraud_detection_mlops/artifacts.py",
            ]
        },
        "poetry_lock_sha256": sha256(PROJECT_ROOT / "poetry.lock"),
        "environment": {
            "python": sys.version.split()[0],
            "sklearn": sklearn.__version__,
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "duckdb": duckdb.__version__,
            "pyarrow": pa.__version__,
            "skops": version("skops"),
            "mlflow": version("mlflow"),
        },
    }
    write_json(audit_path, audit)
    try:
        verified = verify_gold(
            gold_root,
            inventory_path=inventory_path,
            silver_contract=silver_contract,
            protocol_path=protocol_path,
            gold_contract=gold_contract,
        )
        source_dir = Path(verified["gold_path"])
        source_manifest = json.loads((source_dir / "manifest.json").read_text())
        audit["gold_manifest_sha256"] = sha256(source_dir / "manifest.json")
        X_train, y_train, meta_train = load_split(source_dir, source_manifest, "train")
        X_val, y_val, meta_val = load_split(source_dir, source_manifest, "validation")
        if (
            set(y_train.unique()) != {0, 1}
            or set(y_val.unique()) != {0, 1}
            or set(meta_train.TRANSACTION_ID) & set(meta_val.TRANSACTION_ID)
            or meta_train.LABEL_AVAILABLE_AT.max()
            >= pd.Timestamp(protocol["windows"]["validation"]["start"])
            or not np.isfinite(X_train.to_numpy()).all()
            or not np.isfinite(X_val.to_numpy()).all()
        ):
            raise BaselineError("Invalid training classes, feature values or label availability")
        paths = {
            "inventory_sha256": inventory_path,
            "silver_contract_sha256": silver_contract,
            "protocol_sha256": protocol_path,
            "gold_contract_sha256": gold_contract,
            "baseline_config_sha256": config_path,
        }
        with (
            TemporaryDirectory(prefix=".baseline-staging-", dir=parent) as temporary,
            threadpool_limits(limits=config["thread_limit"]),
        ):
            staging = Path(temporary) / run_id
            staging.mkdir()
            results, fitted = {}, {}
            for name in CLASSIFIERS:
                typer.echo(f"Fitting {name} on train; scoring validation...")
                model, scores, measured, timings = fit_candidate(
                    name, X_train, y_train, X_val, y_val, meta_val
                )
                results[name] = measured
                fitted[name] = (model, scores, timings)
                measurement = {
                    "model_id": name,
                    "fit_split": "train",
                    "evaluation_split": "validation",
                    "fit_rows": len(y_train),
                    "configuration": config["models"][name],
                    "feature_columns": FEATURE_COLUMNS,
                    "metrics": measured,
                    **timings,
                }
                save_candidate(
                    staging / "models" / name, model, X_val, scores, meta_val, measurement
                )
            report = selection_report(run_id, results, config)
            write_json(staging / "report.json", report)
            check_inputs(source_dir, source_manifest, identities, paths, audit)
            if tracking_root is not None:
                for name, (model, scores, timings) in fitted.items():
                    audit["tracking"][name] = tracking.log_candidate(
                        model,
                        X_val,
                        scores,
                        name=name,
                        root=tracking_root,
                        parameters={
                            **config["models"][name]["parameters"],
                            "fit_rows": len(y_train),
                            "feature_count": len(FEATURE_COLUMNS),
                        },
                        metrics={
                            **{
                                "validation_" + k: v
                                for k, v in report["comparison"][name].items()
                                if v is not None
                            },
                            **timings,
                        },
                        tags={
                            "baseline_run_id": run_id,
                            "source_commit": inventory["source"]["commit"],
                            "gold_manifest_sha256": audit["gold_manifest_sha256"],
                            "baseline_version": BASELINE_VERSION,
                            "poetry_lock_sha256": audit["poetry_lock_sha256"],
                            "promotion_status": "not_promoted",
                            "score_semantics": "uncalibrated_ranking",
                            "selected_on_validation": str(
                                name == report["selected_model"]
                            ).lower(),
                        },
                    )
            check_inputs(source_dir, source_manifest, identities, paths, audit)
            write_json(
                staging / "manifest.json",
                {
                    "schema_version": 1,
                    "version": BASELINE_VERSION,
                    "run_id": run_id,
                    "source": inventory["source"],
                    **identities,
                    "gold_manifest_sha256": audit["gold_manifest_sha256"],
                    "config": config,
                    "protocol": protocol,
                    "feature_columns": FEATURE_COLUMNS,
                    "split_rows": {"train": len(y_train), "validation": len(y_val)},
                    "input_partitions": [
                        f
                        for f in source_manifest["files"]
                        if f["split"] in ("train", "validation")
                    ],
                    "files": [
                        {
                            "path": name,
                            "size_bytes": (staging / name).stat().st_size,
                            "sha256": sha256(staging / name),
                        }
                        for name in baseline.expected_paths()
                    ],
                    "environment": audit["environment"],
                    "implementation_sha256": audit["implementation_sha256"],
                    "poetry_lock_sha256": audit["poetry_lock_sha256"],
                    "created_at_utc": datetime.now(UTC).isoformat(),
                    "tracking": audit["tracking"],
                    "model_format": "skops",
                },
            )
            result = verify_baseline(
                staging,
                inventory_path=inventory_path,
                silver_contract=silver_contract,
                protocol_path=protocol_path,
                gold_contract=gold_contract,
                config_path=config_path,
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            staging.rename(destination)
            result.update(baseline_path=str(destination), tracking=audit["tracking"])
        audit.update(status="success", result=result)
    except BaseException as exc:
        audit.update(
            status="interrupted" if isinstance(exc, KeyboardInterrupt) else "failed",
            error=f"{type(exc).__name__}: {exc}",
        )
        raise
    finally:
        audit["finished_at_utc"] = datetime.now(UTC).isoformat()
        write_json(audit_path, audit)
    return {**result, "audit_path": str(audit_path)}


@app.command()
def run(
    gold_root: Annotated[Path, typer.Option()] = DEFAULT_GOLD,
    output_root: Annotated[Path, typer.Option()] = DEFAULT_BASELINE,
    inventory: Annotated[Path, typer.Option()] = DEFAULT_INVENTORY,
    silver_contract: Annotated[Path, typer.Option()] = DEFAULT_CONTRACT,
    protocol: Annotated[Path, typer.Option()] = DEFAULT_PROTOCOL,
    gold_contract: Annotated[Path, typer.Option()] = DEFAULT_GOLD_CONTRACT,
    config: Annotated[Path, typer.Option()] = DEFAULT_CONFIG,
    tracking_root: Annotated[Path, typer.Option()] = tracking.DEFAULT_TRACKING,
    track: Annotated[bool, typer.Option("--track/--no-track")] = True,
):
    """Fit train, score validation and log three independent pipelines; no test scoring."""
    try:
        result = run_baseline(
            gold_root,
            output_root,
            inventory_path=inventory,
            silver_contract=silver_contract,
            protocol_path=protocol,
            gold_contract=gold_contract,
            config_path=config,
            tracking_root=tracking_root if track else None,
        )
    except Exception as exc:
        typer.echo(f"Baseline failed: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(json.dumps(result, indent=2))


@app.command()
def verify(
    directory: Annotated[Path, typer.Argument()],
    inventory: Annotated[Path, typer.Option()] = DEFAULT_INVENTORY,
    silver_contract: Annotated[Path, typer.Option()] = DEFAULT_CONTRACT,
    protocol: Annotated[Path, typer.Option()] = DEFAULT_PROTOCOL,
    gold_contract: Annotated[Path, typer.Option()] = DEFAULT_GOLD_CONTRACT,
    config: Annotated[Path, typer.Option()] = DEFAULT_CONFIG,
):
    """Check historical v1 or current v2 artifacts without loading any model."""
    try:
        result = verify_baseline(
            directory,
            inventory_path=inventory,
            silver_contract=silver_contract,
            protocol_path=protocol,
            gold_contract=gold_contract,
            config_path=config,
        )
    except Exception as exc:
        typer.echo(f"Baseline verification failed: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(json.dumps(result, indent=2))


if __name__ == "__main__":
    app()
