import duckdb
import pandas as pd
import pytest

from fraud_detection_mlops.features import DTYPES, FEATURE_COLUMNS, compute_features


def calculate(frame):
    with duckdb.connect() as connection:
        connection.register("transactions", frame)
        compute_features(connection)
        return (
            connection.execute("SELECT * FROM gold_features ORDER BY TRANSACTION_ID")
            .fetchdf()
            .astype(DTYPES)
        )


@pytest.fixture
def events():
    stamps = [
        "2018-04-01 00:00:00",
        "2018-04-01 00:00:00",
        "2018-04-01 00:00:00.000000001",
        "2018-04-02 00:00:00",
        "2018-04-02 00:00:00.000000001",
        "2018-04-08 00:00:00",
        "2018-04-08 00:00:00.000000001",
        "2018-04-09 00:00:00",
        "2018-04-15 00:00:00",
        "2018-04-15 00:00:00.000000001",
    ]
    return pd.DataFrame(
        {
            "TRANSACTION_ID": range(len(stamps)),
            "TX_DATETIME": pd.to_datetime(stamps, format="mixed"),
            "CUSTOMER_ID": [1, 1, 1, 1, 2, 1, 1, 1, 1, 1],
            "TERMINAL_ID": [10, 10, 10, 10, 10, 10, 10, 10, 10, 10],
            "TX_AMOUNT": [0.0, 0.0, 10.0, 20.0, 1000.0, 30.0, 40.0, 50.0, 60.0, 70.0],
            "TX_FRAUD": [0, 1, 1, 0, 1, 0, 1, 0, 1, 0],
        }
    )


def reference(frame):
    """Independent specification oracle using datetime masks, not SQL window functions."""
    rows = []
    for current in frame.to_dict(orient="records"):
        stamp = current["TX_DATETIME"]
        record = {
            name: current[name]
            for name in (
                "TRANSACTION_ID",
                "TX_DATETIME",
                "CUSTOMER_ID",
                "TERMINAL_ID",
                "TX_FRAUD",
                "TX_AMOUNT",
            )
        }
        record.update(
            LABEL_AVAILABLE_AT=stamp + pd.Timedelta(days=7),
            TX_HOUR=stamp.hour,
            TX_WEEKDAY=stamp.isoweekday(),
        )
        for days in (1, 7):
            prior = frame[
                (frame.TX_DATETIME >= stamp - pd.Timedelta(days=days))
                & (frame.TX_DATETIME < stamp)
            ]
            customer = prior[prior.CUSTOMER_ID == current["CUSTOMER_ID"]]
            average = float(customer.TX_AMOUNT.mean()) if len(customer) else 0.0
            terminal = prior[prior.TERMINAL_ID == current["TERMINAL_ID"]]
            known = frame[
                (frame.TERMINAL_ID == current["TERMINAL_ID"])
                & (frame.TX_DATETIME >= stamp - pd.Timedelta(days=7 + days))
                & (frame.TX_DATETIME + pd.Timedelta(days=7) < stamp)
            ]
            record.update(
                {
                    f"CUSTOMER_TX_COUNT_{days}D": len(customer),
                    f"CUSTOMER_AVG_AMOUNT_{days}D": average,
                    f"CUSTOMER_AMOUNT_RATIO_{days}D": current["TX_AMOUNT"] / average
                    if average > 0
                    else 0.0,
                    f"CUSTOMER_AMOUNT_RATIO_VALID_{days}D": int(average > 0),
                    f"TERMINAL_TX_COUNT_{days}D": len(terminal),
                    f"TERMINAL_KNOWN_LABEL_COUNT_{days}D": len(known),
                    f"TERMINAL_KNOWN_FRAUD_COUNT_{days}D": int(known.TX_FRAUD.sum()),
                    f"TERMINAL_KNOWN_FRAUD_RATE_{days}D": float(known.TX_FRAUD.mean())
                    if len(known)
                    else 0.0,
                }
            )
        rows.append(record)
    return (
        pd.DataFrame(rows)
        .loc[:, list(DTYPES)]
        .astype(DTYPES)
        .sort_values("TRANSACTION_ID")
        .reset_index(drop=True)
    )


def test_features_match_independent_oracle_at_exact_nanosecond_boundaries(events):
    pd.testing.assert_frame_equal(
        calculate(events), reference(events), check_exact=False, rtol=1e-12, atol=1e-12
    )


def test_timestamp_peers_are_excluded_and_zero_history_is_explicit(events):
    result = calculate(events).set_index("TRANSACTION_ID")
    assert result.loc[0, "CUSTOMER_TX_COUNT_1D"] == result.loc[1, "CUSTOMER_TX_COUNT_1D"] == 0
    assert result.loc[2, "CUSTOMER_TX_COUNT_1D"] == 2
    assert result.loc[2, "CUSTOMER_AVG_AMOUNT_1D"] == 0
    assert result.loc[2, "CUSTOMER_AMOUNT_RATIO_1D"] == 0
    assert result.loc[2, "CUSTOMER_AMOUNT_RATIO_VALID_1D"] == 0
    # Inclusive left edge retains both peers exactly one day old.
    assert result.loc[3, "CUSTOMER_TX_COUNT_1D"] == 3
    # A new customer has empty history despite the terminal being active.
    assert result.loc[4, "CUSTOMER_TX_COUNT_1D"] == 0
    assert result.loc[4, "TERMINAL_TX_COUNT_1D"] == 2


def test_labels_become_usable_strictly_after_seven_days(events):
    result = calculate(events).set_index("TRANSACTION_ID")
    assert result.loc[5, "TERMINAL_KNOWN_LABEL_COUNT_7D"] == 0
    assert result.loc[6, "TERMINAL_KNOWN_LABEL_COUNT_7D"] == 2
    assert result.loc[6, "TERMINAL_KNOWN_FRAUD_COUNT_7D"] == 1
    assert result.loc[6, "TERMINAL_KNOWN_FRAUD_RATE_7D"] == 0.5
    # Seven-day history lower boundary shifts by precisely one nanosecond.
    assert result.loc[8, "TERMINAL_KNOWN_LABEL_COUNT_7D"] == 5
    assert result.loc[9, "TERMINAL_KNOWN_LABEL_COUNT_7D"] == 4


def test_future_events_and_labels_do_not_change_features_of_past_rows(events):
    before = calculate(events)
    future = pd.DataFrame(
        {
            "TRANSACTION_ID": [999],
            "TX_DATETIME": pd.to_datetime(["2018-04-30"]),
            "CUSTOMER_ID": [1],
            "TERMINAL_ID": [10],
            "TX_AMOUNT": [1e8],
            "TX_FRAUD": [1],
        }
    )
    after = calculate(pd.concat([events, future], ignore_index=True))
    pd.testing.assert_frame_equal(
        before, after[after.TRANSACTION_ID != 999].reset_index(drop=True)
    )


def test_recent_and_current_labels_cannot_change_unavailable_history(events):
    altered = events.copy()
    altered.loc[altered.TX_DATETIME >= pd.Timestamp("2018-04-02"), "TX_FRAUD"] ^= 1
    before, after = calculate(events), calculate(altered)
    eligible = before.TX_DATETIME < pd.Timestamp("2018-04-09")
    pd.testing.assert_frame_equal(
        before.loc[eligible, FEATURE_COLUMNS], after.loc[eligible, FEATURE_COLUMNS]
    )


def test_shuffling_rows_and_renaming_ids_does_not_create_a_peer_arrival_order(events):
    before = calculate(events)
    altered = events.sample(frac=1, random_state=42).copy()
    altered["TRANSACTION_ID"] = 1000 - altered["TRANSACTION_ID"]
    after = calculate(altered)
    after["TRANSACTION_ID"] = 1000 - after["TRANSACTION_ID"]
    after = after.sort_values("TRANSACTION_ID").reset_index(drop=True)
    pd.testing.assert_frame_equal(before, after)


@pytest.mark.parametrize("seed", [0, 17, 42])
def test_random_multi_entity_histories_match_datetime_oracle(seed):
    import numpy as np

    rng = np.random.default_rng(seed)
    minutes = rng.integers(0, 15 * 24 * 60, size=120)
    minutes[:6] = minutes[0]
    amounts = rng.uniform(0, 300, size=120).round(2)
    amounts[rng.random(120) < 0.2] = 0
    frame = pd.DataFrame(
        {
            "TRANSACTION_ID": range(120),
            "TX_DATETIME": pd.Timestamp("2018-04-01") + pd.to_timedelta(minutes, unit="m"),
            "CUSTOMER_ID": rng.integers(0, 5, size=120),
            "TERMINAL_ID": rng.integers(0, 4, size=120),
            "TX_AMOUNT": amounts,
            "TX_FRAUD": rng.integers(0, 2, size=120),
        }
    )
    pd.testing.assert_frame_equal(
        calculate(frame), reference(frame), check_exact=False, rtol=1e-12, atol=1e-12
    )
