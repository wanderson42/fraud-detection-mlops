"""Exact committed source transitions preserve receipts and reject scientific drift."""

import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import pytest
from scripts.platform import source_compatibility as compat

ROOT = Path(__file__).resolve().parents[2]


def test_committed_transition_accepts_exact_operational_changes_and_rejects_science(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    changes = {}
    old = {"science.py": "a" * 64}
    new = dict(old)
    for index, name in enumerate(sorted(compat.ALLOWED)):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("reviewed " + name)
        before = None if name.startswith("scripts/platform/") else str(index) * 64
        after = hashlib.sha256(path.read_bytes()).hexdigest()
        changes[name] = {"before": before, "after": after}
        if before is not None:
            old[name] = before
        new[name] = after
    target = tmp_path / compat.TRANSITION
    target.parent.mkdir()
    target.write_text(
        json.dumps({"schema": "fraud_serving_seed_source_transition_v1", "changes": changes})
    )
    with pytest.raises(ValueError, match="Commit"):
        compat.accept(old, new, tmp_path)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
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
        cwd=tmp_path,
        check=True,
    )
    compat.accept(old, new, tmp_path)
    invalid = dict(new)
    invalid["science.py"] = "f" * 64
    with pytest.raises(ValueError, match="Scientific"):
        compat.accept(old, invalid, tmp_path)
    invalid = dict(new)
    invalid["scripts/operational_round/seed.py"] = "f" * 64
    with pytest.raises(ValueError, match="Scientific|Unreviewed"):
        compat.accept(old, invalid, tmp_path)
    target.write_text(target.read_text() + " ")
    with pytest.raises(ValueError, match="Commit"):
        compat.accept(old, new, tmp_path)


def commit(root):
    subprocess.run(["git", "init", "-q", str(root)], check=True)
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
            "Reviewed source transition",
        ],
        cwd=root,
        check=True,
    )


@pytest.fixture
def refactor(tmp_path):
    transitions = []
    for name in (
        compat.TRANSITION,
        compat.REFACTOR_TRANSITION,
        compat.RETIREMENT_TRANSITION,
        compat.MONITORING_TRANSITION,
        compat.RUNTIME_TRANSITION,
        compat.PROBE_FORWARD_TRANSITION,
        compat.RUNTIME_TAG_TRANSITION,
        compat.BYTECODE_TRANSITION,
        compat.TRACKING_BASELINE_TRANSITION,
        compat.SESSION_COUNTS_TRANSITION,
        compat.SESSION_BUSY_TRANSITION,
        compat.SESSION_ONLY_TRANSITION,
        compat.COMPONENTS_TRANSITION,
        compat.PRESENTATION_TRANSITION,
        compat.LIFECYCLE_PROFILE_TRANSITION,
        compat.EVENT_SOURCE_TRANSITION,
    ):
        src = ROOT / name
        transitions.append(json.loads(src.read_bytes()))
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(src.read_bytes())
    names = set().union(*(v["changes"].keys() for v in transitions))
    for name in names:
        if (ROOT / name).is_file():
            target = tmp_path / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)
    return tmp_path, transitions


def previous(current, transitions):
    saved = dict(current)
    for step in reversed(transitions):
        for name, hashes in step["changes"].items():
            if name in saved or hashes["after"] is None:
                if hashes["before"] is None:
                    saved.pop(name, None)
                else:
                    saved[name] = hashes["before"]
    return saved


@pytest.mark.parametrize(
    "scope", ["round", "updates", "product", "operations", "replay", "session_monitoring"]
)
@pytest.mark.parametrize("before_seed", [False, True])
def test_current_readers_accept_exact_old_profiles_without_editing_them(
    refactor, scope, before_seed
):
    from scripts.model_updates import controller as updates
    from scripts.operational_round import controller as round_controller
    from scripts.platform import kind_operations, kind_replay
    from scripts.product_flow import controller as product
    from scripts.session_monitoring import controller as session_monitoring

    readers = {
        "round": round_controller.pins,
        "updates": updates.pins,
        "product": product.pins,
        "operations": kind_operations.pins,
        "replay": kind_replay.pins,
        "session_monitoring": session_monitoring.pins,
    }
    root, transitions = refactor
    current = readers[scope]()
    saved = previous(current, transitions if before_seed else transitions[-1:])
    original = copy.deepcopy(saved)
    commit(root)
    compat.accept(saved, current, root)
    assert saved == original
    bad = dict(current)
    bad["references/gold_contract_v1.json"] = "f" * 64
    with pytest.raises(ValueError, match="Scientific"):
        compat.accept(saved, bad, root)


def test_refactor_must_be_committed_and_unknown_fitter_change_remains_blocked(refactor):
    from scripts.model_updates.controller import pins

    root, transitions = refactor
    current = pins()
    saved = previous(current, transitions[-1:])
    with pytest.raises(ValueError, match="Commit"):
        compat.accept(saved, current, root)
    commit(root)
    name = "scripts/model_updates/training.py"
    (root / name).write_bytes((root / name).read_bytes() + b"\n# unreviewed fitter change\n")
    current[name] = hashlib.sha256((root / name).read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="Scientific"):
        compat.accept(saved, current, root)


def test_transition_cannot_add_scientific_paths_to_its_allowed_scope(refactor):
    from scripts.model_updates.controller import pins

    root, transitions = refactor
    current = pins()
    saved = previous(current, transitions[-1:])
    file = root / compat.REFACTOR_TRANSITION
    declaration = json.loads(file.read_bytes())
    declaration["changes"]["references/model_update_policy_v1.json"] = {
        "before": "a" * 64,
        "after": "b" * 64,
    }
    file.write_text(json.dumps(declaration))
    commit(root)
    with pytest.raises(ValueError, match="Only the declared"):
        compat.accept(saved, current, root)


def test_documentary_edits_are_separate_from_execution_contracts():
    saved = {"README.md": "a", "references/persistence_catalogue_v1.json": "b", "policy.json": "p"}
    current = {"policy.json": "p"}
    compat.accept(saved, current, ROOT)
    with pytest.raises(ValueError, match="Scientific"):
        compat.accept(saved, {"policy.json": "different"}, ROOT)


def test_baseline_reader_reaches_committed_compatibility_and_preserves_receipt(
    refactor, monkeypatch
):
    from scripts.platform import kind_operations as operations

    root, transitions = refactor
    current = operations.pins()
    saved = previous(current, transitions[-1:])
    owner = {
        key: key + "-owned"
        for key in ("container_id", "node_uid", "docker_engine_id", "owner_receipt_sha256")
    }
    active = {"slot": "blue", "model_sha256": "same-weights"}
    directory = root / "operations"
    directory.mkdir(mode=0o700)
    path = directory / "baseline.json"
    value = {
        "schema": "fraud_kind_operations_baseline_v2",
        "status": "prepared",
        "source_pins": saved,
        "active_state_sha256": operations.digest(active),
        "host": {"machine_id_sha256": "same-host"},
        "owner": owner,
    }
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    original = path.read_bytes()
    commit(root)
    monkeypatch.setattr(operations.f, "ROOT", root)
    monkeypatch.setattr(operations.f, "managed_destination", lambda path, kind: path)
    monkeypatch.setattr(operations, "work", lambda: directory)
    monkeypatch.setattr(operations, "pins", lambda: current)
    monkeypatch.setattr(operations, "active_state", lambda: active)
    monkeypatch.setattr(operations, "host", lambda: {"machine_id_sha256": "same-host"})
    monkeypatch.setattr(operations, "docker_owner", lambda: owner)
    assert operations.read_baseline(path) == (value, owner)
    assert path.read_bytes() == original
    current["references/gold_contract_v1.json"] = "f" * 64
    with pytest.raises(ValueError, match="Scientific"):
        operations.read_baseline(path)
    assert path.read_bytes() == original


def test_tracking_report_reader_accepts_committed_controller_recipe_transition(
    refactor, monkeypatch
):
    from scripts.platform import kind_operations as ops
    from scripts.platform import kind_tracking as tracking

    from fraud_detection_mlops.storage import managed_paths

    root, transitions = refactor
    controller = "scripts/platform/kind_tracking.py"
    client = "scripts/platform/kind_tracking_client.py"
    path = root / client
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((ROOT / client).read_bytes())
    bytecode = next(
        value
        for value in transitions
        if value["schema"] == "fraud_python_bytecode_source_transition_v1"
    )
    report = {
        "schema": "fraud_kind_tracking_rehearsal_v1",
        "status": "success",
        "controller_sha256": bytecode["changes"][controller]["before"],
        "client_sha256": tracking.sha(path),
        "run_id": "a" * 32,
        "model_uri": "models:/m-" + "b" * 32,
        "model_artifact_uri": "mlflow-artifacts:/1/models/m-" + "b" * 32 + "/artifacts",
        "model_sha256": "c" * 64,
    }
    target = root / "data/storage/tracking-report.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(report))
    original = target.read_bytes()
    monkeypatch.setattr(managed_paths, "DATA_DIR", root / "data")
    monkeypatch.setattr(ops.f, "ROOT", root)
    with pytest.raises(ValueError, match="Commit"):
        ops.tracking_report(target)
    commit(root)
    assert ops.tracking_report(target)["record"]["run_id"] == report["run_id"]
    assert target.read_bytes() == original
    report["client_sha256"] = "e" * 64
    target.write_text(json.dumps(report))
    with pytest.raises(ValueError, match="Scientific"):
        ops.tracking_report(target)
