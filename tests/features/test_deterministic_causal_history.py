"""Exact offline/online parity across peers, rolling boundaries and delayed feedback."""

import sqlite3

import pandas as pd
import pytest

from fraud_detection_mlops.features.deterministic_causal_history import deterministic_features
from fraud_detection_mlops.features.feature_schema import FEATURE_COLUMNS
from fraud_detection_mlops.streaming.contracts.transaction_events import TransactionEvent
from fraud_detection_mlops.streaming.features.causal_feature_state import feature_request
from fraud_detection_mlops.streaming.features.deterministic_feature_state import (
    ExactMeanConnection,
)
from fraud_detection_mlops.streaming.replay.temporal_replay_executor import SCHEMA


@pytest.mark.parametrize("reverse", [False, True])
def test_independent_window_membership_produces_bit_identical_predictors(reverse):
    stamps = pd.to_datetime(
        [
            "2018-09-02T00:00:00",
            "2018-09-02T00:00:00",
            "2018-09-02T00:00:00.000000001",
            "2018-09-03T00:00:00",
            "2018-09-03T00:00:00.000000001",
            "2018-09-09T00:00:00",
            "2018-09-09T00:00:00.000000001",
            "2018-09-10T00:00:00",
            "2018-09-10T00:00:00.000000001",
        ],
        format="mixed",
    )
    frame = pd.DataFrame(
        {
            "TRANSACTION_ID": range(len(stamps)),
            "TX_DATETIME": stamps,
            "CUSTOMER_ID": [1] * 9,
            "TERMINAL_ID": [1] * 9,
            "TX_AMOUNT": [33.77, 33.77, 33.77, 79.545, 79.545, 0.0, 0.1, 0.2, 0.3],
            "TX_FRAUD": [0, 1, 0, 1, 0, 1, 0, 0, 1],
        }
    )
    oracle = deterministic_features(frame.iloc[::-1] if reverse else frame).set_index(
        "TRANSACTION_ID"
    )
    with sqlite3.connect(":memory:") as raw:
        for statement in SCHEMA:
            raw.execute(statement)
        connection = ExactMeanConnection(raw)
        labels = []
        for row in frame.itertuples(index=False):
            now = row.TX_DATETIME.value
            for tx, available, label in labels:
                if available <= now:
                    raw.execute(
                        "INSERT OR IGNORE INTO feedback VALUES (?,?,?)", (tx, available, label)
                    )
            raw.execute(
                "INSERT INTO transactions VALUES (?,?,?,?,?)",
                (row.TRANSACTION_ID, now, row.CUSTOMER_ID, row.TERMINAL_ID, row.TX_AMOUNT),
            )
            event = TransactionEvent(
                schema_version=1,
                event_type="transaction",
                transaction_id=row.TRANSACTION_ID,
                customer_id=row.CUSTOMER_ID,
                terminal_id=row.TERMINAL_ID,
                amount=row.TX_AMOUNT,
                tx_datetime=row.TX_DATETIME.isoformat(),
            )
            actual = feature_request(connection, event).model_dump()["features"]
            expected = oracle.loc[row.TRANSACTION_ID]
            for name in FEATURE_COLUMNS:
                assert actual[name] == expected[name], (
                    row.TRANSACTION_ID,
                    name,
                    actual[name],
                    expected[name],
                )
            labels.append((row.TRANSACTION_ID, now + 7 * 86400 * 10**9, row.TX_FRAUD))
