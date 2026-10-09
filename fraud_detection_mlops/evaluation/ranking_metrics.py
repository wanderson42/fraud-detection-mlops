"""Ranking metrics and the temporal_v1 daily customer alert contract."""

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score


class MetricError(ValueError):
    """Predictions do not meet the binary ranking contract."""


def evaluate_ranking(labels, scores, metadata: pd.DataFrame) -> dict:
    """Score transactions and macro-average customer precision over observed days."""
    y, score = np.asarray(labels), np.asarray(scores, dtype="float64")
    if (
        y.ndim != 1
        or score.ndim != 1
        or len(y) != len(score)
        or len(y) != len(metadata)
        or not len(y)
        or not np.isin(y, [0, 1]).all()
        or not np.isfinite(score).all()
        or ((score < 0) | (score > 1)).any()
    ):
        raise MetricError("Expected nonempty aligned binary labels and finite scores in [0, 1]")
    required = {"TRANSACTION_ID", "CUSTOMER_ID", "TX_DATETIME"}
    if not required.issubset(metadata) or metadata[list(required)].isna().any().any():
        raise MetricError("Missing prediction identity or timestamp")
    if metadata.TRANSACTION_ID.duplicated().any():
        raise MetricError("Duplicate transaction IDs")
    for name in ("TRANSACTION_ID", "CUSTOMER_ID"):
        if not pd.api.types.is_integer_dtype(metadata[name].dtype) or (metadata[name] < 0).any():
            raise MetricError("Expected nonnegative integer IDs")
    stamps = metadata.TX_DATETIME
    if not pd.api.types.is_datetime64_dtype(stamps.dtype) or stamps.dt.tz is not None:
        raise MetricError("Expected naive datetime timestamps")
    frame = pd.DataFrame(
        {
            "day": stamps.dt.strftime("%Y-%m-%d").to_numpy(),
            "customer": metadata.CUSTOMER_ID.to_numpy(),
            "score": score,
            "label": y,
        }
    )
    customers = frame.groupby(["day", "customer"], as_index=False).agg(
        score=("score", "max"), label=("label", "max")
    )
    daily = []
    for day, group in customers.groupby("day", sort=True):
        ranked = group.sort_values(["score", "customer"], ascending=[False, True])
        alerts = min(100, len(ranked))
        found = int(ranked.head(alerts).label.sum())
        daily.append(
            {
                "date": day,
                "customers": len(ranked),
                "alerts": alerts,
                "fraudulent_customers_in_alerts": found,
                "precision_at_100": found / alerts,
            }
        )
    positives = int(y.sum())
    return {
        "rows": len(y),
        "fraud_count": positives,
        "fraud_rate": positives / len(y),
        "average_precision": float(average_precision_score(y, score)) if positives else 0.0,
        "roc_auc": float(roc_auc_score(y, score)) if 0 < positives < len(y) else None,
        "daily_customer_precision_at_100": float(
            np.mean([item["precision_at_100"] for item in daily])
        ),
        "daily_customer_metrics": daily,
    }
