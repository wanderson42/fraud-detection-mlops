"""Exercise the real inventory gate after recovered restarts and during failures."""

import json
from types import SimpleNamespace

import pytest
from scripts.platform import kind_observability as k


@pytest.fixture(params=[False, True], ids=["without_session", "with_session"])
def inventory(monkeypatch, request):
    objects, pods = {}, {}
    images = {}
    for role in k.ROLES:
        name = k.RELEASE + "-" + role
        images[role] = {"image": "locked:" + role, "loaded_identities": ["sha256:" + "a" * 64]}
        objects["deployment", name] = {"metadata": {"uid": "dep-" + role}}
        objects["service", name] = {"metadata": {"uid": "svc-" + role}}
        objects["replicaset", name + "-rs"] = {
            "metadata": {
                "uid": "rs-" + role,
                "ownerReferences": [{"controller": True, "uid": "dep-" + role}],
            }
        }
        pods[role] = {
            "metadata": {
                "name": name + "-pod",
                "uid": "pod-" + role,
                "ownerReferences": [
                    {
                        "controller": True,
                        "kind": "ReplicaSet",
                        "name": name + "-rs",
                        "uid": "rs-" + role,
                    }
                ],
            },
            "spec": {"containers": [{"image": "locked:" + role}]},
            "status": {
                "phase": "Running",
                "conditions": [{"type": "Ready", "status": "True"}],
                "containerStatuses": [
                    {
                        "ready": True,
                        "restartCount": 7,
                        "state": {"running": {"startedAt": "2026-10-22T12:00:00Z"}},
                        "imageID": "sha256:" + "a" * 64,
                        "lastState": {"terminated": {"exitCode": 137}},
                    }
                ],
            },
        }
    for role in ("state", "prometheus", "grafana"):
        objects["pvc", k.RELEASE + "-" + role] = {
            "metadata": {"uid": "pvc-" + role},
            "status": {"phase": "Bound"},
            "spec": {"volumeName": "volume-" + role},
        }
    # Keep this offline gate independent of the operator's enabled.json and
    # image receipts; exercise both topologies using synthetic sidecar state.
    monkeypatch.setattr(k, "session_only", lambda: False)
    telemetry = {"image": "locked:session"} if request.param else None
    monkeypatch.setattr(k, "session_telemetry", lambda: telemetry)

    def unexpected_host_read():
        pytest.fail("Readiness fixture attempted to read live platform state")

    monkeypatch.setattr(k, "work", unexpected_host_read)
    monkeypatch.setattr(k.f, "owner", unexpected_host_read)
    if telemetry:
        from scripts.session_monitoring import controller

        monkeypatch.setattr(
            controller,
            "image_record",
            lambda: {"loaded_identities": ["sha256:" + "c" * 64]},
        )
        pods["exporter"]["spec"]["containers"].append(
            {"name": "session-exporter", "image": telemetry["image"]}
        )
        pods["exporter"]["status"]["containerStatuses"].append(
            {
                "name": "session-exporter",
                "ready": True,
                "restartCount": 3,
                "state": {"running": {"startedAt": "2026-10-27T12:00:00Z"}},
                "imageID": "sha256:" + "c" * 64,
            }
        )
    monkeypatch.setattr(k, "image_record", lambda: {"images": images})
    monkeypatch.setattr(k, "owned", lambda value: value)
    monkeypatch.setattr(k, "validate_exporter_mount", lambda pod: None)
    monkeypatch.setattr(k, "get", lambda kind, name: objects[kind, name])

    def query(*args, **kwargs):
        assert args[:3] == ("get", "pods", "-l"), args
        selector = args[args.index("-l") + 1]
        role = selector.rsplit("=", 1)[1]
        return SimpleNamespace(stdout=json.dumps({"items": [pods[role]]}))

    monkeypatch.setattr(k.f, "kube", query)
    return pods, objects


def test_recovered_running_pods_with_restart_history_pass_inventory(inventory):
    result = k.inventory()
    assert set(result["pods"]) == set(k.ROLES)
    assert result["pods"]["grafana"]["pod_uid"] == "pod-grafana"
    assert result["pvcs"]["grafana"]["uid"] == "pvc-grafana"


@pytest.mark.parametrize(
    "failure",
    ["not_ready", "waiting", "terminated", "phase", "pod_condition", "image", "replicaset", "pvc"],
)
def test_current_failures_and_identity_mismatches_remain_blocked(inventory, failure):
    pods, objects = inventory
    pod = pods["grafana"]
    status = pod["status"]["containerStatuses"][0]
    if failure == "not_ready":
        status["ready"] = False
    elif failure == "waiting":
        status["state"] = {"waiting": {"reason": "CrashLoopBackOff"}}
    elif failure == "terminated":
        status["state"] = {"terminated": {"exitCode": 1}}
    elif failure == "phase":
        pod["status"]["phase"] = "Pending"
    elif failure == "pod_condition":
        pod["status"]["conditions"][0]["status"] = "False"
    elif failure == "image":
        status["imageID"] = "sha256:" + "b" * 64
    elif failure == "replicaset":
        objects["replicaset", k.RELEASE + "-grafana-rs"]["metadata"]["uid"] = "foreign"
    elif failure == "pvc":
        objects["pvc", k.RELEASE + "-grafana"]["status"]["phase"] = "Pending"
    with pytest.raises(ValueError) as error:
        k.inventory()
    if failure in {"not_ready", "waiting", "terminated", "phase", "pod_condition"}:
        assert "grafana" in str(error.value) and "restartCount" in str(error.value)


@pytest.mark.parametrize("inventory", [True], indirect=True, ids=["with_session"])
@pytest.mark.parametrize("failure", ["not_ready", "image", "missing"])
def test_session_sidecar_failures_remain_blocked(inventory, failure):
    pods, _ = inventory
    statuses = pods["exporter"]["status"]["containerStatuses"]
    assert len(statuses) == 2
    if failure == "not_ready":
        statuses[1]["ready"] = False
    elif failure == "image":
        statuses[1]["imageID"] = "sha256:" + "d" * 64
    else:
        statuses.pop()
    with pytest.raises(ValueError, match="Session telemetry image/readiness differs"):
        k.inventory()
