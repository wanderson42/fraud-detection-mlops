"""Train fixed candidates, save skops pipelines, and log native MLflow models."""

from contextlib import nullcontext, redirect_stdout
from datetime import UTC, datetime
from importlib.metadata import version
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from typing import Annotated
from uuid import uuid4

import duckdb
import numpy as np
import pandas as pd
import pyarrow as pa
import sklearn
from threadpoolctl import threadpool_limits
import typer

from fraud_detection_mlops.artifacts import sha256, write_json
from fraud_detection_mlops.config import PROJECT_ROOT
from fraud_detection_mlops.data.contracts.gold_contract import DEFAULT_GOLD_CONTRACT
from fraud_detection_mlops.data.contracts.silver_contract import DEFAULT_CONTRACT
from fraud_detection_mlops.data.contracts.temporal_protocol import DEFAULT_PROTOCOL
from fraud_detection_mlops.data.datasets.gold_dataset import DEFAULT_GOLD, verify_gold
from fraud_detection_mlops.data.ingestion.handbook_inventory import DEFAULT_INVENTORY
from fraud_detection_mlops.features.feature_schema import FEATURE_COLUMNS
from fraud_detection_mlops.integrations import mlflow_tracking as tracking
from fraud_detection_mlops.modeling.contracts.model_interface import (
    VERSION as MODEL_INTERFACE_VERSION,
)
from fraud_detection_mlops.modeling.experiments import (
    baseline_policy as baseline,
)
from fraud_detection_mlops.modeling.experiments import candidate_training
from fraud_detection_mlops.modeling.experiments.baseline_policy import (
    CLASSIFIERS,
    DEFAULT_CONFIG,
    BaselineError,
    verify_baseline,
)
from fraud_detection_mlops.modeling.experiments.candidate_training import load_split
from fraud_detection_mlops.modeling.experiments.experiment_provenance import implementation_files
from fraud_detection_mlops.storage.managed_paths import managed_destination

DEFAULT_BASELINE = PROJECT_ROOT / "data/processed/handbook"
BASELINE_VERSION = "baseline_v2"
HTTP_EXPERIMENT = "fraud-temporal-baseline-http-v1"
DEFAULT_HTTP_BASELINE = PROJECT_ROOT / "data/storage/tracking-lab/baselines"
app = typer.Typer(no_args_is_help=True)


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
    http_uri: str | None = None,
) -> dict:
    if http_uri is not None:
        if tracking_root is not None:
            raise BaselineError("Choose a tracking root or HTTP URI, not both")
        http_uri = tracking.validate_http_uri(http_uri)
    if tracking_root is not None:
        tracking_root = Path(tracking_root)
    target = (
        {"tracking_target": {"mode": "http", "uri": http_uri, "experiment": HTTP_EXPERIMENT}}
        if http_uri is not None
        else {}
    )
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
        **target,
        "model_interface_version": MODEL_INTERFACE_VERSION,
        "implementation_sha256": {
            str(p.relative_to(PROJECT_ROOT)): sha256(p) for p in implementation_files(PROJECT_ROOT)
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
                model, scores, measured, timings = candidate_training.fit_candidate(
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
                candidate_training.save_candidate(
                    staging / "models" / name, model, X_val, scores, meta_val, measurement
                )
            report = selection_report(run_id, results, config)
            write_json(staging / "report.json", report)
            check_inputs(source_dir, source_manifest, identities, paths, audit)
            if tracking_root is not None or http_uri is not None:
                for name, (model, scores, timings) in fitted.items():
                    audit["tracking"][name] = tracking.log_candidate(
                        model,
                        X_val,
                        scores,
                        name=name,
                        root=tracking_root,
                        **(
                            {"http_uri": http_uri, "experiment_name": HTTP_EXPERIMENT}
                            if http_uri is not None
                            else {}
                        ),
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
                    **target,
                    "model_format": "skops",
                    "model_interface_version": MODEL_INTERFACE_VERSION,
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
    output_root: Annotated[Path | None, typer.Option()] = None,
    inventory: Annotated[Path, typer.Option()] = DEFAULT_INVENTORY,
    silver_contract: Annotated[Path, typer.Option()] = DEFAULT_CONTRACT,
    protocol: Annotated[Path, typer.Option()] = DEFAULT_PROTOCOL,
    gold_contract: Annotated[Path, typer.Option()] = DEFAULT_GOLD_CONTRACT,
    config: Annotated[Path, typer.Option()] = DEFAULT_CONFIG,
    tracking_root: Annotated[Path | None, typer.Option()] = None,
    tracking_uri: Annotated[str | None, typer.Option()] = None,
    track: Annotated[bool, typer.Option("--track/--no-track")] = True,
):
    """Fit train, score validation and log three independent pipelines; no test scoring."""
    if tracking_uri is not None and (tracking_root is not None or not track):
        raise typer.BadParameter("--tracking-uri is exclusive with --tracking-root and --no-track")
    try:
        if tracking_uri is not None:
            tracking_uri = tracking.validate_http_uri(tracking_uri)
            output_root = managed_destination(
                output_root if output_root is not None else DEFAULT_HTTP_BASELINE, "storage"
            )
        else:
            output_root = output_root if output_root is not None else DEFAULT_BASELINE
            if tracking_root is not None:
                tracking_root = managed_destination(tracking_root, "tracking")
            elif track:
                tracking_root = tracking.DEFAULT_TRACKING
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    try:
        with redirect_stdout(sys.stderr) if tracking_uri is not None else nullcontext():
            result = run_baseline(
                gold_root,
                output_root,
                inventory_path=inventory,
                silver_contract=silver_contract,
                protocol_path=protocol,
                gold_contract=gold_contract,
                config_path=config,
                tracking_root=tracking_root if track else None,
                **({"http_uri": tracking_uri} if tracking_uri is not None else {}),
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
