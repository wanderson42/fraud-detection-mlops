"""The HTTP feature validator keeps Gold SQL domain/coherence rules."""

import duckdb
import pandas as pd
import pytest

from fraud_detection_mlops.features.feature_validation import (
    check_feature_values,
    validate_feature_record,
)


@pytest.mark.parametrize(
    "changes,valid",
    [
        ({}, True),
        (
            {
                "TX_AMOUNT": 10.0,
                "CUSTOMER_AVG_AMOUNT_1D": 5.0,
                "CUSTOMER_AMOUNT_RATIO_1D": 2.0,
                "CUSTOMER_AMOUNT_RATIO_VALID_1D": 1,
                "TERMINAL_KNOWN_LABEL_COUNT_7D": 4,
                "TERMINAL_KNOWN_FRAUD_COUNT_7D": 1,
                "TERMINAL_KNOWN_FRAUD_RATE_7D": 0.25,
            },
            True,
        ),
        ({"TX_AMOUNT": -1.0}, False),
        ({"CUSTOMER_TX_COUNT_7D": -1}, False),
        ({"CUSTOMER_AMOUNT_RATIO_VALID_1D": 1}, False),
        ({"CUSTOMER_AVG_AMOUNT_1D": 2.0}, False),
        ({"CUSTOMER_AMOUNT_RATIO_7D": 0.1}, False),
        ({"TERMINAL_KNOWN_LABEL_COUNT_7D": 4, "TERMINAL_KNOWN_FRAUD_RATE_7D": 0.25}, False),
        ({"TERMINAL_KNOWN_FRAUD_COUNT_1D": 1}, False),
        ({"TERMINAL_KNOWN_FRAUD_RATE_1D": 1.01}, False),
        ({"TX_HOUR": 24}, False),
        ({"TX_WEEKDAY": 8}, False),
    ],
)
def test_python_and_gold_sql_agree(score_payload, changes, valid):
    features = score_payload["features"] | changes
    stamp = pd.Timestamp(score_payload["tx_datetime"])
    row = features | {
        "TRANSACTION_ID": 0,
        "CUSTOMER_ID": 0,
        "TERMINAL_ID": 0,
        "TX_DATETIME": stamp,
        "LABEL_AVAILABLE_AT": stamp + pd.Timedelta(days=7),
        "TX_FRAUD": 0,
    }
    with duckdb.connect() as connection:
        connection.register("records", pd.DataFrame([row]))
        assert (check_feature_values(connection, "records") == 0) is valid
    if valid:
        validate_feature_record(features)
    else:
        with pytest.raises(ValueError):
            validate_feature_record(features)
