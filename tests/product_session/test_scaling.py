"""Expanded capture uses original events; SQL pages remain bounded and causally verified."""

import copy
import hashlib
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pandas as pd
import pytest
from scripts.product_flow import controller as flow
from scripts.product_session import configuration as config
from scripts.product_session import controller as session
from scripts.product_session import reader
from tests.product_flow.conftest import scorer

from fraud_detection_mlops.serving.model_release import load_release
from fraud_detection_mlops.streaming.contracts.customer_day_alerts import CustomerDayAlertConfig
from fraud_detection_mlops.streaming.contracts.transaction_events import (
    LABEL_DELAY_NS,
    FeedbackEvent,
    TransactionEvent,
)
from fraud_detection_mlops.streaming.replay.durable_triage_replay import (
    DurableTriageReplay,
    event_order,
)
from fraud_detection_mlops.streaming.replay.synthetic_event_stream import synthetic_events

ROOT = Path(__file__).resolve().parents[2]


def test_expanded_capture_real_HTTP_pagination_feedback_and_full_reconstruction(
    prepared, release, server, monkeypatch
):
    directory, old, _ = prepared
    runtime = load_release(*release)
    events = synthetic_events()
    for index in range(260):
        stamp = pd.Timestamp("2018-04-15") + pd.Timedelta(index + 1, "s")
        tx = TransactionEvent(
            schema_version=1,
            event_type="transaction",
            transaction_id=1000 + index,
            tx_datetime=stamp.isoformat(),
            customer_id=3 + index % 7,
            terminal_id=10,
            amount=float(index % 80),
        )
        events += [
            tx,
            FeedbackEvent(
                schema_version=1,
                event_type="feedback",
                transaction_id=tx.transaction_id,
                available_at=pd.Timestamp(tx.event_clock_ns + LABEL_DELAY_NS).isoformat(),
                fraud_label=index % 2,
            ),
        ]
    source = directory.parents[1] / "replay/larger-source"
    source.mkdir(parents=True)
    with DurableTriageReplay(
        source / "checkpoint.sqlite",
        run_id="larger-source",
        input_sha256="a" * 64,
        runtime=runtime,
        policy=CustomerDayAlertConfig.model_validate(old["alert_policy"]),
        data_scope="synthetic_fixture",
    ) as engine:
        for event in sorted(events, key=event_order):
            engine.handle(event)
    destination = directory.parent / "expanded"
    active = {
        "slot": "blue",
        "plan": {"releases": {"blue": {"manifest": old["release_manifest"]}}},
    }
    monkeypatch.setattr(flow, "active", lambda: active)
    monkeypatch.setattr(flow.deployment, "check_active", lambda *_: None)
    # Commit guard is isolated here; capture and its scientific verifier remain real.
    original_run = subprocess.run

    def git_show(args, **kwargs):
        if args[:2] == ["git", "show"]:
            return SimpleNamespace(
                returncode=0, stdout=(ROOT / args[-1].split(":", 1)[1]).read_bytes()
            )
        return original_run(args, **kwargs)

    monkeypatch.setattr(config.subprocess, "run", git_show)
    before = reader.sha(source / "checkpoint.sqlite")
    result = config.prepare(
        destination, "expanded", source, "2018-04-15T00:00:00", "2018-04-16T00:00:00", 300
    )
    assert result["counts"]["decisions"] == 262
    receipt = json.loads((destination / "session.json").read_bytes())
    value = config.read(destination, "expanded", receipt)
    pin = reader.sha(destination / "session.json")
    first = session.pump(
        destination,
        value,
        scorer(server[0], value),
        interval=0.05,
        stop_after=10,
        wait=lambda _: None,
    )
    assert first["status"] == "paused" and first["phase_elapsed_seconds"]["warmup"] >= 0
    assert first["scoring_HTTP_attempts"] == 10
    session.pump(destination, value, scorer(server[0], value), interval=0.05, wait=lambda _: None)
    pages = [reader.read(destination, pin, page=i, page_size=100) for i in [1, 2, 3]]
    assert [len(p["cases"]) for p in pages] == [100, 100, 62]
    ids = [c["request"]["transaction_id"] for page in pages for c in page["cases"]]
    assert len(set(ids)) == len(ids) == 262
    assert all(p["totals"]["decisions"] == 262 and sum(p["score_histogram"]) == 262 for p in pages)
    alerts = reader.read(destination, pin, only_alerts=True, page_size=25)
    assert all(c["triage"]["status"] == "alert" for c in alerts["cases"])
    assert all(c["transaction_feedback"] is None for page in pages for c in page["cases"])
    assert reader.read(destination, pin, page=999)["pagination"]["page"] == 3
    session.pump(
        destination,
        value,
        scorer(server[0], value),
        feedback=True,
        interval=0.05,
        wait=lambda _: None,
    )
    verified = session.verify(destination, value)
    assert verified["HTTP_receipts"] == 262
    assert verified["HTTP_latency_ms"]["measurements"] == 262
    assert (
        0
        <= verified["HTTP_latency_ms"]["p50"]
        <= verified["HTTP_latency_ms"]["p95"]
        <= verified["HTTP_latency_ms"]["maximum"]
    )
    assert len(list(destination.glob("preparation-*.json"))) == 1
    assert reader.sha(source / "checkpoint.sqlite") == before
    assert reader.read(destination, pin)["totals"]["feedback"] == 262


@pytest.mark.parametrize(
    "start,end,target",
    [
        ("2018-04-16T00:00:00", "2018-04-15T00:00:00", 2000),
        ("2018-04-15T00:00:00", "2018-04-23T00:00:00", 2000),
        ("2018-04-15T00:00:00+00:00", "2018-04-16T00:00:00", 2000),
        ("2018-04-15T00:00:00", "2018-04-16T00:00:00", 50001),
    ],
)
def test_bad_window_and_budget_rejected(start, end, target):
    with pytest.raises(ValueError):
        config.bounds(start, end, target, json.loads((ROOT / config.POLICY).read_bytes()))


@pytest.mark.parametrize(
    "kwargs", [{"page": 0}, {"page_size": 101}, {"page_size": True}, {"only_alerts": "yes"}]
)
def test_unbounded_or_invalid_page_is_rejected_before_IO(kwargs):
    with pytest.raises(ValueError):
        reader.read(Path("/not/a/session"), "a" * 64, **kwargs)


def test_exact_committed_compatibility_rejects_unreviewed_code_or_old_map(tmp_path, monkeypatch):
    path = tmp_path / "scripts/product_session/controller.py"
    path.parent.mkdir(parents=True)
    path.write_text("reviewed\n")
    current = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()}
    old = {path.name: "a" * 64}
    declaration = tmp_path / config.TRANSITION
    declaration.parent.mkdir()
    declaration.write_text(
        json.dumps(
            {
                "schema": "fraud_product_session_scale_source_transition_v1",
                "before": old,
                "after": current,
            }
        )
    )
    monkeypatch.setattr(config.f, "ROOT", tmp_path)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "reviewed",
        ],
        cwd=tmp_path,
        check=True,
    )
    config.accept(old)
    invalid = copy.deepcopy(old)
    invalid[path.name] = "f" * 64
    with pytest.raises(ValueError, match="code identity"):
        config.accept(invalid)
    path.write_text("unreviewed\n")
    with pytest.raises(ValueError, match="code identity"):
        config.accept(old)


def test_declared_clock_precision_is_not_silently_truncated():
    start = "2018-04-15T00:00:00.000000001"
    end = "2018-04-15T00:00:00.000000002"
    first, last = config.bounds(start, end, 2, json.loads((ROOT / config.POLICY).read_bytes()))
    assert last.value - first.value == 1 and first.isoformat() == start
