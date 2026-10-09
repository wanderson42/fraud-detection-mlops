"""Unfitted HistGradientBoostingClassifier pipeline factory."""

from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.pipeline import Pipeline

DEFAULT_PARAMETERS = {
    "learning_rate": 0.1,
    "max_iter": 100,
    "max_leaf_nodes": 15,
    "min_samples_leaf": 50,
    "l2_regularization": 1.0,
    "early_stopping": False,
    "categorical_features": None,
    "class_weight": "balanced",
    "random_state": 42,
}


def hist_gradient_boosting() -> Pipeline:
    return Pipeline(
        [
            (
                "classifier",
                HistGradientBoostingClassifier(**DEFAULT_PARAMETERS),
            )
        ]
    )
