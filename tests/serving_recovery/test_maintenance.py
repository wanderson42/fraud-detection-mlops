"""Same-model maintenance, scoped compatibility and recoverable state publication."""

import copy
import json
import subprocess

import pytest
from scripts.platform import serving_recovery as r


def active():
    image = {"image": "old-carrier", "identity": "old"}
    return {
        "version": "1",
        "run_id": "run",
        "model_sha256": "a" * 64,
        "slot": "blue",
        "owner_id": "owner",
        "paths": {"blue": "blue", "green": "green"},
        "plan": {
            "images": {"runtime": "api", "gateway": "gateway", "artifacts": image},
            "slot_images": {
                s: {"runtime": "api", "artifacts": copy.deepcopy(image)} for s in ["blue", "green"]
            },
            "releases": {"blue": {"model": "a" * 64}, "green": {"model": "a" * 64}},
            "owner_id": "owner",
        },
    }


def fixture_intent(tmp_path, monkeypatch):
    from fraud_detection_mlops.storage import managed_paths

    monkeypatch.setattr(managed_paths, "DATA_DIR", tmp_path)
    folder = tmp_path / "storage/kind-platform/serving-maintenance/seed-restart-v1"
    folder.mkdir(parents=True)
    before = active()
    after = copy.deepcopy(before)
    after["maintenance_receipt"] = str(folder / "applied.json")
    after["plan"]["images"]["artifacts"] = {"image": "fixed-carrier", "identity": "fixed"}
    for slot in ["blue", "green"]:
        after["plan"]["slot_images"][slot]["artifacts"] = {
            "image": "fixed-carrier",
            "identity": "fixed",
        }
    state = [copy.deepcopy(before)]
    monkeypatch.setattr(r, "current", lambda: copy.deepcopy(state[0]))
    monkeypatch.setattr(r, "work", lambda key: folder)
    monkeypatch.setattr(
        r.updates, "workspace", lambda: tmp_path / "storage/kind-platform/model-updates"
    )
    (tmp_path / "storage/kind-platform/model-updates").mkdir()

    def atomic(path, value):
        state[0] = copy.deepcopy(value)

    monkeypatch.setattr(r, "atomic_json", atomic)
    monkeypatch.setattr(r.deployment, "files", lambda plan: None)
    monkeypatch.setattr(r, "config", dict)

    def apply_desired(plan, *_args, **_kwargs):
        assert plan["receipt_directory"] == str(folder)

    monkeypatch.setattr(r.deployment, "apply", apply_desired)
    monkeypatch.setattr(
        r.deployment, "check_active", lambda *args: {"score": {"model_sha256": "a" * 64}}
    )
    intent = {"before_active": before, "after_active": after, "approval_sha256": "b" * 64}
    monkeypatch.setattr(r, "read", lambda key: intent)
    return folder, intent, state


def test_maintenance_only_allows_carrier_changes():
    before = active()
    after = copy.deepcopy(before)
    after["plan"]["images"]["artifacts"] = "new"
    r.check_same_model(before, after)
    for name in ["version", "model_sha256", "slot", "run_id", "owner_id"]:
        changed = copy.deepcopy(after)
        changed[name] = "foreign"
        with pytest.raises(ValueError):
            r.check_same_model(before, changed)
    changed = copy.deepcopy(after)
    changed["plan"]["images"]["runtime"] = "foreign"
    with pytest.raises(ValueError):
        r.check_same_model(before, changed)


def test_reviewed_apply_updates_current_without_touching_model_receipts(tmp_path, monkeypatch):
    folder, intent, state = fixture_intent(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="exact"):
        r.apply("seed-restart-v1", "wrong", True)
    assert not (folder / "applied.json").exists()
    result = r.apply("seed-restart-v1", "b" * 64, True)
    assert result["champion_modified"] is False
    assert state[0] == intent["after_active"]
    monkeypatch.setattr(r.f, "workspace", lambda: tmp_path / "storage/kind-platform")
    assert r.resolve_active(intent["before_active"]) == intent["after_active"]
    assert r.apply("seed-restart-v1", "b" * 64, True) == result


def test_failure_before_current_publication_can_resume_same_approval(tmp_path, monkeypatch):
    folder, intent, state = fixture_intent(tmp_path, monkeypatch)
    atomic = r.atomic_json

    def fail(*_):
        raise OSError("interrupted before current.json publication")

    monkeypatch.setattr(r, "atomic_json", fail)
    with pytest.raises(OSError):
        r.apply("seed-restart-v1", "b" * 64, True)
    assert state[0] == intent["before_active"] and (folder / "applied.json").exists()
    monkeypatch.setattr(r, "atomic_json", atomic)
    r.apply("seed-restart-v1", "b" * 64, True)
    assert state[0] == intent["after_active"]


def test_foreign_current_is_never_overwritten(tmp_path, monkeypatch):
    folder, _, state = fixture_intent(tmp_path, monkeypatch)
    state[0]["model_sha256"] = "f" * 64
    with pytest.raises(ValueError, match="Another deployment"):
        r.apply("seed-restart-v1", "b" * 64, True)
    assert not (folder / "applying.json").exists()


def test_make_maintenance_requires_writer_and_exact_approval(dispatch):
    result, calls = dispatch("serving-recovery-apply")
    assert result.returncode != 0 and calls == []
    result, calls = dispatch(
        "serving-recovery-apply", SERVING_NO_OTHER_WRITERS="1", SERVING_APPROVAL_SHA256="a" * 64
    )
    assert result.returncode == 0
    assert calls[-1][-3:] == ["--approval-sha256", "a" * 64, "--no-other-writers"]


def test_completed_HTTP_flow_receipt_and_ledger_survive_operational_transition(
    prepared, server, tmp_path, monkeypatch
):
    import shutil

    from scripts.platform import source_compatibility
    from scripts.product_flow import controller as flow
    from tests.product_flow.conftest import scorer

    folder, value, _ = prepared
    original_root = r.f.ROOT
    committed_root = tmp_path / "reviewed"
    committed_root.mkdir()
    declarations = [
        (path, json.loads((original_root / path).read_bytes()))
        for path in (
            source_compatibility.TRANSITION,
            source_compatibility.REFACTOR_TRANSITION,
            source_compatibility.RETIREMENT_TRANSITION,
            source_compatibility.MONITORING_TRANSITION,
            source_compatibility.RUNTIME_TRANSITION,
            source_compatibility.PROBE_FORWARD_TRANSITION,
            source_compatibility.RUNTIME_TAG_TRANSITION,
            source_compatibility.BYTECODE_TRANSITION,
            source_compatibility.TRACKING_BASELINE_TRANSITION,
            source_compatibility.SESSION_COUNTS_TRANSITION,
            source_compatibility.SESSION_BUSY_TRANSITION,
            source_compatibility.SESSION_ONLY_TRANSITION,
            source_compatibility.COMPONENTS_TRANSITION,
            source_compatibility.PRESENTATION_TRANSITION,
            source_compatibility.LIFECYCLE_PROFILE_TRANSITION,
        )
    ]
    current_pins = flow.pins()
    saved_pins = dict(current_pins)
    # Walk the reviewed transition chain backwards to reconstruct a pre-seed receipt.
    for _, declaration in reversed(declarations):
        for name, hashes in declaration["changes"].items():
            if name in saved_pins or hashes["after"] is None:
                if hashes["before"] is None:
                    saved_pins.pop(name, None)
                else:
                    saved_pins[name] = hashes["before"]
            if (original_root / name).is_file():
                dest = committed_root / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(original_root / name, dest)
    for path, _ in declarations:
        dest = committed_root / path
        dest.parent.mkdir(exist_ok=True)
        shutil.copyfile(original_root / path, dest)
    subprocess.run(["git", "init", "-q", str(committed_root)], check=True)
    subprocess.run(["git", "add", "."], cwd=committed_root, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "reviewed",
        ],
        cwd=committed_root,
        check=True,
    )
    value.update(
        schema="fraud_product_flow_input_v1",
        source_pins=saved_pins,
        active={
            "slot": "blue",
            "plan": {"releases": {"blue": {"manifest": value["release_manifest"]}}},
        },
    )
    c = flow
    c.run_events(folder, value, scorer(server[0], value))
    (folder / "input.json").write_text(json.dumps(value))
    original_bytes = (folder / "input.json").read_bytes()
    checkpoint_bytes = (folder / "checkpoint.sqlite").read_bytes()
    monkeypatch.setattr(c.f, "ROOT", committed_root)
    monkeypatch.setattr(c, "work", lambda key: folder)
    monkeypatch.setattr(c, "policy", lambda: value["policy"])
    monkeypatch.setattr(c, "pins", lambda: current_pins)
    read = c.read("fixture")
    assert read == value
    assert c.verify_ledger(folder, read)["HTTP_receipts"] == 2
    assert (folder / "input.json").read_bytes() == original_bytes
    assert (folder / "checkpoint.sqlite").read_bytes() == checkpoint_bytes


@pytest.mark.parametrize("tamper", [None, "command", "owner", "image", "restarts"])
def test_restart_probe_spec_allows_forced_retry_and_preserves_failed_job(
    tmp_path, monkeypatch, tamper
):
    from types import SimpleNamespace

    from fraud_detection_mlops.storage import managed_paths

    monkeypatch.setattr(managed_paths, "DATA_DIR", tmp_path)
    folder = tmp_path / "storage/probe"
    folder.mkdir(parents=True)
    snapshot = active()
    snapshot["plan"]["releases"]["blue"]["manifest_sha256"] = "b" * 64
    image = {"image": "fixed-carrier", "loaded_identities": ["sha256:" + "a" * 64]}
    statuses = {"imageID": "sha256:" + "a" * 64, "state": {"terminated": {"exitCode": 0}}}
    calls = []
    created = []
    bad = [False]

    def kube(*args, **kwargs):
        calls.append(args)
        if args[0] == "create":
            created.append(json.loads(kwargs["data"]))
            return SimpleNamespace(stdout="")
        if args[:2] == ("get", "job"):
            return SimpleNamespace(
                stdout=json.dumps(
                    {
                        **created[-1],
                        "metadata": {**created[-1]["metadata"], "uid": "job-uid"},
                        "status": {"succeeded": 1},
                    }
                )
            )
        if args[:2] == ("get", "pods"):
            return SimpleNamespace(
                stdout=json.dumps(
                    {
                        "items": [
                            {
                                "metadata": {
                                    "name": "probe-pod",
                                    "uid": "pod-uid",
                                    "ownerReferences": [
                                        {
                                            "uid": "foreign" if tamper == "owner" else "job-uid",
                                            "controller": True,
                                        }
                                    ],
                                },
                                "spec": {}
                                if tamper == "command"
                                else copy.deepcopy(created[-1]["spec"]["template"]["spec"]),
                                "status": {
                                    "phase": "Succeeded",
                                    "initContainerStatuses": [
                                        {
                                            **statuses,
                                            "name": "release",
                                            "restartCount": 2 if tamper == "restarts" else 1,
                                            "imageID": "sha256:" + "f" * 64
                                            if tamper == "image"
                                            else statuses["imageID"],
                                        }
                                    ],
                                    "containerStatuses": [{**statuses, "name": "verify"}],
                                },
                            }
                        ]
                    }
                )
            )
        if args[0] == "logs":
            state = (
                "canonical_release_seeded" if "--previous" in args else "canonical_release_reused"
            )
            if bad[0]:
                state = "wrong"
            assert "--previous" not in args, (
                "Previous container logs may already have been collected"
            )
            return SimpleNamespace(
                stdout=json.dumps(
                    {
                        "status": state,
                        "model_sha256": snapshot["model_sha256"],
                        "files_written": 0,
                        "files_reused": 3,
                    }
                )
            )
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(r.f, "kube", kube)
    if tamper:
        with pytest.raises(ValueError):
            r.seed_job(image, snapshot, folder)
        assert not (folder / "restart-check.json").exists()
        assert not any(c[0] == "delete" for c in calls)
        return
    assert r.seed_job(image, snapshot, folder)["init_restarts"] == 1
    calls.clear()
    assert r.seed_job(image, snapshot, folder)["previous_log_required"] is False
    assert not any(c[0] in {"create", "delete"} for c in calls)
    spec = created[0]["spec"]["template"]["spec"]
    assert spec["restartPolicy"] == "OnFailure" and created[0]["spec"]["backoffLimit"] > 1
    assert spec["automountServiceAccountToken"] is False
    assert spec["volumes"] == [{"name": "model", "emptyDir": {"sizeLimit": "64Mi"}}]
    assert spec["initContainers"][0]["volumeMounts"] == spec["containers"][0]["volumeMounts"]
    assert "SystemExit(17" in spec["initContainers"][0]["command"][-1]
    assert not any(c[0] == "delete" for c in calls)
    second = tmp_path / "storage/rejected-probe"
    second.mkdir()
    calls.clear()
    bad[0] = True
    with pytest.raises(ValueError, match="logs"):
        r.seed_job(image, snapshot, second)
    assert not any(c[0] == "delete" for c in calls)
    assert (second / "restart-job.json").exists() and not (second / "restart-check.json").exists()


@pytest.mark.parametrize("changed", [False, True])
def test_prepare_resumes_existing_carrier_without_build_or_new_job(tmp_path, monkeypatch, changed):
    from fraud_detection_mlops.storage import managed_paths

    monkeypatch.setattr(managed_paths, "DATA_DIR", tmp_path)
    folder = tmp_path / "storage/maintenance"
    root = tmp_path / "source"
    seed = root / "scripts/operational_round/seed.py"
    dockerfile = root / "infra/kind/serving/Dockerfile.artifacts"
    for file in (seed, dockerfile):
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(file.name)
    before = active()
    for slot in ("blue", "green"):
        source = tmp_path / slot
        source.mkdir()
        before["paths"][slot] = str(source)
        for name in ("manifest.json", "pipeline.skops", "smoke.json"):
            (source / name).write_text(slot + name)
    monkeypatch.setattr(r.f, "ROOT", root)
    monkeypatch.setattr(r.f, "owner", lambda: {"node_uid": "node"})
    monkeypatch.setattr(r, "work", lambda _: folder)
    monkeypatch.setattr(r, "committed", lambda: {"reviewed": "yes"})
    monkeypatch.setattr(r, "current", lambda: copy.deepcopy(before))
    monkeypatch.setattr(r, "config", dict)
    monkeypatch.setattr(r.deployment, "check_active", lambda *_: {"ready": True})
    builds = []
    monkeypatch.setattr(r.original, "image_command", lambda *a, **k: builds.append(a))
    loaded = {"id": "sha256:" + "a" * 64}
    monkeypatch.setattr(r.original, "load_image", lambda _: loaded)
    monkeypatch.setattr(r.original, "cri", lambda _: loaded)
    probes = []

    def probe(image, active, folder):
        probes.append(image)
        if len(probes) == 1:
            r.f.private_json(
                folder / "restart-job.json",
                {"spec": {"template": {"spec": {"initContainers": [{"image": image["image"]}]}}}},
            )
            raise ValueError("logs unavailable")
        return {"status": "seed_restart_verified"}

    monkeypatch.setattr(r, "seed_job", probe)
    with pytest.raises(ValueError, match="logs"):
        r.prepare("same", True)
    snapshot = (folder / "restart-job.json").read_bytes()
    if changed:
        (tmp_path / "blue/pipeline.skops").write_text("changed")
        with pytest.raises(ValueError, match="release bytes differ"):
            r.prepare("same", True)
        assert len(probes) == 1
    else:
        result = r.prepare("same", True)
        assert result["before_active"] == before
        assert result["status"] == "prepared_awaiting_approval"
        assert probes[0] == probes[1]
    assert len(builds) == 1
    assert (folder / "restart-job.json").read_bytes() == snapshot
