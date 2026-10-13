"""Capacity selection respects customer aggregation, dates and label exclusion."""

import numpy as np
import pandas as pd
import pytest

from fraud_detection_mlops.streaming.decisions.development_threshold_selection import (
    SCORING_COLUMNS,
    select_development_threshold,
    selection_days,
)


@pytest.fixture
def development_scores():
    frames = []
    for index, day in enumerate(selection_days()):
        frames.append(
            pd.DataFrame(
                {
                    "TRANSACTION_ID": np.arange(100) + index * 100,
                    "CUSTOMER_ID": np.arange(100),
                    "TX_DATETIME": pd.Timestamp(day) + pd.Timedelta(hours=10),
                    "score": 0.1 + index * 0.01 + np.linspace(0.3, 0, 100),
                }
            )
        )
    return pd.concat(frames, ignore_index=True)[SCORING_COLUMNS]


def test_threshold_is_median_of_daily_100th_customer_not_pooled_transaction_quantile(
    development_scores,
):
    result = select_development_threshold(development_scores)
    assert result["rows"] == 2100 and result["days"] == 21
    assert result["score_threshold"] == pytest.approx(0.2)
    assert [r["cutoff_customer_id"] for r in result["daily_cutoffs"]] == [99] * 21
    repeated = development_scores.iloc[[1099]].copy()
    repeated.TRANSACTION_ID = 99999
    repeated.score = 0.9
    enlarged = pd.concat([development_scores, repeated], ignore_index=True)
    adjusted = select_development_threshold(enlarged)
    assert adjusted["daily_cutoffs"][10]["customers"] == 100
    assert adjusted["daily_cutoffs"][10]["cutoff_customer_id"] == 98
    assert adjusted["score_threshold"] > result["score_threshold"]


def test_many_transactions_of_one_customer_cannot_fill_missing_customer_budget(development_scores):
    development_scores.loc[development_scores.CUSTOMER_ID == 99, "CUSTOMER_ID"] = 98
    with pytest.raises(ValueError, match="100 distinct"):
        select_development_threshold(development_scores)


def test_ties_use_customer_id_and_input_row_order_does_not_change_selection(development_scores):
    development_scores.score = 0.5
    shuffled = development_scores.sample(frac=1, random_state=42)
    result = select_development_threshold(shuffled)
    assert result == select_development_threshold(development_scores)
    assert result["score_threshold"] == 0.5
    assert all(r["cutoff_customer_id"] == 99 for r in result["daily_cutoffs"])


@pytest.mark.parametrize(
    "case", ["missing_day", "september", "duplicate", "labels", "nan", "out_of_range", "timezone"]
)
def test_invalid_scope_or_scores_are_rejected(development_scores, case):
    frame = development_scores.copy()
    if case == "missing_day":
        frame = frame.iloc[100:]
    elif case == "september":
        frame.loc[:99, "TX_DATETIME"] = pd.Timestamp("2018-09-16")
    elif case == "duplicate":
        frame.loc[1, "TRANSACTION_ID"] = frame.loc[0, "TRANSACTION_ID"]
    elif case == "labels":
        frame["TX_FRAUD"] = 1
    elif case == "nan":
        frame.loc[0, "score"] = np.nan
    elif case == "out_of_range":
        frame.loc[0, "score"] = 1.01
    else:
        frame.TX_DATETIME = frame.TX_DATETIME.dt.tz_localize("UTC")
    with pytest.raises(ValueError):
        select_development_threshold(frame)
