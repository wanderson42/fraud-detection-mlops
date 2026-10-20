"""Read-only checkpoint/exporter integration and managed immutable reports."""

import hashlib
import json
import sqlite3
import sys

import pytest
from scripts.monitoring import feature_health, feature_monitoring
from scripts.monitoring.collector import snapshot
from scripts.monitoring.monitoring_exporter import Monitor
from scripts.replay.paced_triage_replay import execute

from fraud_detection_mlops.storage import managed_paths


def test_existing_checkpoint_and_exporter_preserve_source_bytes(setup_job, monkeypatch):
    s = setup_job
    execute(s["factory"], s["output"], s["metadata"], s["runtime"], s["policy"])

    def hashes():
        return {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in s["output"].iterdir()
            if p.is_file()
        }

    before = hashes()
    monkeypatch.setattr(
        s["runtime"].pipeline, "predict_proba", lambda *a: pytest.fail("rescoring")
    )
    result = snapshot(s["output"])
    assert result["feature_health"]["status"] == "insufficient_support"
    assert result["feature_health"]["current"]["rows"] == result["counts"]["decisions"]
    monitor = Monitor(s["output"], 0.1)
    monitor.refresh()
    status, _, metrics = monitor.response("/metrics")
    assert status == 200 and b"fraud_feature_comparison_ready 0" in metrics
    assert b"fraud_replay_events_total" in metrics and hashes() == before


def test_sql_reference_stays_fixed_while_current_window_advances():
    con = sqlite3.connect(":memory:")
    con.executescript(
        "CREATE TABLE transactions (transaction_id INTEGER, tx_ns INTEGER); CREATE TABLE decisions (transaction_id INTEGER, request TEXT);"
    )
    for batch in range(3):
        rows = feature_monitoring.generated_rows(days=batch * 7)
        con.executemany(
            "INSERT INTO transactions VALUES (?,?)",
            [(batch * 1000 + i, stamp) for i, (stamp, _) in enumerate(rows)],
        )
        con.executemany(
            "INSERT INTO decisions VALUES (?,?)",
            [(batch * 1000 + i, raw) for i, (_, raw) in enumerate(rows)],
        )
        observed = feature_health.snapshot(con, (batch + 1) * 1000)
        if batch == 0:
            identity = observed["reference"]["window_request_sha256"]
            # A claimed row count cannot override actual overlap of selected IDs.
            assert not feature_health.snapshot(con, 100000)["comparison_ready"]
        assert observed["reference"]["window_request_sha256"] == identity
        assert observed["current"]["rows"] == 1000
        assert observed["comparison_ready"] is (batch > 0)
    con.close()


def test_cli_refuses_unmanaged_output_before_state_access(tmp_path, monkeypatch):
    monkeypatch.setattr(managed_paths, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(feature_monitoring, "snapshot", lambda *a: pytest.fail("state accessed"))
    monkeypatch.setattr(
        sys, "argv", ["monitor", "snapshot", "--output", str(tmp_path / "outside.json")]
    )
    with pytest.raises(SystemExit) as exc:
        feature_monitoring.main()
    assert exc.value.code == 1 and not (tmp_path / "outside.json").exists()


def test_demo_report_is_immutable_and_does_not_read_native_state(tmp_path, monkeypatch):
    monkeypatch.setattr(managed_paths, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(feature_monitoring, "snapshot", lambda *a: pytest.fail("native state"))
    output = tmp_path / "data/reports/demo.json"
    monkeypatch.setattr(sys, "argv", ["monitor", "demo", "--output", str(output)])
    feature_monitoring.main()
    before = output.read_bytes()
    result = json.loads(before)
    assert result["status"] == "success" and not result["native_state_read"]
    with pytest.raises(SystemExit):
        feature_monitoring.main()
    assert output.read_bytes() == before


def test_container_context_and_dashboard_rule_contracts():
    import yaml

    root = feature_monitoring.ROOT
    dockerfile = (root / "infra/observability/Dockerfile").read_text()
    allowlist = (root / "infra/observability/Dockerfile.dockerignore").read_text()
    assert (
        "COPY references/gold_contract_v1.json /app/references/gold_contract_v1.json" in dockerfile
    )
    assert "!references/\n!references/gold_contract_v1.json\n" in allowlist
    assert "!references/**" not in allowlist and "pip install" not in dockerfile
    rules = yaml.safe_load((root / "infra/observability/alerts.yml").read_text())["groups"][0][
        "rules"
    ]
    selected = {r["alert"]: r for r in rules}
    assert "FraudExporterUnavailable" in selected and "FraudScoreDistributionChanged" in selected
    assert "and on(job, instance)" in selected["FraudFeatureDistributionChanged"]["expr"]
    dashboard = json.loads(
        (root / "infra/observability/grafana/dashboards/feature-health.json").read_text()
    )
    assert dashboard["uid"] == "fraud-feature-health" and len(dashboard["panels"]) == 10
    for panel in dashboard["panels"][1:]:
        assert panel["datasource"]["uid"] == "fraud-prometheus"


def test_invalid_persisted_feature_is_a_quality_alert_not_a_dead_collector(setup_job):
    s = setup_job
    execute(s["factory"], s["output"], s["metadata"], s["runtime"], s["policy"])
    with sqlite3.connect(s["output"] / "checkpoint.sqlite") as con:
        identity, payload = con.execute(
            "SELECT transaction_id,request FROM decisions LIMIT 1"
        ).fetchone()
        request = json.loads(payload)
        del request["features"]["TX_AMOUNT"]
        con.execute(
            "UPDATE decisions SET request=? WHERE transaction_id=?",
            (json.dumps(request), identity),
        )
    monitor = Monitor(s["output"], 0.1)
    monitor.refresh()
    status, _, metrics = monitor.response("/metrics")
    assert status == 200 and b"fraud_monitor_collector_up 1" in metrics
    assert b'fraud_feature_schema_or_clock_invalid_rows{window="current"} 1' in metrics
    assert b"fraud_feature_jensen_shannon{feature=" not in metrics
