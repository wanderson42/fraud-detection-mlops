"""Predeclared, algorithm-independent policy for the new canonical laboratory round."""

import hashlib
import json
import math
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "references/operational_round_policy_v1.json"


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def load(path=POLICY, root=ROOT):
    value = json.loads(path.read_bytes())
    require(
        value["schema"] == "fraud_operational_round_policy_v1"
        and value["registered_model"] == "fraud-detector"
        and value["candidates"] == ["dummy_prior", "logistic_regression", "hist_gradient_boosting"]
        and value["eligible_candidates"] == ["logistic_regression", "hist_gradient_boosting"]
        and value["fit_split"] == "train"
        and value["selection_split"] == "validation"
        and value["test_evaluated"] is False
        and value["historical_models_used"] is False
        and value["production_promotion"] is False,
        "Unsupported operational round policy",
    )
    for name, expected in value["input_contracts"].items():
        require(sha(root / "references" / name) == expected, "Input policy changed: " + name)
    for name in ["minimum_average_precision", "minimum_daily_customer_precision_at_100"]:
        require(type(value[name]) is float and 0 < value[name] <= 1, "Invalid quality threshold")
    return value


def round_id(value):
    require(bool(re.fullmatch(r"[a-z][a-z0-9-]{2,39}", value)), "Use a short lowercase round ID")
    return value


def decide(comparison, policy):
    require(set(comparison) == set(policy["candidates"]), "All three new candidates are mandatory")
    for metrics in comparison.values():
        require(
            all(
                type(metrics[k]) in (int, float)
                and math.isfinite(metrics[k])
                and 0 <= metrics[k] <= 1
                for k in ["average_precision", "daily_customer_precision_at_100"]
            ),
            "Incomplete or invalid candidate metrics",
        )
    checks = {}
    for name in policy["eligible_candidates"]:
        measured = comparison[name]
        checks[name] = {
            "minimum_AP": measured["average_precision"] >= policy["minimum_average_precision"],
            "minimum_P100": measured["daily_customer_precision_at_100"]
            >= policy["minimum_daily_customer_precision_at_100"],
            "beats_dummy_AP": measured["average_precision"]
            > comparison["dummy_prior"]["average_precision"],
        }
    eligible = [name for name in policy["eligible_candidates"] if all(checks[name].values())]
    ranking = sorted(eligible, key=lambda n: (-comparison[n]["average_precision"], n))
    return {
        "passed": bool(ranking),
        "selected_model": ranking[0] if ranking else None,
        "checks": checks,
        "selection_split": "validation",
        "test_evaluated": False,
        "historical_models_used": False,
        "production_promotion": False,
    }
