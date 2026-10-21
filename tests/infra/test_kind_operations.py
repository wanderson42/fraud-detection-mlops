"""Failure boundaries and recovery ordering without touching a Docker daemon."""

from contextlib import nullcontext
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts.platform import kind_operations as k


@pytest.fixture
def rig(tmp_path, monkeypatch):
    directory = tmp_path / "operations"
    directory.mkdir(mode=0o700)
    monkeypatch.setattr(k, "work", lambda: directory)
    monkeypatch.setattr(k.f, "managed_destination", lambda path, _: path)
    system = {
        "boot_id": "boot-a",
        "machine_id_sha256": "host-a",
        "runtime_inotify_sufficient": True,
    }
    owner = {
        "container_id": "owned",
        "node_uid": "node-a",
        "docker_engine_id": "engine-a",
        "running": True,
        "paused": False,
        "restarting": False,
        "owner_receipt_sha256": "receipt-a",
    }
    inventory = {}
    for kind, name in k.STOP_ORDER:
        key = ("Deployment" if kind == "deployment" else "StatefulSet") + "/" + name
        inventory[key] = {
            "uid": name,
            "replicas": 1,
            "spec_sha256": "spec",
            "configuration_sha256": "config",
        }
    inventory["PersistentVolumeClaim/state"] = {
        "uid": "pvc-a",
        "replicas": None,
        "spec_sha256": "volume-a",
        "configuration_sha256": "config",
    }
    monkeypatch.setattr(k, "host", lambda: copy.deepcopy(system))
    monkeypatch.setattr(k, "pins", lambda: {"controller": "code-a"})
    monkeypatch.setattr(k, "docker_owner", lambda: copy.deepcopy(owner))
    monkeypatch.setattr(k.f, "owner", lambda: None)
    monkeypatch.setattr(k, "resources", lambda: copy.deepcopy(inventory))
    monkeypatch.setattr(k, "no_jobs_or_bare_writers", lambda: None)
    monkeypatch.setattr(k, "wait_api", lambda: None)
    monkeypatch.setattr(k, "probes", lambda record: {"readback": "unchanged"})
    monkeypatch.setattr(k, "tracking_report", lambda path: {"record": {"run_id": "run-a"}})
    calls = []

    def scale(baseline, kind, name, count):
        calls.append((kind, name, count))
        key = ("Deployment" if kind == "deployment" else "StatefulSet") + "/" + name
        inventory[key]["replicas"] = count

    def docker(args, **kwargs):
        calls.append(tuple(args))
        assert args[-1] == "owned"
        owner["running"] = args[1] == "start"
        return SimpleNamespace(stdout="", returncode=0)

    monkeypatch.setattr(k, "change_replica", scale)
    monkeypatch.setattr(k.monitor, "command", docker)
    path = k.prepare(Path("tracking.json"))
    return SimpleNamespace(
        path=path, directory=directory, host=system, owner=owner, inventory=inventory, calls=calls
    )


def test_controlled_cycle_orders_dependencies_and_preserves_identity(rig):
    k.stop(rig.path, True)
    assert rig.calls[:8] == [(a, b, 0) for a, b in k.STOP_ORDER]
    assert rig.calls[8] == ("docker", "stop", "--timeout", "120", "owned")
    k.start(rig.path, True)
    assert rig.calls[9] == ("docker", "start", "owned")
    assert rig.calls[10:] == [(a, b, 1) for a, b in k.START_ORDER]
    result = k.verify(rig.path)
    assert result["controlled_stop_start_validated"]
    assert not result["host_reboot_validated"]
    assert not result["backup_restore_validated"]
    assert rig.inventory["PersistentVolumeClaim/state"]["uid"] == "pvc-a"


@pytest.mark.parametrize(
    "key", ["container_id", "node_uid", "docker_engine_id", "owner_receipt_sha256"]
)
def test_foreign_identity_rejected_before_mutation(rig, key):
    rig.owner[key] = "foreign"
    with pytest.raises(ValueError, match="ownership differs"):
        k.stop(rig.path, True)
    assert not rig.calls


def test_foreign_host_and_changed_controller_rejected(rig, monkeypatch):
    rig.host["machine_id_sha256"] = "foreign"
    with pytest.raises(ValueError, match="another host"):
        k.start(rig.path, True)
    rig.host["machine_id_sha256"] = "host-a"
    monkeypatch.setattr(k, "pins", lambda: {"controller": "changed"})
    with pytest.raises(ValueError, match="code identity"):
        k.stop(rig.path, True)
    assert not rig.calls


@pytest.mark.parametrize("action", [k.stop, k.start])
def test_writer_declaration_required(rig, action):
    with pytest.raises(ValueError, match="Explicit"):
        action(rig.path, False)
    assert not rig.calls


@pytest.mark.parametrize("field", ["uid", "spec_sha256", "configuration_sha256"])
def test_resource_drift_blocks_stop_and_records_failure(rig, field):
    rig.inventory["PersistentVolumeClaim/state"][field] = "changed"
    with pytest.raises(ValueError, match="identity differs"):
        k.stop(rig.path, True)
    result = json.loads(next(rig.directory.glob("stop-*.json")).read_text())
    assert result["status"] == "failed"
    assert not rig.calls


def test_concurrent_resume_prevents_node_stop(rig, monkeypatch):
    original = k.change_replica

    def race(*args):
        original(*args)
        if args[2] == k.STOP_ORDER[-1][1]:
            rig.inventory["Deployment/fraud-tracking"]["replicas"] = 1

    monkeypatch.setattr(k, "change_replica", race)
    with pytest.raises(ValueError, match="resumed before node stop"):
        k.stop(rig.path, True)
    assert not any(call[0] == "docker" for call in rig.calls)


def test_partial_shutdown_can_resume_saved_plan(rig):
    rig.inventory["Deployment/fraud-tracking"]["replicas"] = 0
    result = k.start(rig.path, True)
    assert result["original_replica_plan_restored"]
    assert all(x["replicas"] in (None, 1) for x in rig.inventory.values())


def test_paused_node_is_not_started_or_adopted(rig):
    rig.owner["paused"] = True
    with pytest.raises(ValueError, match="paused"):
        k.start(rig.path, True)
    assert not rig.calls


def test_reboot_requires_changed_boot_and_preboot_stop(rig):
    k.stop(rig.path, True)
    k.start(rig.path, True)
    with pytest.raises(ValueError, match="boot_id did not change"):
        k.verify(rig.path, True)
    rig.host["boot_id"] = "boot-b"
    k.start(rig.path, True)
    result = k.verify(rig.path, True)
    assert result["host_reboot_validated"]
    assert result["inotify_persistence_after_reboot_validated"]


def test_stop_after_reboot_cannot_prove_planned_reboot(rig):
    rig.host["boot_id"] = "boot-b"
    k.stop(rig.path, True)
    k.start(rig.path, True)
    with pytest.raises(ValueError, match="not recorded before reboot"):
        k.verify(rig.path, True)


def test_verification_needs_stop_and_matching_readback(rig, monkeypatch):
    with pytest.raises(ValueError, match="No completed"):
        k.verify(rig.path)
    k.stop(rig.path, True)
    k.start(rig.path, True)
    monkeypatch.setattr(k, "probes", lambda _: {"readback": "changed"})
    with pytest.raises(ValueError, match="readback/model/monitoring differs"):
        k.verify(rig.path)
    assert json.loads(max(rig.directory.glob("verify-*.json")).read_text())["status"] == "failed"


def test_rehearsal_failure_restores_services_but_stays_failed(rig, monkeypatch):
    monkeypatch.setattr(k, "prepare", lambda _: rig.path)

    def fail(*args):
        raise ValueError("injected read failure")

    monkeypatch.setattr(k, "verify", fail)
    with pytest.raises(ValueError, match="injected"):
        k.rehearse(Path("tracking.json"), True)
    receipt = json.loads(next(rig.directory.glob("rehearsal-*.json")).read_text())
    assert receipt["status"] == "failed"
    assert receipt["recovery_attempt"]["status"] == "started"
    assert rig.owner["running"]


@pytest.mark.parametrize("active", ["job", "pod"])
def test_unreviewed_writers_rejected(monkeypatch, active):
    jobs = [{"status": {"active": 1}}] if active == "job" else []
    pods = [{"metadata": {}, "status": {"phase": "Running"}}] if active == "pod" else []
    monkeypatch.setattr(
        k.f,
        "kube",
        lambda *a, **kw: SimpleNamespace(
            stdout=json.dumps({"items": jobs if a[1] == "jobs" else pods})
        ),
    )
    with pytest.raises(ValueError, match="Active Jobs|unmanaged"):
        k.no_jobs_or_bare_writers()


def test_resource_inventory_requires_exact_workloads_and_hashes_secrets(monkeypatch):
    items = [
        {
            "kind": "Deployment" if kind == "deployment" else "StatefulSet",
            "metadata": {"name": name, "uid": name},
            "spec": {"replicas": 1},
        }
        for kind, name in k.STOP_ORDER
    ]
    items.append(
        {
            "kind": "Secret",
            "metadata": {"name": "credential", "uid": "secret-a"},
            "data": {"password": "private-base64-value"},
        }
    )
    monkeypatch.setattr(
        k.f, "kube", lambda *a, **kw: SimpleNamespace(stdout=json.dumps({"items": items}))
    )
    result = k.resources()
    assert "private-base64-value" not in json.dumps(result)
    assert result["Secret/credential"]["configuration_sha256"] == k.digest(
        {"data": {"password": "private-base64-value"}}
    )
    items.append({"kind": "Deployment", "metadata": {"name": "foreign"}, "spec": {}})
    with pytest.raises(ValueError, match="Unexpected"):
        k.resources()


def test_scale_uses_optimistic_preconditions_and_waits_for_termination(monkeypatch):
    spec = {"replicas": 1, "selector": {"matchLabels": {"component": "blue"}}}
    current = {"metadata": {"uid": "u", "resourceVersion": "v42"}, "spec": spec}
    base = {
        "resources": {
            "Deployment/blue": {
                "uid": "u",
                "spec_sha256": k.digest({"selector": spec["selector"]}),
            }
        }
    }
    monkeypatch.setattr(k, "workload", lambda *a: current)
    calls = []

    def kube(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(stdout=json.dumps({"items": []}))

    monkeypatch.setattr(k.f, "kube", kube)
    k.change_replica(base, "deployment", "blue", 0)
    assert calls[0] == (
        "scale",
        "deployment/blue",
        "--current-replicas=1",
        "--resource-version=v42",
        "--replicas=0",
    )
    assert calls[1] == ("get", "pods", "-l", "component=blue", "-o", "json")
    current["metadata"]["uid"] = "foreign"
    with pytest.raises(ValueError, match="changed before scaling"):
        k.change_replica(base, "deployment", "blue", 0)
    assert len(calls) == 2


def test_read_probes_use_existing_history_s3_and_http_without_fit(monkeypatch):
    run = {
        "run": {
            "info": {"run_id": "run-a", "status": "FINISHED"},
            "data": {"tags": [{"key": "b"}, {"key": "a"}]},
        }
    }
    monkeypatch.setattr(k.f, "inventory", lambda: {"pvcs": {"pg": "same"}})
    monkeypatch.setattr(k.tracking, "inventory", lambda: {"deployment_uid": "same"})
    monkeypatch.setattr(k.tracking, "port_forward", lambda: nullcontext("uri"))
    monkeypatch.setattr(
        k.monitor,
        "http",
        lambda uri, path: (
            run
            if "/runs/get" in path
            else {"metrics": [{"step": 0, "value": 0.25}, {"step": 1, "value": 0.75}]}
        ),
    )
    calls = []
    monkeypatch.setattr(
        k.tracking,
        "storage_pod",
        lambda action, record: calls.append(action) or {"model_sha256": "same"},
    )
    plan = {
        "releases": {
            "blue": {"manifest": {"serving_release_id": "release", "model_sha256": "model"}}
        }
    }
    monkeypatch.setattr(k.delivery, "plan", lambda: plan)
    monkeypatch.setattr(
        k.serving, "observe", lambda _: {"pods": {"blue": {"name": "b"}, "gateway": {"name": "g"}}}
    )
    monkeypatch.setattr(k.serving, "current_values", dict)
    monkeypatch.setattr(k.serving, "values", lambda _: {})
    monkeypatch.setattr(k.delivery, "forward", lambda _: nullcontext("uri"))
    monkeypatch.setattr(
        k.serving, "wait_route", lambda *a: {"score": 0.5, "invalid_input_http_status": 422}
    )
    monkeypatch.setattr(k.monitor, "inventory", lambda: {"pvcs": {"state": "same"}})
    monkeypatch.setattr(
        k.monitor, "verify_http", lambda: {"dashboard_color_policy_verified": "semantic_colors_v1"}
    )
    first = k.probes({"run_id": "run-a"})
    run["run"]["data"]["tags"].reverse()
    assert k.probes({"run_id": "run-a"}) == first
    assert calls == ["verify", "verify"]
    assert not first["fit_executed"] and not first["tracking_model_loaded"]


def test_docker_owner_does_not_need_live_kubernetes(tmp_path, monkeypatch):
    saved = {"schema": "fraud_kind_owner_v1", "container_id": "owned", "node_uid": "uid"}
    (tmp_path / "owner.json").write_text(json.dumps(saved))
    monkeypatch.setattr(k.f, "workspace", lambda: tmp_path)
    monkeypatch.setattr(k.f, "managed_destination", lambda p, _: p)
    item = {
        "Id": "owned",
        "Name": "/" + k.NODE,
        "Config": {"Labels": {"io.x-k8s.kind.cluster": k.f.CLUSTER}},
        "State": {"Running": False},
    }
    monkeypatch.setattr(
        k.monitor,
        "command",
        lambda a, **kw: SimpleNamespace(
            stdout=json.dumps([item]) if a[1] == "inspect" else "engine\n"
        ),
    )
    result = k.docker_owner()
    assert not result["running"] and result["docker_engine_id"] == "engine"
    item["Config"]["Labels"]["io.x-k8s.kind.cluster"] = "foreign"
    with pytest.raises(ValueError, match="refuse adoption"):
        k.docker_owner()


def test_matching_start_receipt_required_after_stop(rig, monkeypatch):
    k.stop(rig.path, True)
    # An external actor restoring replicas cannot stand in for the recorded start gate.
    rig.owner["running"] = True
    for item in rig.inventory.values():
        if item["replicas"] is not None:
            item["replicas"] = 1
    with pytest.raises(ValueError, match="No completed start"):
        k.verify(rig.path)
