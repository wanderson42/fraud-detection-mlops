"""Bounded aggregate details; no raw requests, labels or identifiers on the wire."""

import math

FEATURES = (
    "TX_AMOUNT",
    "TX_HOUR",
    "TX_WEEKDAY",
    "CUSTOMER_TX_COUNT_1D",
    "CUSTOMER_AVG_AMOUNT_1D",
    "CUSTOMER_AMOUNT_RATIO_1D",
    "CUSTOMER_AMOUNT_RATIO_VALID_1D",
    "CUSTOMER_TX_COUNT_7D",
    "CUSTOMER_AVG_AMOUNT_7D",
    "CUSTOMER_AMOUNT_RATIO_7D",
    "CUSTOMER_AMOUNT_RATIO_VALID_7D",
    "TERMINAL_TX_COUNT_1D",
    "TERMINAL_KNOWN_LABEL_COUNT_1D",
    "TERMINAL_KNOWN_FRAUD_COUNT_1D",
    "TERMINAL_KNOWN_FRAUD_RATE_1D",
    "TERMINAL_TX_COUNT_7D",
    "TERMINAL_KNOWN_LABEL_COUNT_7D",
    "TERMINAL_KNOWN_FRAUD_COUNT_7D",
    "TERMINAL_KNOWN_FRAUD_RATE_7D",
)
DISTRIBUTIONS = (
    "TX_AMOUNT",
    "CUSTOMER_AVG_AMOUNT_7D",
    "CUSTOMER_TX_COUNT_7D",
    "TERMINAL_KNOWN_FRAUD_RATE_7D",
)
QUALITY = (
    "mature_days",
    "mature_transactions",
    "mature_fraud_transactions",
    "mature_alerts",
    "mature_fraud_customer_days",
    "captured_customer_days",
    "missed_customer_days",
    "nonfraudulent_alert_customer_days",
    "precision",
    "recall",
    "average_precision",
)
ROLES = ("api", "gateway", "tracking", "postgres", "rustfs", "receiver", "prometheus", "grafana")
STATUSES = ("alert", "below_threshold", "already_alerted", "budget_exhausted")
BASE = {
    "feature_comparison_ready",
    "feature_reference_rows",
    "feature_current_rows",
    "feature_invalid_rows",
    "feature_future_clock_rows",
    "feature_incoherent_rows",
    "feature_max_lag_seconds",
    "pending_decision_feedback",
    "scoring_dates",
    "daily_customer_budget",
    "score_comparison_ready",
    "score_jensen_shannon",
}
KEYS = (
    BASE
    | {"feature_missing_" + n for n in FEATURES}
    | {"feature_invalid_" + n for n in FEATURES}
    | {"feature_js_" + n for n in DISTRIBUTIONS}
    | {"quality_" + n for n in QUALITY}
    | {"triage_" + n for n in STATUSES}
    | {"pod_" + n + "_" + field for n in ROLES for field in ("observed", "ready", "restarts")}
)
NULLABLE = (
    {"score_jensen_shannon", "feature_max_lag_seconds"}
    | {"feature_js_" + n for n in DISTRIBUTIONS}
    | {"quality_" + n for n in ("precision", "recall", "average_precision")}
)


def validate(value):
    if not isinstance(value, dict) or set(value) != KEYS:
        raise ValueError("Unexpected detail fields")
    for key, number in value.items():
        if key.endswith(("_ready", "_observed")) and (
            type(number) is not int or number not in (0, 1)
        ):
            raise ValueError("Invalid binary detail: " + key)
        if number is None and key in NULLABLE:
            continue
        if (
            type(number) not in (int, float)
            or not math.isfinite(number)
            or not 0 <= number <= 10**12
        ):
            raise ValueError("Invalid detail value: " + key)
        if (
            key.endswith(("_ready", "_observed"))
            or key
            in {
                "quality_precision",
                "quality_recall",
                "quality_average_precision",
                "score_jensen_shannon",
            }
            or key.startswith(("feature_missing_", "feature_js_"))
        ) and number > 1:
            raise ValueError("Invalid bounded detail: " + key)
    if value["feature_comparison_ready"] == 0 and any(
        value["feature_js_" + n] is not None for n in DISTRIBUTIONS
    ):
        raise ValueError("Divergence without support")
    if value["score_comparison_ready"] == 0 and value["score_jensen_shannon"] is not None:
        raise ValueError("Score divergence without support")
    return value


def collect(db, counts, watermark, policy):
    # Existing descriptive windows/quality definitions remain authoritative.
    from scripts.monitoring import feature_health, statistics

    health = feature_health.snapshot(db, counts["decisions"])
    rows = db.execute(
        "SELECT t.tx_ns,t.customer_id,json_extract(d.response,'$.score'),CASE WHEN json_extract(a.payload,'$.status')='alert' THEN 1 ELSE 0 END,f.fraud_label FROM decisions d JOIN transactions t USING(transaction_id) JOIN alert_decisions a USING(transaction_id) LEFT JOIN feedback f USING(transaction_id) ORDER BY t.tx_ns,t.transaction_id"
    ).fetchall()
    if len(rows) != counts["decisions"] or any(label not in (None, 0, 1) for *_, label in rows):
        raise ValueError("Incomplete or invalid triage join")
    # Complete observed cohorts; this never asserts coverage of a whole source day.
    quality = statistics.mature_quality(rows, watermark or 0, all(r[4] is not None for r in rows))
    current = health["current"]
    value = {
        "feature_comparison_ready": int(health["comparison_ready"]),
        "feature_reference_rows": health["reference"]["rows"],
        "feature_current_rows": current["rows"],
        "feature_invalid_rows": current["schema_or_clock_invalid_rows"],
        "feature_future_clock_rows": current["future_clock_rows"],
        "feature_incoherent_rows": current["incoherent_rows"],
        "feature_max_lag_seconds": current["max_feature_lag_seconds"],
        "pending_decision_feedback": sum(r[4] is None for r in rows),
        "scoring_dates": len({r[0] // statistics.DAY_NS for r in rows}),
        "daily_customer_budget": policy["daily_customer_budget"],
        "score_comparison_ready": int(len(rows) >= 2000),
        "score_jensen_shannon": statistics.jensen_shannon(
            statistics.score_counts([r[2] for r in rows[:1000]]),
            statistics.score_counts([r[2] for r in rows[-1000:]]),
        )
        if len(rows) >= 2000
        else None,
    }
    value.update({"quality_" + k: v for k, v in quality.items()})
    value.update(
        {
            "feature_missing_" + k: v / current["rows"] if current["rows"] else 0
            for k, v in current["missing_values"].items()
        }
    )
    value.update({"feature_invalid_" + k: v for k, v in current["invalid_values"].items()})
    value.update({"feature_js_" + k: v["jensen_shannon"] for k, v in health["features"].items()})
    statuses = dict(
        db.execute(
            "SELECT json_extract(payload,'$.status'),count(*) FROM alert_decisions GROUP BY 1"
        )
    )
    value.update({"triage_" + k: statuses.get(k, 0) for k in STATUSES})
    return value


def pod_snapshot(items):
    prefixes = {
        "api": "fraud-detector-blue-",
        "gateway": "fraud-detector-gateway-",
        "tracking": "fraud-tracking-",
        "postgres": "fraud-platform-postgres-",
        "rustfs": "fraud-platform-rustfs-",
        "receiver": "fraud-observability-exporter-",
        "prometheus": "fraud-observability-prometheus-",
        "grafana": "fraud-observability-grafana-",
    }
    result = {}
    for role, prefix in prefixes.items():
        pods = [
            p
            for p in items
            if p["metadata"]["name"].startswith(prefix)
            and not p["metadata"].get("deletionTimestamp")
        ]
        ready = bool(pods) and all(
            any(
                c.get("type") == "Ready" and c.get("status") == "True"
                for c in p.get("status", {}).get("conditions", [])
            )
            for p in pods
        )
        result.update(
            {
                "pod_" + role + "_observed": int(bool(pods)),
                "pod_" + role + "_ready": int(ready),
                "pod_" + role + "_restarts": sum(
                    c.get("restartCount", 0)
                    for p in pods
                    for c in p.get("status", {}).get("containerStatuses", [])
                ),
            }
        )
    return result
