"""Paired validation effects and day influence; no IID significance claims."""

import numpy as np
import pandas as pd

from fraud_detection_mlops.features import METADATA_DTYPES
from fraud_detection_mlops.modeling.metrics import evaluate_ranking


def paired_predictions(reference, candidate):
    for frame in (reference, candidate):
        evaluate_ranking(frame.TX_FRAUD, frame.SCORE, frame)
    left = reference.sort_values("TRANSACTION_ID").reset_index(drop=True)
    right = candidate.sort_values("TRANSACTION_ID").reset_index(drop=True)
    if not left[list(METADATA_DTYPES)].equals(right[list(METADATA_DTYPES)]):
        raise ValueError("Paired comparison requires identical transaction metadata")
    return right


def measured(frame):
    result = evaluate_ranking(frame.TX_FRAUD, frame.SCORE, frame)
    if frame.TX_FRAUD.nunique() < 2:
        result["average_precision"] = None
    return result


def daily_metrics(frame):
    rows = []
    for day, group in frame.groupby(frame.TX_DATETIME.dt.strftime("%Y-%m-%d")):
        result = measured(group)
        operational = result.pop("daily_customer_metrics")[0]
        positives = int(group.groupby("CUSTOMER_ID").TX_FRAUD.max().sum())
        found = operational["fraudulent_customers_in_alerts"]
        rows.append(
            {
                "date": day,
                **result,
                **{k: v for k, v in operational.items() if k != "date"},
                "fraudulent_customers": positives,
                "missed_fraudulent_customers": positives - found,
                "customer_recall_at_100": found / positives if positives else None,
            }
        )
    return pd.DataFrame(rows)


def difference(candidate, reference):
    return None if candidate is None or reference is None else candidate - reference


def compare_predictions(reference, candidates, policy):
    catalog = policy["candidates"]
    if set(candidates) != {c["id"] for c in catalog}:
        raise ValueError("Comparison requires the complete candidate catalog")
    reference = reference.sort_values("TRANSACTION_ID").reset_index(drop=True)
    base = measured(reference)
    stamps = reference.TX_DATETIME.dt.strftime("%Y-%m-%d")
    days = sorted(stamps.unique())
    if len(days) != policy["data_policy"]["validation_days"] or len(days) < 2:
        raise ValueError("Validation day coverage differs from protocol")
    base_daily = daily_metrics(reference).set_index("date")
    summary, daily, influence = [], [], []
    gate = policy["development_gate"]
    for definition in catalog:
        name = definition["id"]
        current = paired_predictions(reference, candidates[name])
        values = measured(current)
        ap_delta = difference(values["average_precision"], base["average_precision"])
        p_delta = (
            values["daily_customer_precision_at_100"] - base["daily_customer_precision_at_100"]
        )
        if ap_delta is None or not np.isfinite([ap_delta, p_delta]).all():
            raise ValueError("Full validation must contain both classes")
        summary.append(
            {
                "candidate_id": name,
                "feature_count": definition["feature_count"],
                **{k: v for k, v in values.items() if k != "daily_customer_metrics"},
                "delta_ap": ap_delta,
                "delta_precision_at_100": p_delta,
                "eligible_for_review": (
                    ap_delta + 1e-12 >= gate["minimum_absolute_ap_gain"]
                    and p_delta + 1e-12
                    >= gate["minimum_absolute_daily_customer_precision_at_100_gain"]
                ),
            }
        )
        for row in daily_metrics(current).to_dict("records"):
            baseline_day = base_daily.loc[row["date"]]
            reference_ap = baseline_day["average_precision"]
            if pd.isna(reference_ap):
                reference_ap = None
            daily.append(
                {
                    **row,
                    "candidate_id": name,
                    "delta_ap": difference(row["average_precision"], reference_ap),
                    "delta_precision_at_100": row["precision_at_100"]
                    - baseline_day.precision_at_100,
                }
            )
        for omitted in days:
            keep = stamps != omitted
            left, right = measured(reference[keep]), measured(current[keep])
            influence.append(
                {
                    "candidate_id": name,
                    "omitted_day": omitted,
                    "remaining_days": len(days) - 1,
                    "reference_ap": left["average_precision"],
                    "candidate_ap": right["average_precision"],
                    "delta_ap": difference(right["average_precision"], left["average_precision"]),
                    "delta_precision_at_100": right["daily_customer_precision_at_100"]
                    - left["daily_customer_precision_at_100"],
                }
            )
    summary = pd.DataFrame(summary)
    eligible = (
        summary[summary.eligible_for_review]
        .sort_values(
            [
                "average_precision",
                "daily_customer_precision_at_100",
                "feature_count",
                "candidate_id",
            ],
            ascending=[False, False, True, True],
        )
        .candidate_id.tolist()
    )
    decision = {
        "eligible_ranking": eligible,
        "candidate_for_review": eligible[0] if eligible else None,
        "fallback": "retain_reference",
        "promotion_status": "not_promoted",
        "freeze_review_required": True,
        "formal_superiority_claim": False,
    }
    return summary, pd.DataFrame(daily), pd.DataFrame(influence), decision
