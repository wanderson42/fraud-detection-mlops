"""Fixed candidate policy and non-executable verification of experiment artifacts."""

from datetime import date, timedelta
import json
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from fraud_detection_mlops.artifacts import sha256
from fraud_detection_mlops.bronze import DEFAULT_INVENTORY, load_inventory
from fraud_detection_mlops.config import PROJECT_ROOT
from fraud_detection_mlops.features import (
    DAY_NS,
    FEATURE_COLUMNS,
    LABEL_DELAY_DAYS,
    METADATA_DTYPES,
)
from fraud_detection_mlops.gold import DEFAULT_GOLD_CONTRACT
from fraud_detection_mlops.modeling.metrics import evaluate_ranking
from fraud_detection_mlops.silver import DEFAULT_CONTRACT
from fraud_detection_mlops.temporal import DEFAULT_PROTOCOL, load_protocol

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


class BaselineError(ValueError):
    """A run fails the fixed validation-only baseline contract."""


def load_configuration(path: Path) -> dict:
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


def load_inputs(inventory_path, silver_contract, protocol_path, gold_contract, config_path):
    config = load_configuration(config_path)
    inventory = load_inventory(inventory_path)
    protocol = load_protocol(protocol_path, inventory)
    if protocol["random_seed"] != 42:
        raise BaselineError("baseline_v1 fixes random_seed=42")
    identities = {
        name: sha256(path)
        for name, path in {
            "inventory_sha256": inventory_path,
            "silver_contract_sha256": silver_contract,
            "protocol_sha256": protocol_path,
            "gold_contract_sha256": gold_contract,
            "baseline_config_sha256": config_path,
        }.items()
    }
    return config, inventory, protocol, identities


def rank_models(results: dict, config: dict) -> list[str]:
    eligible = [name for name in results if config["models"][name]["selection_eligible"]]
    return sorted(eligible, key=lambda name: (-results[name]["average_precision"], name))


def expected_paths(version: str = "baseline_v2") -> list[str]:
    model_file = "model.joblib" if version == "baseline_v1" else "model.skops"
    return ["report.json"] + [
        f"models/{name}/{filename}"
        for name in CLASSIFIERS
        for filename in (model_file, "metrics.json", "validation_predictions.parquet")
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
    config, inventory, protocol, identities = load_inputs(
        inventory_path, silver_contract, protocol_path, gold_contract, config_path
    )
    manifest = json.loads((directory / "manifest.json").read_text())
    if (
        manifest.get("schema_version") != 1
        or manifest.get("version") not in {"baseline_v1", "baseline_v2"}
        or (manifest.get("version") == "baseline_v2" and manifest.get("model_format") != "skops")
        or manifest.get("source") != inventory["source"]
        or manifest.get("protocol") != protocol
        or manifest.get("config") != config
        or manifest.get("feature_columns") != FEATURE_COLUMNS
        or any(manifest.get(name) != value for name, value in identities.items())
    ):
        raise BaselineError("Baseline manifest source, policy or feature contract mismatch")
    expected = expected_paths(manifest["version"])
    files = manifest.get("files", [])
    if [f.get("path") for f in files] != expected or {
        str(p.relative_to(directory)) for p in directory.rglob("*") if p.is_file()
    } != set(expected + ["manifest.json"]):
        raise BaselineError("Incomplete or untracked baseline artifacts")
    for item in files:
        path = directory / item["path"]
        if path.stat().st_size != item.get("size_bytes") or sha256(path) != item.get("sha256"):
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
    ranking = rank_models(results, config)
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
