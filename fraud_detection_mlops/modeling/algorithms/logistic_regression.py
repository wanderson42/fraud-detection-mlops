"""Unfitted LogisticRegression pipeline factory."""

from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

DEFAULT_PARAMETERS = {
    "C": 1.0,
    "l1_ratio": 0.0,
    "solver": "lbfgs",
    "max_iter": 1000,
    "class_weight": "balanced",
    "random_state": 42,
}


def logistic_regression() -> Pipeline:
    return Pipeline(
        [
            ("scale", StandardScaler()),
            ("classifier", LogisticRegression(**DEFAULT_PARAMETERS)),
        ]
    )
