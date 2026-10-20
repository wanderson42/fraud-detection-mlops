"""Descriptive, bounded health of persisted requests; no model or label access."""

from bisect import bisect_right
from datetime import UTC, datetime
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
import re

from scripts.monitoring.statistics import jensen_shannon

ROOT = Path(__file__).resolve().parents[2]
WINDOW = 1000
THRESHOLD = 0.1
BOUNDS = {
    "TX_AMOUNT": [0, 10, 30, 60, 100, 200, 500],
    "CUSTOMER_AVG_AMOUNT_7D": [0, 10, 30, 60, 100, 200, 500],
    "CUSTOMER_TX_COUNT_7D": [0, 1, 5, 10, 20, 50, 100],
    "TERMINAL_KNOWN_FRAUD_RATE_7D": [0, 0.01, 0.05, 0.1, 0.25, 0.5, 1],
}
CLOCK = re.compile(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?\Z")
REQUEST_FIELDS = {
    "schema_version",
    "feature_contract_version",
    "transaction_id",
    "customer_id",
    "terminal_id",
    "tx_datetime",
    "feature_as_of",
    "features",
}


@lru_cache(maxsize=1)
def contract():
    payload = (ROOT / "references/gold_contract_v1.json").read_bytes()
    value = json.loads(payload)
    if value["version"] != "gold_v1" or not set(BOUNDS) <= value["feature_columns"].keys():
        raise ValueError("Unsupported monitoring feature contract")
    return value["feature_columns"], hashlib.sha256(payload).hexdigest()


def clock_ns(value):
    if not isinstance(value, str) or (match := CLOCK.fullmatch(value)) is None:
        raise ValueError("Invalid request clock")
    delta = datetime.fromisoformat(match[1]).replace(tzinfo=UTC) - datetime(1970, 1, 1, tzinfo=UTC)
    return (delta.days * 86400 + delta.seconds) * 10**9 + int((match[2] or "").ljust(9, "0"))


def valid_value(name, dtype, value):
    if dtype.startswith("int"):
        valid = type(value) is int and 0 <= value <= (127 if dtype == "int8" else 2**63 - 1)
    else:
        valid = (
            type(value) in (int, float)
            and 0 <= value <= 1.7976931348623157e308
            and math.isfinite(value)
        )
    if not valid:
        return False
    if name == "TX_HOUR":
        return value <= 23
    if name == "TX_WEEKDAY":
        return 1 <= value <= 7
    if "RATIO_VALID" in name or "FRAUD_RATE" in name:
        return value <= 1
    return True


def coherent(features):
    for days in (1, 7):
        average = features[f"CUSTOMER_AVG_AMOUNT_{days}D"]
        ratio = features[f"CUSTOMER_AMOUNT_RATIO_{days}D"]
        labels = features[f"TERMINAL_KNOWN_LABEL_COUNT_{days}D"]
        frauds = features[f"TERMINAL_KNOWN_FRAUD_COUNT_{days}D"]
        rate = features[f"TERMINAL_KNOWN_FRAUD_RATE_{days}D"]
        if (
            frauds > labels
            or features[f"CUSTOMER_AMOUNT_RATIO_VALID_{days}D"] != int(average > 0)
            or abs(rate - (frauds / labels if labels else 0)) > 1e-12
            or abs(ratio - (features["TX_AMOUNT"] / average if average > 0 else 0))
            > 1e-10 * max(1, ratio)
        ):
            return False
    return True


def profile(rows):
    """Rows: (persisted transaction clock in ns, original request JSON)."""
    types, _ = contract()
    missing, invalid = dict.fromkeys(types, 0), dict.fromkeys(types, 0)
    bins = {name: [0] * (len(bounds) + 1) for name, bounds in BOUNDS.items()}
    bad_rows = bad_clocks = future_clocks = incoherent = 0
    lags = []
    fingerprint = hashlib.sha256()
    for stamp, raw in rows:
        fingerprint.update(json.dumps([stamp, raw], separators=(",", ":")).encode() + b"\n")
        try:
            if len(raw) > 65536:
                raise ValueError("Oversized request")
            request = json.loads(raw)
        except ValueError, TypeError:
            request = None
        if not isinstance(request, dict):
            request = {}
        features = request.get("features")
        if not isinstance(features, dict):
            features = {}
        bad = (
            set(request) != REQUEST_FIELDS
            or type(request.get("schema_version")) is not int
            or request.get("schema_version") != 1
            or request.get("feature_contract_version") != "gold_v1"
            or set(features) != set(types)
        )
        valid_features = set(features) == set(types)
        for name, dtype in types.items():
            value = features.get(name)
            valid = valid_value(name, dtype, value)
            missing[name] += value is None
            invalid[name] += not valid
            bad |= not valid
            valid_features &= valid
            if valid and name in BOUNDS:
                bins[name][bisect_right(BOUNDS[name], value)] += 1
        if valid_features and not coherent(features):
            incoherent += 1
            bad = True
        try:
            event, as_of = (
                clock_ns(request.get("tx_datetime")),
                clock_ns(request.get("feature_as_of")),
            )
            if event != stamp:
                raise ValueError("Persisted and request event clocks differ")
            future_clocks += as_of > event
            if as_of <= event:
                lags.append((event - as_of) / 1e9)
            source = datetime.fromisoformat(request["tx_datetime"])
            if (
                as_of != event
                or features.get("TX_HOUR") != source.hour
                or features.get("TX_WEEKDAY") != source.isoweekday()
            ):
                raise ValueError("Gold source clocks disagree")
        except ValueError, OverflowError:
            bad_clocks += 1
            bad = True
        bad_rows += bool(bad)
    return {
        "rows": len(rows),
        "schema_or_clock_invalid_rows": bad_rows,
        "invalid_clock_rows": bad_clocks,
        "future_clock_rows": future_clocks,
        "incoherent_rows": incoherent,
        "max_feature_lag_seconds": max(lags) if lags else None,
        "missing_values": missing,
        "invalid_values": invalid,
        "histograms": bins,
        "window_request_sha256": fingerprint.hexdigest(),
        "first_source_ns": rows[0][0] if rows else None,
        "last_source_ns": rows[-1][0] if rows else None,
    }


def compare(reference, current, *, nonoverlapping):
    ref, now = profile(reference), profile(current)
    invalid = ref["schema_or_clock_invalid_rows"] or now["schema_or_clock_invalid_rows"]
    ready = nonoverlapping and len(reference) == len(current) == WINDOW and not invalid
    return {
        "schema": "fraud_operational_feature_health_v1",
        "scope": "persisted_request_windows_not_training_baseline_or_model_quality_or_CT",
        "contract_sha256": contract()[1],
        "window_rows": WINDOW,
        "reference_policy": "first_1000_scored_decisions_in_same_checkpoint",
        "current_policy": "latest_1000_scored_decisions_in_same_checkpoint",
        "freshness_scope": "feature_as_of_vs_transaction_clock_not_wall_clock_ingestion_freshness",
        "nonoverlapping": nonoverlapping,
        "comparison_ready": ready,
        "status": "invalid_data" if invalid else "ready" if ready else "insufficient_support",
        "threshold": THRESHOLD,
        "reference": ref,
        "current": now,
        "features": {
            name: {
                "bounds": bounds,
                "jensen_shannon": max(
                    0.0, min(1.0, jensen_shannon(ref["histograms"][name], now["histograms"][name]))
                )
                if ready
                else None,
            }
            for name, bounds in BOUNDS.items()
        },
        "production_promotion": False,
        "training_triggered": False,
    }


def snapshot(connection, decisions):
    def read(direction):
        return connection.execute(
            "SELECT t.transaction_id,t.tx_ns,d.request FROM decisions d "
            "JOIN transactions t USING(transaction_id) "
            f"ORDER BY t.tx_ns {direction},t.transaction_id {direction} LIMIT ?",
            (WINDOW,),
        ).fetchall()

    reference, current = read("ASC"), list(reversed(read("DESC")))
    disjoint = not ({row[0] for row in reference} & {row[0] for row in current})
    return compare(
        [(stamp, raw) for _, stamp, raw in reference],
        [(stamp, raw) for _, stamp, raw in current],
        nonoverlapping=decisions >= 2 * WINDOW and disjoint,
    )


def exposition(value):
    lines = []

    def metric(name, samples):
        lines.extend(
            [f"# HELP {name} Descriptive persisted feature window health.", f"# TYPE {name} gauge"]
        )
        for labels, number in samples:
            if number is not None:
                suffix = (
                    "{" + ",".join(f'{key}="{val}"' for key, val in labels.items()) + "}"
                    if labels
                    else ""
                )
                lines.append(f"{name}{suffix} {float(number):.17g}")

    metric("fraud_feature_comparison_ready", [({}, value["comparison_ready"])])
    for field in (
        "rows",
        "schema_or_clock_invalid_rows",
        "invalid_clock_rows",
        "future_clock_rows",
        "incoherent_rows",
        "max_feature_lag_seconds",
    ):
        metric(
            "fraud_feature_" + field,
            [({"window": window}, value[window][field]) for window in ("reference", "current")],
        )
    metric(
        "fraud_feature_missing_fraction",
        [
            (
                {"feature": name},
                count / value["current"]["rows"] if value["current"]["rows"] else None,
            )
            for name, count in value["current"]["missing_values"].items()
        ],
    )
    metric(
        "fraud_feature_invalid_values",
        [({"feature": name}, count) for name, count in value["current"]["invalid_values"].items()],
    )
    metric(
        "fraud_feature_jensen_shannon",
        [({"feature": name}, item["jensen_shannon"]) for name, item in value["features"].items()],
    )
    return "\n".join(lines) + "\n"
