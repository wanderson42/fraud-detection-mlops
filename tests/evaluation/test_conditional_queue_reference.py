from collections import Counter
from itertools import combinations, product
import math

import numpy as np
import pandas as pd
import pytest

from fraud_detection_mlops.evaluation.conditional_queue_reference import uniform_queue_reference
from fraud_detection_mlops.evaluation.paired_comparison import daily_metrics


def daily_counts(spec, budget):
    return pd.DataFrame(
        [
            {
                "date": str(pd.Timestamp("2018-09-02") + pd.Timedelta(days=i))[:10],
                "customers": n,
                "fraudulent_customers": positives,
                "alerts": min(budget, n),
                "fraudulent_customers_in_alerts": found,
            }
            for i, (n, positives, found) in enumerate(spec)
        ]
    )


@pytest.mark.parametrize(
    "spec,budget", [([(5, 2, 2), (4, 1, 1)], 2), ([(4, 2, 2), (4, 2, 1)], 2), ([(2, 1, 1)], 100)]
)
def test_reference_matches_exhaustive_customer_queue_enumeration(spec, budget):
    # Labels are fixed, including the same positive customer IDs on repeated days.
    options = [
        [sum(c < positives for c in queue) for queue in combinations(range(n), min(budget, n))]
        for n, positives, _ in spec
    ]
    outcomes = sorted(sum(draws) for draws in product(*options))
    observed = sum(found for _, _, found in spec)
    tail = sum(x >= observed for x in outcomes) / len(outcomes)
    counts = Counter(outcomes)
    expected = sum(x * count for x, count in counts.items()) / len(outcomes)
    quantiles = [outcomes[math.ceil(q * len(outcomes)) - 1] for q in (0.025, 0.975)]
    result = uniform_queue_reference(daily_counts(spec, budget), alert_budget=budget)
    assert result["tail_probability_at_least_observed"] == pytest.approx(tail, abs=1e-12)
    assert result["expected_uniform_captured_customer_days"] == pytest.approx(expected)
    assert [
        result["uniform_capture_reference_interval"][k] for k in ("lower", "upper")
    ] == quantiles
    assert result["generalization_confidence_interval"] is None
    assert result["formal_superiority_claim"] is False


def test_reference_uses_existing_customer_day_maximum_score_and_label_contract():
    frame = pd.DataFrame(
        {
            "TRANSACTION_ID": [0, 1, 2, 3, 4, 5],
            "CUSTOMER_ID": [0, 0, 1, 1, 2, 3],
            "TX_DATETIME": pd.to_datetime(["2018-09-02"] * 6),
            "TX_FRAUD": [0, 1, 0, 0, 1, 0],
            "SCORE": [0.7, 0.2, 0.1, 0.0, 0.6, 0.3],
        }
    )
    result = uniform_queue_reference(daily_metrics(frame))
    assert result["total_alerts"] == 4  # Six transactions become four customer/day entries.
    assert result["observed_captured_customer_days"] == 2
    assert result["tail_probability_at_least_observed"] == pytest.approx(1.0)
    assert result["excess_captured_customer_days"] == 0


def test_extreme_tail_stays_in_log_domain_without_reporting_false_zero_probability():
    frame = daily_counts([(5000, 100, 100)] * 14, 100)
    result = uniform_queue_reference(frame)
    oracle = -14 * math.log(math.comb(5000, 100)) / math.log(10)
    assert result["log10_tail_probability_at_least_observed"] == pytest.approx(oracle, abs=1e-8)
    assert result["tail_probability_at_least_observed"] is None
    assert result["linear_probability_underflow"] is True
    assert math.isfinite(result["log10_tail_probability_at_least_observed"])


@pytest.mark.parametrize(
    "column,value",
    [
        ("customers", 0),
        ("customers", 3.0),
        ("customers", True),
        ("alerts", 1),
        ("fraudulent_customers", 5),
        ("fraudulent_customers_in_alerts", 3),
        ("fraudulent_customers", np.nan),
        ("date", "2018-W36-7"),
    ],
)
def test_invalid_counts_and_dates_cannot_manufacture_an_operational_benchmark(column, value):
    frame = daily_counts([(4, 2, 1)], 2)
    frame[column] = value
    with pytest.raises(ValueError):
        uniform_queue_reference(frame, alert_budget=2)


def test_duplicate_and_empty_days_are_rejected_instead_of_counted_as_replicates():
    frame = daily_counts([(4, 2, 1)], 2)
    for invalid in [pd.concat([frame, frame]), frame.iloc[:0]]:
        with pytest.raises(ValueError):
            uniform_queue_reference(invalid, alert_budget=2)


def test_zero_fraud_is_a_degenerate_reference_not_missing_data():
    result = uniform_queue_reference(daily_counts([(10, 0, 0)], 100))
    assert result["expected_uniform_captured_customer_days"] == 0
    assert result["uniform_capture_reference_interval"] == {"mass": 0.95, "lower": 0, "upper": 0}
    assert result["tail_probability_at_least_observed"] == 1
