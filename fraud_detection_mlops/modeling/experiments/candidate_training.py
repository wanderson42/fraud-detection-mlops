"""Shared fitting and prediction artifacts for contract-compatible candidates."""

from pathlib import Path

import duckdb
import pandas as pd
import pyarrow.parquet as pq

from fraud_detection_mlops.artifacts import write_json
from fraud_detection_mlops.evaluation.ranking_metrics import evaluate_ranking
from fraud_detection_mlops.features.feature_schema import FEATURE_COLUMNS, METADATA_DTYPES
from fraud_detection_mlops.integrations import skops_persistence as persistence
from fraud_detection_mlops.modeling.algorithms.model_catalog import registered_model
from fraud_detection_mlops.modeling.contracts.experiment_errors import BaselineError
from fraud_detection_mlops.modeling.contracts.model_interface import (
    ModelContractError,
    ModelSpec,
    build_model,
    fit_and_score,
    validate_labels,
)
from fraud_detection_mlops.modeling.contracts.prediction_schema import PREDICTION_DTYPES


def make_model(model_id):
    try:
        return registered_model(model_id)
    except KeyError as exc:
        raise BaselineError(f"Unknown registered model: {model_id}") from exc


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


def fit_candidate(
    name,
    X_train,
    y_train,
    X_val,
    y_val,
    metadata,
    *,
    model_spec: ModelSpec | None = None,
    parameters=None,
    random_state=42,
):
    """Common integration point; temporal authorization stays with the caller's policy."""
    validate_labels(y_val, X_val, require_both=False)
    if (
        not isinstance(y_val, pd.Series)
        or not y_val.index.equals(X_val.index)
        or not metadata.index.equals(X_val.index)
        or len(metadata) != len(X_val)
        or ("TX_FRAUD" in metadata and not metadata.TX_FRAUD.equals(y_val))
    ):
        raise ModelContractError("Evaluation labels, features and metadata are not aligned")
    if model_spec is None:
        if parameters is not None or random_state != 42:
            raise ModelContractError("Fixed baseline parameters require an explicit model spec")
        model = make_model(name)
    else:
        if model_spec.model_id != name:
            raise ModelContractError("Candidate identity differs from its model spec")
        model = build_model(model_spec, parameters=parameters, random_state=random_state)
    model, scores, timings = fit_and_score(model, X_train, y_train, X_val)
    return model, scores, evaluate_ranking(y_val, scores, metadata), timings


def save_candidate(part, model, X_val, scores, metadata, measurement):
    part.mkdir(parents=True)
    persistence.save_pipeline(model, part / "model.skops", X_val, scores)
    predictions = (
        metadata[list(METADATA_DTYPES)]
        .assign(SCORE=scores)
        .astype(PREDICTION_DTYPES)
        .reset_index(drop=True)
    )
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
