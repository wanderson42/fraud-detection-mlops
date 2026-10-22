"""Predeclared update budgets, mature labels and relative quality gates."""

import math
from pathlib import Path

import pandas as pd
from scripts.operational_round.policy import decide, require
from scripts.operational_round.policy import load as base_load

ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "references/model_update_policy_v1.json"


def load(path=POLICY):
    return validate(base_load(Path(path)))


def validate(value):
    base = base_load()
    frozen = [
        "schema",
        "registered_model",
        "candidates",
        "eligible_candidates",
        "fit_split",
        "selection_split",
        "test_evaluated",
        "historical_models_used",
        "production_promotion",
        "input_contracts",
    ]
    require(all(value[k] == base[k] for k in frozen), "Unsupported data or candidate contract")
    require(value["update_protocol"] == "fraud_model_update_v1", "Unsupported update protocol")
    for key in ("minimum_absolute_AP_gain", "maximum_absolute_P100_drop"):
        x = value[key]
        require(
            type(x) in (int, float) and math.isfinite(x) and 0 <= x <= 1, "Invalid relative gate"
        )
    require(value["minimum_absolute_AP_gain"] > 0, "Improvement must be positive")
    require(pd.Timestamp(value["comparison_as_of"]).tzinfo is None, "Use a naive source clock")
    require(
        0 < value["maximum_HTTP_p95_ms"] <= 10000
        and type(value["http_measurements"]) is int
        and 10 <= value["http_measurements"] <= 100
        and type(value["thread_limit"]) is int
        and 1 <= value["thread_limit"] <= 2,
        "Invalid HTTP budget",
    )
    require(
        set(value["optimization"]) == set(value["candidates"]), "Declare optimization per model"
    )
    for name, spec in value["optimization"].items():
        require(spec["mode"] in {"fixed", "grid", "optuna"}, "Unsupported optimization mode")
        require(isinstance(spec.get("parameters", {}), dict), "Use named parameter overrides")
        require(
            all(
                k.startswith("classifier__") and not k.endswith("random_state")
                for k in spec.get("parameters", {})
            ),
            "Only classifier parameters may vary",
        )
        if name == "dummy_prior":
            require(spec == {"mode": "fixed", "parameters": {}}, "Dummy remains fixed")
        if spec["mode"] != "fixed":
            require(
                type(spec["trials"]) is int
                and type(spec["timeout_seconds"]) is int
                and 1 <= spec["trials"] <= 30
                and 1 <= spec["timeout_seconds"] <= 600,
                "Declare a bounded search budget",
            )
            require(
                spec["space"]
                and all(
                    k.startswith("classifier__") and not k.endswith("random_state")
                    for k in spec["space"]
                ),
                "Invalid search space",
            )
            for choices in spec["space"].values():
                require(
                    isinstance(choices, list)
                    and choices
                    and all(type(x) in (int, float, str, bool, type(None)) for x in choices),
                    "Search domains are explicit categorical choices",
                )
    windows = value["inner_windows"]
    require(
        pd.Timestamp(windows["fit_start"])
        < pd.Timestamp(windows["fit_end_exclusive"])
        <= pd.Timestamp(windows["selection_start"])
        < pd.Timestamp(windows["selection_end_exclusive"]),
        "Invalid inner temporal windows",
    )
    return value


def mature(metadata, as_of):
    clock = pd.Timestamp(as_of)
    require(
        not metadata.empty
        and metadata.LABEL_AVAILABLE_AT.notna().all()
        and metadata.LABEL_AVAILABLE_AT.max() <= clock,
        "Comparison labels are not mature at the declared source clock",
    )


def compare(metrics, incumbent, policy):
    absolute = decide(metrics, policy)
    checks = {}
    for name in policy["eligible_candidates"]:
        measured = metrics[name]
        checks[name] = {
            **absolute["checks"][name],
            "minimum_AP_gain": measured["average_precision"] - incumbent["average_precision"]
            >= policy["minimum_absolute_AP_gain"],
            "P100_noninferiority": measured["daily_customer_precision_at_100"]
            >= incumbent["daily_customer_precision_at_100"] - policy["maximum_absolute_P100_drop"],
        }
    eligible = sorted(
        (n for n, v in checks.items() if all(v.values())),
        key=lambda n: (-metrics[n]["average_precision"], n),
    )
    return {
        "passed": bool(eligible),
        "selected_model": eligible[0] if eligible else None,
        "checks": checks,
        "incumbent_metrics": incumbent,
        "decision": "challenger_qualified" if eligible else "retain_champion",
    }
