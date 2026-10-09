"""Prespecified finite-period reference metrics; no generalization or promotion test."""

import pandas as pd

from fraud_detection_mlops.evaluation.conditional_queue_reference import uniform_queue_reference
from fraud_detection_mlops.evaluation.paired_comparison import daily_metrics, measured


def assessment_measurements(predictions: pd.DataFrame, policy: dict) -> tuple[dict, list]:
    window = policy["windows"]["assessment"]
    days = [
        str(d.date())
        for d in pd.date_range(window["start"], window["end_exclusive"], inclusive="left")
    ]
    observed = sorted(predictions.TX_DATETIME.dt.strftime("%Y-%m-%d").unique())
    if observed != days:
        raise ValueError("Assessment metrics require all prespecified days, without extra dates")
    metrics = measured(predictions)
    daily = daily_metrics(predictions)
    if daily.date.tolist() != days:
        raise ValueError("Assessment daily report lost date coverage")
    # pandas represents undefined daily AP/recall as NaN; JSON must retain explicit nulls.
    rows = daily.astype(object).where(pd.notna(daily), None).to_dict("records")
    metrics.update(
        captured_customer_days=int(daily.fraudulent_customers_in_alerts.sum()),
        missed_customer_days=int(daily.missed_fraudulent_customers.sum()),
        fraudulent_customer_days=int(daily.fraudulent_customers.sum()),
        daily_mean_customer_recall_at_100=(
            float(daily.customer_recall_at_100.mean())
            if daily.customer_recall_at_100.notna().any()
            else None
        ),
        days_with_defined_customer_recall=int(daily.customer_recall_at_100.notna().sum()),
        conditional_queue_reference=uniform_queue_reference(daily),
    )
    return metrics, rows
