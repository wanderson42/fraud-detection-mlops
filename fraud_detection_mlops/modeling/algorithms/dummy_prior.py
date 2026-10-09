"""Unfitted DummyClassifier pipeline factory."""

from sklearn.dummy import DummyClassifier
from sklearn.pipeline import Pipeline

DEFAULT_PARAMETERS = {"strategy": "prior", "random_state": 42}


def dummy_prior() -> Pipeline:
    return Pipeline([("classifier", DummyClassifier(**DEFAULT_PARAMETERS))])
