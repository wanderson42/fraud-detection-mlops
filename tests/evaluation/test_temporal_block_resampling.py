import json

import numpy as np
import pandas as pd
import pytest
from sklearn.metrics import average_precision_score

from fraud_detection_mlops.evaluation.temporal_block_resampling import (
    analyze_block_sensitivity,
    circular_day_indices,
    day_multiplicities,
    pooled_ap_cache,
    resampling_summary,
    weighted_pooled_ap,
)


def test_circular_blocks_keep_adjacency_wrap_and_exact_calendar_length():
    for length in (2, 3, 4, 7):
        indices = circular_day_indices(14, length, 2000)
        assert indices.shape == (2000, 14)
        assert np.array_equal(indices, circular_day_indices(14, length, 2000))
        assert indices.min() == 0 and indices.max() == 13
        for position in range(1, 14):
            if position % length:
                assert np.all((indices[:, position] - indices[:, position - 1]) % 14 == 1)
        counts = day_multiplicities(indices, 14)
        assert np.all(counts.sum(axis=1) == 14)
        assert np.array_equal(counts[0], np.bincount(indices[0], minlength=14))


@pytest.mark.parametrize("days,length,reps", [(1, 1, 1), (14, 0, 1), (14, 15, 1), (14, 2, 0)])
def test_invalid_resampling_dimensions_fail(days, length, reps):
    with pytest.raises(ValueError):
        circular_day_indices(days, length, reps)


@pytest.fixture
def tied_predictions():
    return pd.DataFrame(
        {
            "TX_DATETIME": pd.to_datetime(
                ["2018-09-02"] * 3 + ["2018-09-03"] * 2 + ["2018-09-04"] * 2
            ),
            "TX_FRAUD": [1, 0, 0, 1, 0, 0, 1],
            "SCORE": [0.8, 0.8, 0.1, 0.8, 0.2, 0.9, 0.1],
        }
    )


def test_cached_pooled_ap_matches_explicit_replicated_rows_with_ties(tied_predictions):
    frame = tied_predictions
    days = ["2018-09-02", "2018-09-03", "2018-09-04"]
    weights = np.array([[1, 1, 1], [2, 0, 1], [0, 2, 1], [0, 0, 3]])
    values = weighted_pooled_ap(pooled_ap_cache(frame, days), weights)
    stamps = frame.TX_DATETIME.dt.strftime("%Y-%m-%d")
    for value, row in zip(values, weights, strict=True):
        duplicate = pd.concat(
            [
                frame.loc[stamps == day]
                for day, count in zip(days, row, strict=True)
                for _ in range(count)
            ]
        )
        assert value == pytest.approx(average_precision_score(duplicate.TX_FRAUD, duplicate.SCORE))
    assert values[0] == pytest.approx(10 / 21)
    assert values[0] != pytest.approx((0.5 + 1.0 + 0.5) / 3)


@pytest.mark.parametrize("label", [0, 1])
def test_single_class_ap_is_undefined_not_silently_a_perfect_or_zero_ranking(
    tied_predictions, label
):
    frame = tied_predictions.assign(TX_FRAUD=label)
    days = ["2018-09-02", "2018-09-03", "2018-09-04"]
    assert np.isnan(weighted_pooled_ap(pooled_ap_cache(frame, days), np.ones((2, 3)))).all()


@pytest.mark.parametrize(
    "weights", [np.array([[0, 0, 0]]), np.array([[1, -1, 1]]), np.array([[np.nan, 1, 1]])]
)
def test_invalid_day_weights_cannot_create_intervals(tied_predictions, weights):
    with pytest.raises(ValueError):
        weighted_pooled_ap(
            pooled_ap_cache(tied_predictions, ["2018-09-02", "2018-09-03", "2018-09-04"]), weights
        )


def test_undefined_replicates_suppress_interval_and_report_their_count():
    result = resampling_summary(np.array([0.2, np.nan, 0.5]), 0.3)
    assert result["defined_replicates"] == 2 and result["undefined_replicates"] == 1
    assert result["central_resampling_interval"] is None
    assert result["interval_suppressed_reason"] == "undefined_replicates"
    assert result["coverage_validated"] is False
    json.dumps(result, allow_nan=False)


def test_complete_constant_daily_example_keeps_all_lengths_and_no_generalization_claim():
    stamps = pd.date_range("2018-09-02", periods=14).repeat(2)
    frame = pd.DataFrame(
        {
            "TRANSACTION_ID": range(28),
            "TX_DATETIME": stamps,
            "CUSTOMER_ID": [1, 2] * 14,
            "TX_FRAUD": [0, 1] * 14,
            "SCORE": [0.7, 0.3] * 14,
        }
    )
    result = analyze_block_sensitivity(
        frame, {"windows": {"assessment": {"start": "2018-09-02", "end_exclusive": "2018-09-16"}}}
    )
    assert [row["block_length_days"] for row in result["configurations"]] == [2, 3, 4, 7]
    assert [row["blocks_drawn_per_replicate"] for row in result["configurations"]] == [7, 5, 4, 2]
    for row in result["configurations"]:
        for name in ("pooled_average_precision", "mean_daily_customer_precision_at_100"):
            metric = row["metrics"][name]
            assert metric["observed"] == 0.5
            assert metric["central_resampling_interval"] == {"lower": 0.5, "upper": 0.5}
            assert metric["coverage_validated"] is False
    assert result["interpretation"]["generalization_confidence_interval"] is None
    assert result["selected_optimal_block_length"] is None
    json.dumps(result, allow_nan=False)
