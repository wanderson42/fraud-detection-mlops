"""Retained-node diagnostics without Docker or historical data."""

import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest
from scripts.platform import kind_bootstrap_diagnostics as diagnostic


@pytest.fixture
def foundation(tmp_path):
    calls, reports = [], []
    node = [
        {
            "Id": "node-id",
            "Config": {
                "Env": ["HIDDEN=secret-value"],
                "Labels": {
                    "io.x-k8s.kind.cluster": "fraud-platform",
                    "io.x-k8s.kind.role": "control-plane",
                },
            },
            "State": {"Status": "running", "Running": True},
            "HostConfig": {"Memory": 0},
        }
    ]
    config = {
        "node": node,
        "kind_returncode": 1,
        "cri": {
            "containers": [
                {
                    "id": "a" * 64,
                    "metadata": {"name": "kube-apiserver"},
                    "state": "CONTAINER_EXITED",
                }
            ]
        },
    }

    def run(args, **kwargs):
        calls.append(list(args))
        code, output = 0, ""
        if list(args[:3]) == ["kind", "create", "cluster"]:
            code, output = config["kind_returncode"], "connection refused secret-value"
        elif list(args[:2]) == ["docker", "inspect"]:
            code, output = (
                (0, json.dumps(config["node"])) if config["node"] else (1, "No such container")
            )
        elif "ps" in args:
            output = json.dumps(config["cri"])
        elif "logs" in args:
            output = "startup failure secret-value"
        elif "journalctl" in args:
            output = "failed to start kubelet"
        return SimpleNamespace(returncode=code, stdout=output, stderr=output if code else "")

    f = SimpleNamespace(
        CLUSTER="fraud-platform",
        ROOT=tmp_path,
        credentials=lambda: {"key": "secret-value"},
        run=run,
        workspace=lambda: tmp_path,
    )

    def require(condition, message):
        if not condition:
            raise ValueError(message)

    f.require = require
    f.private_json = lambda path, value: reports.append((path, value))

    def create():
        completed = f.run(["kind", "version"])
        assert completed.returncode == 0
        completed = f.run(
            ["kind", "create", "cluster", "--name", "fraud-platform", "--wait", "180s"]
        )
        require(completed.returncode == 0, "Creation failed")
        return {"status": "cluster_created"}

    f.create = create
    return f, calls, reports, config


def test_failure_retains_node_captures_components_and_restores_original_runner(foundation):
    f, calls, reports, _ = foundation
    original = f.run
    result, path = diagnostic.diagnose(f, "create")
    assert f.run is original
    assert result["status"] == "bootstrap_failed"
    assert "--retain" in next(c for c in calls if c[:3] == ["kind", "create", "cluster"])
    assert ["kind", "version"] in calls
    assert result["node"]["retained_node_present"]
    assert result["node"]["components"][0]["name"] == "kube-apiserver"
    assert "secret-value" not in json.dumps(result)
    assert "Config" not in result["node"]
    assert len(reports) == 1 and reports[0][0] == path
    assert not result["ownership_checks_bypassed"]
    assert not any("delete" in c or "sysctl" in c for c in calls)


def test_inspect_does_not_call_create_or_kind(foundation):
    f, calls, reports, _ = foundation
    result, _ = diagnostic.diagnose(f, "inspect")
    assert result["status"] == "diagnostic_only"
    assert all(c[0] == "docker" for c in calls)
    assert len(reports) == 1


def test_foreign_owner_labels_block_node_exec(foundation):
    f, calls, _, config = foundation
    config["node"][0]["Config"]["Labels"]["io.x-k8s.kind.cluster"] = "energy-mlops"
    result, _ = diagnostic.diagnose(f, "inspect")
    assert "Owner labels differ" in result["node"]["error"]
    assert not any("exec" in c for c in calls)


def test_missing_node_is_reported_without_exec(foundation):
    f, calls, _, config = foundation
    config["node"] = None
    result, _ = diagnostic.diagnose(f, "inspect")
    assert not result["node"]["retained_node_present"]
    assert not any("exec" in c for c in calls)


def test_success_uses_original_creation_path_without_claiming_service_acceptance(foundation):
    f, _, reports, config = foundation
    config["kind_returncode"] = 0
    result, _ = diagnostic.diagnose(f, "create")
    assert result["creation"] == {"status": "cluster_created"}
    assert not result["native_foundation_validated"]
    assert result["kind_failure"] is None
    assert len(reports) == 1


def test_timeout_preserves_failed_receipt_and_runner(foundation):
    f, _, reports, _ = foundation
    original = f.run
    f.create = lambda: (_ for _ in ()).throw(subprocess.TimeoutExpired("kind", 300))
    result, _ = diagnostic.diagnose(f, "create")
    assert result["status"] == "bootstrap_failed"
    assert f.run is original
    assert len(reports) == 1


def test_wrong_dedicated_cluster_name_rejected_before_commands(foundation):
    f, calls, reports, _ = foundation
    f.CLUSTER = "energy-mlops"
    with pytest.raises(ValueError, match="Dedicated"):
        diagnostic.diagnose(f, "create")
    assert calls == [] and reports == []


@pytest.mark.parametrize("identity", ["../other", "x" * 64, "a;sh", "a" * 65])
def test_invalid_runtime_container_id_never_reaches_logs_command(foundation, identity):
    f, calls, _, config = foundation
    config["cri"]["containers"][0]["id"] = identity
    result, _ = diagnostic.diagnose(f, "inspect")
    assert result["node"]["components"] == []
    assert not any("logs" in c for c in calls)


def test_redaction_and_output_bound():
    value = "secret-value -----BEGIN PRIVATE KEY-----\nprivate-bytes\n-----END PRIVATE KEY----- https://name:password@proxy/"
    result = diagnostic.redact(value, ["secret-value"])
    assert all(s not in result for s in ["secret-value", "private-bytes", "password"])
    assert len(diagnostic.redact("x" * 50000, [])) == diagnostic.LIMIT


def test_capture_error_does_not_emit_exception_arguments():
    def fail(*args, **kwargs):
        raise ValueError("secret in exception")

    assert diagnostic.capture(fail, ["docker"], []) == {"error": "ValueError"}


def test_summary_contains_selected_logs_not_full_node_config(foundation):
    f, _, _, _ = foundation
    result, path = diagnostic.diagnose(f, "inspect")
    compact = diagnostic.summary(result, path)
    assert compact["components"][0]["name"] == "kube-apiserver"
    assert "Config" not in json.dumps(compact)
    assert "secret-value" not in json.dumps(compact)


def test_unreadable_host_metadata_is_bounded(tmp_path, monkeypatch):
    def refuse(*args, **kwargs):
        raise OSError("unavailable")

    monkeypatch.setattr(Path, "read_text", refuse)
    value = diagnostic.host_summary(tmp_path)
    assert value["memory"] is None
    assert value["fs.inotify.max_user_instances"] is None
