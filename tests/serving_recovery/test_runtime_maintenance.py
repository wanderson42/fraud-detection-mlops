"""Same-model runtime maintenance, interrupted publication and explicit rollback."""

import copy
import json
from pathlib import Path

import pytest
from scripts.platform import runtime_maintenance as m
from scripts.platform import serving_recovery as r
from tests.serving_recovery.test_maintenance import active

LIVE_GUARD = m.live_guard
READ = m.read


@pytest.fixture
def intent(tmp_path, monkeypatch):
    from fraud_detection_mlops.storage import managed_paths

    monkeypatch.setattr(managed_paths, "DATA_DIR", tmp_path)
    workspace = tmp_path / "storage/kind-platform"
    folder = workspace / "serving-maintenance/runtime-v1"
    folder.mkdir(parents=True)
    current_path = workspace / "model-updates/current.json"
    current_path.parent.mkdir()
    before = active()
    after = copy.deepcopy(before)
    after["maintenance_receipt"] = str(folder / "applied.json")
    image = {"image": "candidate", "identity": "new", "loaded_identities": ["new"]}
    after["plan"]["images"]["runtime"] = image
    for slot in ("blue", "green"):
        after["plan"]["slot_images"][slot]["runtime"] = copy.deepcopy(image)
    state = {"before_active": before, "after_active": after, "approval_sha256": "a" * 64}
    r.atomic_json(current_path, before)
    monkeypatch.setattr(r, "work", lambda _: folder)
    monkeypatch.setattr(r.f, "workspace", lambda: workspace)
    monkeypatch.setattr(r.updates, "workspace", lambda: current_path.parent)
    monkeypatch.setattr(m, "read", lambda _: copy.deepcopy(state))
    monkeypatch.setattr(m.deployment, "files", lambda _: None)
    monkeypatch.setattr(m, "live_guard", lambda _: None)
    monkeypatch.setattr(
        m.deployment,
        "check_active",
        lambda *_: {"score": {"model_sha256": before["model_sha256"]}},
    )
    calls = []
    monkeypatch.setattr(m.deployment, "apply", lambda *a, **kw: calls.append(a))
    return folder, state, calls


def test_runtime_only_never_relaxes_the_old_carrier_boundary():
    before = active()
    after = copy.deepcopy(before)
    after["plan"]["images"]["runtime"] = "new"
    m.runtime_only(before, after)
    with pytest.raises(ValueError):
        r.check_same_model(before, after)
    for key in ("model_sha256", "version", "run_id", "slot", "owner_id", "paths"):
        foreign = copy.deepcopy(after)
        foreign[key] = "foreign"
        with pytest.raises(ValueError):
            m.runtime_only(before, foreign)
    foreign = copy.deepcopy(after)
    foreign["plan"]["images"]["artifacts"] = "foreign"
    with pytest.raises(ValueError):
        m.runtime_only(before, foreign)


def test_apply_idempotence_and_rollback_keep_resolvable_history(intent):
    folder, state, calls = intent
    with pytest.raises(ValueError, match="exact"):
        m.change("runtime-v1", "wrong", True)
    assert calls == []
    result = m.change("runtime-v1", "a" * 64, True)
    receipt_bytes = (folder / "applied.json").read_bytes()
    assert result["champion_modified"] is False
    assert r.current() == state["after_active"]
    assert r.resolve_active(state["before_active"]) == state["after_active"]
    assert m.change("runtime-v1", "a" * 64, True) == result
    assert len(calls) == 1
    m.change("runtime-v1", m.rollback_digest(state), True, rollback=True)
    rolled = r.current()
    assert rolled["plan"] == state["before_active"]["plan"]
    assert r.resolve_active(state["before_active"]) == rolled
    assert r.resolve_active(state["after_active"]) == rolled
    assert (folder / "applied.json").read_bytes() == receipt_bytes
    m.change("runtime-v1", m.rollback_digest(state), True, rollback=True)
    assert len(calls) == 2
    with pytest.raises(ValueError, match="Another"):
        m.change("runtime-v1", "a" * 64, True)


def test_interruption_after_receipt_before_pointer_can_resume(intent, monkeypatch):
    folder, state, calls = intent
    atomic = r.atomic_json
    monkeypatch.setattr(r, "atomic_json", lambda *_: (_ for _ in ()).throw(OSError("interrupted")))
    with pytest.raises(OSError):
        m.change("runtime-v1", "a" * 64, True)
    assert r.current() == state["before_active"]
    assert (folder / "applied.json").exists()
    monkeypatch.setattr(r, "atomic_json", atomic)
    m.change("runtime-v1", "a" * 64, True)
    assert r.current() == state["after_active"]
    assert len(calls) == 2


def test_failed_rollout_can_restore_old_runtime_before_new_pointer_is_published(
    intent, monkeypatch
):
    folder, state, calls = intent

    def broken(*_, **__):
        raise ValueError("readiness timeout")

    monkeypatch.setattr(m.deployment, "apply", broken)
    with pytest.raises(ValueError, match="readiness"):
        m.change("runtime-v1", "a" * 64, True)
    assert (folder / "runtime-authorized.json").exists()
    assert not (folder / "applied.json").exists()
    monkeypatch.setattr(m.deployment, "apply", lambda *a, **kw: calls.append(a))
    m.change("runtime-v1", m.rollback_digest(state), True, rollback=True)
    saved = json.loads((folder / "rolled-back.json").read_bytes())
    assert saved["rollback_from_unpublished_attempt"] is True
    assert saved["before_active"] == state["before_active"]
    assert r.resolve_active(state["before_active"]) == r.current()
    assert r.current()["plan"] == state["before_active"]["plan"]


def test_foreign_state_and_missing_writer_declaration_never_apply(intent):
    folder, state, calls = intent
    with pytest.raises(ValueError, match="writers"):
        m.change("runtime-v1", "a" * 64, False)
    foreign = copy.deepcopy(state["before_active"])
    foreign["model_sha256"] = "f" * 64
    r.atomic_json(r.updates.workspace() / "current.json", foreign)
    with pytest.raises(ValueError, match="Another"):
        m.change("runtime-v1", "a" * 64, True)
    assert calls == [] and not (folder / "runtime-authorized.json").exists()


def test_make_runtime_requires_concrete_baseline_and_approval(dispatch):
    result, calls = dispatch("serving-runtime-prepare")
    assert result.returncode != 0 and not calls
    result, calls = dispatch(
        "serving-runtime-prepare", SERVING_NO_OTHER_WRITERS="1", PLATFORM_BASELINE="baseline.json"
    )
    assert result.returncode == 0 and "--baseline" in calls[-1]
    for target in ("serving-runtime-apply", "serving-runtime-rollback"):
        previous_count = len(calls)
        result, calls = dispatch(target, SERVING_NO_OTHER_WRITERS="1")
        assert result.returncode != 0 and len(calls) == previous_count


@pytest.mark.parametrize("tamper", [None, "image", "foreign_after_gate", "HTTP"])
def test_native_probe_is_isolated_reuses_carrier_and_only_cleans_up_owned_pod(
    intent, monkeypatch, tamper
):
    from contextlib import contextmanager
    from types import SimpleNamespace

    folder, value, _ = intent
    after = value["after_active"]
    after["plan"]["slot_images"]["blue"]["artifacts"] = {
        "image": "carrier",
        "loaded_identities": ["sha256:" + "c" * 64],
    }
    after["plan"]["slot_images"]["blue"]["runtime"]["loaded_identities"] = ["sha256:" + "a" * 64]
    templates = {
        "blue": {
            "spec": {
                "template": {
                    "spec": {
                        "automountServiceAccountToken": False,
                        "containers": [
                            {
                                "name": "api",
                                "image": "old",
                                "securityContext": {"readOnlyRootFilesystem": True},
                            }
                        ],
                        "initContainers": [{"name": "release", "image": "carrier"}],
                        "volumes": [
                            {"name": "model", "emptyDir": {}},
                            {"name": "tmp", "emptyDir": {}},
                        ],
                    }
                }
            }
        }
    }
    state = {"pod": None, "deleted": 0, "created": 0}

    def kube(*args, **kwargs):
        if args[:2] == ("get", "pod"):
            return SimpleNamespace(stdout=json.dumps(state["pod"]) if state["pod"] else "")
        if args[0] == "create":
            state["created"] += 1
            pod = json.loads(kwargs["data"])
            assert "app.kubernetes.io/instance" not in pod["metadata"]["labels"]
            assert pod["spec"]["automountServiceAccountToken"] is False
            assert pod["spec"]["initContainers"][0]["image"] == "carrier"
            pod["metadata"]["uid"] = "own-pod"
            pod["status"] = {
                "containerStatuses": [
                    {"imageID": "sha256:" + ("f" if tamper == "image" else "a") * 64}
                ],
                "initContainerStatuses": [{"imageID": "sha256:" + "c" * 64}],
            }
            state["pod"] = pod
        if args[0] == "delete":
            assert state["pod"]["metadata"]["uid"] == "own-pod"
            state["deleted"] += 1
            state["pod"] = None
        return SimpleNamespace(stdout="")

    @contextmanager
    def forward(desired, uid):
        assert uid == "own-pod" and desired["spec"]["containers"][0]["image"] == "candidate"
        yield "http://isolated"

    def gate(*_):
        if tamper == "foreign_after_gate":
            state["pod"]["metadata"]["uid"] = "foreign"
        if tamper == "HTTP":
            raise ValueError("HTTP parity mismatch")
        return {"score": {"model_sha256": after["model_sha256"]}}

    monkeypatch.setattr(m.f, "kube", kube)
    monkeypatch.setattr(m, "probe_forward", forward)
    monkeypatch.setattr(m.deployment, "gate", gate)
    if tamper:
        with pytest.raises(ValueError):
            m.isolated_probe(folder, after, templates)
        assert state["deleted"] == 0
    else:
        result = m.isolated_probe(folder, after, templates)
        assert result["canonical_route_changed"] is False and state["deleted"] == 1
        assert m.isolated_probe(folder, after, templates) == result
        assert state["created"] == 1


@pytest.mark.parametrize(
    "field", [None, "weights", "runtime_environment", "corruption", "checker"]
)
def test_qualification_must_bind_same_weights_versions_integrity_and_checker(
    tmp_path, monkeypatch, field
):
    before = active()
    manifest = {"serving_release_id": "a" * 32, "runtime_environment": {"python": "3.14.4"}}
    before["plan"]["releases"]["blue"] = {"manifest": manifest, "manifest_sha256": "b" * 64}
    before["paths"]["blue"] = str(tmp_path)
    (tmp_path / "smoke.json").write_text(json.dumps({"expected_score": 0.25}))
    report = {
        "status": "success",
        "version": "laboratory_serving_docker_check_v1",
        "image": {"os": "linux", "architecture": "amd64"},
        "manifest_sha256": "b" * 64,
        "serving_release_id": "a" * 32,
        "http_score": {"model_sha256": before["model_sha256"], "score": 0.25},
        "runtime_environment": manifest["runtime_environment"],
        "release_files_unchanged": True,
        "restart_recovery": "passed",
        "invalid_input_http_status": 422,
        "runtime": {"readonly_write_probes": "passed", "uid": 10001, "gid": 10001},
        "failure_scenarios": {
            name: {"startup_rejected": True}
            for name in ("wrong_manifest_sha256", "corrupt_model", "missing_model")
        },
        "check_script_sha256": m.p.sha(m.f.ROOT / "scripts/serving/check_docker_runtime.py"),
    }
    if field == "weights":
        report["http_score"]["model_sha256"] = "foreign"
    elif field == "runtime_environment":
        report["runtime_environment"] = {"python": "3.15"}
    elif field == "corruption":
        report["failure_scenarios"]["corrupt_model"]["startup_rejected"] = False
    elif field == "checker":
        report["check_script_sha256"] = "f" * 64
    path = tmp_path / "qualification.json"
    path.write_text(json.dumps(report))
    if field:
        with pytest.raises(ValueError, match="qualification"):
            m.qualification_report(path, before)
    else:
        assert m.qualification_report(path, before) == report


@pytest.mark.parametrize("tamper", [None, "image", "spec", "UID", "PVC", "alias"])
def test_live_guard_allows_only_reviewed_runtime_field_and_helm_revision_history(
    intent, monkeypatch, tamper
):
    from contextlib import contextmanager
    from types import SimpleNamespace

    folder, value, _ = intent
    templates = {
        s: {
            "metadata": {"uid": "uid-" + s},
            "spec": {
                "replicas": 1 if s == "blue" else 0,
                "template": {
                    "spec": {"containers": [{"image": "old", "name": "api"}], "volumes": []}
                },
            },
        }
        for s in ("blue", "green")
    }
    value["templates"] = templates
    baseline = {
        "resources": {
            **{
                "Deployment/fraud-detector-" + s: {"uid": "uid-" + s, "spec_sha256": "old"}
                for s in templates
            },
            "PersistentVolumeClaim/data": {"uid": "pvc-uid", "spec_sha256": "pvc"},
            "Secret/sh.helm.release.v1.fraud-detector.v1": {"uid": "old-history"},
        }
    }
    value["baseline_path"] = str(folder / "baseline.json")
    Path(value["baseline_path"]).write_text(json.dumps(baseline))
    resources = copy.deepcopy(baseline["resources"])
    resources.pop("Secret/sh.helm.release.v1.fraud-detector.v1")
    resources["Secret/sh.helm.release.v1.fraud-detector.v11"] = {"uid": "new-history"}
    if tamper == "PVC":
        resources["PersistentVolumeClaim/data"]["uid"] = "foreign-pvc"

    @contextmanager
    def forward():
        yield "http://registry"

    def kube(*args, **_):
        slot = args[2].removeprefix("fraud-detector-")
        live = copy.deepcopy(templates[slot])
        live["spec"]["template"]["spec"]["containers"][0]["image"] = (
            "foreign" if tamper == "image" else "candidate"
        )
        if tamper == "spec":
            live["spec"]["replicas"] = 4
        if tamper == "UID":
            live["metadata"]["uid"] = "foreign"
        return SimpleNamespace(stdout=json.dumps(live))

    monkeypatch.setattr(m.operations, "resources", lambda: resources)
    monkeypatch.setattr(m.deployment.old, "owned", lambda _: True)
    monkeypatch.setattr(
        m.f, "helm", lambda *args, **kw: SimpleNamespace(stdout=json.dumps({"ownerId": "owner"}))
    )
    monkeypatch.setattr(m.deployment, "route", lambda: "blue")
    monkeypatch.setattr(m.deployment, "alias", lambda _: "foreign" if tamper == "alias" else "1")
    monkeypatch.setattr(r.kind_tracking, "port_forward", forward)
    monkeypatch.setattr(m.f, "kube", kube)
    if tamper:
        with pytest.raises(ValueError):
            LIVE_GUARD(value)
    else:
        LIVE_GUARD(value)


def test_prepare_binds_qualified_image_and_resumes_without_replacing_active_state(
    intent, monkeypatch
):
    from types import SimpleNamespace

    folder, _value, _calls = intent
    folder.chmod(0o700)
    baseline = folder / "baseline.json"
    baseline.write_text("{}\n")
    report = folder.parents[2].parent / "serving/qualification.json"
    report.parent.mkdir()
    report.write_text("{}\n")
    image_id = "sha256:" + "a" * 64
    monkeypatch.setattr(m, "read", READ)
    monkeypatch.setattr(m, "committed", lambda: {"reviewed": "source"})
    monkeypatch.setattr(
        m.operations, "check", lambda _: {"status": "current_platform_readbacks_verified"}
    )
    monkeypatch.setattr(m, "qualification_report", lambda *_: {"image": {"id": image_id}})
    monkeypatch.setattr(m.f, "owner", lambda: {"node_uid": "same-node"})
    monkeypatch.setattr(
        m.f,
        "run",
        lambda *_: SimpleNamespace(
            stdout=json.dumps([{"Id": image_id, "Os": "linux", "Architecture": "amd64"}])
        ),
    )
    monkeypatch.setattr(
        m.f,
        "kube",
        lambda *_: SimpleNamespace(
            stdout=json.dumps(
                {"metadata": {"uid": "deployment-uid"}, "spec": {"template": {"spec": {}}}}
            )
        ),
    )
    loads = []
    monkeypatch.setattr(r.original, "image_command", lambda args, **_: loads.append(args))
    monkeypatch.setattr(r.original, "load_image", lambda _: {"id": "sha256:" + "b" * 64})
    probes = []

    def probe(directory, after, templates):
        probes.append(after)
        checked = {
            "status": "runtime_probe_verified",
            "runtime_image": after["plan"]["images"]["runtime"],
        }
        r.f.private_json(directory / "runtime-probe.json", checked)
        return checked

    monkeypatch.setattr(m, "isolated_probe", probe)
    before_bytes = (r.updates.workspace() / "current.json").read_bytes()
    prepared = m.prepare("runtime-v1", report, baseline, True)
    assert prepared["status"] == "runtime_prepared_awaiting_approval"
    assert prepared["after_active"]["plan"]["images"]["runtime"]["docker_identity"] == image_id
    assert len(probes) == len(loads) == 1
    assert (r.updates.workspace() / "current.json").read_bytes() == before_bytes
    assert m.prepare("runtime-v1", report, baseline, True) == prepared
    assert len(probes) == len(loads) == 1
    report.write_text('{"changed":true}\n')
    with pytest.raises(ValueError, match="qualification changed"):
        m.read("runtime-v1")
    assert (r.updates.workspace() / "current.json").read_bytes() == before_bytes
