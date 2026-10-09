"""Skops persistence for complete pipelines; no pickle migration path."""

from pathlib import Path

import numpy as np
from sklearn.pipeline import Pipeline
import skops.io as sio

from fraud_detection_mlops.features.feature_schema import FEATURE_COLUMNS
from fraud_detection_mlops.modeling.contracts.model_interface import (
    ModelContractError,
    validate_pipeline,
    validate_probabilities,
)

# Reviewed objects required by the pinned HistGradientBoosting implementation.
TRUSTED_TYPES = {
    "sklearn._loss.link.LogitLink",
    "sklearn._loss.loss.HalfBinomialLoss",
    "sklearn.ensemble._hist_gradient_boosting.binning._BinMapper",
    "sklearn.ensemble._hist_gradient_boosting.predictor.TreePredictor",
}


class PersistenceError(ValueError):
    """A persisted pipeline does not satisfy the feature or score contract."""


def load_pipeline(path: Path, *, feature_columns=FEATURE_COLUMNS) -> Pipeline:
    return load_pipeline_bytes(path.read_bytes(), feature_columns=feature_columns)


def load_pipeline_bytes(data: bytes, *, feature_columns=FEATURE_COLUMNS) -> Pipeline:
    """Review and load the same bytes; file changes cannot bypass a prior hash check."""
    columns = list(feature_columns)
    if not columns or columns != [c for c in FEATURE_COLUMNS if c in columns]:
        raise PersistenceError("Expected an ordered subset of Gold features")
    unknown = set(sio.get_untrusted_types(data=data)) - TRUSTED_TYPES
    if unknown:
        raise PersistenceError(f"Unreviewed skops types: {sorted(unknown)}")
    model = sio.loads(data, trusted=sorted(TRUSTED_TYPES))
    try:
        validate_pipeline(model, columns)
    except ModelContractError as exc:
        raise PersistenceError(str(exc)) from exc
    return model


def check_scores(probabilities, scores) -> None:
    try:
        values = validate_probabilities(probabilities, len(scores))
    except ModelContractError as exc:
        raise PersistenceError(str(exc)) from exc
    expected = np.asarray(scores)
    if expected.shape != (len(values),) or not np.allclose(
        values[:, 1], expected, rtol=1e-12, atol=1e-12
    ):
        raise PersistenceError("Persisted pipeline changed validation scores")


def save_pipeline(model: Pipeline, path: Path, features, scores) -> None:
    sio.dump(model, path)
    restored = load_pipeline(path, feature_columns=list(features.columns))
    check_scores(restored.predict_proba(features), scores)
