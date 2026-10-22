"""Offline validation metrics; score ranking never becomes a claim of certain fraud."""

import numpy as np
import pandas as pd
from sklearn.metrics import confusion_matrix, precision_recall_curve, roc_curve

from fraud_detection_mlops.evaluation.ranking_metrics import evaluate_ranking
from fraud_detection_mlops.serving.request_schema import ScoreResponse
from fraud_detection_mlops.streaming.contracts.customer_day_alerts import CustomerDayAlertConfig
from fraud_detection_mlops.streaming.contracts.transaction_events import TransactionEvent
from fraud_detection_mlops.streaming.decisions.customer_day_alert_policy import (
    decide_customer_day_alert,
)


def scores(output, rows):
    values = np.asarray(output, dtype="float64")
    if values.shape == (rows, 2):
        values = values[:, 1]  # The registered binary score contract orders classes as [0, 1].
    if (
        values.shape != (rows,)
        or not np.isfinite(values).all()
        or ((values < 0) | (values > 1)).any()
    ):
        raise ValueError("Expected finite binary ranking scores in [0, 1]")
    return values


def rate(numerator, denominator):
    return numerator / denominator if denominator else None


def counts(tp, fp, fn, tn):
    return {
        "true_positive": tp,
        "false_positive": fp,
        "false_negative": fn,
        "true_negative": tn,
        "precision": rate(tp, tp + fp),
        "recall": rate(tp, tp + fn),
    }


def measure(labels, predicted, metadata, threshold):
    values = scores(predicted, len(labels))
    base = evaluate_ranking(labels, values, metadata)
    if len(set(labels)) != 2:
        raise ValueError("Both labels are required for a complete validation performance report")
    precision, recall, _ = precision_recall_curve(labels, values)
    fpr, tpr, _ = roc_curve(labels, values)

    def points(x, y):
        # Thin only the displayed points; scalar metrics are computed from every prediction.
        indices = np.unique(np.linspace(0, len(x) - 1, min(250, len(x)), dtype=int))
        return {"x": x[indices].tolist(), "y": y[indices].tolist()}

    tn, fp, fn, tp = confusion_matrix(labels, values >= threshold, labels=[0, 1]).ravel()
    return {
        "metrics": {k: v for k, v in base.items() if k != "daily_customer_metrics"},
        "curves": {"precision_recall": points(recall, precision), "roc": points(fpr, tpr)},
        "threshold_transactions": {"threshold": threshold, **counts(*map(int, (tp, fp, fn, tn)))},
        "daily_top100": base["daily_customer_metrics"],
    }


def queue_policy(features, metadata, predicted, alert_policy, model_id):
    """Reuse the actual pure policy reducer; labels are joined only after selecting alerts."""
    config = CustomerDayAlertConfig.model_validate(alert_policy)
    frame = metadata.copy().assign(score=scores(predicted, len(metadata)))
    if not frame.index.equals(features.index):
        raise ValueError("Features and validation metadata differ")
    frame["amount"] = features.TX_AMOUNT
    frame = frame.sort_values(["TX_DATETIME", "TRANSACTION_ID"])
    selected, state = set(), {}
    for row in frame.itertuples():
        day = row.TX_DATETIME.strftime("%Y-%m-%d")
        customers = state.setdefault(day, set())
        transaction = TransactionEvent(
            schema_version=1,
            event_type="transaction",
            transaction_id=int(row.TRANSACTION_ID),
            customer_id=int(row.CUSTOMER_ID),
            terminal_id=int(row.TERMINAL_ID),
            tx_datetime=pd.Timestamp(row.TX_DATETIME).isoformat(),
            amount=float(row.amount),
        )
        response = ScoreResponse(
            schema_version=1,
            transaction_id=int(row.TRANSACTION_ID),
            score=float(row.score),
            score_semantics="uncalibrated_ranking",
            model_id=model_id,
            model_sha256=config.model_sha256,
            serving_release_id=config.serving_release_id,
            feature_contract_version="gold_v1",
        )
        decision = decide_customer_day_alert(
            config,
            transaction,
            response,
            already_alerted=int(row.CUSTOMER_ID) in customers,
            daily_budget_used=len(customers),
        )
        if decision.status == "alert":
            customers.add(int(row.CUSTOMER_ID))
            selected.add((day, int(row.CUSTOMER_ID)))
    # Evaluation unit is customer/day; any positive transaction makes that day's customer positive.
    grouped = (
        frame.assign(day=frame.TX_DATETIME.dt.strftime("%Y-%m-%d"))
        .groupby(["day", "CUSTOMER_ID"])
        .TX_FRAUD.max()
    )
    tp = fp = fn = tn = 0
    for (day, customer), label in grouped.items():
        flagged = (day, int(customer)) in selected
        tp += int(flagged and label == 1)
        fp += int(flagged and label == 0)
        fn += int(not flagged and label == 1)
        tn += int(not flagged and label == 0)
    return {
        "unit": "customer_day",
        "policy": alert_policy,
        "alerts": len(selected),
        "customers_days": len(grouped),
        **counts(tp, fp, fn, tn),
        "scope": "offline_validation_replay_of_first_crossing_policy_not_observed_live_performance",
    }
