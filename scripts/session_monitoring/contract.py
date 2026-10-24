"""Stdlib wire contract: aggregates only, fixed identity, bounded cardinality."""

import json
import math
import re

COUNTS = (
    "events",
    "transactions",
    "feedback",
    "decisions",
    "alerts",
    "customers",
    "decision_feedback",
    "http_attempts",
)
STATUSES = (
    "prepared",
    "warming_up",
    "running",
    "paused",
    "awaiting_feedback",
    "draining_feedback",
    "complete",
    "failed",
)
BINDING = {
    "session_id",
    "session_sha256",
    "model_sha256",
    "serving_release_id",
    "expected_events",
    "expected_decisions",
}


def require(test, message):
    if not test:
        raise ValueError(message)


def number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def binding(value):
    require(set(value) == BINDING, "Unexpected binding fields")
    require(
        isinstance(value["session_id"], str)
        and re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", value["session_id"]),
        "Invalid session ID",
    )
    for name, width in (("session_sha256", 64), ("model_sha256", 64), ("serving_release_id", 32)):
        require(
            isinstance(value[name], str)
            and re.fullmatch("[a-f0-9]{" + str(width) + "}", value[name]),
            "Invalid identity",
        )
    for name in ("expected_events", "expected_decisions"):
        require(type(value[name]) is int and value[name] > 0, "Invalid expected count")
    return value


def validate(value, expected, now):
    require(
        set(value)
        == {
            "binding",
            "captured_at",
            "producer_at",
            "producer_status",
            "counts",
            "watermark",
            "latency_ms",
        },
        "Unexpected payload fields",
    )
    binding(value["binding"])
    require(value["binding"] == expected, "Foreign session or model")
    require(
        number(value["captured_at"]) and now - 30 <= value["captured_at"] <= now + 5,
        "Expired or future collection",
    )
    require(
        number(value["producer_at"]) and value["producer_at"] <= now + 5, "Invalid producer time"
    )
    require(value["producer_status"] in STATUSES, "Invalid producer status")
    counts = value["counts"]
    require(
        set(counts) == set(COUNTS)
        and all(type(v) is int and 0 <= v <= 10**9 for v in counts.values()),
        "Invalid counts",
    )
    require(
        counts["decisions"] <= expected["expected_decisions"]
        and counts["events"] <= expected["expected_events"],
        "Capture exceeded",
    )
    require(
        counts["alerts"] <= counts["customers"] <= counts["decisions"]
        and counts["decision_feedback"] <= counts["decisions"]
        and counts["feedback"] <= counts["transactions"] <= counts["events"],
        "Inconsistent counts",
    )
    require(
        counts["decisions"] <= counts["http_attempts"] <= counts["decisions"] * 3,
        "Invalid committed HTTP attempts",
    )
    mark = value["watermark"]
    require(
        isinstance(mark, list)
        and len(mark) == 3
        and (mark == [None] * 3 or all(type(v) is int and v >= 0 for v in mark)),
        "Invalid watermark",
    )
    latency = value["latency_ms"]
    require(set(latency) == {"p50", "p95", "maximum"}, "Invalid latency fields")
    require(
        all(number(v) for v in latency.values())
        and latency["p50"] <= latency["p95"] <= latency["maximum"],
        "Invalid latency",
    )
    require(
        counts["decisions"] > 0 or all(v == 0 for v in latency.values()),
        "Latency without decisions",
    )
    return value


def dumps(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def quantile(ordered, q):
    if not ordered:
        return 0.0
    index = (len(ordered) - 1) * q
    low = int(index)
    return ordered[low] + (ordered[min(low + 1, len(ordered) - 1)] - ordered[low]) * (index - low)
