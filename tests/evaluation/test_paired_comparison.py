import numpy as np
import pandas as pd
import pytest
from tests.experiment_fixtures import tiny_policy

from fraud_detection_mlops.evaluation.paired_comparison import compare_predictions


def test_gate_uses_operational_gain_and_prefers_smaller_candidate_in_metric_tie():
    stamps = pd.to_datetime(["2018-05-06"] * 110 + ["2018-05-07"] * 110)
    labels = np.tile([0] * 108 + [1, 1], 2).astype("int8")
    reference = pd.DataFrame(
        {
            "TRANSACTION_ID": np.arange(220, dtype="int64"),
            "TX_DATETIME": stamps,
            "CUSTOMER_ID": np.tile(np.arange(110, dtype="int64"), 2),
            "TERMINAL_ID": np.zeros(220, dtype="int64"),
            "LABEL_AVAILABLE_AT": stamps + pd.Timedelta(days=7),
            "TX_FRAUD": labels,
            "SCORE": np.where(labels, 0.1, 0.8),
        }
    )
    candidate = reference.assign(SCORE=np.where(labels, 0.9, 0.4))
    policy = tiny_policy()
    summary, _, _, decision = compare_predictions(
        reference, {c["id"]: candidate for c in policy["candidates"]}, policy
    )
    assert summary.delta_precision_at_100.tolist() == pytest.approx([0.02] * 3)
    assert summary.eligible_for_review.all()
    assert decision["candidate_for_review"] == "without_terminal_volume_and_fraud_counts"
    assert decision["promotion_status"] == "not_promoted"
    assert decision["freeze_review_required"] is True
    with pytest.raises(ValueError, match="complete"):
        compare_predictions(reference, {}, policy)


def prediction_frame(scores):
    stamp = pd.to_datetime(
        [
            "2018-05-06 01:00",
            "2018-05-06 02:00",
            "2018-05-06 03:00",
            "2018-05-07 01:00",
            "2018-05-07 02:00",
            "2018-05-07 03:00",
        ]
    )
    return pd.DataFrame(
        {
            "TRANSACTION_ID": np.arange(6, dtype="int64"),
            "TX_DATETIME": stamp,
            "CUSTOMER_ID": np.array([1, 1, 2, 1, 2, 3], dtype="int64"),
            "TERMINAL_ID": np.ones(6, dtype="int64"),
            "LABEL_AVAILABLE_AT": stamp + pd.Timedelta(days=7),
            "TX_FRAUD": np.array([1, 0, 0, 1, 0, 0], dtype="int8"),
            "SCORE": scores,
        }
    )


def test_paired_effects_ignore_row_order_and_leave_days_out_without_refitting():
    reference = prediction_frame([0.2, 0.4, 0.3, 0.8, 0.1, 0.05])
    candidate = prediction_frame([0.9, 0.4, 0.3, 0.8, 0.1, 0.05]).iloc[::-1]
    policy = tiny_policy()
    summary, daily, influence, decision = compare_predictions(
        reference, {c["id"]: candidate for c in policy["candidates"]}, policy
    )
    assert summary.delta_ap.tolist() == pytest.approx([0.25] * 3)
    assert summary.delta_precision_at_100.tolist() == pytest.approx([0] * 3)
    assert len(daily) == len(influence) == 6
    for _, group in influence.groupby("candidate_id"):
        assert group.omitted_day.tolist() == ["2018-05-06", "2018-05-07"]
        assert group.delta_ap.tolist() == pytest.approx([0, 2 / 3])
    assert decision["candidate_for_review"] is None
    assert decision["formal_superiority_claim"] is False


@pytest.mark.parametrize("change", ["label", "customer", "missing", "duplicate"])
def test_population_changes_are_rejected(change):
    reference = prediction_frame([0.2, 0.4, 0.3, 0.8, 0.1, 0.05])
    candidate = reference.copy()
    if change == "label":
        candidate.loc[0, "TX_FRAUD"] = 0
    elif change == "customer":
        candidate.loc[0, "CUSTOMER_ID"] = 4
    elif change == "missing":
        candidate = candidate.iloc[:-1]
    else:
        candidate = pd.concat([candidate, candidate.iloc[:1]])
    policy = tiny_policy()
    with pytest.raises(ValueError):
        compare_predictions(reference, {c["id"]: candidate for c in policy["candidates"]}, policy)


def test_single_class_day_effect_is_undefined_not_evidence_of_perfection():
    reference = prediction_frame([0.8, 0.4, 0.3, 0.7, 0.1, 0.05])
    reference.loc[3, "TX_FRAUD"] = 0
    policy = tiny_policy()
    summary, _, influence, _ = compare_predictions(
        reference, {c["id"]: reference.copy() for c in policy["candidates"]}, policy
    )
    assert influence[influence.omitted_day == "2018-05-06"].delta_ap.isna().all()
    assert summary.average_precision.notna().all()
