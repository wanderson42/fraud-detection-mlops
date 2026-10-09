import json

import pandas as pd
import pytest

from fraud_detection_mlops.evaluation.temporal_dependence_diagnostics import (
    descriptive_acf,
    diagnose_dependence,
)


@pytest.fixture
def predictions():
    return pd.DataFrame(
        {
            "TRANSACTION_ID": range(8),
            "TX_DATETIME": pd.to_datetime(
                [f"2018-09-{day:02d} 12:00:00" for day in range(2, 6) for _ in range(2)]
            ),
            "CUSTOMER_ID": [1, 2, 1, 3, 1, 2, 1, 4],
            "TERMINAL_ID": [1, 0, 1, 1, 1, 0, 1, 0],
            "TX_FRAUD": [0, 1, 1, 0, 0, 1, 1, 0],
            "SCORE": [0.8, 0.2] * 4,
        }
    )


@pytest.fixture
def policy():
    return {"windows": {"assessment": {"start": "2018-09-02", "end_exclusive": "2018-09-06"}}}


def test_acf_matches_hand_calculated_demeaned_products():
    values = descriptive_acf([0.5, 1.0, 0.5, 1.0])
    assert [v["calendar_pairs"] for v in values["lags"]] == [3, 2, 1]
    assert [v["coefficient"] for v in values["lags"]] == pytest.approx([-0.75, 0.5, -0.25])
    assert values["confidence_bands"] is None and values["independence_test"] is False


@pytest.mark.parametrize("value", [0.0, 0.1, 0.72892744863614])
def test_constant_floating_series_remains_undefined(value):
    result = descriptive_acf([value] * 14)
    assert result["undefined_reason"] == "constant_series"
    assert all(r["coefficient"] is None for r in result["lags"])


def test_undefined_day_is_retained_in_calendar():
    result = descriptive_acf([0.5, None, 0.6, 0.7])
    assert result["days"] == 4 and result["undefined_reason"] == "undefined_daily_values"
    assert [r["calendar_pairs"] for r in result["lags"]] == [3, 2, 1]
    assert all(r["coefficient"] is None for r in result["lags"])


def test_recurrence_overlap_and_day_influence_have_independent_small_oracles(predictions, policy):
    result = diagnose_dependence(predictions, policy)
    recurrence = result["entity_recurrence"]["customers"]
    assert recurrence["unique_entities"] == 4 and recurrence["entity_days"] == 8
    assert recurrence["entities_seen_on_multiple_days"] == 2
    assert recurrence["days_observed_histogram"] == [
        {"days_observed": 1, "entities": 2},
        {"days_observed": 2, "entities": 1},
        {"days_observed": 4, "entities": 1},
    ]
    overlap = recurrence["overlap_by_lag"]
    assert overlap[0]["mean_jaccard"] == pytest.approx(1 / 3)
    assert overlap[1]["mean_shared_entities"] == 1.5
    assert overlap[1]["mean_jaccard"] == pytest.approx(2 / 3)
    assert recurrence["independent_cluster_count"] is None
    assert result["full_window_metrics"]["average_precision"] == 0.5
    assert result["leave_one_day_out"][0]["average_precision"] == pytest.approx(11 / 18)
    assert result["leave_one_day_out"][1]["average_precision"] == pytest.approx(4 / 9)
    assert all(r["remaining_days"] == 3 for r in result["leave_one_day_out"])
    assert result["interpretation"]["resampling_method_selected"] is None
    assert result["interpretation"]["generalization_confidence_interval"] is None
    json.dumps(result, allow_nan=False)


def test_single_class_day_keeps_explicit_null_without_shortening_series(predictions, policy):
    predictions.loc[:1, "TX_FRAUD"] = 0
    result = diagnose_dependence(predictions, policy)
    assert result["daily"][0]["average_precision"] is None
    assert result["daily"][0]["customer_recall_at_100"] is None
    assert result["daily_metric_autocorrelation"]["average_precision"]["days"] == 4
    assert (
        result["daily_metric_autocorrelation"]["average_precision"]["undefined_reason"]
        == "undefined_daily_values"
    )
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("mutation", ["missing_day", "extra_day", "negative_terminal"])
def test_invalid_coverage_and_entity_identity_are_rejected(predictions, policy, mutation):
    if mutation == "missing_day":
        predictions = predictions.iloc[2:]
    elif mutation == "extra_day":
        predictions.loc[0, "TX_DATETIME"] = pd.Timestamp("2018-09-16")
    else:
        predictions.loc[0, "TERMINAL_ID"] = -1
    with pytest.raises(ValueError):
        diagnose_dependence(predictions, policy)
