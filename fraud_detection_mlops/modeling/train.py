"""Fit fixed baselines on train and compare rankings on validation only."""

from datetime import UTC, date, datetime, timedelta
import hashlib
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import Annotated
from uuid import uuid4
import warnings

import duckdb
import joblib
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import sklearn
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits
import typer

from fraud_detection_mlops.bronze import DEFAULT_INVENTORY, _write_json, load_inventory
from fraud_detection_mlops.features import (
    DAY_NS,
    FEATURE_COLUMNS,
    LABEL_DELAY_DAYS,
    METADATA_DTYPES,
)
from fraud_detection_mlops.gold import DEFAULT_GOLD, DEFAULT_GOLD_CONTRACT, verify_gold
from fraud_detection_mlops.modeling.metrics import evaluate_ranking
from fraud_detection_mlops.profiling import PROJECT_ROOT
from fraud_detection_mlops.silver import DEFAULT_CONTRACT
from fraud_detection_mlops.temporal import DEFAULT_PROTOCOL, load_protocol

DEFAULT_BASELINE = PROJECT_ROOT / "data/processed/handbook"
DEFAULT_CONFIG = PROJECT_ROOT / "references/baseline_protocol_v1.json"
MODEL_PARAMETERS = {
    "dummy_prior": {"strategy": "prior", "random_state": 42},
    "logistic_regression": {
        "C": 1.0,
        "l1_ratio": 0.0,
        "solver": "lbfgs",
        "max_iter": 1000,
        "class_weight": "balanced",
        "random_state": 42,
    },
    "hist_gradient_boosting": {
        "learning_rate": 0.1,
        "max_iter": 100,
        "max_leaf_nodes": 15,
        "min_samples_leaf": 50,
        "l2_regularization": 1.0,
        "early_stopping": False,
        "categorical_features": None,
        "class_weight": "balanced",
        "random_state": 42,
    },
}
CLASSIFIERS = {
    "dummy_prior": DummyClassifier,
    "logistic_regression": LogisticRegression,
    "hist_gradient_boosting": HistGradientBoostingClassifier,
}
PREDICTION_DTYPES = {**METADATA_DTYPES, "SCORE": "float64"}
PREDICTION_SCHEMA = pa.schema(
    [
        (name, pa.timestamp("ns") if dtype == "datetime64[ns]" else pa.type_for_alias(dtype))
        for name, dtype in PREDICTION_DTYPES.items()
    ]
)
app = typer.Typer(no_args_is_help=True)


class BaselineError(ValueError):
    """A run fails the fixed validation-only baseline contract."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _configuration(path: Path) -> dict:
    value = json.loads(path.read_text())
    models = {
        name: {
            "selection_eligible": name != "dummy_prior",
            "classifier": cls.__name__,
            "parameters": MODEL_PARAMETERS[name],
            "preprocessor": "StandardScaler" if name == "logistic_regression" else None,
        }
        for name, cls in CLASSIFIERS.items()
    }
    expected = {
        "schema_version": 1,
        "version": "baseline_v1",
        "gold_version": "gold_v1",
        "temporal_version": "temporal_v1",
        "fit_split": "train",
        "evaluation_split": "validation",
        "test_evaluated": False,
        "selection_metric": "average_precision",
        "selection_tie_break": "model_id_ascending",
        "refit_before_test": False,
        "thread_limit": 4,
        "models": models,
    }
    if value != expected:
        raise BaselineError("Unsupported baseline configuration; version new experiment policies")
    return value


def make_model(model_id: str) -> Pipeline:
    if model_id not in CLASSIFIERS:
        raise BaselineError(f"Unknown baseline model: {model_id}")
    steps = []
    if model_id == "logistic_regression":
        steps.append(("scale", StandardScaler()))
    steps.append(("classifier", CLASSIFIERS[model_id](**MODEL_PARAMETERS[model_id])))
    return Pipeline(steps)


def _inputs(inventory_path, silver_contract, protocol_path, gold_contract, config_path):
    config = _configuration(config_path)
    inventory = load_inventory(inventory_path)
    protocol = load_protocol(protocol_path, inventory)
    if protocol["random_seed"] != 42:
        raise BaselineError("baseline_v1 fixes random_seed=42")
    identities = {
        name: _sha256(path)
        for name, path in {
            "inventory_sha256": inventory_path,
            "silver_contract_sha256": silver_contract,
            "protocol_sha256": protocol_path,
            "gold_contract_sha256": gold_contract,
            "baseline_config_sha256": config_path,
        }.items()
    }
    return config, inventory, protocol, identities


def _read_split(directory: Path, manifest: dict, split: str) -> tuple:
    """Training loader has no code path for test rows."""
    if split not in ("train", "validation"):
        raise BaselineError("Baseline loader permits only train and validation")
    paths = [str(directory / f["path"]) for f in manifest["files"] if f["split"] == split]
    with duckdb.connect() as connection:
        connection.read_parquet(paths, hive_partitioning=False).create_view("selected")
        names = list(METADATA_DTYPES) + FEATURE_COLUMNS
        frame = connection.execute(
            "SELECT " + ", ".join(names) + " FROM selected ORDER BY TX_DATETIME, TRANSACTION_ID"
        ).fetchdf()
    metadata = frame[list(METADATA_DTYPES)].astype(METADATA_DTYPES)
    return frame[FEATURE_COLUMNS].astype("float64"), metadata.TX_FRAUD.copy(), metadata


def _ranking(results: dict, config: dict) -> list[str]:
    eligible = [name for name in results if config["models"][name]["selection_eligible"]]
    return sorted(eligible, key=lambda name: (-results[name]["average_precision"], name))


def _expected_paths() -> list[str]:
    return ["report.json"] + [
        f"models/{name}/{filename}"
        for name in CLASSIFIERS
        for filename in ("model.joblib", "metrics.json", "validation_predictions.parquet")
    ]


def verify_baseline(
    directory: Path,
    *,
    inventory_path: Path = DEFAULT_INVENTORY,
    silver_contract: Path = DEFAULT_CONTRACT,
    protocol_path: Path = DEFAULT_PROTOCOL,
    gold_contract: Path = DEFAULT_GOLD_CONTRACT,
    config_path: Path = DEFAULT_CONFIG,
) -> dict:
    """Check bytes and recompute validation metrics without deserializing a model."""
    config, inventory, protocol, identities = _inputs(
        inventory_path, silver_contract, protocol_path, gold_contract, config_path
    )
    manifest = json.loads((directory / "manifest.json").read_text())
    if (
        manifest.get("schema_version") != 1
        or manifest.get("version") != "baseline_v1"
        or manifest.get("source") != inventory["source"]
        or manifest.get("protocol") != protocol
        or manifest.get("config") != config
        or manifest.get("feature_columns") != FEATURE_COLUMNS
        or any(manifest.get(name) != value for name, value in identities.items())
    ):
        raise BaselineError("Baseline manifest source, policy or feature contract mismatch")
    expected = _expected_paths()
    files = manifest.get("files", [])
    if [f.get("path") for f in files] != expected or {
        str(p.relative_to(directory)) for p in directory.rglob("*") if p.is_file()
    } != set(expected + ["manifest.json"]):
        raise BaselineError("Incomplete or untracked baseline artifacts")
    for item in files:
        path = directory / item["path"]
        if path.stat().st_size != item.get("size_bytes") or _sha256(path) != item.get("sha256"):
            raise BaselineError(f"Baseline integrity mismatch: {item['path']}")
    results, reference = {}, None
    window = protocol["windows"]["validation"]
    first, end = date.fromisoformat(window["start"]), date.fromisoformat(window["end_exclusive"])
    days = [str(first + timedelta(days=i)) for i in range((end - first).days)]
    for name in CLASSIFIERS:
        part = directory / "models" / name
        parquet = pq.ParquetFile(part / "validation_predictions.parquet")
        if not parquet.schema_arrow.equals(PREDICTION_SCHEMA, check_metadata=False):
            raise BaselineError("Prediction schema mismatch")
        predictions = parquet.read().to_pandas().astype(PREDICTION_DTYPES)
        stamps = predictions.TX_DATETIME
        if (
            stamps.isna().any()
            or not stamps.is_monotonic_increasing
            or sorted(stamps.dt.strftime("%Y-%m-%d").unique()) != days
            or predictions[list(METADATA_DTYPES)].isna().any().any()
            or (predictions.TERMINAL_ID < 0).any()
            or not np.array_equal(
                predictions.LABEL_AVAILABLE_AT.astype("int64"),
                stamps.astype("int64") + LABEL_DELAY_DAYS * DAY_NS,
            )
        ):
            raise BaselineError("Prediction metadata, availability or date coverage mismatch")
        if reference is None:
            reference = predictions[list(METADATA_DTYPES)].copy()
        elif not reference.equals(predictions[list(METADATA_DTYPES)]):
            raise BaselineError("Candidate prediction populations do not match")
        metrics = json.loads((part / "metrics.json").read_text())
        computed = evaluate_ranking(predictions.TX_FRAUD, predictions.SCORE, predictions)
        if (
            metrics.get("model_id") != name
            or metrics.get("metrics") != computed
            or metrics.get("configuration") != config["models"][name]
            or metrics.get("feature_columns") != FEATURE_COLUMNS
            or metrics.get("fit_split") != "train"
            or metrics.get("evaluation_split") != "validation"
            or metrics.get("fit_rows") != manifest.get("split_rows", {}).get("train")
            or computed["rows"] != manifest.get("split_rows", {}).get("validation")
        ):
            raise BaselineError("Recorded metrics, configuration or rows do not reconcile")
        for duration in ("fit_seconds", "predict_seconds"):
            if (
                not isinstance(metrics.get(duration), (int, float))
                or not np.isfinite(metrics[duration])
                or metrics[duration] < 0
            ):
                raise BaselineError("Invalid experiment timing")
        results[name] = computed
    report = json.loads((directory / "report.json").read_text())
    ranking = _ranking(results, config)
    comparison = {
        name: {key: value for key, value in m.items() if key != "daily_customer_metrics"}
        for name, m in results.items()
    }
    if (
        report.get("comparison") != comparison
        or report.get("eligible_ranking") != ranking
        or report.get("selected_model") != ranking[0]
        or report.get("test_evaluated") is not False
        or report.get("refit_before_test") is not False
        or report.get("decision_threshold") is not None
        or report.get("selection_metric") != "average_precision"
        or report.get("feature_columns") != FEATURE_COLUMNS
        or report.get("run_id") != manifest.get("run_id")
        or report.get("selected_beats_dummy_ap")
        != (results[ranking[0]]["average_precision"] > results["dummy_prior"]["average_precision"])
        or report.get("validation_all_genuine_reference")
        != {"accuracy": 1 - results["dummy_prior"]["fraud_rate"], "fraud_recall": 0.0}
    ):
        raise BaselineError("Validation selection report mismatch")
    return {
        "baseline_path": str(directory),
        "run_id": manifest["run_id"],
        "verified_outputs": len(files),
        "selected_model": ranking[0],
        "comparison": comparison,
        "test_evaluated": False,
        "status": "success",
    }


def run_baseline(
    gold_root: Path = DEFAULT_GOLD,
    output_root: Path = DEFAULT_BASELINE,
    *,
    inventory_path: Path = DEFAULT_INVENTORY,
    silver_contract: Path = DEFAULT_CONTRACT,
    protocol_path: Path = DEFAULT_PROTOCOL,
    gold_contract: Path = DEFAULT_GOLD_CONTRACT,
    config_path: Path = DEFAULT_CONFIG,
) -> dict:
    config, inventory, protocol, identities = _inputs(
        inventory_path, silver_contract, protocol_path, gold_contract, config_path
    )
    parent = output_root / inventory["source"]["commit"]
    run_id = uuid4().hex
    destination = parent / "baseline_v1" / run_id
    audit_path = parent / "runs" / f"baseline_{run_id}.json"
    audit = {
        "schema_version": 1,
        "run_id": run_id,
        "status": "running",
        "started_at_utc": datetime.now(UTC).isoformat(),
        "source": inventory["source"],
        **identities,
        "test_evaluated": False,
        "implementation_sha256": {
            str(p.relative_to(PROJECT_ROOT)): _sha256(p)
            for p in (Path(__file__), Path(__file__).with_name("metrics.py"))
        },
        "poetry_lock_sha256": _sha256(PROJECT_ROOT / "poetry.lock"),
        "environment": {
            "python": sys.version.split()[0],
            "sklearn": sklearn.__version__,
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "duckdb": duckdb.__version__,
            "pyarrow": pa.__version__,
            "joblib": joblib.__version__,
        },
    }
    _write_json(audit_path, audit)
    try:
        verified = verify_gold(
            gold_root,
            inventory_path=inventory_path,
            silver_contract=silver_contract,
            protocol_path=protocol_path,
            gold_contract=gold_contract,
        )
        source_dir = Path(verified["gold_path"])
        manifest_path = source_dir / "manifest.json"
        gold_hash = _sha256(manifest_path)
        source_manifest = json.loads(manifest_path.read_text())
        audit["gold_manifest_sha256"] = gold_hash
        X_train, y_train, meta_train = _read_split(source_dir, source_manifest, "train")
        X_val, y_val, meta_val = _read_split(source_dir, source_manifest, "validation")
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
        results = {}
        parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix=".baseline-staging-", dir=parent) as temporary:
            staging = Path(temporary) / run_id
            staging.mkdir()
            with threadpool_limits(limits=config["thread_limit"]):
                for name in CLASSIFIERS:
                    typer.echo(f"Fitting {name} on train; scoring validation...")
                    model = make_model(name)
                    with warnings.catch_warnings():
                        warnings.simplefilter("error", ConvergenceWarning)
                        start = perf_counter()
                        model.fit(X_train, y_train)
                        fit_seconds = perf_counter() - start
                    start = perf_counter()
                    classes = list(model.classes_)
                    if classes != [0, 1]:
                        raise BaselineError("Expected binary model classes [0, 1]")
                    scores = model.predict_proba(X_val)[:, classes.index(1)]
                    predict_seconds = perf_counter() - start
                    measured = evaluate_ranking(y_val, scores, meta_val)
                    results[name] = measured
                    part = staging / "models" / name
                    part.mkdir(parents=True)
                    joblib.dump(model, part / "model.joblib", compress=3)
                    # Only reload the artifact just written by this process.
                    restored = joblib.load(part / "model.joblib")
                    if not np.allclose(
                        restored.predict_proba(X_val)[:, 1], scores, rtol=1e-12, atol=1e-12
                    ):
                        raise BaselineError("Persisted pipeline changed validation scores")
                    predictions = meta_val.copy()
                    predictions["SCORE"] = scores
                    predictions = predictions.astype(PREDICTION_DTYPES)
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
                    _write_json(
                        part / "metrics.json",
                        {
                            "model_id": name,
                            "fit_split": "train",
                            "evaluation_split": "validation",
                            "fit_rows": len(y_train),
                            "configuration": config["models"][name],
                            "feature_columns": FEATURE_COLUMNS,
                            "metrics": measured,
                            "fit_seconds": fit_seconds,
                            "predict_seconds": predict_seconds,
                        },
                    )
            ranking = _ranking(results, config)
            _write_json(
                staging / "report.json",
                {
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
                        "accuracy": 1 - float(y_val.mean()),
                        "fraud_recall": 0.0,
                    },
                },
            )
            records = [
                {
                    "path": name,
                    "size_bytes": (staging / name).stat().st_size,
                    "sha256": _sha256(staging / name),
                }
                for name in _expected_paths()
            ]
            if (
                _sha256(manifest_path) != gold_hash
                or any(
                    _sha256(source_dir / f["path"]) != f["sha256"]
                    for f in source_manifest["files"]
                )
                or any(
                    _sha256(path) != identities[name]
                    for name, path in {
                        "inventory_sha256": inventory_path,
                        "silver_contract_sha256": silver_contract,
                        "protocol_sha256": protocol_path,
                        "gold_contract_sha256": gold_contract,
                        "baseline_config_sha256": config_path,
                    }.items()
                )
                or _sha256(PROJECT_ROOT / "poetry.lock") != audit["poetry_lock_sha256"]
                or any(
                    _sha256(PROJECT_ROOT / name) != digest
                    for name, digest in audit["implementation_sha256"].items()
                )
            ):
                raise BaselineError("Inputs or implementation changed during baseline run")
            _write_json(
                staging / "manifest.json",
                {
                    "schema_version": 1,
                    "version": "baseline_v1",
                    "run_id": run_id,
                    "source": inventory["source"],
                    **identities,
                    "gold_manifest_sha256": gold_hash,
                    "config": config,
                    "protocol": protocol,
                    "feature_columns": FEATURE_COLUMNS,
                    "split_rows": {"train": len(y_train), "validation": len(y_val)},
                    "input_partitions": [
                        f
                        for f in source_manifest["files"]
                        if f["split"] in ("train", "validation")
                    ],
                    "files": records,
                    "environment": audit["environment"],
                    "implementation_sha256": audit["implementation_sha256"],
                    "poetry_lock_sha256": audit["poetry_lock_sha256"],
                    "created_at_utc": datetime.now(UTC).isoformat(),
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
            result["baseline_path"] = str(destination)
        audit["status"] = "success"
        audit["result"] = result
    except BaseException as exc:
        audit["status"] = "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
        audit["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        audit["finished_at_utc"] = datetime.now(UTC).isoformat()
        _write_json(audit_path, audit)
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
):
    """Train fixed candidates and publish validation comparison; no test evaluation."""
    try:
        result = run_baseline(
            gold_root,
            output_root,
            inventory_path=inventory,
            silver_contract=silver_contract,
            protocol_path=protocol,
            gold_contract=gold_contract,
            config_path=config,
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
    """Verify a validation experiment without loading executable model objects."""
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
