"""Score distribution and ranking statistics; current outcomes require mature cohorts."""

from bisect import bisect_right
from collections import defaultdict
import math

DAY_NS = 86400 * 10**9
SCORE_BOUNDS = [i / 10 for i in range(1, 10)]
LATENCY_BOUNDS = [0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0]


def score_counts(scores):
    counts = [0] * 10
    for score in scores:
        if not math.isfinite(score) or not 0 <= score <= 1:
            raise ValueError("Invalid probability in monitoring input")
        counts[bisect_right(SCORE_BOUNDS, score)] += 1
    return counts


def jensen_shannon(reference, current):
    if len(reference) != len(current) or not sum(reference) or not sum(current):
        return None
    p = [v / sum(reference) for v in reference]
    q = [v / sum(current) for v in current]
    return sum(
        0.5 * (a * math.log2(a / ((a + b) / 2)) if a else 0)
        + 0.5 * (b * math.log2(b / ((a + b) / 2)) if b else 0)
        for a, b in zip(p, q, strict=True)
    )


def average_precision(rows):
    positives = sum(label for _, label in rows)
    if not positives:
        return None
    grouped = defaultdict(lambda: [0, 0])
    for score, label in rows:
        grouped[score][0] += 1
        grouped[score][1] += label
    seen = found = 0
    area = 0.0
    for score in sorted(grouped, reverse=True):
        count, frauds = grouped[score]
        seen += count
        found += frauds
        area += (frauds / positives) * (found / seen)
    return area


def mature_quality(rows, watermark_ns: int, complete: bool):
    """rows: source_ns, customer, score, actual_alert, observed_label_or_None."""
    days = defaultdict(list)
    for row in rows:
        days[(row[0] // DAY_NS) * DAY_NS].append(row)
    matured = []
    closed_days = 0
    for day, group in days.items():
        if (complete or day + 8 * DAY_NS <= watermark_ns) and all(r[4] is not None for r in group):
            closed_days += 1
            matured.extend(group)
    customers = defaultdict(lambda: [0, 0])
    for stamp, customer, score, alert, label in matured:
        key = (stamp // DAY_NS, customer)
        customers[key][0] = max(customers[key][0], int(alert))
        customers[key][1] = max(customers[key][1], label)
    alerts = sum(v[0] for v in customers.values())
    frauds = sum(v[1] for v in customers.values())
    captures = sum(a * f for a, f in customers.values())
    return {
        "mature_days": closed_days,
        "mature_transactions": len(matured),
        "mature_fraud_transactions": sum(r[4] for r in matured),
        "mature_alerts": alerts,
        "mature_fraud_customer_days": frauds,
        "captured_customer_days": captures,
        "missed_customer_days": frauds - captures,
        "nonfraudulent_alert_customer_days": alerts - captures,
        "precision": captures / alerts if alerts else None,
        "recall": captures / frauds if frauds else None,
        "average_precision": average_precision([(r[2], r[4]) for r in matured])
        if matured
        else None,
    }
