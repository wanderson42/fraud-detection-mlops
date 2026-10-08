"""Skops persistence for complete pipelines; no pickle migration path."""

from pathlib import Path

import numpy as np
from sklearn.pipeline import Pipeline
import skops.io as sio

from fraud_detection_mlops.features import FEATURE_COLUMNS

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
    columns = list(feature_columns)
    if not columns or columns != [c for c in FEATURE_COLUMNS if c in columns]:
        raise PersistenceError("Expected an ordered subset of Gold features")
    unknown = set(sio.get_untrusted_types(file=path)) - TRUSTED_TYPES
    if unknown:
        raise PersistenceError(f"Unreviewed skops types: {sorted(unknown)}")
    model = sio.load(path, trusted=sorted(TRUSTED_TYPES))
    if (
        type(model) is not Pipeline
        or list(model.classes_) != [0, 1]
        or list(model.feature_names_in_) != columns
    ):
        raise PersistenceError("Unexpected pipeline classes or feature columns")
    return model


def check_scores(probabilities, scores) -> None:
    values = np.asarray(probabilities)
    if (
        values.shape != (len(scores), 2)
        or not np.isfinite(values).all()
        or not np.allclose(values[:, 1], scores, rtol=1e-12, atol=1e-12)
    ):
        raise PersistenceError("Persisted pipeline changed validation scores")


def save_pipeline(model: Pipeline, path: Path, features, scores) -> None:
    sio.dump(model, path)
    restored = load_pipeline(path, feature_columns=list(features.columns))
    check_scores(restored.predict_proba(features), scores)
