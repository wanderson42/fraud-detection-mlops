"""Aggregate scope, actual receiver HTTP, real Helm rendering and recovery guards."""

import copy
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
from threading import Thread
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from scripts.monitoring import feature_health, statistics
from scripts.session_monitoring import components, contract, details, server
import yaml

ROOT = Path(__file__).resolve().parents[2]


def zeros():
    return {key: None if key in details.NULLABLE else 0 for key in details.KEYS}


def db_fixture():
    db = sqlite3.connect(":memory:")
    db.executescript("""
    CREATE TABLE transactions(transaction_id INTEGER,tx_ns INTEGER,customer_id INTEGER);
    CREATE TABLE decisions(transaction_id INTEGER,request TEXT,response TEXT);
    CREATE TABLE alert_decisions(transaction_id INTEGER,payload TEXT);
    CREATE TABLE feedback(transaction_id INTEGER,fraud_label INTEGER);
    """)
    features = dict.fromkeys(feature_health.contract()[0], 0)
    features["TX_WEEKDAY"] = 7
    stamp = feature_health.clock_ns("2018-09-16T00:00:00")
    request = {
        "schema_version": 1,
        "feature_contract_version": "gold_v1",
        "transaction_id": 1,
        "customer_id": 1,
        "terminal_id": 1,
        "tx_datetime": "2018-09-16T00:00:00",
        "feature_as_of": "2018-09-16T00:00:00",
        "features": features,
    }
    for i in range(1, 5):
        request.update(transaction_id=i, customer_id=i)
        db.execute("INSERT INTO transactions VALUES(?,?,?)", (i, stamp, i))
        db.execute(
            "INSERT INTO decisions VALUES(?,?,?)",
            (i, json.dumps(request), json.dumps({"score": 0.9 if i < 3 else 0.1})),
        )
        db.execute(
            "INSERT INTO alert_decisions VALUES(?,?)",
            (i, json.dumps({"status": "alert" if i < 3 else "below_threshold"})),
        )
        db.execute("INSERT INTO feedback VALUES(?,?)", (i, int(i in (1, 3))))
    return db, stamp


def test_actual_sqlite_cohort_health_and_missing_drift():
    db, stamp = db_fixture()
    try:
        value = details.collect(
            db, {"decisions": 4}, stamp + 8 * statistics.DAY_NS, {"daily_customer_budget": 100}
        )
        value.update(details.pod_snapshot([]))
        details.validate(value)
        assert value["quality_mature_transactions"] == 4
        assert value["quality_precision"] == value["quality_recall"] == 0.5
        assert value["feature_invalid_rows"] == 0
        assert value["feature_current_rows"] == 4
        assert value["feature_comparison_ready"] == 0
        assert value["feature_js_TX_AMOUNT"] is None
        assert value["score_jensen_shannon"] is None
    finally:
        db.close()


def test_pending_feedback_not_treated_as_genuine():
    db, stamp = db_fixture()
    try:
        db.execute("DELETE FROM feedback WHERE transaction_id=1")
        value = details.collect(db, {"decisions": 4}, stamp, {"daily_customer_budget": 100})
        assert value["pending_decision_feedback"] == 1
        assert value["quality_mature_transactions"] == 0
        assert value["quality_precision"] is None
    finally:
        db.close()


@pytest.mark.parametrize(
    "change", ["unknown", "nan", "negative", "fraction", "unsupported_js", "raw_id"]
)
def test_wire_rejects_unbounded_or_unsupported_details(change):
    value = zeros()
    if change == "unknown":
        value["extra"] = 1
    elif change == "nan":
        value["quality_precision"] = float("nan")
    elif change == "negative":
        value["feature_current_rows"] = -1
    elif change == "fraction":
        value["feature_missing_TX_AMOUNT"] = 2
    elif change == "unsupported_js":
        value["feature_js_TX_AMOUNT"] = 0.1
    else:
        value["customer_id"] = 161
    with pytest.raises(ValueError):
        details.validate(value)


def test_readiness_not_assumed_from_running_phase():
    pod = {
        "metadata": {"name": "fraud-platform-postgres-0"},
        "status": {
            "phase": "Running",
            "conditions": [{"type": "Ready", "status": "False"}],
            "containerStatuses": [{"restartCount": 2}],
        },
    }
    value = details.pod_snapshot([pod])
    assert value["pod_postgres_observed"] == 1
    assert value["pod_postgres_ready"] == 0
    assert value["pod_postgres_restarts"] == 2
    assert value["pod_api_observed"] == value["pod_api_ready"] == 0


def projection():
    binding = {
        "session_id": "fixture",
        "session_sha256": "a" * 64,
        "model_sha256": "b" * 64,
        "serving_release_id": "c" * 32,
        "expected_events": 10,
        "expected_decisions": 2,
    }
    value = {
        "binding": binding,
        "captured_at": 100,
        "producer_at": 100,
        "producer_status": "complete",
        "counts": dict.fromkeys(contract.COUNTS, 0),
        "watermark": [None] * 3,
        "latency_ms": {"p50": 0, "p95": 0, "maximum": 0},
        "details": zeros(),
    }
    return binding, value


def test_actual_HTTP_receiver_extended_payload_and_expiry():
    binding, value = projection()
    clock = [100]
    receiver = server.Projection(binding, clock=lambda: clock[0])
    http = ThreadingHTTPServer(("127.0.0.1", 0), server.handler(receiver, "s" * 64))
    thread = Thread(target=http.serve_forever, daemon=True)
    thread.start()
    url = "http://127.0.0.1:" + str(http.server_address[1])
    try:
        request = Request(
            url + "/projection",
            data=contract.dumps(value),
            method="PUT",
            headers={"Authorization": "Bearer " + "s" * 64},
        )
        with urlopen(request) as response:
            assert response.status == 200
        with urlopen(url + "/metrics") as response:
            metrics = response.read().decode()
        assert "fraud_session_detail_feature_current_rows{" in metrics
        assert "fraud_session_detail_feature_js_TX_AMOUNT{" not in metrics
        clock[0] = 131
        assert receiver.summary()["source_valid"] is False
        # Old projections still accepted after restart, and do not claim detail capability.
        legacy = copy.deepcopy(value)
        legacy.pop("details")
        legacy["captured_at"] = legacy["producer_at"] = 131
        receiver.publish(legacy)
        assert receiver.summary()["source_valid"] is True
        assert "fraud_session_detail_" not in receiver.metrics()
        invalid = copy.deepcopy(legacy)
        invalid["captured_at"] = 132
        invalid["details"] = {**zeros(), "customer_id": 1}
        bad = Request(
            url + "/projection",
            data=contract.dumps(invalid),
            method="PUT",
            headers={"Authorization": "Bearer " + "s" * 64},
        )
        with pytest.raises(HTTPError) as error:
            urlopen(bad)
        assert error.value.code == 400
    finally:
        http.shutdown()
        http.server_close()
        thread.join()


def test_capability_is_not_implied_by_source_compatibility():
    with pytest.raises(ValueError, match="lacks current"):
        components.capability({"source_pins": {}})


def test_rollback_guards_do_not_require_ready_receiver(monkeypatch):
    monkeypatch.setattr(
        components.k, "inventory", lambda: pytest.fail("Ready inventory blocks recovery")
    )
    monkeypatch.setattr(
        components.k,
        "get",
        lambda kind, name: {"metadata": {"uid": name}, "spec": {"volumeName": name}},
    )
    monkeypatch.setattr(components.k, "owned", lambda item: item)
    monkeypatch.setattr(components.k, "protected_inventory", list)
    monkeypatch.setattr(components.s.session.flow, "active", lambda: {"model": "same"})
    assert components.protected()["active"] == {"model": "same"}


def test_wrong_apply_approval_has_no_mutation(monkeypatch):
    plan = {"fixture": 1}
    monkeypatch.setattr(components, "read_plan", lambda key: plan)
    monkeypatch.setattr(
        components.s, "atomic", lambda *args: pytest.fail("Mutation before approval")
    )
    with pytest.raises(ValueError, match="Approval differs"):
        components.apply("fixture", "wrong")


def test_real_Helm_five_dashboards_one_receiver_and_two_PVCs(tmp_path):
    if not shutil.which("helm"):
        pytest.skip("Helm required")
    values = {
        "stateReady": True,
        "legacyReplay": False,
        "sessionComponents": True,
        "sessionTelemetry": {
            "enabled": True,
            "image": "fraud-session-monitor:" + "b" * 32,
            "binding": projection()[0],
        },
    }
    path = tmp_path / "values.json"
    path.write_text(json.dumps(values))
    done = subprocess.run(
        [
            "helm",
            "template",
            "fraud-observability",
            str(ROOT / "infra/helm/fraud-observability"),
            "--values",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    objects = [o for o in yaml.safe_load_all(done.stdout) if o]
    dashboards = next(
        o
        for o in objects
        if o["kind"] == "ConfigMap" and o["metadata"]["name"].endswith("-dashboards")
    )["data"]
    assert len(dashboards) == 5
    assert len([o for o in objects if o["kind"] == "PersistentVolumeClaim"]) == 2
    exporter = next(
        o
        for o in objects
        if o["kind"] == "Deployment" and o["metadata"]["name"].endswith("-exporter")
    )
    assert len(exporter["spec"]["template"]["spec"]["containers"]) == 1
    for raw in dashboards.values():
        d = json.loads(raw)
        assert len({p["id"] for p in d["panels"]}) == len(d["panels"])
    assert (
        dashboards["product-session.json"].strip()
        == (ROOT / "infra/helm/fraud-observability/files/session/dashboard.json")
        .read_text()
        .strip()
    )


def test_wire_quality_denominator_cannot_exceed_decisions():
    binding, value = projection()
    value["details"]["quality_mature_transactions"] = 1
    with pytest.raises(ValueError, match="cohort exceeds"):
        contract.validate(value, binding, 100)


def test_recovery_restores_exact_accepted_bytes(tmp_path):
    raw = b'{ "retained": true }\n\n'
    path = tmp_path / "image.json"
    path.write_bytes(b"new-candidate")
    components.restore_bytes(path, raw)
    assert path.read_bytes() == raw
    assert path.stat().st_mode & 0o077 == 0


def test_rollout_failure_retains_rollback_path_without_ready_inventory(tmp_path, monkeypatch):
    from contextlib import nullcontext

    monkeypatch.setattr(components.f, "ROOT", tmp_path)
    monkeypatch.setattr(components.f, "managed_destination", lambda path, *args: path)
    tmp_path = tmp_path / "data/storage/observability"
    tmp_path.mkdir(parents=True)
    prior = {name: {"image": "prior"} for name in components.FILES}
    plan = {
        "schema": "fixture",
        "protected": {"same": True},
        "before_values": {"old": True},
        "desired_values": {"new": True},
        "binding": {"session_id": "fixture"},
        "accepted_sources": {"same": True},
        "candidate": {"image": "candidate", "loaded_identities": ["image-id"]},
        "before": prior,
    }
    monkeypatch.setattr(components, "read_plan", lambda key: plan)
    monkeypatch.setattr(components, "protected", lambda: {"same": True})
    monkeypatch.setattr(components, "no_watch", nullcontext)
    monkeypatch.setattr(components, "work", lambda key: tmp_path)
    monkeypatch.setattr(components.s, "work", lambda: tmp_path)
    monkeypatch.setattr(components.s.session, "load", lambda key: (tmp_path, {}))
    monkeypatch.setattr(components, "sources", lambda path: {"same": True})
    monkeypatch.setattr(components, "inspect_image", lambda key: {"id": "image-id"})
    monkeypatch.setattr(components.k, "credentials", lambda: {"owner_id": "owner"})
    monkeypatch.setattr(components.f, "owner", lambda: {"node_uid": "node"})
    monkeypatch.setattr(components.s, "pins", dict)
    monkeypatch.setattr(components.k, "values", lambda ready: {"new": True})
    monkeypatch.setattr(
        components.k,
        "helm",
        lambda *args, **kwargs: SimpleNamespace(stdout=json.dumps({"old": True})),
    )
    monkeypatch.setattr(
        components.k, "install", lambda ready: (_ for _ in ()).throw(ValueError("rollout failed"))
    )
    # The normal atomic writer writes real private files here.
    with pytest.raises(ValueError, match="rollout failed"):
        components.apply("fixture", components.sha_value(plan))
    assert (tmp_path / "applying.json").exists()
    assert (tmp_path / components.MARKER).exists()
    assert not (tmp_path / "applied.json").exists()
    for name in components.FILES:
        assert json.loads((tmp_path / name).read_text())["image"] == "candidate"


def test_extended_collector_uses_real_reconciled_HTTP_checkpoint(prepared, server, monkeypatch):
    from scripts.product_session import controller as session
    from scripts.session_monitoring import collector
    from tests.product_flow.conftest import scorer
    from tests.session_monitoring.test_collection import make_receipt

    directory, value, _ = prepared
    binding = make_receipt(directory, value)
    session.pump(directory, value, scorer(server[0], value), interval=0.05, wait=lambda _: None)
    session.pump(
        directory,
        value,
        scorer(server[0], value),
        interval=0.05,
        feedback=True,
        wait=lambda _: None,
    )
    monkeypatch.setattr(
        components.f, "kube", lambda *args: SimpleNamespace(stdout='{"items": []}')
    )
    checkpoint_before = (directory / "checkpoint.sqlite").read_bytes()
    projection = collector.collect(directory, binding, include_details=True)
    assert (directory / "checkpoint.sqlite").read_bytes() == checkpoint_before
    assert (
        projection["details"]["quality_mature_transactions"] == projection["counts"]["decisions"]
    )
    assert projection["details"]["feature_current_rows"] == projection["counts"]["decisions"]
    assert projection["details"]["pending_decision_feedback"] == 0
    assert projection["details"]["feature_comparison_ready"] == 0
    contract.validate(projection, binding, projection["captured_at"])
