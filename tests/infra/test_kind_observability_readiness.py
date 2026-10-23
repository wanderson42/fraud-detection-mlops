"""Exercise the real inventory gate after recovered restarts and during failures."""

import json
from types import SimpleNamespace

import pytest
from scripts.platform import kind_observability as k


@pytest.fixture
def inventory(monkeypatch):
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
    monkeypatch.setattr(k, "image_record", lambda: {"images": images})
    monkeypatch.setattr(k, "owned", lambda value: value)
    monkeypatch.setattr(k, "validate_exporter_mount", lambda pod: None)
    monkeypatch.setattr(k, "get", lambda kind, name: objects[kind, name])

    def query(*args, **kwargs):
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
