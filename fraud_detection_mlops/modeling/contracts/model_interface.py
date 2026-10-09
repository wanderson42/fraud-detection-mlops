"""Model integration rules shared by training, persistence and future search."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import json
import re
from time import perf_counter
import warnings

import numpy as np
import pandas as pd
from sklearn.base import clone, is_classifier
from sklearn.exceptions import ConvergenceWarning, NotFittedError
from sklearn.pipeline import Pipeline
from sklearn.utils import get_tags
from sklearn.utils.validation import check_is_fitted

from fraud_detection_mlops.features.feature_schema import FEATURE_COLUMNS

VERSION = "model_interface_v1"


class ModelContractError(ValueError):
    """A contribution violates the model input, construction or output contract."""


@dataclass(frozen=True)
class ModelSpec:
    """Explicit identity and a factory returning a new, unfitted complete Pipeline."""

    model_id: str
    factory: Callable[[], Pipeline]


def validate_features(features: pd.DataFrame, columns: Sequence[str]) -> None:
    """Require finite real data and an explicit, canonical Gold feature subset."""
    expected = list(columns)
    if (
        not expected
        or expected != [name for name in FEATURE_COLUMNS if name in expected]
        or not isinstance(features, pd.DataFrame)
        or features.empty
        or list(features.columns) != expected
        or not features.index.is_unique
        or features.isna().any().any()
        or any(
            not pd.api.types.is_numeric_dtype(dtype)
            or pd.api.types.is_bool_dtype(dtype)
            or pd.api.types.is_complex_dtype(dtype)
            for dtype in features.dtypes
        )
        or not np.isfinite(features.to_numpy(dtype="float64")).all()
    ):
        raise ModelContractError("Expected finite real features in the declared Gold order")


def validate_labels(
    labels: pd.Series, features: pd.DataFrame, *, require_both: bool = True
) -> None:
    if (
        not isinstance(labels, pd.Series)
        or not labels.index.equals(features.index)
        or not pd.api.types.is_integer_dtype(labels.dtype)
        or labels.isna().any()
        or not set(labels.unique()).issubset({0, 1})
        or (require_both and set(labels.unique()) != {0, 1})
    ):
        raise ModelContractError(
            "Expected aligned binary integer labels; training needs both classes"
        )


def validate_pipeline(model: Pipeline, columns: Sequence[str] | None = None) -> None:
    if (
        type(model) is not Pipeline
        or not is_classifier(model)
        or not callable(getattr(model, "predict_proba", None))
    ):
        raise ModelContractError("Expected a complete classification Pipeline with predict_proba")
    if columns is not None:
        classes = np.asarray(getattr(model, "classes_", []))
        if (
            classes.shape != (2,)
            or not np.issubdtype(classes.dtype, np.integer)
            or not np.array_equal(classes, [0, 1])
            or list(getattr(model, "feature_names_in_", [])) != list(columns)
        ):
            raise ModelContractError("Expected fitted classes [0, 1] and declared feature names")


def require_unfitted(model: Pipeline) -> None:
    validate_pipeline(model)
    for _, step in model.steps:
        if step is None or isinstance(step, str) or not get_tags(step).requires_fit:
            continue
        try:
            check_is_fitted(step)
        except NotFittedError:
            continue
        raise ModelContractError("Factory and training require an unfitted Pipeline")


def build_model(
    spec: ModelSpec,
    *,
    parameters: Mapping[str, object] | None = None,
    random_state: int = 42,
) -> Pipeline:
    """Clone a fresh factory result; use native Pipeline parameter names and seed."""
    if (
        not isinstance(spec, ModelSpec)
        or not isinstance(spec.model_id, str)
        or re.fullmatch(r"[a-z][a-z0-9_]*", spec.model_id) is None
        or not callable(spec.factory)
        or type(random_state) is not int
        or not 0 <= random_state < 2**32
    ):
        raise ModelContractError("Expected an explicit model identity, factory and integer seed")
    model = spec.factory()
    require_unfitted(model)
    if parameters is not None and (
        not isinstance(parameters, Mapping)
        or any(not isinstance(name, str) or "__" not in name for name in parameters)
    ):
        raise ModelContractError("Use named Pipeline hyperparameters, not replacement steps")
    overrides = dict(parameters or {})
    try:
        json.dumps(overrides, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ModelContractError(
            "Search parameters must be finite JSON-compatible values"
        ) from exc
    if any(name.split("__")[-1] == "random_state" for name in overrides):
        raise ModelContractError("Use random_state separately from search parameters")
    try:
        model = clone(model).set_params(**overrides)
        seeds = {
            name: random_state
            for name in model.get_params(deep=True)
            if name.split("__")[-1] == "random_state"
        }
        model.set_params(**seeds)
    except (TypeError, ValueError) as exc:
        raise ModelContractError(f"Invalid model parameters: {exc}") from exc
    require_unfitted(model)
    return model


def validate_probabilities(probabilities, rows: int) -> np.ndarray:
    values = np.asarray(probabilities)
    if (
        values.shape != (rows, 2)
        or not np.issubdtype(values.dtype, np.number)
        or np.issubdtype(values.dtype, np.complexfloating)
        or not np.isfinite(values).all()
        or ((values < 0) | (values > 1)).any()
        or not np.allclose(values.sum(axis=1), 1.0, rtol=0, atol=1e-12)
    ):
        raise ModelContractError("Expected aligned finite binary probabilities in [0, 1]")
    return values


def predict_scores(model: Pipeline, features: pd.DataFrame) -> np.ndarray:
    """Return fraud scores in input row order; never sort or learn transformations."""
    columns = list(getattr(model, "feature_names_in_", []))
    validate_pipeline(model, columns)
    validate_features(features, columns)
    probabilities = validate_probabilities(model.predict_proba(features), len(features))
    return probabilities[:, 1].astype("float64", copy=True)


def fit_and_score(
    model: Pipeline,
    features: pd.DataFrame,
    labels: pd.Series,
    evaluation_features: pd.DataFrame,
) -> tuple[Pipeline, np.ndarray, dict[str, float]]:
    """Fit the provided training population only and score a separate feature batch."""
    if not isinstance(features, pd.DataFrame):
        raise ModelContractError("Training features must be a named DataFrame")
    columns = list(features.columns)
    validate_features(features, columns)
    validate_labels(labels, features)
    validate_features(evaluation_features, columns)
    require_unfitted(model)
    with warnings.catch_warnings():
        warnings.simplefilter("error", ConvergenceWarning)
        start = perf_counter()
        fitted = model.fit(features, labels)
        fit_seconds = perf_counter() - start
    if fitted is not model:
        raise ModelContractError("Pipeline.fit must return the same estimator")
    start = perf_counter()
    scores = predict_scores(model, evaluation_features)
    return model, scores, {"fit_seconds": fit_seconds, "predict_seconds": perf_counter() - start}
