import numpy as np
import pandas as pd
import pytest
from tests.modeling.test_experiments import tiny_policy

from fraud_detection_mlops.modeling.comparison import compare_predictions


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
