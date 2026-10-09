"""Shared feature-domain/coherence checks, independent of dataset publication."""

from collections.abc import Mapping
import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import duckdb

from fraud_detection_mlops.features.feature_schema import (
    DAY_NS,
    DTYPES,
    FEATURE_DTYPES,
    LABEL_DELAY_DAYS,
    WINDOW_DAYS,
)


def validate_feature_record(values: Mapping[str, int | float]) -> None:
    """Gold feature domains/coherences for a single precomputed inference record."""
    if set(values) != set(FEATURE_DTYPES):
        raise ValueError("Expected exactly the Gold features")
    for name, dtype in FEATURE_DTYPES.items():
        value = values[name]
        if dtype.startswith("int"):
            maximum = 127 if dtype == "int8" else 2**63 - 1
            valid = type(value) is int and 0 <= value <= maximum
        else:
            valid = type(value) in (int, float) and math.isfinite(value) and value >= 0
        if not valid:
            raise ValueError(f"Invalid Gold feature: {name}")
    if not 0 <= values["TX_HOUR"] <= 23 or not 1 <= values["TX_WEEKDAY"] <= 7:
        raise ValueError("Invalid Gold clock features")
    for days in WINDOW_DAYS:
        average = values[f"CUSTOMER_AVG_AMOUNT_{days}D"]
        ratio = values[f"CUSTOMER_AMOUNT_RATIO_{days}D"]
        expected_ratio = values["TX_AMOUNT"] / average if average > 0 else 0.0
        labels = values[f"TERMINAL_KNOWN_LABEL_COUNT_{days}D"]
        frauds = values[f"TERMINAL_KNOWN_FRAUD_COUNT_{days}D"]
        rate = values[f"TERMINAL_KNOWN_FRAUD_RATE_{days}D"]
        expected_rate = frauds / labels if labels else 0.0
        if (
            frauds > labels
            or rate > 1
            or values[f"CUSTOMER_AMOUNT_RATIO_VALID_{days}D"] != int(average > 0)
            or abs(rate - expected_rate) > 1e-12
            or abs(ratio - expected_ratio) > 1e-10 * max(1, ratio)
        ):
            raise ValueError(f"Incoherent Gold features for {days}-day history")


def check_feature_values(connection: duckdb.DuckDBPyConnection, view: str) -> int:
    checks = [
        "TRANSACTION_ID < 0",
        "CUSTOMER_ID < 0",
        "TERMINAL_ID < 0",
        "TX_FRAUD NOT IN (0, 1)",
        "TX_HOUR NOT BETWEEN 0 AND 23",
        "TX_WEEKDAY NOT BETWEEN 1 AND 7",
        f"epoch_ns(LABEL_AVAILABLE_AT) != epoch_ns(TX_DATETIME) + {LABEL_DELAY_DAYS * DAY_NS}",
    ]
    checks += [f"{name} IS NULL" for name in DTYPES]
    checks += [
        f"NOT isfinite({name}) OR {name} < 0"
        for name, dtype in FEATURE_DTYPES.items()
        if dtype == "float64"
    ]
    checks += [f"{name} < 0" for name in FEATURE_DTYPES if "COUNT" in name]
    for d in WINDOW_DAYS:
        checks += [
            f"TERMINAL_KNOWN_FRAUD_COUNT_{d}D > TERMINAL_KNOWN_LABEL_COUNT_{d}D",
            f"TERMINAL_KNOWN_FRAUD_RATE_{d}D > 1",
            f"CUSTOMER_AMOUNT_RATIO_VALID_{d}D != CAST(CUSTOMER_AVG_AMOUNT_{d}D > 0 AS TINYINT)",
            f"abs(TERMINAL_KNOWN_FRAUD_RATE_{d}D - coalesce(TERMINAL_KNOWN_FRAUD_COUNT_{d}D::DOUBLE / nullif(TERMINAL_KNOWN_LABEL_COUNT_{d}D, 0), 0)) > 1e-12",
            f"abs(CUSTOMER_AMOUNT_RATIO_{d}D - coalesce(TX_AMOUNT / nullif(CUSTOMER_AVG_AMOUNT_{d}D, 0), 0)) > 1e-10 * greatest(1, CUSTOMER_AMOUNT_RATIO_{d}D)",
        ]
    return connection.execute(
        f"SELECT count(*) FROM {view} WHERE " + " OR ".join(checks)
    ).fetchone()[0]
