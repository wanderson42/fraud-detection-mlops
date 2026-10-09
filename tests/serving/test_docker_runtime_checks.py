"""Controlled checker failures and real HTTP against a synthetic release; no Docker engine."""

from contextlib import contextmanager
import importlib.util
import json
from pathlib import Path
import socket
import subprocess
import sys
import time

import pytest
from sklearn.ensemble import HistGradientBoostingClassifier

from fraud_detection_mlops.serving.model_release import ServingReleaseError, load_release

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/serving/check_docker_runtime.py"
SPEC = importlib.util.spec_from_file_location("docker_runtime_checks", SCRIPT)
checks = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checks)


@contextmanager
def http_runtime(directory, digest):
    # Bind before launching Uvicorn to avoid a free-port race.
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        code = (
            "import uvicorn; from pathlib import Path; "
            "from fraud_detection_mlops.serving.http_service import create_app; "
            f"app=create_app(Path({str(directory)!r}), {digest!r}); "
            f"uvicorn.run(app, fd={listener.fileno()}, log_level='error')"
        )
        process = subprocess.Popen(
            [sys.executable, "-c", code],
            pass_fds=(listener.fileno(),),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            base_url = f"http://127.0.0.1:{port}"
            for _ in range(100):
                assert process.poll() is None, "Synthetic HTTP process exited"
                try:
                    if checks.request_json(base_url, "/ready")[0] == 200:
                        break
                except OSError, ValueError:
                    time.sleep(0.05)
            else:
                pytest.fail("Synthetic HTTP process not ready")
            yield base_url
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def test_checker_accepts_real_http_and_rejects_score_or_identity_drift(serving_release):
    directory, digest = serving_release
    manifest, smoke, before = checks.read_release(directory, digest)
    with http_runtime(directory, digest) as base_url:
        response = checks.check_score(base_url, smoke, manifest)
        assert response["serving_release_id"] == manifest["serving_release_id"]
        with pytest.raises(checks.CheckFailed, match="Paridade"):
            checks.check_score(base_url, {**smoke, "expected_score": 0.5}, manifest)
        with pytest.raises(checks.CheckFailed, match="Identidade"):
            checks.check_score(base_url, smoke, {**manifest, "serving_release_id": "f" * 32})
        invalid = {**smoke["request"], "transaction_id": True}
        assert checks.request_json(base_url, "/score", invalid)[0] == 422
    assert checks.hashes(directory) == before


@pytest.mark.parametrize("scenario", ["wrong_manifest", "corrupt_model", "missing_model"])
def test_failure_fixtures_rejected_by_existing_loader_without_refit(
    serving_release,
    monkeypatch,
    scenario,
):
    directory, digest = serving_release
    before = checks.hashes(directory)
    monkeypatch.setattr(
        HistGradientBoostingClassifier, "fit", lambda *a, **k: pytest.fail("refit")
    )
    with checks.failure_copy(directory) as copy:
        if scenario == "corrupt_model":
            with (copy / "pipeline.skops").open("ab") as stream:
                stream.write(b"controlled-corruption")
        elif scenario == "missing_model":
            (copy / "pipeline.skops").unlink()
        else:
            digest = "0" * 64
        with pytest.raises(ServingReleaseError):
            load_release(copy, digest)
    assert not copy.exists()
    assert checks.hashes(directory) == before


class FailedContainer:
    def __init__(self, exit_code=3, oom=False, logs="Release bytes changed: pipeline.skops"):
        self.exit_code, self.oom, self.logs = exit_code, oom, logs
        self.removed = []

    def inspect(self, name):
        return {"State": {"Running": False, "ExitCode": self.exit_code, "OOMKilled": self.oom}}

    def run(self, *args, **kwargs):
        return self.logs

    def remove(self, name):
        self.removed.append(name)


@pytest.mark.parametrize(
    "exit_code,oom,logs",
    [(0, False, "pipeline.skops"), (137, True, "pipeline.skops"), (3, False, "ImportError")],
)
def test_expected_startup_failure_does_not_accept_crash_for_wrong_reason(exit_code, oom, logs):
    docker = FailedContainer(exit_code, oom, logs)
    with pytest.raises(checks.CheckFailed):
        checks.check_failed_start(docker, "owned", "pipeline.skops")
    assert not docker.removed


def test_expected_contract_failure_recorded_and_container_removed():
    docker = FailedContainer()
    assert checks.check_failed_start(docker, "owned", "pipeline.skops")["startup_rejected"]
    assert docker.removed == ["owned"]


def test_running_invalid_release_fails_at_deadline():
    docker = FailedContainer()
    with pytest.raises(checks.CheckFailed, match="60s"):
        checks.check_failed_start(docker, "owned", "pipeline.skops", timeout=0)


def test_partial_docker_start_is_cleaned_using_only_unique_owned_name(monkeypatch, tmp_path):
    docker = checks.Docker()
    calls = []

    def run(*args, **kwargs):
        calls.append(args)
        if args[0] == "start":
            raise checks.CheckFailed("start failed")
        return "created-id"

    monkeypatch.setattr(docker, "run", run)
    try:
        with pytest.raises(checks.CheckFailed, match="start failed"):
            docker.start("sha256:fixed", tmp_path, "f" * 64, "partial")
    finally:
        docker.cleanup()
    owned = calls[0][calls[0].index("--name") + 1]
    assert owned.startswith("fraud-serving-check-")
    assert calls[-1] == ("rm", "--force", owned)
    assert docker.containers == []
    assert all(call[0] != "prune" for call in calls)


def test_readiness_rejects_another_release_before_accepting_health(monkeypatch):
    class ReadyContainer:
        def inspect(self, name):
            return {
                "State": {"Running": True, "Health": {"Status": "healthy"}},
                "NetworkSettings": {
                    "Ports": {"8000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8080"}]}
                },
            }

    monkeypatch.setattr(checks, "request_json", lambda *a: (200, {"serving_release_id": "wrong"}))
    with pytest.raises(checks.CheckFailed, match="outra release"):
        checks.wait_ready(ReadyContainer(), "owned", "expected")


def test_missing_docker_does_not_write_receipt_or_mutate_release(serving_release, monkeypatch):
    directory, digest = serving_release
    before = checks.hashes(directory)
    monkeypatch.setattr(checks.shutil, "which", lambda _: None)
    with pytest.raises(checks.CheckFailed, match="Docker"):
        checks.assess("image", directory, digest)
    assert checks.hashes(directory) == before


def test_existing_receipt_is_preserved_before_docker_access(tmp_path, monkeypatch):
    output = tmp_path / "report.json"
    output.write_text('{"existing": true}')
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            "--image",
            "image",
            "--release",
            str(tmp_path),
            "--manifest-sha256",
            "f" * 64,
            "--output",
            str(output),
        ],
    )
    monkeypatch.setattr(checks, "assess", lambda *a: pytest.fail("Docker should not be accessed"))
    assert checks.main() == 1
    assert json.loads(output.read_text()) == {"existing": True}


def test_receipt_publish_refuses_overwrite_and_removes_temporary_file(tmp_path):
    output = tmp_path / "report.json"
    checks.save_report(output, {"status": "success"})
    original = output.read_bytes()
    with pytest.raises(FileExistsError):
        checks.save_report(output, {"status": "replaced"})
    assert output.read_bytes() == original
    assert list(tmp_path.iterdir()) == [output]
