"""Concurrent helper failures are propagated; freshness and identity cannot be fabricated."""

import json
import sqlite3
import time

import pytest
from scripts.product_session import controller as session
from scripts.product_session import reader
from scripts.session_monitoring import collector
from scripts.session_monitoring.server import Projection
from tests.product_flow.conftest import scorer
from tests.product_session.operations import Observer, Worker, progressing, sample


def wait_for(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline
        time.sleep(0.01)


def test_observer_collects_committed_HTTP_arrivals_during_a_real_producer(prepared, server):
    directory, value, _ = prepared
    (directory / "input.json").write_text(json.dumps(value))
    (directory / "session.json").write_text(
        json.dumps({"session_id": "fixture", "input_sha256": reader.sha(directory / "input.json")})
    )
    binding, _ = collector.identity(directory)
    projection = Projection(binding)
    observed = []

    def publish(payload):
        projection.publish(payload)
        observed.append(payload["counts"]["decisions"])

    observer = Observer(
        lambda: collector.collect(directory, binding), publish, interval=0.01
    ).start()
    worker = Worker(lambda: session.pump(directory, value, scorer(server[0], value), interval=0.2))
    try:
        wait_for(worker.done.is_set)
        assert worker.join()["status"] == "awaiting_feedback"
        wait_for(lambda: 2 in observed)
        assert 1 in observed and 2 in observed
        assert observer.latest()["counts"]["decisions"] == 2
        assert projection.summary()["source_valid"]
    finally:
        observer.stop()


def test_worker_propagates_HTTP_failure_without_false_success():
    def fail():
        raise ValueError("HTTP identity differs")

    worker = Worker(fail)
    with pytest.raises(ValueError, match="HTTP identity"):
        worker.join()


def test_observer_retries_schema_initialization_but_not_identity_failure():
    calls = []

    def collect():
        calls.append(1)
        if len(calls) < 3:
            raise sqlite3.OperationalError("no such table: identity")
        return {"counts": {"events": 1}}

    observer = Observer(collect, lambda _: None, interval=0.01).start()
    wait_for(lambda: len(calls) >= 3)
    assert observer.stop()["counts"]["events"] == 1

    def foreign():
        raise ValueError("Foreign checkpoint")

    observer = Observer(foreign, lambda _: None, interval=0.01).start()
    wait_for(lambda: observer.error is not None)
    with pytest.raises(ValueError, match="Foreign"):
        observer.stop()


def test_progress_requires_two_increasing_metrics_inside_live_window():
    records = [
        {"producer_status": "complete", "prometheus_decisions": 10, "committed_decisions": 10}
    ]
    assert not progressing(records, 10)
    records = [{"producer_status": "running", "prometheus_decisions": 3, "committed_decisions": 4}]
    assert not progressing(records * 2, 10)
    assert progressing(
        records
        + [{"producer_status": "running", "prometheus_decisions": 5, "committed_decisions": 6}],
        10,
    )
    assert not progressing(
        records
        + [{"producer_status": "running", "prometheus_decisions": 5, "committed_decisions": 10}],
        10,
    )


@pytest.mark.parametrize("failure", ["binding", "ahead", "stale", "collected"])
def test_observation_rejects_other_binding_future_counts_and_stale_source(failure):
    binding = {"session_sha256": "a" * 64}
    aggregate = {"binding": binding, "counts": {"decisions": 4}, "producer_status": "running"}
    current, metric, valid = 5, 3, 1
    if failure == "binding":
        aggregate["binding"] = {"session_sha256": "b" * 64}
    elif failure == "ahead":
        metric = 6
    elif failure == "stale":
        valid = 0
    else:
        aggregate["counts"]["decisions"] = 6
    with pytest.raises(ValueError):
        sample(binding, aggregate, current, metric, valid)


def test_isolated_UI_probe_does_not_receive_storage_or_tracking_credentials(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from tests.product_session.live import test_live_operations as native

    python = tmp_path / ".tox/portal-ui/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text("isolated")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "must-not-leave-parent")
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "must-not-leave-parent")
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(
            returncode=0, stdout='FRAUD_LIVE_PORTAL_PROBE={"status":"verified"}\n', stderr=""
        )

    monkeypatch.setattr(native.subprocess, "run", run)
    assert native.probe_ui(tmp_path, "portal", "a" * 64) == {"status": "verified"}
    assert not (
        {"AWS_SECRET_ACCESS_KEY", "MLFLOW_TRACKING_URI", "VIRTUAL_ENV"} & set(calls[0][1]["env"])
    )
    assert calls[0][0][0] == str(python)


def test_isolated_UI_failure_cannot_be_reported_as_native_success(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from tests.product_session.live import test_live_operations as native

    python = tmp_path / ".tox/portal-ui/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text("isolated")
    monkeypatch.setattr(
        native.subprocess,
        "run",
        lambda *_, **__: SimpleNamespace(returncode=1, stdout="", stderr="UI failed"),
    )
    with pytest.raises(ValueError, match="UI probe failed"):
        native.probe_ui(tmp_path, "portal", "a" * 64)
