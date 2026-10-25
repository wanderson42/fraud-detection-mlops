"""Image identity, isolated inspection and byte-accounting boundaries."""

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "scripts/serving/image_footprint.py"
SPEC = importlib.util.spec_from_file_location("image_footprint", SOURCE)
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def test_installed_inventory_separates_record_and_filesystem_and_skips_links(tmp_path):
    root = tmp_path / "site-packages"
    (root / "model/tests").mkdir(parents=True)
    (root / "model/weights.py").write_bytes(b"12345")
    (root / "model/tests/test_math.py").write_bytes(b"12")
    (root / "model/code.pyc").write_bytes(b"123")
    outside = tmp_path / "secret"
    outside.write_bytes(b"x" * 100)
    (root / "model/link").symlink_to(outside)
    distribution = SimpleNamespace(
        metadata={"Name": "model"},
        version="1",
        files=[
            "model/weights.py",
            "model/weights.py",
            "model/link",
            "../secret",
            "absent",
        ],
        locate_file=lambda entry: root / entry,
    )
    result = audit.installed_inventory([root, root], [distribution])
    assert result["site_packages"]["logical_bytes"] == 10
    assert result["site_packages"]["bytecode_bytes"] == 3
    assert result["site_packages"]["test_path_bytes"] == 2
    assert result["distributions"][0]["record_logical_bytes"] == 5
    assert result["distributions"][0]["record_missing_files"] == 1


def test_collect_pins_id_and_never_serializes_image_environment_or_history_commands():
    calls = []

    def runner(args, **kwargs):
        calls.append((args, kwargs))
        if args[1:3] == ["image", "inspect"]:
            return json.dumps(
                [
                    {
                        "Id": "sha256:abc",
                        "Size": 757000000,
                        "Architecture": "amd64",
                        "Os": "linux",
                        "Config": {"Env": ["SECRET=do-not-publish"]},
                        "RootFS": {"Layers": ["sha256:layer"]},
                    }
                ]
            )
        if args[1] == "run":
            assert args[-4:] == ["sha256:abc", "-I", "-", "--inside"]
            for option in (
                "--pull=never",
                "--network=none",
                "--read-only",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges",
                "--user=10001:10001",
            ):
                assert option in args
            assert not any(option.startswith(("--volume", "--mount")) for option in args)
            assert "def installed_inventory" in kwargs["input_text"]
            return '{"distributions": []}'
        assert args[1:3] == ["image", "history"]
        assert args[-1] == "sha256:abc"
        assert "CreatedBy" not in " ".join(args)
        return '{"id":"layer","size_display":"1MB"}\n'

    report = audit.collect("local-api:accepted", runner=runner)
    assert report["image"]["Size"] == 757000000
    assert "do-not-publish" not in json.dumps(report)
    assert report["optimization_validated"] is False
    assert report["champion_modified"] is False
    assert len(calls) == 3


def test_inspect_failure_does_not_start_a_container():
    calls = []

    def runner(args, **kwargs):
        calls.append(args)
        raise ValueError("image unavailable")

    with pytest.raises(ValueError, match="unavailable"):
        audit.collect("missing:tag", runner=runner)
    assert len(calls) == 1


def test_publish_preserves_existing_receipt_and_digest(tmp_path):
    target = tmp_path / "audit"
    result = audit.publish({"image_id": "sha256:abc"}, target)
    data = result.read_bytes()
    assert (target / "report.sha256").read_text().strip() == hashlib.sha256(data).hexdigest()
    with pytest.raises(ValueError, match="already exists"):
        audit.publish({"different": True}, target)
    assert result.read_bytes() == data


def test_publish_does_not_follow_output_symlink(tmp_path):
    outside = tmp_path / "preserved"
    outside.mkdir()
    target = tmp_path / "link"
    target.symlink_to(outside)
    with pytest.raises(ValueError, match="already exists"):
        audit.publish({}, target)
    assert not list(outside.iterdir())


def test_timeout_cleans_up_only_the_named_probe():
    calls = []

    def runner(args, **kwargs):
        calls.append(args)
        if args[1:3] == ["image", "inspect"]:
            return '[{"Id":"sha256:abc"}]'
        if args[1] == "run":
            raise subprocess.TimeoutExpired(args, 180)
        assert args[:4] == ["docker", "container", "rm", "--force"]
        assert args[-1] == calls[1][calls[1].index("--name") + 1]
        assert args[-1].startswith("fraud-image-audit-")
        return ""

    with pytest.raises(subprocess.TimeoutExpired):
        audit.collect("local:api", runner=runner)
    assert len(calls) == 3


def test_stdlib_probe_executes_from_stdin_in_isolated_python():
    result = subprocess.run(
        [sys.executable, "-I", "-", "--inside"],
        input=SOURCE.read_text(),
        text=True,
        capture_output=True,
        timeout=30,
        check=True,
    )
    report = json.loads(result.stdout)
    assert report["scope"].startswith("readable_site_packages")
    assert report["python"]
    assert report["distributions"]
