"""Exercise the real isolated forward; never mock the canonical name guard away."""

import copy
import json
import os
from types import SimpleNamespace

import pytest
from scripts.platform import runtime_maintenance as m
from scripts.platform import source_compatibility as compat
from tests.serving_recovery.test_runtime_maintenance import intent as runtime_intent

intent = runtime_intent


@pytest.fixture
def probe(monkeypatch):
    name = "fraud-runtime-check-" + "a" * 12
    desired = {
        "metadata": {
            "name": name,
            "namespace": m.f.NAMESPACE,
            "labels": {"fraud.runtime-maintenance": name},
        },
        "spec": {"containers": [{"name": "api", "image": "qualified-image"}]},
    }
    live = {**copy.deepcopy(desired), "status": {"phase": "Running"}}
    live["metadata"]["uid"] = "own-uid"
    monkeypatch.setattr(m, "pod_get", lambda _: copy.deepcopy(live))
    calls = []
    processes = []

    class Process:
        def __init__(self, args, **kwargs):
            self.alive = True
            self.terminated = 0
            calls.append(args)
            os.write(kwargs["stdout"].fileno(), b"Forwarding from 127.0.0.1:32123 -> 8000\n")
            processes.append(self)

        def poll(self):
            return None if self.alive else 0

        def terminate(self):
            self.terminated += 1
            self.alive = False

        def wait(self, **_):
            return 0

    monkeypatch.setattr(m.subprocess, "Popen", Process)
    return desired, live, calls, processes


def test_real_probe_forward_uses_exact_namespace_pod_loopback_and_releases_child(probe):
    desired, _, calls, processes = probe
    # This is the guard that rejected the author's original preparation.
    with (
        pytest.raises(ValueError, match="Only the owned canonical"),
        m.deployment.old.forward(desired["metadata"]["name"]),
    ):
        pytest.fail("Canonical helper must still reject isolated probes")
    assert not calls
    with m.probe_forward(desired, "own-uid") as url:
        assert url == "http://127.0.0.1:32123"
        args = calls[-1]
        assert args[args.index("--address") + 1] == "127.0.0.1"
        assert args[args.index("--context") + 1] == m.f.CONTEXT
        assert args[args.index("--namespace") + 1] == m.f.NAMESPACE
        assert args[-2:] == ["pod/" + desired["metadata"]["name"], ":8000"]
    assert processes[0].terminated == 1


@pytest.mark.parametrize("tamper", ["UID", "spec", "labels", "phase", "canonical_name"])
def test_foreign_or_expired_probe_is_rejected_before_starting_any_forward(probe, tamper):
    desired, live, calls, _ = probe
    if tamper == "UID":
        live["metadata"]["uid"] = "foreign"
    elif tamper == "spec":
        live["spec"]["containers"][0]["image"] = "foreign"
    elif tamper == "labels":
        live["metadata"]["labels"]["app.kubernetes.io/instance"] = "fraud-detector"
    elif tamper == "phase":
        live["status"]["phase"] = "Failed"
    else:
        desired["metadata"]["name"] = "fraud-detector-blue-abc"
    with pytest.raises(ValueError), m.probe_forward(desired, "own-uid"):
        pytest.fail("Foreign probe must never be forwarded")
    assert not calls


def test_probe_identity_is_rechecked_after_HTTP_and_child_always_stops(probe):
    desired, live, _, processes = probe
    with pytest.raises(ValueError, match="UID/spec/phase"), m.probe_forward(desired, "own-uid"):
        live["metadata"]["uid"] = "foreign"
    assert processes[0].terminated == 1


def test_HTTP_exception_keeps_original_failure_and_stops_only_own_forward(probe):
    desired, _, _, processes = probe
    with (
        pytest.raises(ValueError, match="HTTP parity mismatch"),
        m.probe_forward(desired, "own-uid"),
    ):
        raise ValueError("HTTP parity mismatch")
    assert processes[0].terminated == 1


@pytest.mark.parametrize("missing", [False, True])
def test_expired_or_missing_probe_archives_draft_and_preserves_old_object(
    intent, monkeypatch, missing
):
    folder, _, _ = intent
    name = "fraud-runtime-check-" + "b" * 12
    desired = {
        "metadata": {
            "name": name,
            "namespace": m.f.NAMESPACE,
            "labels": {"fraud.runtime-maintenance": name},
        },
        "spec": {"containers": [{"image": "qualified"}]},
    }
    live = {**copy.deepcopy(desired), "status": {"phase": "Failed", "reason": "DeadlineExceeded"}}
    live["metadata"]["uid"] = "old-uid"
    m.f.private_json(folder / "runtime-probe-spec.json", desired)
    m.f.private_json(folder / "runtime-probe-uid.json", {"uid": "old-uid"})
    monkeypatch.setattr(m, "pod_get", lambda _: None if missing else copy.deepcopy(live))
    monkeypatch.setattr(
        m.f, "kube", lambda *_a, **_kw: pytest.fail("Renewal must not delete retained pods")
    )
    assert m.renew_probe(folder, desired, None if missing else live)
    renewed = json.loads((folder / "runtime-probe-spec.json").read_text())
    assert renewed["spec"] == desired["spec"]
    assert renewed["metadata"]["name"] != name
    assert not (folder / "runtime-probe-uid.json").exists()
    assert not (folder / "runtime-probe-renewal.json").exists()
    assert len(list((folder / "runtime-probe-attempts").glob("*.json"))) == 1


def test_renewal_refuses_foreign_UID_and_does_not_archive_or_change_draft(intent, monkeypatch):
    folder, _, _ = intent
    desired = {"metadata": {"name": "fraud-runtime-check-" + "b" * 12}, "spec": {}}
    live = {**copy.deepcopy(desired), "status": {"phase": "Failed", "reason": "DeadlineExceeded"}}
    live["metadata"]["uid"] = "foreign"
    m.f.private_json(folder / "runtime-probe-spec.json", desired)
    m.f.private_json(folder / "runtime-probe-uid.json", {"uid": "own"})
    original = (folder / "runtime-probe-spec.json").read_bytes()
    with pytest.raises(ValueError, match="foreign"):
        m.renew_probe(folder, desired, live)
    assert (folder / "runtime-probe-spec.json").read_bytes() == original
    assert not (folder / "runtime-probe-attempts").exists()


def test_interrupted_renewal_resumes_after_pointer_write(intent, monkeypatch):
    folder, _, _ = intent
    name = "fraud-runtime-check-" + "d" * 12
    desired = {
        "metadata": {
            "name": name,
            "namespace": m.f.NAMESPACE,
            "labels": {"fraud.runtime-maintenance": name},
        },
        "spec": {},
    }
    live = {**copy.deepcopy(desired), "status": {"phase": "Failed", "reason": "DeadlineExceeded"}}
    live["metadata"]["uid"] = "old"
    m.f.private_json(folder / "runtime-probe-spec.json", desired)
    m.f.private_json(folder / "runtime-probe-uid.json", {"uid": "old"})
    monkeypatch.setattr(m, "pod_get", lambda _: copy.deepcopy(live))
    atomic = m.recovery.atomic_json

    def interrupted(path, value):
        atomic(path, value)
        raise OSError("interrupted after draft pointer write")

    monkeypatch.setattr(m.recovery, "atomic_json", interrupted)
    with pytest.raises(OSError):
        m.renew_probe(folder, desired, live)
    renewed = json.loads((folder / "runtime-probe-spec.json").read_text())
    monkeypatch.setattr(m.recovery, "atomic_json", atomic)
    assert m.renew_probe(folder, renewed, None)
    assert json.loads((folder / "runtime-probe-spec.json").read_text()) == renewed
    assert len(list((folder / "runtime-probe-attempts").glob("*.json"))) == 1


def test_fix_transition_accepts_exact_old_draft_sources_without_rewriting_them(tmp_path):
    import subprocess

    root = tmp_path / "reviewed"
    root.mkdir()
    declaration = json.loads((m.f.ROOT / compat.BYTECODE_TRANSITION).read_text())
    old = {"scientific-contract": "same"}
    current = dict(old)
    for name, hashes in declaration["changes"].items():
        source = m.f.ROOT / name
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
        old[name] = hashes["before"]
        current[name] = hashes["after"]
    target = root / compat.BYTECODE_TRANSITION
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(declaration))
    draft = root / "runtime-preparing.json"
    draft.write_text(json.dumps({"source_pins": old, "node_uid": "same-node"}))
    original = draft.read_bytes()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    with pytest.raises(ValueError, match="Commit"):
        compat.accept(old, current, root)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "reviewed narrow fix",
        ],
        cwd=root,
        check=True,
    )
    compat.accept(old, current, root)
    assert draft.read_bytes() == original
    bad = {**current, "scientific-contract": "changed"}
    with pytest.raises(ValueError, match="Scientific"):
        compat.accept(old, bad, root)


def test_reviewed_fix_resumes_original_draft_with_same_node_image_and_unchanged_file(
    intent, monkeypatch, tmp_path
):
    import subprocess

    folder, _value, mutations = intent
    folder.chmod(0o700)
    source_root = m.f.ROOT
    declaration = json.loads((source_root / compat.BYTECODE_TRANSITION).read_text())
    approved_root = tmp_path / "committed-fix"
    for name in declaration["changes"]:
        target = approved_root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((source_root / name).read_bytes())
    target = approved_root / compat.BYTECODE_TRANSITION
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(declaration))
    subprocess.run(["git", "init", "-q", str(approved_root)], check=True)
    subprocess.run(["git", "add", "."], cwd=approved_root, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "reviewed forward correction",
        ],
        cwd=approved_root,
        check=True,
    )
    before = {name: hashes["before"] for name, hashes in declaration["changes"].items()}
    after = {name: hashes["after"] for name, hashes in declaration["changes"].items()}
    state = {"pins": before, "failed": False}
    monkeypatch.setattr(m, "committed", lambda: copy.deepcopy(state["pins"]))
    # Baseline checking is an external dependency in this unit test.
    monkeypatch.setattr(m.operations, "check", lambda _: None, raising=False)
    monkeypatch.setattr(m.recovery, "config", dict)
    monkeypatch.setattr(m.f, "ROOT", approved_root)
    monkeypatch.setattr(m.f, "owner", lambda: {"node_uid": "same-node"})
    image_id = "sha256:" + "a" * 64
    monkeypatch.setattr(m, "qualification_report", lambda *_: {"image": {"id": image_id}})
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
                {"metadata": {"uid": "same-deployment"}, "spec": {"template": {"spec": {}}}}
            )
        ),
    )
    tags = []
    monkeypatch.setattr(
        m.recovery.original, "image_command", lambda args, **_: tags.append(args[-1])
    )
    monkeypatch.setattr(m.recovery.original, "load_image", lambda _: {"id": "sha256:" + "b" * 64})

    def isolated(directory, _after, _templates):
        if not state["failed"]:
            state["failed"] = True
            raise ValueError("retained canonical-only forward failure")
        value = {"status": "runtime_probe_verified"}
        m.f.private_json(directory / "runtime-probe.json", value)
        return value

    monkeypatch.setattr(m, "isolated_probe", isolated)
    baseline = folder / "baseline.json"
    baseline.write_text("{}\n")
    report = tmp_path / "serving/report.json"
    report.parent.mkdir()
    report.write_text("{}\n")
    active_path = m.recovery.updates.workspace() / "current.json"
    active_bytes = active_path.read_bytes()
    with pytest.raises(ValueError, match="canonical-only"):
        m.prepare("runtime-v1", report, baseline, True)
    draft_bytes = (folder / "runtime-preparing.json").read_bytes()
    state["pins"] = after
    prepared = m.prepare("runtime-v1", report, baseline, True)
    assert prepared["source_pins"] == after
    assert prepared["status"] == "runtime_prepared_awaiting_approval"
    assert tags[0] == tags[1]
    assert (folder / "runtime-preparing.json").read_bytes() == draft_bytes
    assert active_path.read_bytes() == active_bytes
    assert not mutations
