"""Explicit expanded-window preparation and exact compatibility for accepted sessions."""

from contextlib import closing
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import subprocess
import time
from uuid import uuid4

import pandas as pd
from scripts.platform import kind_foundation as f
from scripts.platform import source_compatibility
from scripts.product_flow import controller as flow
from scripts.product_session import reader

from fraud_detection_mlops.data.datasets.native_replay_dataset import readonly_connection
from fraud_detection_mlops.serving.model_release import ReleaseManifest
from fraud_detection_mlops.streaming.contracts.customer_day_alerts import CustomerDayAlertConfig
from fraud_detection_mlops.streaming.contracts.transaction_events import clock_ns

POLICY = "references/product_session_scale_policy_v1.json"
TRANSITION = "references/product_session_scale_source_transition_v1.json"


def code_pins():
    return {v.name: reader.sha(v) for v in (f.ROOT / "scripts/product_session").glob("*.py")}


def accept(saved):
    current = code_pins()
    if saved == current:
        return
    # Advance only exact committed maps, keeping legacy and scale receipts immutable.
    for name, schema in (
        (TRANSITION, "fraud_product_session_scale_source_transition_v1"),
        (
            "references/session_busy_source_transition_v1.json",
            "fraud_session_busy_source_transition_v1",
        ),
    ):
        path = f.ROOT / name
        if not path.is_file():
            continue
        declaration = json.loads(reader.checked(path).read_bytes())
        if declaration.get("schema") != schema or declaration.get("before") != saved:
            continue
        committed = subprocess.run(
            ["git", "show", "HEAD:" + name], cwd=f.ROOT, capture_output=True, check=False
        )
        flow.p.require(
            committed.returncode == 0 and committed.stdout == path.read_bytes(),
            "Commit the reviewed session source transition",
        )
        saved = declaration["after"]
        if saved == current:
            return
    raise ValueError("Session code identity changed outside the exact reviewed transition")


def bounds(start, end, target, limits):
    clock_ns(start)
    clock_ns(end)
    first, last = pd.Timestamp(start), pd.Timestamp(end)
    flow.p.require(
        first.tzinfo is None
        and last.tzinfo is None
        and first < last
        and last - first <= timedelta(days=limits["maximum_window_days"]),
        "Use an increasing timezone-naive Handbook window of at most seven days",
    )
    flow.p.require(
        type(target) is int and 2 <= target <= limits["maximum_target_decisions"],
        "Target decisions must be 2..50000",
    )
    return first, last


def prepare(directory, key, source, start, end, target):
    begun = time.monotonic()
    values = {**flow.pins(), POLICY: reader.sha(f.ROOT / POLICY)}
    for name, expected in values.items():
        committed = subprocess.run(
            ["git", "show", "HEAD:" + name], cwd=f.ROOT, capture_output=True, check=False
        )
        flow.p.require(
            committed.returncode == 0 and hashlib.sha256(committed.stdout).hexdigest() == expected,
            "Commit expanded session dependencies before prepare: " + name,
        )
    before_code = code_pins()
    limits = json.loads((f.ROOT / POLICY).read_bytes())
    first, last = bounds(start, end, target, limits)
    source = f.managed_destination(Path(source) / "checkpoint.sqlite", "replay")
    before = flow.inputs.source_files(source)
    with closing(readonly_connection(source)) as conn:
        count = conn.execute(
            "SELECT count(*) FROM transactions WHERE tx_ns>=? AND tx_ns<?",
            (clock_ns(start), clock_ns(end)),
        ).fetchone()[0]
    flow.p.require(count >= 2, "Window contains fewer than two transactions")
    flow.p.require(
        limits["schema"] == "fraud_product_session_scale_policy_v1", "Invalid expanded policy"
    )
    config = flow.policy()
    config.update(
        context_start=(first - timedelta(days=limits["context_days"])).isoformat(),
        scoring_start=first.isoformat(),
        scored_transactions=min(target, count),
        maximum_scored_with_timestamp_peers=min(target, count) + limits["maximum_timestamp_peers"],
    )
    current = flow.active()
    flow.deployment.check_active(
        current, json.loads((f.ROOT / "references/model_update_policy_v1.json").read_bytes())
    )
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    flow.p.require(
        directory.stat().st_mode & 0o077 == 0 and not any(directory.iterdir()),
        "Expanded preparation requires a new private directory; preserve incomplete attempts",
    )
    capture = flow.inputs.capture(source, directory / "events.sqlite", config)
    flow.p.require(
        capture["scoring_window_ns"][1] <= clock_ns(end)
        and before == flow.inputs.source_files(source),
        "Window or source changed during capture",
    )
    slot = current["slot"]
    manifest = current["plan"]["releases"][slot]["manifest"]
    release = ReleaseManifest.model_validate(manifest)
    alert = CustomerDayAlertConfig(
        schema_version=1,
        version="first_crossing_customer_day_v1",
        model_sha256=release.model_sha256,
        serving_release_id=release.serving_release_id,
        score_threshold=config["score_threshold"],
        daily_customer_budget=config["daily_customer_budget"],
    )
    value = {
        "schema": "fraud_product_flow_input_v1",
        "flow_id": "session-" + key,
        "implementation_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=f.ROOT, text=True
        ).strip(),
        "source_pins": {name: digest for name, digest in values.items() if name != POLICY},
        "policy": config,
        "source_state": str(source.parent),
        "capture": capture,
        "active": current,
        "release_manifest": manifest,
        "alert_policy": alert.model_dump(),
        "expanded_window": {
            "start": first.isoformat(),
            "end_exclusive": last.isoformat(),
            "target_decisions": target,
            "eligible_transactions": count,
            "limits_sha256": reader.sha(f.ROOT / POLICY),
        },
        "fit_executed": False,
        "champion_modified": False,
    }
    flow.p.require(
        before_code == code_pins()
        and values == {**flow.pins(), POLICY: reader.sha(f.ROOT / POLICY)},
        "Implementation changed during capture",
    )
    f.private_json(directory / "input.json", value)
    receipt = {
        "schema": "fraud_product_session_v2",
        "session_id": key,
        "input_sha256": reader.sha(directory / "input.json"),
        "code_pins": code_pins(),
        "expanded_window": value["expanded_window"],
    }
    f.private_json(directory / "session.json", receipt)
    f.private_json(
        directory / ("preparation-" + uuid4().hex + ".json"),
        {
            "schema": "fraud_product_session_timing_v1",
            "phase": "preparation",
            "elapsed_seconds": time.monotonic() - begun,
            "scored_decisions": capture["counts"]["decisions"],
            "context_transactions": capture["counts"]["transactions"]
            - capture["counts"]["decisions"],
            "champion_modified": False,
        },
    )
    return {
        "status": "expanded_session_prepared",
        "session_id": key,
        "session_sha256": reader.sha(directory / "session.json"),
        "directory": str(directory),
        "window": value["expanded_window"],
        "counts": capture["counts"],
        "champion_modified": False,
    }


def read(directory, key, receipt):
    value = json.loads(reader.checked(directory / "input.json").read_bytes())
    flow.p.require(
        receipt["input_sha256"] == reader.sha(directory / "input.json")
        and value["flow_id"] == "session-" + key,
        "Expanded input identity differs",
    )
    source_compatibility.accept(value["source_pins"], flow.pins(), f.ROOT)
    flow.p.require(
        value["capture"]["input_sha256"]
        == reader.sha(reader.checked(directory / "events.sqlite")),
        "Expanded event bytes changed",
    )
    flow.p.require(
        receipt["expanded_window"] == value["expanded_window"]
        and value["expanded_window"]["limits_sha256"] == reader.sha(f.ROOT / POLICY),
        "Expanded policy identity differs",
    )
    limits = json.loads((f.ROOT / POLICY).read_bytes())
    window = value["expanded_window"]
    bounds(window["start"], window["end_exclusive"], window["target_decisions"], limits)
    manifest = ReleaseManifest.model_validate(value["release_manifest"])
    CustomerDayAlertConfig.model_validate(value["alert_policy"])
    flow.p.require(
        value["capture"]["counts"]["decisions"]
        <= window["target_decisions"] + limits["maximum_timestamp_peers"]
        and value["capture"]["scoring_window_ns"][0] == clock_ns(window["start"])
        and value["capture"]["scoring_window_ns"][1] <= clock_ns(window["end_exclusive"])
        and value["active"]["plan"]["releases"][value["active"]["slot"]]["manifest"]
        == manifest.model_dump(),
        "Expanded window or release differs",
    )
    return value
