"""Stable feature constants; legacy computation functions load on demand."""

from importlib import import_module

from fraud_detection_mlops.features.feature_schema import (
    DAY_NS,
    DTYPES,
    FEATURE_COLUMNS,
    FEATURE_DTYPES,
    LABEL_DELAY_DAYS,
    METADATA_DTYPES,
    WINDOW_DAYS,
)

__all__ = [
    "DAY_NS",
    "DTYPES",
    "FEATURE_COLUMNS",
    "FEATURE_DTYPES",
    "LABEL_DELAY_DAYS",
    "METADATA_DTYPES",
    "WINDOW_DAYS",
    "compute_features",
    "feature_query",
]


def __getattr__(name):
    if name not in {"compute_features", "feature_query"}:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module(f"{__name__}.causal_history"), name)
