"""Explicit model factories; experiment policies decide which models may be fitted."""

from sklearn.dummy import DummyClassifier
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from fraud_detection_mlops.modeling.interface import ModelContractError, ModelSpec, build_model

# Historical baseline definitions remain fixed when new contributions are added.
BASELINE_PARAMETERS = {
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
BASELINE_CLASSIFIERS = {
    "dummy_prior": DummyClassifier,
    "logistic_regression": LogisticRegression,
    "hist_gradient_boosting": HistGradientBoostingClassifier,
}


def dummy_prior() -> Pipeline:
    return Pipeline([("classifier", DummyClassifier(**BASELINE_PARAMETERS["dummy_prior"]))])


def logistic_regression() -> Pipeline:
    return Pipeline(
        [
            ("scale", StandardScaler()),
            ("classifier", LogisticRegression(**BASELINE_PARAMETERS["logistic_regression"])),
        ]
    )


def hist_gradient_boosting() -> Pipeline:
    return Pipeline(
        [
            (
                "classifier",
                HistGradientBoostingClassifier(**BASELINE_PARAMETERS["hist_gradient_boosting"]),
            )
        ]
    )


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
