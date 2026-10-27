"""Collect actual causal HTTP checkpoints before and after pause/feedback; preserve bytes."""

import json

import pytest
from scripts.product_session import controller as session
from scripts.product_session import reader
from scripts.session_monitoring import collector
from tests.product_flow.conftest import scorer


def make_receipt(directory, value):
    (directory / "input.json").write_text(json.dumps(value))
    receipt = {
        "schema": "fraud_product_session_v1",
        "session_id": "fixture",
        "input_sha256": reader.sha(directory / "input.json"),
    }
    (directory / "session.json").write_text(json.dumps(receipt))
    return collector.identity(directory)[0]


def test_same_checkpoint_progress_and_feedback_aggregates(prepared, server):
    directory, value, _ = prepared
    binding = make_receipt(directory, value)
    initial = collector.collect(directory, binding)
    assert initial["counts"]["decisions"] == 0 and initial["producer_status"] == "prepared"
    session.pump(
        directory,
        value,
        scorer(server[0], value),
        interval=0.05,
        stop_after=1,
        wait=lambda _: None,
    )
    first = collector.collect(directory, binding)
    assert first["counts"]["decisions"] == 1 and first["counts"]["decision_feedback"] == 0
    assert first["producer_status"] == "paused"
    session.pump(directory, value, scorer(server[0], value), interval=0.05, wait=lambda _: None)
    second = collector.collect(directory, binding)
    assert second["counts"]["decisions"] > first["counts"]["decisions"]
    session.pump(
        directory,
        value,
        scorer(server[0], value),
        interval=0.05,
        feedback=True,
        wait=lambda _: None,
    )
    before = {
        p.name: reader.sha(p)
        for p in directory.iterdir()
        if p.is_file() and not p.name.endswith(("-shm", "-wal"))
    }
    final = collector.collect(directory, binding)
    live = reader.read(directory, binding["session_sha256"])
    assert final["counts"]["decisions"] == live["totals"]["decisions"]
    assert final["counts"]["alerts"] == live["totals"]["alerts"]
    assert final["counts"]["decision_feedback"] == final["counts"]["decisions"]
    assert final["counts"]["http_attempts"] >= final["counts"]["decisions"]
    assert final["latency_ms"]["maximum"] >= final["latency_ms"]["p95"] > 0
    assert not ({"cases", "request", "response", "features", "fraud_label"} & set(final))
    assert before == {p.name: reader.sha(p) for p in directory.iterdir() if p.name in before}
    heartbeat = directory / "heartbeat.json"
    saved_heartbeat = heartbeat.read_bytes()
    heartbeat.unlink()
    with pytest.raises(ValueError, match="Missing heartbeat"):
        collector.collect(directory, binding)
    heartbeat.write_bytes(saved_heartbeat)
    value["release_manifest"]["model_sha256"] = "f" * 64
    (directory / "input.json").write_text(json.dumps(value))
    with pytest.raises(ValueError, match="source changed"):
        collector.collect(directory, binding)
