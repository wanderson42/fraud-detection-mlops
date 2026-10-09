"""Explicit model contributions; experiment policies authorize their use."""

from sklearn.dummy import DummyClassifier
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline

from fraud_detection_mlops.modeling.algorithms.dummy_prior import (
    DEFAULT_PARAMETERS as DUMMY_PRIOR_PARAMETERS,
)
from fraud_detection_mlops.modeling.algorithms.dummy_prior import dummy_prior
from fraud_detection_mlops.modeling.algorithms.hist_gradient_boosting import (
    DEFAULT_PARAMETERS as HIST_GRADIENT_BOOSTING_PARAMETERS,
)
from fraud_detection_mlops.modeling.algorithms.hist_gradient_boosting import hist_gradient_boosting
from fraud_detection_mlops.modeling.algorithms.logistic_regression import (
    DEFAULT_PARAMETERS as LOGISTIC_REGRESSION_PARAMETERS,
)
from fraud_detection_mlops.modeling.algorithms.logistic_regression import logistic_regression
from fraud_detection_mlops.modeling.contracts.model_interface import (
    ModelContractError,
    ModelSpec,
    build_model,
)

DEFAULT_MODEL_PARAMETERS = {
    "dummy_prior": DUMMY_PRIOR_PARAMETERS,
    "logistic_regression": LOGISTIC_REGRESSION_PARAMETERS,
    "hist_gradient_boosting": HIST_GRADIENT_BOOSTING_PARAMETERS,
}

CLASSIFIER_TYPES = {
    "dummy_prior": DummyClassifier,
    "logistic_regression": LogisticRegression,
    "hist_gradient_boosting": HistGradientBoostingClassifier,
}
MODEL_CATALOG = {
    "dummy_prior": ModelSpec("dummy_prior", dummy_prior),
    "logistic_regression": ModelSpec("logistic_regression", logistic_regression),
    "hist_gradient_boosting": ModelSpec("hist_gradient_boosting", hist_gradient_boosting),
}


def registered_model(model_id: str) -> Pipeline:
    """Resolve an explicit contribution without expanding any experiment policy."""
    spec = MODEL_CATALOG[model_id]
    if spec.model_id != model_id:
        raise ModelContractError("Catalog key differs from its model identity")
    return build_model(spec)
