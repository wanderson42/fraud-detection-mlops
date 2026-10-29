"""Render both chart profiles and exercise ownership/inventory boundaries offline."""
# ruff: noqa: F811

import json
import shutil
import subprocess
from types import SimpleNamespace

import pytest
from scripts.platform import kind_observability as k
from scripts.platform import session_observability as bootstrap
from scripts.platform import source_compatibility as compatibility
from scripts.session_monitoring import controller as monitoring
from tests.infra.test_kind_observability_readiness import inventory  # noqa: F401
import yaml


def rendered(tmp_path, legacy=True, enabled=True):
    if not shutil.which("helm"):
        pytest.skip("Helm required for real rendering")
    value = {
        "ownerId": "a" * 32,
        "stateReady": True,
        "legacyReplay": legacy,
        "sessionTelemetry": {"enabled": enabled},
    }
    if enabled:
        value["sessionTelemetry"].update(
            image="fraud-session-monitor:" + "b" * 32,
            binding={
                "session_id": "fixture",
                "session_sha256": "c" * 64,
                "model_sha256": "d" * 64,
                "serving_release_id": "e" * 32,
                "expected_events": 20,
                "expected_decisions": 2,
            },
        )
    path = tmp_path / "values.json"
    path.write_text(json.dumps(value))
    done = subprocess.run(
        ["helm", "template", k.RELEASE, str(k.CHART), "--values", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    return done


def test_session_only_has_one_receiver_no_replay_mount_or_fake_dashboard(tmp_path):
    done = rendered(tmp_path, legacy=False)
    assert done.returncode == 0, done.stderr
    items = {(v["kind"], v["metadata"]["name"]): v for v in yaml.safe_load_all(done.stdout) if v}
    assert ("PersistentVolumeClaim", k.RELEASE + "-state") not in items
    assert {name for kind, name in items if kind == "PersistentVolumeClaim"} == {
        k.RELEASE + "-prometheus",
        k.RELEASE + "-grafana",
    }
    pod = items["Deployment", k.RELEASE + "-exporter"]["spec"]["template"]
    bootstrap.validate_receiver(pod)
    assert {c["name"] for c in pod["spec"]["containers"]} == {"session-exporter"}
    config = items["ConfigMap", k.RELEASE + "-config"]["data"]
    prom = yaml.safe_load(config["prometheus.yml"])
    assert [v["job_name"] for v in prom["scrape_configs"]] == ["fraud-product-session"]
    assert prom["rule_files"] == ["/etc/prometheus/session-alerts.yml"]
    assert set(items["ConfigMap", k.RELEASE + "-dashboards"]["data"]) == {"product-session.json"}
    grafana = items["Deployment", k.RELEASE + "-grafana"]["spec"]["template"]["spec"][
        "containers"
    ][0]
    environment = {v["name"]: v.get("value") for v in grafana["env"]}
    assert environment["GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH"].endswith(
        "product-session.json"
    )
    assert environment["GF_PLUGINS_PREINSTALL_AUTO_UPDATE"] == "false"
    assert environment["GF_AUTH_ANONYMOUS_ENABLED"] == "false"


def test_default_legacy_profile_still_has_its_state_and_original_scrape(tmp_path):
    done = rendered(tmp_path, legacy=True, enabled=False)
    assert done.returncode == 0, done.stderr
    items = {(v["kind"], v["metadata"]["name"]): v for v in yaml.safe_load_all(done.stdout) if v}
    assert ("PersistentVolumeClaim", k.RELEASE + "-state") in items
    assert {
        v["name"]
        for v in items["Deployment", k.RELEASE + "-exporter"]["spec"]["template"]["spec"][
            "containers"
        ]
    } == {"exporter"}
    config = yaml.safe_load(items["ConfigMap", k.RELEASE + "-config"]["data"]["prometheus.yml"])
    assert [v["job_name"] for v in config["scrape_configs"]] == ["fraud-replay"]


def test_session_only_cannot_be_enabled_without_session_receiver(tmp_path):
    done = rendered(tmp_path, legacy=False, enabled=False)
    assert done.returncode != 0


@pytest.mark.parametrize(
    "change", ["raw_volume", "writable_mount", "extra_container", "service_token"]
)
def test_receiver_privilege_or_raw_state_mount_rejected(change):
    pod = {
        "spec": {
            "containers": [
                {
                    "name": "session-exporter",
                    "volumeMounts": [
                        {"name": "session-binding", "mountPath": "/binding", "readOnly": True}
                    ],
                }
            ],
            "automountServiceAccountToken": False,
            "enableServiceLinks": False,
            "volumes": [{"name": "session-binding", "configMap": {"name": "binding"}}],
        }
    }
    if change == "raw_volume":
        pod["spec"]["volumes"].append({"name": "raw", "hostPath": {"path": "/state"}})
    elif change == "writable_mount":
        pod["spec"]["containers"][0]["volumeMounts"][0]["readOnly"] = False
    elif change == "extra_container":
        pod["spec"]["containers"].append({"name": "other"})
    else:
        pod["spec"]["automountServiceAccountToken"] = True
    with pytest.raises(ValueError):
        bootstrap.validate_receiver(pod)


def test_reviewed_source_transition_does_not_accept_runtime_change(monkeypatch):
    def committed(args, **kwargs):
        path = bootstrap.f.ROOT / args[2].removeprefix("HEAD:")
        return SimpleNamespace(returncode=0, stdout=path.read_bytes())

    monkeypatch.setattr(compatibility.subprocess, "run", committed)
    current = monitoring.pins()
    transition = json.loads((bootstrap.f.ROOT / bootstrap.TRANSITION).read_bytes())["changes"]
    saved = {**current, **{name: value["before"] for name, value in transition.items()}}
    compatibility.accept(saved, current, bootstrap.f.ROOT)
    saved["scripts/session_monitoring/server.py"] = "0" * 64
    with pytest.raises(ValueError, match="Scientific code|Unreviewed operational"):
        compatibility.accept(saved, current, bootstrap.f.ROOT)


def test_profile_rejects_another_node_and_legacy_snapshot(tmp_path, monkeypatch):
    root = tmp_path / "observability"
    root.mkdir(mode=0o700)
    monkeypatch.setattr(k, "work", lambda: root)
    monkeypatch.setattr(f := bootstrap.f, "managed_destination", lambda path, area: path)
    monkeypatch.setattr(k, "credentials", lambda: {"owner_id": "owner"})
    monkeypatch.setattr(f, "owner", lambda: {"node_uid": "node"})
    monkeypatch.setattr(bootstrap, "source_pins", lambda: {"reviewed": "hash"})
    value = {
        "schema": "fraud_session_only_observability_profile_v1",
        "owner_id": "owner",
        "node_uid": "foreign",
        "source_pins": {"reviewed": "hash"},
    }
    path = root / "session-only.json"
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    with pytest.raises(ValueError, match="ownership"):
        bootstrap.profile()
    value["node_uid"] = "node"
    path.write_text(json.dumps(value))
    assert bootstrap.profile() == value
    (root / "snapshot.json").write_text("{}")
    with pytest.raises(ValueError, match="legacy replay"):
        bootstrap.profile()


def test_inventory_accepts_receiver_identity_without_legacy_exporter_or_pvc(
    inventory, monkeypatch
):
    pods, objects = inventory
    image = {"image": "locked:session", "loaded_identities": ["sha256:" + "c" * 64]}
    monkeypatch.setattr(k, "session_only", lambda: True)
    monkeypatch.setattr(k, "session_telemetry", lambda: {"image": image["image"]})
    monkeypatch.setattr(monitoring, "image_record", lambda: image)
    pod = pods["exporter"]
    pod["spec"].update(
        containers=[
            {
                "name": "session-exporter",
                "image": image["image"],
                "volumeMounts": [
                    {"name": "session-binding", "mountPath": "/binding", "readOnly": True}
                ],
            }
        ],
        automountServiceAccountToken=False,
        enableServiceLinks=False,
        volumes=[{"name": "session-binding", "configMap": {"name": "binding"}}],
    )
    pod["status"]["containerStatuses"] = [
        {
            "name": "session-exporter",
            "ready": True,
            "state": {"running": {}},
            "imageID": "sha256:" + "c" * 64,
        }
    ]
    # Running state is an actual populated Kubernetes state, not an empty dict.
    pod["status"]["containerStatuses"][0]["state"]["running"]["startedAt"] = "2026-10-28T00:00:00Z"
    objects.pop(("pvc", k.RELEASE + "-state"))
    result = k.inventory()
    assert set(result["pvcs"]) == {"prometheus", "grafana"}
    assert result["pods"]["exporter"]["image_id"] == "sha256:" + "c" * 64


@pytest.mark.parametrize("existing", ["snapshot", "release", "unowned_resources"])
def test_bootstrap_refuses_adopting_a_legacy_or_unowned_installation(
    tmp_path, monkeypatch, existing
):
    root = tmp_path / "observability"
    root.mkdir()
    monkeypatch.setattr(k, "work", lambda: root)
    monkeypatch.setattr(bootstrap, "source_pins", dict)
    monkeypatch.setattr(bootstrap, "committed_file", lambda _: None)
    monkeypatch.setattr(monitoring, "committed", lambda: None)
    monkeypatch.setattr(
        monitoring.session, "load", lambda _: (tmp_path, {"flow_id": "session-fixture"})
    )
    monkeypatch.setattr(bootstrap.session_acceptance, "snapshot", lambda *a: None)
    monkeypatch.setattr(
        monitoring.collector, "identity", lambda _: ({"session_sha256": "a" * 64}, {})
    )
    monkeypatch.setattr(k, "initialize", lambda: None)
    monkeypatch.setattr(k, "image_record", dict)
    monkeypatch.setattr(
        k, "releases", lambda: [{"name": "existing"}] if existing == "release" else []
    )
    monkeypatch.setattr(
        k.f,
        "kube",
        lambda *a: SimpleNamespace(
            stdout=json.dumps(
                {"items": [{"kind": "Deployment"}] if existing == "unowned_resources" else []}
            )
        ),
    )
    if existing == "snapshot":
        (root / "snapshot.json").write_text("{}")
    with pytest.raises(ValueError):
        bootstrap.prepare("fixture")
    assert not (root / "session-only.json").exists()
