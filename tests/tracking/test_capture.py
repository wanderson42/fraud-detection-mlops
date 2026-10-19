"""Offline failure, integrity and bounded-process checks for the opt-in capture."""

from copy import deepcopy
import json
import os
from pathlib import Path
import signal
import sys
import time

import pytest
from scripts.tracking import capture as job
from scripts.tracking import capture_worker as worker
from scripts.tracking import local_stack as stack


@pytest.fixture
def lab(tmp_path):
    for name in ["infra/tracking", "infra/observability"]:
        (tmp_path / name).mkdir(parents=True)
        (tmp_path / name / ".env").write_text("not-to-be-read=secret\n")
    return tmp_path


def fixture_files(directory):
    worker.save(
        directory / "metadata.json",
        {
            "tables": {"metrics": {"rows": 2}},
            "locations": [
                {"table": "runs", "id": "run", "uri": "mlflow-artifacts:/1/run/artifacts"}
            ],
        },
    )
    (directory / "objects").mkdir()
    payload = directory / "objects/0000.bin"
    payload.write_bytes(b"archived-json")
    worker.save(
        directory / "objects.json",
        [{"key": "v1/1/run/artifacts/probe.json", **job.file_record(payload, directory)}],
    )


@pytest.fixture
def harness(lab, monkeypatch):
    calls = []
    identities = {
        name: {
            "container_id": ("a" if name == "postgres" else "b") * 64,
            "image_id": job.PG_IMAGE if name == "postgres" else "sha256:" + "c" * 64,
            "postgres_volume": stack.VOLUME if name == "postgres" else None,
        }
        for name in ["postgres", "mlflow"]
    }
    monkeypatch.setattr(
        stack, "inspect_service", lambda root, service: deepcopy(identities[service])
    )

    def tool(root, directory, action, deadline):
        calls.append(action)
        if action == "objects":
            fixture_files(directory)
        return {"status": "ready"}

    def run(args, deadline, **kwargs):
        calls.append(args)
        if "pg_dump" in args:
            kwargs["destination"].write_bytes(b"PGDMP-pinned-dump")
        if "pg_restore" in args:
            return b"TABLE DATA public alembic_version mlflow\n"
        if "{{.State.Running}}" in args:
            return b"false\n"
        return b""

    monkeypatch.setattr(job, "tool", tool)
    monkeypatch.setattr(job, "run", run)
    return calls, identities


def test_capture_is_closed_and_verified_after_exact_source_restart(lab, harness):
    calls, identities = harness
    result = job.capture(lab, no_other_writers=True)
    directory = Path(result["directory"])
    assert result["status"] == "captured_verified" and result["source_recovered"]
    assert result["containers_before"] == result["containers_after"] == identities
    assert not result["postgres_backup_restore_validated"]
    assert not result["primary_storage_migrated"]
    assert job.verify(directory, result["manifest_sha256"])["objects"] == 1
    assert directory.stat().st_mode & 0o777 == 0o700
    assert Path(result["report"]).stat().st_mode & 0o777 == 0o600
    assert calls.index("check") < calls.index("inventory") < calls.index("objects")
    assert ["docker", "start", identities["mlflow"]["container_id"]] in calls
    assert not any(isinstance(c, list) and ("up" in c or "down" in c or "rm" in c) for c in calls)
    dump = next(c for c in calls if isinstance(c, list) and "pg_dump" in c)
    assert "--format=custom" in dump and "--clean" not in dump


@pytest.mark.parametrize(
    "stage", ["stop", "inventory", "dump", "toc", "objects", "verify", "keyboard", "term"]
)
def test_capture_failure_always_requests_source_restart_without_success(
    lab, harness, monkeypatch, stage
):
    original_run, original_tool = job.run, job.tool

    def run(args, *a, **kw):
        if (
            stage == "stop"
            and "stop" in args
            or stage == "dump"
            and "pg_dump" in args
            or stage == "toc"
            and "pg_restore" in args
        ):
            raise ValueError("private credential")
        return original_run(args, *a, **kw)

    def tool(root, directory, action, deadline):
        if action == "inventory" and stage in {"inventory", "keyboard", "term"}:
            raise {
                "inventory": ValueError,
                "keyboard": KeyboardInterrupt,
                "term": InterruptedError,
            }[stage]("secret")
        if stage == "objects" and action == "objects":
            raise ValueError("private signed URL")
        return original_tool(root, directory, action, deadline)

    monkeypatch.setattr(job, "run", run)
    monkeypatch.setattr(job, "tool", tool)
    if stage == "verify":
        monkeypatch.setattr(
            job, "verify", lambda *a: (_ for _ in ()).throw(ValueError("hash mismatch"))
        )
    with pytest.raises((ValueError, KeyboardInterrupt, InterruptedError)):
        job.capture(lab, no_other_writers=True)
    receipt = next((stack.workspace(lab) / "receipts").glob("capture-*.json"))
    result = json.loads(receipt.read_bytes())
    assert result["status"] == "failed" and result["source_recovered"]
    assert "private" not in receipt.read_text() and "secret" not in receipt.read_text()
    assert not list((stack.workspace(lab) / "captures").rglob("manifest.json"))
    assert any(isinstance(c, list) and c[:2] == ["docker", "start"] for c in harness[0])


def test_no_writers_declaration_required_before_any_side_effect(lab, monkeypatch):
    monkeypatch.setattr(stack, "exclusive", lambda *a: pytest.fail("must not lock/create"))
    with pytest.raises(ValueError, match="no-other-writers"):
        job.capture(lab)
    assert not (lab / "data").exists()


@pytest.mark.parametrize("failure", ["preflight", "image", "restart"])
def test_preflight_and_recovery_errors_never_leave_success(lab, harness, monkeypatch, failure):
    if failure == "preflight":
        monkeypatch.setattr(
            stack, "inspect_service", lambda *a: (_ for _ in ()).throw(ValueError("wrong service"))
        )
    elif failure == "image":
        monkeypatch.setattr(
            job, "tool", lambda *a: (_ for _ in ()).throw(ValueError("stale image"))
        )
    else:
        monkeypatch.setattr(
            job, "restart", lambda *a: (_ for _ in ()).throw(RuntimeError("unhealthy"))
        )
    with pytest.raises((ValueError, RuntimeError)):
        job.capture(lab, no_other_writers=True)
    result = json.loads(next((stack.workspace(lab) / "receipts").glob("*.json")).read_bytes())
    assert result["status"] == "failed" and not result["source_recovered"]
    assert result["source_stop_requested"] == (failure == "restart")
    assert not list((stack.workspace(lab) / "captures").rglob("manifest.json"))


@pytest.mark.parametrize(
    "damage", ["bytes", "missing", "extra", "link", "mapping", "manifest", "foreign_key"]
)
def test_independent_local_verification_rejects_tampering(lab, harness, damage):
    result = job.capture(lab, no_other_writers=True)
    directory = Path(result["directory"])
    path = directory / "objects/0000.bin"
    if damage == "bytes":
        path.write_bytes(b"corrupt")
    elif damage == "missing":
        path.unlink()
    elif damage == "extra":
        (directory / "unlisted").write_text("data")
    elif damage == "link":
        path.unlink()
        path.symlink_to(directory / "postgres.dump")
    elif damage == "manifest":
        (directory / "manifest.json").write_text("{}")
    else:
        objects = json.loads((directory / "objects.json").read_bytes())
        if damage == "mapping":
            objects[0]["sha256"] = "0" * 64
        else:
            objects[0]["key"] = "other-bucket/unknown"
        (directory / "objects.json").write_bytes(worker.encoded(objects))
        manifest = json.loads((directory / "manifest.json").read_bytes())
        manifest["files"] = [
            job.file_record(directory / r["file"], directory) for r in manifest["files"]
        ]
        (directory / "manifest.json").write_bytes(worker.encoded(manifest))
    with pytest.raises((ValueError, KeyError)):
        job.verify(directory, result["manifest_sha256"] if damage == "manifest" else None)


@pytest.mark.parametrize("identity", ["../x", "", "b" * 31, "B" * 32, "/tmp/a"])
def test_unmanaged_capture_ids_are_rejected(lab, identity):
    with pytest.raises(ValueError, match="ID"):
        job.capture_directory(lab, identity)


def test_real_binary_subprocess_roundtrip_and_bounds(tmp_path):
    output = tmp_path / "postgres.dump"
    code = "import sys;sys.stdout.buffer.write(b'PGDMP'+b'\\x00\\xff'*1000)"
    job.run([sys.executable, "-c", code], time.monotonic() + 10, destination=output, limit=4096)
    assert output.read_bytes() == b"PGDMP" + b"\x00\xff" * 1000
    assert output.stat().st_mode & 0o777 == 0o600
    echoed = job.run(
        [sys.executable, "-c", "import sys;sys.stdout.buffer.write(sys.stdin.buffer.read())"],
        time.monotonic() + 10,
        input_path=output,
        limit=4096,
    )
    assert echoed == output.read_bytes()
    with pytest.raises(ValueError, match="budget"):
        job.run([sys.executable, "-c", "print('x'*100000)"], time.monotonic() + 10, limit=100)
    with pytest.raises(ValueError, match="deadline"):
        job.run([sys.executable, "-c", "import time;time.sleep(5)"], time.monotonic() + 0.05)
    with pytest.raises(ValueError, match="withheld") as error:
        job.run(
            [sys.executable, "-c", "import sys;sys.stderr.write('secret');sys.exit(1)"],
            time.monotonic() + 10,
        )
    assert "secret" not in str(error.value)


def test_termination_restores_handler_and_enters_cleanup():
    previous = signal.getsignal(signal.SIGTERM)
    with pytest.raises(InterruptedError), job.recover_on_term():
        os.kill(os.getpid(), signal.SIGTERM)
    assert signal.getsignal(signal.SIGTERM) == previous


def test_tool_binds_only_managed_directory_preserves_environment_and_cleans_up(lab, monkeypatch):
    directory = job.capture_directory(lab, "c" * 32)
    directory.mkdir(parents=True)
    calls = []
    monkeypatch.setenv("TRACKING_CAPTURE_DIRECTORY", "unchanged")

    def run(args, deadline, **kw):
        calls.append((args, kw))
        return worker.encoded(
            {"status": "ready", "worker_code_sha256": job.sha(Path(worker.__file__))}
        )

    monkeypatch.setattr(job, "run", run)
    monkeypatch.setattr(job.subprocess, "run", lambda *a, **kw: calls.append((a, kw)))
    job.tool(lab, directory, "check", time.monotonic() + 5)
    args, kwargs = calls[0]
    assert kwargs["environment"]["TRACKING_CAPTURE_DIRECTORY"] == str(directory)
    assert os.environ["TRACKING_CAPTURE_DIRECTORY"] == "unchanged"
    assert "capture.compose.yaml" in args[args.index("run") - 1]
    assert calls[-1][0][0][:3] == ["docker", "rm", "-f"]


def test_build_only_prepares_worker_image_without_recreating_source(lab, monkeypatch):
    calls = []
    monkeypatch.setattr(stack, "command", lambda args, **kwargs: calls.append((args, kwargs)))
    assert job.build(lab)["status"] == "built"
    args, kwargs = calls[0]
    assert args[-2:] == ["build", "capture"] and "up" not in args
    assert kwargs["timeout"] == 900
