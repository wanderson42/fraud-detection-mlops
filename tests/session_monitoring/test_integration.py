"""Real Helm manifests and strict source transition; no native acceptance implied."""

import json
from pathlib import Path
import shutil
import subprocess

import pytest
from scripts.platform import kind_observability as k
from scripts.platform import source_compatibility as compatibility
from scripts.session_monitoring import controller
import yaml

ROOT = Path(__file__).resolve().parents[2]


def render(tmp_path, enabled):
    if not shutil.which("helm"):
        pytest.skip("Helm required for real rendering")
    values = {"ownerId": "a" * 32, "stateReady": True, "sessionTelemetry": {"enabled": enabled}}
    if enabled:
        values["sessionTelemetry"].update(
            image="fraud-session-monitor:" + "a" * 32,
            binding={
                "session_id": "arrival-2k-v1",
                "session_sha256": "a" * 64,
                "model_sha256": "b" * 64,
                "serving_release_id": "c" * 32,
                "expected_events": 272936,
                "expected_decisions": 2001,
            },
        )
    path = tmp_path / "values.json"
    path.write_text(json.dumps(values))
    done = subprocess.run(
        ["helm", "template", k.RELEASE, str(k.CHART), "--values", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    return {(v["kind"], v["metadata"]["name"]): v for v in yaml.safe_load_all(done.stdout) if v}


def test_optional_extension_reuses_owned_release_no_new_workload_or_raw_mount(tmp_path):
    old, new = render(tmp_path, False), render(tmp_path, True)
    assert set(new) - set(old) == {("ConfigMap", k.RELEASE + "-session-binding")}
    spec = new["Deployment", k.RELEASE + "-exporter"]["spec"]["template"]["spec"]
    assert len(spec["containers"]) == 2 and not spec["automountServiceAccountToken"]
    sidecar = spec["containers"][1]
    assert sidecar["volumeMounts"] == [
        {"name": "session-binding", "mountPath": "/binding", "readOnly": True}
    ]
    assert sidecar["securityContext"]["readOnlyRootFilesystem"]
    assert sidecar["env"][0]["valueFrom"]["secretKeyRef"]["name"] == controller.SECRET
    config = new["ConfigMap", k.RELEASE + "-config"]["data"]
    prom = yaml.safe_load(config["prometheus.yml"])
    assert [v["job_name"] for v in prom["scrape_configs"]] == [
        "fraud-replay",
        "fraud-product-session",
    ]
    assert len(prom["rule_files"]) == 2
    assert config["alerts.yml"] == old["ConfigMap", k.RELEASE + "-config"]["data"]["alerts.yml"]
    dashboards = new["ConfigMap", k.RELEASE + "-dashboards"]["data"]
    assert all(
        dashboards[name] == body
        for name, body in old["ConfigMap", k.RELEASE + "-dashboards"]["data"].items()
    )
    dashboard = json.loads(dashboards["product-session.json"])
    assert dashboard["uid"] == "fraud-product-session"
    assert all(
        "source_valid" in t["expr"]
        for p in dashboard["panels"]
        if p.get("title")
        in {"Decisões confirmadas", "HTTP p95 da sessão", "Alertas para investigação"}
        for t in p["targets"]
    )


def test_feature_retained_by_future_observability_up_without_changing_old_image_pins(monkeypatch):
    monkeypatch.setattr(k, "credentials", lambda: {"owner_id": "a" * 32})
    monkeypatch.setattr(
        k, "image_record", lambda: {"images": {role: {"image": role} for role in k.ROLES}}
    )
    feature = {"enabled": True, "image": "fraud-session-monitor:" + "a" * 32, "binding": {}}
    monkeypatch.setattr(k, "session_telemetry", lambda: feature)
    assert k.values(True)["sessionTelemetry"] == feature
    assert not any(
        "session_monitoring" in v or "/session-monitoring/" in v for v in k.runtime_pins()
    )


def test_exact_transition_preserves_receipts_but_rejects_science_change():
    transition = json.loads((ROOT / compatibility.MONITORING_TRANSITION).read_bytes())
    assert set(transition["changes"]) == compatibility.MONITORING_ALLOWED
    assert all(v["after"] == k.state.sha(ROOT / name) for name, v in transition["changes"].items())


def test_partial_helm_failure_resumes_without_requiring_old_pod_ready(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from scripts.session_monitoring import collector

    binding = {
        "session_id": "arrival-2k-v1",
        "session_sha256": "a" * 64,
        "model_sha256": "b" * 64,
        "serving_release_id": "c" * 32,
        "expected_events": 10,
        "expected_decisions": 2,
    }
    proposal = {
        "binding": binding,
        "source_pins": {},
        "owner_id": "a" * 32,
        "image": "fraud-session-monitor:" + "d" * 32,
    }
    (tmp_path / "prepared.json").write_text(json.dumps(proposal))
    (tmp_path / "image.json").write_text(json.dumps({"image": proposal["image"]}))
    state = {
        "broken": False,
        "installs": 0,
        "values": {"ownerId": "a" * 32},
        "protected": [("Pod", "champion", "original-uid")],
    }
    monkeypatch.setattr(controller, "work", lambda: tmp_path)
    monkeypatch.setattr(k.f, "managed_destination", lambda path, *_: path)
    monkeypatch.setattr(controller, "committed", lambda: None)
    monkeypatch.setattr(
        controller.session.flow, "active", lambda: {"slot": "blue", "model_sha256": "b" * 64}
    )
    monkeypatch.setattr(controller, "pins", dict)
    monkeypatch.setattr(controller, "image_record", lambda: {"image": proposal["image"]})
    monkeypatch.setattr(controller, "token", lambda: "t" * 64)
    monkeypatch.setattr(controller.session, "load", lambda _: (tmp_path, {}))
    monkeypatch.setattr(collector, "identity", lambda _: (binding, {}))
    monkeypatch.setattr(k, "credentials", lambda: {"owner_id": "a" * 32})
    monkeypatch.setattr(k, "values", lambda _: {"ownerId": "a" * 32})
    monkeypatch.setattr(
        k, "helm", lambda *a, **kw: SimpleNamespace(stdout=json.dumps(state["values"]))
    )
    monkeypatch.setattr(k, "protected_inventory", lambda: state["protected"])
    monkeypatch.setattr(k, "get", lambda *a: None)
    monkeypatch.setattr(k.f, "kube", lambda *a, **kw: None)
    monkeypatch.setattr(
        controller, "atomic", lambda path, value: path.write_text(json.dumps(value))
    )
    monkeypatch.setattr(k, "verify_http", lambda: {"scrape_target_verified": True})

    def inventory():
        assert not state["broken"], "Retried before resuming Helm installation"
        return {"pvcs": {"state": {"uid": "same-pvc"}}}

    def install(_):
        state["installs"] += 1
        state["values"] = {
            "ownerId": "a" * 32,
            "sessionTelemetry": {"enabled": True, "image": proposal["image"], "binding": binding},
        }
        if state["installs"] == 1:
            state["broken"] = True
            raise ValueError("retained partial rollout")
        state["broken"] = False

    monkeypatch.setattr(k, "inventory", inventory)
    monkeypatch.setattr(k, "install", install)
    with pytest.raises(ValueError, match="partial rollout"):
        controller.up()
    assert (tmp_path / "enabled.json").exists() and (
        tmp_path / ("activation-" + "a" * 64 + ".json")
    ).exists()
    result = controller.up()
    assert result["monitoring_pvcs_preserved"] and state["installs"] == 2
    state["protected"] = [("Pod", "champion", "foreign-uid")]
    with pytest.raises(ValueError, match="Protected identities"):
        controller.up()


def test_explicit_session_rebinding_archives_previous_identity(tmp_path, monkeypatch):
    from scripts.session_monitoring import collector

    old_binding = {
        "session_id": "old",
        "session_sha256": "a" * 64,
        "model_sha256": "b" * 64,
        "serving_release_id": "c" * 32,
        "expected_events": 10,
        "expected_decisions": 2,
    }
    old = {
        "binding": old_binding,
        "source_pins": {},
        "owner_id": "d" * 32,
        "image": "fraud-session-monitor:" + "e" * 32,
    }
    new_binding = {**old_binding, "session_id": "next", "session_sha256": "f" * 64}
    (tmp_path / "prepared.json").write_text(json.dumps(old))
    (tmp_path / "enabled.json").write_text(json.dumps(old))
    (tmp_path / "image.json").write_text(json.dumps({"image": old["image"]}))
    monkeypatch.setattr(controller, "work", lambda: tmp_path)
    monkeypatch.setattr(k.f, "managed_destination", lambda path, *_: path)
    monkeypatch.setattr(controller, "committed", lambda: None)
    monkeypatch.setattr(
        controller.session.flow, "active", lambda: {"slot": "blue", "model_sha256": "b" * 64}
    )
    monkeypatch.setattr(controller, "pins", dict)
    monkeypatch.setattr(controller, "image_record", lambda: {"image": old["image"]})
    monkeypatch.setattr(controller.session, "load", lambda _: (tmp_path, {}))
    monkeypatch.setattr(collector, "identity", lambda _: (new_binding, {}))
    monkeypatch.setattr(k, "credentials", lambda: {"owner_id": old["owner_id"]})
    monkeypatch.setattr(
        controller, "atomic", lambda path, value: path.write_text(json.dumps(value))
    )
    with pytest.raises(ValueError, match="previous session SHA"):
        controller.prepare("next")
    with pytest.raises(ValueError, match="previous session SHA"):
        controller.prepare("next", "0" * 64)
    result = controller.prepare("next", "a" * 64)
    assert result["binding"] == new_binding
    assert json.loads((tmp_path / ("binding-" + "a" * 64 + ".json")).read_bytes()) == old
    assert json.loads((tmp_path / "enabled.json").read_bytes()) == old
    assert (
        json.loads((tmp_path / "prepared.json").read_bytes())["replaces_session_sha256"]
        == "a" * 64
    )
    assert controller.prepare("next")["status"] == "session_monitor_already_prepared"
