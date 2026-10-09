"""Canonical metadata and score types for persisted predictions."""

import pyarrow as pa

from fraud_detection_mlops.features.feature_schema import METADATA_DTYPES

PREDICTION_DTYPES = {**METADATA_DTYPES, "SCORE": "float64"}

PREDICTION_SCHEMA = pa.schema(
    [
        (name, pa.timestamp("ns") if dtype == "datetime64[ns]" else pa.type_for_alias(dtype))
        for name, dtype in PREDICTION_DTYPES.items()
    ]
)
