"""Independent feature/score parity and descriptive quality of the actual alert ledger."""

from contextlib import closing
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from fraud_detection_mlops.data.datasets.native_replay_dataset import readonly_connection
from fraud_detection_mlops.data.datasets.reference_assessment_dataset import window_days
from fraud_detection_mlops.evaluation.ranking_metrics import evaluate_ranking
from fraud_detection_mlops.features.feature_schema import FEATURE_COLUMNS, FEATURE_DTYPES
from fraud_detection_mlops.serving.request_schema import ScoreRequest, ScoreResponse
from fraud_detection_mlops.streaming.contracts.customer_day_alerts import CustomerDayAlertConfig


def ledger_frame(
    state: Path, oracle: pd.DataFrame, policy: CustomerDayAlertConfig, protocol: dict
) -> pd.DataFrame:
    """Check every score and decision against an independently computed Gold row."""
    expected = oracle.sort_values(["TX_DATETIME", "TRANSACTION_ID"]).reset_index(drop=True)
    values, scores, decisions = [], [], []
    with closing(readonly_connection(state)) as connection:
        rows = connection.execute(
            "SELECT t.transaction_id,t.customer_id,t.terminal_id,t.tx_ns,d.request,d.response,a.payload,f.fraud_label "
            "FROM decisions d JOIN transactions t USING(transaction_id) "
            "JOIN alert_decisions a USING(transaction_id) JOIN feedback f USING(transaction_id) "
            "ORDER BY t.tx_ns,t.transaction_id"
        )
        budgets, alerted = {}, set()
        for number, row in enumerate(rows):
            if number >= len(expected):
                raise ValueError("Unexpected scored transaction in replay ledger")
            (
                tx_id,
                customer,
                terminal,
                stamp,
                request_bytes,
                response_bytes,
                decision_bytes,
                label,
            ) = row
            reference = expected.iloc[number]
            request = ScoreRequest.model_validate_json(request_bytes)
            response = ScoreResponse.model_validate_json(response_bytes)
            decision = json.loads(decision_bytes)
            clock = pd.Timestamp(stamp).isoformat()
            if (
                tx_id != int(reference.TRANSACTION_ID)
                or customer != int(reference.CUSTOMER_ID)
                or terminal != int(reference.TERMINAL_ID)
                or stamp != reference.TX_DATETIME.value
                or label != int(reference.TX_FRAUD)
                or request.transaction_id != tx_id
                or request.customer_id != customer
                or request.terminal_id != terminal
                or request.tx_datetime != clock
                or request.feature_as_of != clock
                or response.transaction_id != tx_id
                or response.model_sha256 != policy.model_sha256
                or response.serving_release_id != policy.serving_release_id
            ):
                raise ValueError("Replay request, score or mature label identity differs")
            features = request.model_dump()["features"]
            values.append([features[name] for name in FEATURE_COLUMNS])
            scores.append(response.score)
            day = clock[:10]
            key = (day, customer)
            used = budgets.get(day, 0)
            status = (
                "already_alerted"
                if key in alerted
                else "below_threshold"
                if response.score < policy.score_threshold
                else "budget_exhausted"
                if used >= policy.daily_customer_budget
                else "alert"
            )
            if status == "alert":
                used += 1
                budgets[day] = used
                alerted.add(key)
            if (
                decision["status"] != status
                or decision["daily_budget_used"] != used
                or decision["transaction_id"] != tx_id
                or decision["customer_id"] != customer
                or decision["decision_clock"] != clock
                or decision["score"] != response.score
                or decision["policy_sha256"] != policy.policy_sha256
                or decision["alert_id"]
                != (
                    hashlib.sha256(f"{policy.policy_sha256}:{day}:{customer}".encode()).hexdigest()
                    if status == "alert"
                    else None
                )
            ):
                raise ValueError(
                    "Causal decision differs from independent threshold/budget simulation"
                )
            decisions.append(decision)
        if len(values) != len(expected):
            raise ValueError("Missing scored transaction or mature feedback in replay ledger")
        stored_alerts = [
            json.loads(r[0]) for r in connection.execute("SELECT payload FROM alerts")
        ]
        if sorted(stored_alerts, key=lambda x: x["transaction_id"]) != sorted(
            [d for d in decisions if d["status"] == "alert"], key=lambda x: x["transaction_id"]
        ):
            raise ValueError("Emitted alerts differ from committed causal decisions")
    actual = np.asarray(values)
    for column, name in enumerate(FEATURE_COLUMNS):
        target = expected[name].to_numpy()
        matches = (
            np.array_equal(actual[:, column], target)
            if FEATURE_DTYPES[name] != "float64"
            else np.allclose(
                actual[:, column],
                target,
                rtol=protocol["parity"]["float_features_rtol"],
                atol=protocol["parity"]["float_features_atol"],
            )
        )
        if not matches:
            raise ValueError(f"Independent offline feature parity failed: {name}")
    if not np.allclose(
        scores,
        expected.offline_score,
        rtol=protocol["parity"]["scores_rtol"],
        atol=protocol["parity"]["scores_atol"],
    ):
        raise ValueError("Independent offline score parity failed")
    expected["score"] = scores
    expected["alert_status"] = [d["status"] for d in decisions]
    expected["alert_id"] = [d["alert_id"] for d in decisions]
    return expected


def triage_quality(frame: pd.DataFrame, config: CustomerDayAlertConfig, window: dict) -> dict:
    """A customer-day capture includes any fraud that day, even after the alert."""
    frame = frame.copy()
    frame["day"] = frame.TX_DATETIME.dt.strftime("%Y-%m-%d")
    days = window_days(window)
    if sorted(frame.day.unique()) != days:
        raise ValueError("Quality requires every prespecified replay day")
    daily, signed_minutes = [], []
    for day, group in frame.groupby("day", sort=True):
        customers = group.groupby("CUSTOMER_ID").agg(
            score=("score", "max"), label=("TX_FRAUD", "max")
        )
        emitted = group[group.alert_status == "alert"]
        alerted = set(emitted.CUSTOMER_ID)
        if len(alerted) != len(emitted) or len(emitted) > config.daily_customer_budget:
            raise ValueError("Actual alert ledger violates per-customer/day capacity")
        frauds = set(customers.index[customers.label == 1])
        captured = len(alerted & frauds)
        retrospective = set(
            customers.reset_index()
            .sort_values(["score", "CUSTOMER_ID"], ascending=[False, True])
            .head(config.daily_customer_budget)
            .CUSTOMER_ID
        )
        first_fraud = group[group.TX_FRAUD == 1].groupby("CUSTOMER_ID").TX_DATETIME.min()
        for alert in emitted.itertuples():
            if alert.CUSTOMER_ID in first_fraud:
                signed_minutes.append(
                    (alert.TX_DATETIME - first_fraud[alert.CUSTOMER_ID]).total_seconds() / 60
                )
        full = len(emitted) == config.daily_customer_budget
        daily.append(
            {
                "date": day,
                "transactions": len(group),
                "customers": len(customers),
                "alerts": len(emitted),
                "unused_budget": config.daily_customer_budget - len(emitted),
                "budget_occupancy": len(emitted) / config.daily_customer_budget,
                "budget_exhaustion_clock": emitted.TX_DATETIME.max().isoformat() if full else None,
                "captured_customer_days": captured,
                "missed_customer_days": len(frauds) - captured,
                "nonfraudulent_alert_customer_days": len(alerted - frauds),
                "fraudulent_customer_days": len(frauds),
                "actual_alert_precision": captured / len(emitted) if len(emitted) else None,
                "customer_recall": captured / len(frauds) if len(frauds) else None,
                "qualified_customers_suppressed_by_budget": int(
                    group.loc[group.alert_status == "budget_exhausted", "CUSTOMER_ID"].nunique()
                ),
                "retrospective_top100_alerts": len(retrospective),
                "retrospective_top100_captures": len(retrospective & frauds),
                "retrospective_overlap_customers": len(retrospective & alerted),
                "causal_minus_retrospective_captures": captured - len(retrospective & frauds),
            }
        )
    return {
        "ranking": evaluate_ranking(frame.TX_FRAUD, frame.score, frame),
        "daily_causal_triage": daily,
        "captured_customer_days": sum(d["captured_customer_days"] for d in daily),
        "missed_customer_days": sum(d["missed_customer_days"] for d in daily),
        "nonfraudulent_alert_customer_days": sum(
            d["nonfraudulent_alert_customer_days"] for d in daily
        ),
        "alert_minus_first_fraud_minutes": {
            "scope": "captured_fraudulent_customer_days_only_signed_difference_not_prevention",
            "count": len(signed_minutes),
            "p50": float(np.median(signed_minutes)) if signed_minutes else None,
            "p95": float(np.quantile(signed_minutes, 0.95)) if signed_minutes else None,
        },
        "comparison_interpretation": "different_delivery_time_and_policy_descriptive_only",
        "formal_superiority_claim": False,
        "generalization_confidence_interval": None,
        "money_saved": None,
        "production_promotion": False,
    }
