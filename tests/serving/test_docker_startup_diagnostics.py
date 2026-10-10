"""Preserve Docker stderr and exit-state evidence before startup cleanup."""

import importlib.util
import json
from pathlib import Path
import subprocess

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/serving/check_docker_runtime.py"
SPEC = importlib.util.spec_from_file_location("docker_startup_diagnostics", SCRIPT)
checks = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checks)


@pytest.mark.parametrize("stdout", ["", "starting\n"])
def test_docker_logs_preserves_stderr_with_or_without_stdout(monkeypatch, stdout):
    stderr = "PermissionError: release cannot be read\n"
    monkeypatch.setattr(
        checks.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, stdout, stderr),
    )
    assert checks.Docker().run("logs", "owned", check=False) == (stdout + stderr).strip()


def test_json_command_keeps_stderr_separate(monkeypatch):
    output = '{"MemoryLimit": true}'
    monkeypatch.setattr(
        checks.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, output, "warning\n"),
    )
    information = checks.Docker().run("info", "--format", "{{json .}}")
    assert json.loads(information) == {"MemoryLimit": True}


@pytest.mark.parametrize(
    "exit_code,oom,engine_error,stderr",
    [
        (1, False, "", "PermissionError: release cannot be read\n"),
        (137, True, "process killed", ""),
    ],
)
def test_stopped_service_includes_state_and_stderr(
    monkeypatch, exit_code, oom, engine_error, stderr
):
    state = {
        "Running": False,
        "Status": "exited",
        "ExitCode": exit_code,
        "OOMKilled": oom,
        "Error": engine_error,
    }

    def run(command, **kwargs):
        if command[1] == "inspect":
            return subprocess.CompletedProcess(command, 0, json.dumps([{"State": state}]), "")
        assert command[1] == "logs"
        return subprocess.CompletedProcess(command, 0, "", stderr)

    monkeypatch.setattr(checks.subprocess, "run", run)
    with pytest.raises(checks.CheckFailed, match="Serviço encerrou") as error:
        checks.wait_ready(checks.Docker(), "owned", "release")
    message = str(error.value)
    assert f'"ExitCode": {exit_code}' in message
    assert f'"OOMKilled": {str(oom).lower()}' in message
    assert f'"Error": "{engine_error}"' in message
    if stderr:
        assert stderr.strip() in message
    else:
        assert "(sem saída capturada)" in message
