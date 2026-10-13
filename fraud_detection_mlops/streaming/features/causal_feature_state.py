"""Compute Gold v1 predictors from transactions and feedback already committed/observed."""

import sqlite3

import pandas as pd

from fraud_detection_mlops.features.feature_schema import DAY_NS, WINDOW_DAYS
from fraud_detection_mlops.features.feature_validation import validate_feature_record
from fraud_detection_mlops.serving.request_schema import ScoreRequest
from fraud_detection_mlops.streaming.contracts.transaction_events import (
    LABEL_DELAY_NS,
    TransactionEvent,
)


def feature_request(connection: sqlite3.Connection, event: TransactionEvent) -> ScoreRequest:
    """Timestamp peers and labels available at this exact instant remain excluded."""
    now = event.event_clock_ns
    stamp = pd.Timestamp(now)
    values = {"TX_AMOUNT": event.amount, "TX_HOUR": stamp.hour, "TX_WEEKDAY": stamp.isoweekday()}
    for days in WINDOW_DAYS:
        count, average = connection.execute(
            "SELECT COUNT(*), COALESCE(AVG(amount), 0.0) FROM transactions "
            "WHERE customer_id = ? AND tx_ns >= ? AND tx_ns < ?",
            (event.customer_id, now - days * DAY_NS, now),
        ).fetchone()
        terminal_count = connection.execute(
            "SELECT COUNT(*) FROM transactions WHERE terminal_id = ? AND tx_ns >= ? AND tx_ns < ?",
            (event.terminal_id, now - days * DAY_NS, now),
        ).fetchone()[0]
        labels, frauds = connection.execute(
            "SELECT COUNT(*), COALESCE(SUM(f.fraud_label), 0) "
            "FROM transactions t JOIN feedback f USING (transaction_id) "
            "WHERE t.terminal_id = ? AND t.tx_ns >= ? AND t.tx_ns < ? "
            "AND f.available_ns < ?",
            (event.terminal_id, now - LABEL_DELAY_NS - days * DAY_NS, now - LABEL_DELAY_NS, now),
        ).fetchone()
        values.update(
            {
                f"CUSTOMER_TX_COUNT_{days}D": count,
                f"CUSTOMER_AVG_AMOUNT_{days}D": float(average),
                f"CUSTOMER_AMOUNT_RATIO_{days}D": event.amount / average if average > 0 else 0.0,
                f"CUSTOMER_AMOUNT_RATIO_VALID_{days}D": int(average > 0),
                f"TERMINAL_TX_COUNT_{days}D": terminal_count,
                f"TERMINAL_KNOWN_LABEL_COUNT_{days}D": labels,
                f"TERMINAL_KNOWN_FRAUD_COUNT_{days}D": frauds,
                f"TERMINAL_KNOWN_FRAUD_RATE_{days}D": frauds / labels if labels else 0.0,
            }
        )
    validate_feature_record(values)
    clock = stamp.isoformat()
    return ScoreRequest.model_validate(
        {
            "schema_version": 1,
            "feature_contract_version": "gold_v1",
            "transaction_id": event.transaction_id,
            "customer_id": event.customer_id,
            "terminal_id": event.terminal_id,
            "tx_datetime": clock,
            "feature_as_of": clock,
            "features": values,
        }
    )
