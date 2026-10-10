"""Exercise the checker preflight with Docker's serialized info field names."""

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/serving/check_docker_runtime.py"
SPEC = importlib.util.spec_from_file_location("docker_engine_capabilities", SCRIPT)
checks = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checks)


class ImageInspectionReached(RuntimeError):
    """Stop before container access after a successful engine preflight."""


def engine_info():
    # Docker serializes the Go CPUCfsQuota field with json:"CpuCfsQuota".
    return {
        "OSType": "linux",
        "MemoryLimit": True,
        "SwapLimit": True,
        "CpuCfsPeriod": True,
        "CpuCfsQuota": True,
    }


def prepare_probe(monkeypatch, information):
    class ProbeDocker:
        def __init__(self):
            self.cleaned = False

        def run(self, *args):
            if args == ("context", "inspect"):
                return json.dumps(
                    [{"Endpoints": {"docker": {"Host": "unix:///var/run/docker.sock"}}}]
                )
            if args == ("info", "--format", "{{json .}}"):
                return json.dumps(information)
            if args[:2] == ("image", "inspect"):
                raise ImageInspectionReached
            raise AssertionError(f"Unexpected Docker access: {args}")

        def cleanup(self):
            self.cleaned = True

    probe = ProbeDocker()
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    monkeypatch.setattr(checks, "Docker", lambda: probe)
    monkeypatch.setattr(checks.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(checks, "read_release", lambda *args: ({}, {}, {}))
    monkeypatch.setattr(checks, "hashes", lambda *args: {})
    return probe


def test_docker_json_cpu_quota_key_reaches_image_inspection(monkeypatch, tmp_path):
    information = engine_info()
    assert "CPUCfsQuota" not in information
    probe = prepare_probe(monkeypatch, information)
    with pytest.raises(ImageInspectionReached):
        checks.assess("image", tmp_path, "f" * 64)
    assert probe.cleaned


@pytest.mark.parametrize("field", ["MemoryLimit", "SwapLimit", "CpuCfsQuota"])
@pytest.mark.parametrize("value", [False, None])
def test_missing_or_unsupported_engine_limit_remains_rejected(monkeypatch, tmp_path, field, value):
    information = engine_info()
    if value is None:
        information.pop(field)
        if field == "CpuCfsQuota":
            information["CPUCfsQuota"] = True
    else:
        information[field] = value
    probe = prepare_probe(monkeypatch, information)
    with pytest.raises(checks.CheckFailed, match="limites de memória/swap/CPU") as error:
        checks.assess("image", tmp_path, "f" * 64)
    assert f"{field}={value!r}" in str(error.value)
    assert probe.cleaned
