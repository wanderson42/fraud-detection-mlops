"""Control the outage check with generated state and simulated HTTP/Docker only."""

from copy import deepcopy
import json
import subprocess

import pytest
from scripts.validation import exporter_recovery as check


@pytest.fixture
def simulated_stack(tmp_path, monkeypatch):
    def setup(mode="success"):
        infra = tmp_path / "infra/observability"
        infra.mkdir(parents=True)
        (infra / ".env").touch()
        (infra / "compose.yaml").touch()
        state = tmp_path / "data/replay/paced-native-v1"
        (state / "assessment").mkdir(parents=True)
        for name in (
            "metadata.json",
            "checkpoint.sqlite",
            "telemetry.sqlite",
            "assessment/result.json",
            "assessment/receipt.json",
        ):
            (state / name).write_bytes(b"generated state; no native data")
        context = {
            "phase": "baseline",
            "clock": 0,
            "alert_polls": 0,
            "recovery_polls": 0,
            "commands": [],
            "interrupted": False,
        }
        baseline = {
            "complete": True,
            "counts": deepcopy(check.EXPECTED),
            "session_running": 0,
            "pending_uncommitted_events": 0,
            "unmeasured_committed_events": 0,
            "pending_feedback_transactions": 0,
        }
        if mode == "incomplete":
            baseline["complete"] = False
        if mode == "writer_active":
            baseline["session_running"] = 1
        if mode == "counts_differ":
            baseline["counts"]["events"] -= 1

        def clock():
            context["clock"] += 1
            return context["clock"]

        def run(command, **kwargs):
            assert kwargs["check"] is True and kwargs["timeout"] == 40
            context["commands"].append(command)
            if "stop" in command:
                context["phase"] = "stopped"
                if mode == "stop_error":
                    raise subprocess.CalledProcessError(1, command)
            elif "start" in command:
                context["phase"] = "recovering"
                if mode == "start_error":
                    raise subprocess.CalledProcessError(1, command)
                if mode == "state_changed":
                    (state / "checkpoint.sqlite").write_bytes(b"unexpected mutation")
            else:
                pytest.fail("Unexpected Docker command")
            return subprocess.CompletedProcess(command, 0)

        def get(url):
            if url.endswith("/summary"):
                if context["phase"] == "stopped":
                    raise OSError("exporter unavailable")
                return deepcopy(baseline)
            if "/api/v1/rules?" in url:
                rules = [] if mode == "missing_rule" else [{"name": check.ALERT, "health": "ok"}]
                return {"status": "success", "data": {"groups": [{"rules": rules}]}}
            if "/api/v1/query?" in url:
                up = 0 if mode == "scrape_unhealthy" else 1
                if context["phase"] == "recovering":
                    context["recovery_polls"] += 1
                    up = int(context["recovery_polls"] >= 2 and mode != "recovery_timeout")
                values = {
                    "up": up,
                    "fraud_monitor_collector_up": up,
                    "fraud_replay_events_total": 556824,
                }
                rows = [
                    {"metric": {"__name__": name}, "value": [0, str(value)]}
                    for name, value in values.items()
                ]
                if mode == "duplicate_target":
                    rows.append(deepcopy(rows[0]))
                return {"status": "success", "data": {"resultType": "vector", "result": rows}}
            if url.endswith("/api/v1/alerts"):
                active = []
                if mode == "already_alerting" and context["phase"] == "baseline":
                    active = [
                        {
                            "labels": {"alertname": check.ALERT, "job": "fraud-replay"},
                            "state": "firing",
                        }
                    ]
                if context["phase"] == "stopped":
                    context["alert_polls"] += 1
                    if mode == "interrupt" and not context["interrupted"]:
                        context["interrupted"] = True
                        raise KeyboardInterrupt()
                    if mode == "transient_http" and context["alert_polls"] == 1:
                        raise OSError("temporary Prometheus error")
                    if mode != "never_fires":
                        active = [
                            {
                                "labels": {"alertname": check.ALERT, "job": "fraud-replay"},
                                "state": "firing" if context["alert_polls"] >= 2 else "pending",
                            }
                        ]
                return {"status": "success", "data": {"alerts": active}}
            pytest.fail("Unexpected HTTP endpoint: " + url)

        monkeypatch.setattr(check, "get", get)
        monkeypatch.setattr(check.subprocess, "run", run)
        monkeypatch.setattr(check.time, "monotonic", clock)
        monkeypatch.setattr(check.time, "sleep", lambda _: None)
        return tmp_path, context

    return setup


def saved_report(root):
    reports = list(
        (root / "data/replay/measurements/paced-native-v1").glob("exporter-recovery-*/result.json")
    )
    assert len(reports) == 1
    return json.loads(reports[0].read_text())


@pytest.mark.parametrize("mode", ["success", "transient_http"])
def test_alert_recovery_and_hashes_are_reported_without_replaying(simulated_stack, mode):
    root, context = simulated_stack(mode)
    report = check.run_check(root)
    assert report["status"] == "success"
    assert report["state_files_unchanged"] is True
    assert report["hashes_before"] == report["hashes_after"]
    assert report["stop_request_to_firing_observed_seconds"] > 0
    assert report["start_request_to_recovery_observed_seconds"] > 0
    assert report["summary_before"] == report["summary_after"]
    assert report["production_sla_validated"] is False
    assert saved_report(root) == report
    assert len(context["commands"]) == 2
    assert all(command[-1] == "exporter" for command in context["commands"])
    assert "stop" in context["commands"][0] and "start" in context["commands"][1]


@pytest.mark.parametrize(
    "mode",
    [
        "missing_rule",
        "scrape_unhealthy",
        "already_alerting",
        "incomplete",
        "writer_active",
        "counts_differ",
        "duplicate_target",
    ],
)
def test_invalid_baseline_never_stops_a_service(simulated_stack, mode):
    root, context = simulated_stack(mode)
    with pytest.raises(RuntimeError):
        check.run_check(root)
    assert context["commands"] == []
    assert saved_report(root)["status"] == "failed"


@pytest.mark.parametrize(
    "mode,error",
    [
        ("never_fires", RuntimeError),
        ("stop_error", subprocess.CalledProcessError),
        ("interrupt", KeyboardInterrupt),
    ],
)
def test_failed_outage_phase_still_restores_and_checks_service(simulated_stack, mode, error):
    root, context = simulated_stack(mode)
    with pytest.raises(error):
        check.run_check(root)
    assert len(context["commands"]) == 2 and "start" in context["commands"][-1]
    report = saved_report(root)
    assert report["status"] == "failed" and report["state_files_unchanged"] is True
    assert "recovery_verified" in [event["event"] for event in report["events"]]


@pytest.mark.parametrize(
    "mode,error",
    [
        ("start_error", subprocess.CalledProcessError),
        ("recovery_timeout", RuntimeError),
        ("state_changed", RuntimeError),
    ],
)
def test_failed_restart_recovery_or_mutation_is_not_a_success(simulated_stack, mode, error):
    root, context = simulated_stack(mode)
    with pytest.raises(error):
        check.run_check(root)
    assert "start" in context["commands"][-1]
    assert saved_report(root)["status"] == "failed"
    if mode == "state_changed":
        assert saved_report(root)["state_files_unchanged"] is False


def test_missing_project_is_rejected_before_docker(tmp_path, monkeypatch):
    monkeypatch.setattr(check.subprocess, "run", lambda *a, **kw: pytest.fail("Docker called"))
    with pytest.raises(SystemExit, match="raiz do projeto"):
        check.run_check(tmp_path)


def test_cli_prints_result_and_report_location(simulated_stack, capsys):
    root, _ = simulated_stack()
    check.main(["--project-root", str(root)])
    output = capsys.readouterr().out
    assert '"status": "success"' in output and '"report_path":' in output
