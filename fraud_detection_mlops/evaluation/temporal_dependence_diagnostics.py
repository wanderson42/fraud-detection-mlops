"""Exploratory dependence and day influence; no chosen bootstrap or temporal test."""

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score

from fraud_detection_mlops.evaluation.temporal_reference_metrics import assessment_measurements

VERSION = "reference_dependence_diagnostics_v1"
SERIES = (
    "fraud_rate",
    "average_precision",
    "roc_auc",
    "precision_at_100",
    "customer_recall_at_100",
)


def descriptive_acf(values: list, max_lag: int = 7) -> dict:
    """Demeaned sample ACF with fixed n denominator; undefined days are not dropped."""
    n = len(values)
    if n < 2:
        raise ValueError("Autocorrelation diagnostics require at least two days")
    reason = None
    if any(v is None or not np.isfinite(v) for v in values):
        reason = "undefined_daily_values"
        centered = None
    else:
        array = np.asarray(values, dtype=float)
        centered = array - np.mean(array)
        if np.ptp(array) == 0:
            reason = "constant_series"
    denominator = None if reason else float(centered @ centered)
    return {
        "definition": "sum_of_lagged_demeaned_products_over_sum_of_squared_deviations",
        "days": n,
        "undefined_reason": reason,
        "lags": [
            {
                "lag_days": lag,
                "calendar_pairs": n - lag,
                "coefficient": (
                    float(centered[lag:] @ centered[:-lag] / denominator) if not reason else None
                ),
            }
            for lag in range(1, min(max_lag, n - 1) + 1)
        ],
        "confidence_bands": None,
        "independence_test": False,
    }


def entity_recurrence(frame: pd.DataFrame, column: str, days: list[str]) -> dict:
    activity = frame.groupby(column).agg(
        days_observed=("day", "nunique"), transactions=("TRANSACTION_ID", "size")
    )
    counts = activity.days_observed.value_counts().sort_index()
    daily_sets = [set(frame.loc[frame.day == day, column]) for day in days]
    overlaps = []
    for lag in range(1, min(7, len(days) - 1) + 1):
        shared, jaccard, later_fraction = [], [], []
        for before, after in zip(daily_sets[:-lag], daily_sets[lag:], strict=True):
            common = len(before & after)
            shared.append(common)
            jaccard.append(common / len(before | after))
            later_fraction.append(common / len(after))
        overlaps.append(
            {
                "lag_days": lag,
                "calendar_pairs": len(shared),
                "mean_shared_entities": float(np.mean(shared)),
                "mean_jaccard": float(np.mean(jaccard)),
                "mean_fraction_of_later_day_entities_seen_earlier": float(np.mean(later_fraction)),
            }
        )
    return {
        "unique_entities": len(activity),
        "entity_days": int(activity.days_observed.sum()),
        "entities_seen_on_multiple_days": int((activity.days_observed > 1).sum()),
        "days_observed_histogram": [
            {"days_observed": int(day_count), "entities": int(entities)}
            for day_count, entities in counts.items()
        ],
        "transactions_per_entity": {
            name: float(activity.transactions.quantile(quantile))
            for name, quantile in (("median", 0.5), ("p90", 0.9), ("p99", 0.99))
        },
        "overlap_by_lag": overlaps,
        "independent_cluster_count": None,
    }


def diagnose_dependence(predictions: pd.DataFrame, policy: dict) -> dict:
    """Inspect the complete saved window without interpreting recurrence as independence."""
    if (
        "TERMINAL_ID" not in predictions
        or predictions.TERMINAL_ID.isna().any()
        or not pd.api.types.is_integer_dtype(predictions.TERMINAL_ID)
        or (predictions.TERMINAL_ID < 0).any()
    ):
        raise ValueError("Dependence diagnostics require nonnegative integer terminal IDs")
    metrics, daily = assessment_measurements(predictions, policy)
    days = [row["date"] for row in daily]
    if len(days) < 2:
        raise ValueError("Day influence diagnostics require at least two days")
    frame = predictions.assign(day=predictions.TX_DATETIME.dt.strftime("%Y-%m-%d"))
    influence = []
    for index, omitted in enumerate(days):
        remaining = frame.loc[frame.day != omitted]
        ap = (
            float(average_precision_score(remaining.TX_FRAUD, remaining.SCORE))
            if remaining.TX_FRAUD.nunique() == 2
            else None
        )
        precision = float(
            np.mean([d["precision_at_100"] for i, d in enumerate(daily) if i != index])
        )
        influence.append(
            {
                "omitted_day": omitted,
                "remaining_days": len(days) - 1,
                "remaining_rows": len(remaining),
                "average_precision": ap,
                "delta_average_precision": (
                    ap - metrics["average_precision"]
                    if ap is not None and metrics["average_precision"] is not None
                    else None
                ),
                "daily_customer_precision_at_100": precision,
                "delta_daily_customer_precision_at_100": (
                    precision - metrics["daily_customer_precision_at_100"]
                ),
            }
        )
    return {
        "version": VERSION,
        "analysis_scope": "post_assessment_exploratory_diagnostics",
        "assessment_window": policy["windows"]["assessment"],
        "days": len(days),
        "rows": len(frame),
        "full_window_metrics": {
            key: metrics[key]
            for key in (
                "average_precision",
                "daily_customer_precision_at_100",
                "daily_mean_customer_recall_at_100",
            )
        },
        "daily": daily,
        "daily_metric_autocorrelation": {
            name: descriptive_acf([d[name] for d in daily]) for name in SERIES
        },
        "entity_recurrence": {
            name: entity_recurrence(frame, column, days)
            for name, column in (("customers", "CUSTOMER_ID"), ("terminals", "TERMINAL_ID"))
        },
        "leave_one_day_out": influence,
        "interpretation": {
            "short_window_days": len(days),
            "recurrence_does_not_prove_dependence": True,
            "small_autocorrelation_does_not_prove_independence": True,
            "leave_one_day_out_is_not_a_confidence_interval": True,
            "label_delay_does_not_determine_block_length": True,
            "resampling_method_selected": None,
            "resampling_block_length_days": None,
            "effective_independent_sample_size": None,
            "generalization_confidence_interval": None,
            "formal_temporal_hypothesis_test": False,
            "production_promotion": False,
        },
    }
