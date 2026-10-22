"""Actual SQLite process death, duplicate safety, input bindings and rendered Helm boundaries."""

import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import tomllib

import pytest
from scripts.platform import kind_replay as controller
from scripts.platform import kind_replay_worker as worker
import yaml

ROOT = Path(__file__).resolve().parents[2]
RUN = "a" * 32


def launch(path, identity=RUN):
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "scripts.platform.kind_replay_worker",
            "--state",
            str(path),
            "--run-id",
            identity,
        ],
        cwd=ROOT,
        env={**os.environ, "OMP_NUM_THREADS": "2", "OPENBLAS_NUM_THREADS": "2"},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )


def marker(process, path):
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if path.exists():
            return json.loads(path.read_text())
        if process.poll() is not None:
            pytest.fail(process.stderr.read())
        time.sleep(0.05)
    pytest.fail("Worker did not publish phase marker")


def stop(process):
    if process.poll() is None:
        process.send_signal(signal.SIGKILL)
    process.communicate(timeout=10)


def interrupted(path):
    process = launch(path)
    try:
        value = marker(process, path / "interrupted.json")
        assert value["before"]["counts"]["events"] == 8
        assert (path / "checkpoint.sqlite-journal").exists()
    finally:
        stop(process)
    return value


def test_real_kill_open_transaction_recovers_exact_ledger_and_reuses_without_fit(
    tmp_path, monkeypatch
):
    state = tmp_path / "run"
    interrupted(state)
    # The restarted process must use the pinned synthetic release without fitting again.
    monkeypatch.setattr(
        worker, "synthetic_release", lambda _: pytest.fail("Restart fitted a model")
    )
    with worker.threadpool_limits(limits=2):
        result = worker.run(state, RUN)
    assert result["status"] == "success"
    assert result["inflight_transaction_rollback_validated"]
    assert result["all_schema_and_table_rows_equal"]
    assert result["duplicate_redelivery_without_rescoring_validated"]
    assert result["input_deliveries"] == 22
    assert result["counts"]["events"] == 20
    assert result["counts"]["decisions"] == result["counts"]["alert_decisions"] == 10
    assert result["counts"]["feedback"] == result["counts"]["transactions"] == 10
    assert result["accepted_after_restart"] == 12 and result["reused_after_restart"] == 10
    assert worker.logical(state / "checkpoint.sqlite") == worker.logical(
        state / "reference.sqlite"
    )
    assert worker.run(state, RUN) == result


@pytest.mark.parametrize("change", ["run", "release", "input", "unknown", "symlink"])
def test_foreign_or_changed_state_fails_without_success(tmp_path, change):
    state = tmp_path / "run"
    if change in {"unknown", "symlink"}:
        state.mkdir()
        if change == "unknown":
            (state / "foreign").write_text("preserve")
        else:
            link = tmp_path / "link"
            link.symlink_to(state, target_is_directory=True)
            state = link
    else:
        interrupted(state)
        if change == "release":
            (state / "release/manifest.json").write_text("{}")
        if change == "input":
            setup = json.loads((state / "setup.json").read_text())
            setup["input_sha256"] = "0" * 64
            (state / "setup.json").write_text(json.dumps(setup))
    with pytest.raises(ValueError):
        worker.run(state, "b" * 32 if change == "run" else RUN)
    assert not (state / "result.json").exists()


def test_logical_digest_includes_alerts_and_checkpoint(tmp_path):
    state = tmp_path / "run"
    interrupted(state)
    with worker.threadpool_limits(limits=2):
        worker.run(state, RUN)
    before = worker.logical(state / "checkpoint.sqlite")
    with worker.closing(worker.sqlite3.connect(state / "checkpoint.sqlite")) as connection:
        connection.execute("UPDATE checkpoint SET transaction_id=999")
        connection.commit()
    assert worker.logical(state / "checkpoint.sqlite") != before


def helm(*args, check=True):
    binary = shutil.which("helm")
    assert binary, "Helm is required for real template validation"
    return subprocess.run([binary, *map(str, args)], capture_output=True, text=True, check=check)


def test_real_helm_single_writer_retained_claim_and_no_credential_or_host_mounts():
    helm("lint", "--strict", controller.CHART)
    docs = list(yaml.safe_load_all(helm("template", "fraud-replay-test", controller.CHART).stdout))
    assert {x["kind"] for x in docs} == {"PersistentVolumeClaim", "Deployment"}
    pvc = next(x for x in docs if x["kind"] == "PersistentVolumeClaim")
    spec = next(x for x in docs if x["kind"] == "Deployment")["spec"]
    assert pvc["metadata"]["annotations"]["helm.sh/resource-policy"] == "keep"
    assert spec["replicas"] == 1 and spec["strategy"]["type"] == "Recreate"
    pod = spec["template"]["spec"]
    assert pod["automountServiceAccountToken"] is False and pod["enableServiceLinks"] is False
    assert pod["securityContext"]["runAsNonRoot"]
    assert len(pod["containers"]) == 1
    container = pod["containers"][0]
    assert container["imagePullPolicy"] == "Never" and "env" not in container
    assert container["securityContext"]["readOnlyRootFilesystem"]
    assert all("hostPath" not in volume and "secret" not in volume for volume in pod["volumes"])
    assert "persistentVolumeClaim" in pod["volumes"][0]


@pytest.mark.parametrize("value", ["image=ubuntu:latest", "runId=invalid"])
def test_real_helm_rejects_unpinned_values(value):
    assert helm(
        "template", "fraud-replay-test", controller.CHART, "--set", value, check=False
    ).returncode


def test_runtime_lock_matches_poetry_and_excludes_monitoring_and_model_search():
    locked = {p["name"]: p for p in tomllib.loads((ROOT / "poetry.lock").read_text())["package"]}
    text = (ROOT / "infra/kind/replay/requirements.txt").read_text()
    for block in text.split("\n"):
        if "==" in block:
            name, version = block.split()[0].split("==")
            assert locked[name]["version"] == version
    for name in ["mlflow", "optuna", "shap", "boto3", "duckdb"]:
        assert name + "==" not in text


def test_controller_failure_keeps_owned_compute_and_writes_failed_receipt(tmp_path, monkeypatch):
    monkeypatch.setattr(controller, "initialize", lambda: None)
    monkeypatch.setattr(controller, "work", lambda: tmp_path)
    monkeypatch.setattr(controller, "image_record", lambda: {"image": "fraud-kind-replay:" + RUN})
    monkeypatch.setattr(controller.f, "inventory", lambda: {"same": "foundation"})
    monkeypatch.setattr(controller, "objects", lambda *_: [])
    monkeypatch.setattr(controller.f, "private_json", lambda p, v: p.write_text(json.dumps(v)))
    calls = []
    monkeypatch.setattr(controller.f, "helm", lambda *a, **_: calls.append(a))
    monkeypatch.setattr(
        controller.f, "kube", lambda *_a, **_k: type("Done", (), {"stdout": "", "stderr": ""})()
    )
    monkeypatch.setattr(
        controller, "wait_marker", lambda *a, **kw: (_ for _ in ()).throw(ValueError("injected"))
    )
    with pytest.raises(ValueError, match="injected"):
        controller.rehearse()
    assert not any("uninstall" in c for c in calls)
    receipt = json.loads(next(tmp_path.glob("rehearsal-*.json")).read_text())
    assert receipt["status"] == "failed" and receipt["phase"] == "await_open_transaction"


def test_locked_runtime_contains_all_unconditional_dependencies():
    locked = {p["name"]: p for p in tomllib.loads((ROOT / "poetry.lock").read_text())["package"]}
    text = (ROOT / "infra/kind/replay/requirements.txt").read_text()
    names = {line.split("==")[0] for line in text.splitlines() if "==" in line}
    for name in names:
        for dependency, spec in locked[name].get("dependencies", {}).items():
            if isinstance(spec, dict) and "markers" in spec:
                continue
            assert dependency in names, (name, dependency)
        for item in locked[name]["files"]:
            if item["file"].endswith(".whl"):
                assert "--hash=" + item["hash"] in text


def test_controller_pod_refuses_a_second_writer_and_foreign_owner(monkeypatch):
    pod = {
        "metadata": {
            "name": "pod",
            "uid": "uid",
            "annotations": {"fraud.replay/run-id": RUN},
            "ownerReferences": [{"uid": "foreign", "controller": True}],
        },
        "spec": {"containers": [{"name": "replay", "image": "image"}]},
    }
    monkeypatch.setattr(
        controller, "objects", lambda kind, _: [pod, pod] if kind == "pods" else []
    )
    with pytest.raises(ValueError, match="More than one"):
        controller.pod_for("release", RUN, {"image": "image"})
    monkeypatch.setattr(controller, "objects", lambda kind, _: [pod] if kind == "pods" else [])
    monkeypatch.setattr(controller, "inventory", lambda *_: {"deployment_uid": "owned"})
    with pytest.raises(ValueError, match="ownership chain"):
        controller.pod_for("release", RUN, {"image": "image"})
