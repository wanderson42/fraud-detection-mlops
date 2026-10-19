"""Filesystem and S3 contract checks; no Docker service or native dataset required."""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts.storage import archive


class MemoryS3:
    def __init__(self):
        self.objects = {}
        self.puts = []
        self.fail_key = None

    def ensure_bucket(self):
        pass

    def head(self, key):
        if key not in self.objects:
            raise archive.MissingObject(key)
        return len(self.objects[key])

    def put(self, key, path):
        if key == self.fail_key:
            raise RuntimeError("injected upload failure")
        assert key not in self.objects, "immutable objects must not be replaced"
        self.objects[key] = path.read_bytes()
        self.puts.append(key)

    def get(self, key, path):
        self.head(key)
        path.write_bytes(self.objects[key])

    def listed(self, prefix):
        return {key for key in self.objects if key.startswith(prefix)}


@pytest.fixture
def project(tmp_path):
    (tmp_path / "data/releases/example").mkdir(parents=True)
    (tmp_path / "data/releases/example/model.bin").write_bytes(b"opaque bytes; never deserialize")
    (tmp_path / "data/releases/example/release.json").write_text('{"release_id":"fixture"}')
    return tmp_path


def prepared(project):
    identity = archive.prepare(project, ["data/releases/example"])
    return identity, json.loads(
        (project / "data/storage/bundles" / identity / "manifest.json").read_bytes()
    )


@pytest.mark.parametrize("reverse_selection", [False, True])
def test_release_directory_and_sibling_export_use_canonical_manifest_order(
    project, reverse_selection
):
    release = project / "data/serving/reference-v1"
    release.mkdir(parents=True)
    for name in ("manifest.json", "pipeline.skops", "smoke.json"):
        (release / name).write_bytes(b"opaque fixture " + name.encode())
    (project / "data/serving-export.json").write_text('{"status":"fixture"}')
    selections = ["data/serving/reference-v1", "data/serving-export.json"]
    if reverse_selection:
        selections.reverse()
    identity = archive.prepare(project, selections)
    bundle = project / "data/storage/bundles" / identity
    document = json.loads((bundle / "manifest.json").read_bytes())
    paths = [row["path"] for row in document["files"]]
    assert paths == [
        "data/serving-export.json",
        "data/serving/reference-v1/manifest.json",
        "data/serving/reference-v1/pipeline.skops",
        "data/serving/reference-v1/smoke.json",
    ]
    assert archive.prepare(project, list(reversed(selections))) == identity
    assert archive.prepare(project, paths) == identity
    client = MemoryS3()
    assert archive.publish(project, identity, client)["uploaded_objects"] == 5
    assert archive.publish(project, identity, client)["reused_objects"] == 5
    restored = Path(archive.restore(project, identity, client)["restored_directory"])
    archive.verify_files(restored, document)


def test_roundtrip_resume_manifest_last_and_isolated_restore(project):
    identity, document = prepared(project)
    client = MemoryS3()
    result = archive.publish(project, identity, client)
    assert result["uploaded_objects"] == 3
    assert client.puts[-1].endswith("/manifest.json")
    result = archive.publish(project, identity, client)
    assert result["reused_objects"] == 3 and result["uploaded_objects"] == 0
    assert len(client.puts) == 3
    restored = Path(archive.restore(project, identity, client)["restored_directory"])
    archive.verify_files(restored, document)
    for row in document["files"]:
        assert (restored / row["path"]).read_bytes() == (project / row["path"]).read_bytes()
    assert archive.prepare(project, ["data/releases/example"]) == identity
    with pytest.raises(ValueError, match="already exists"):
        archive.restore(project, identity, client)


def test_interrupted_upload_has_no_completion_marker_and_can_resume(project):
    identity, document = prepared(project)
    client = MemoryS3()
    prefix = f"pilot/v1/{identity}/"
    client.fail_key = prefix + document["files"][1]["path"]
    with pytest.raises(RuntimeError, match="injected"):
        archive.publish(project, identity, client)
    assert prefix + "manifest.json" not in client.objects
    assert len(client.objects) == 1
    client.fail_key = None
    result = archive.publish(project, identity, client)
    assert result["uploaded_objects"] == 2 and result["reused_objects"] == 1


def test_same_size_remote_corruption_is_not_trusted_or_overwritten(project):
    identity, document = prepared(project)
    client = MemoryS3()
    key = f"pilot/v1/{identity}/" + document["files"][0]["path"]
    client.objects[key] = b"x" * document["files"][0]["bytes"]
    with pytest.raises(ValueError, match="SHA-256 conflict"):
        archive.publish(project, identity, client)
    assert not client.puts
    assert client.objects[key] == b"x" * document["files"][0]["bytes"]


def test_local_bundle_tamper_rejected_before_network(project):
    identity, document = prepared(project)
    file = project / "data/storage/bundles" / identity / document["files"][0]["path"]
    file.write_bytes(b"corrupt")
    client = MemoryS3()
    with pytest.raises(ValueError, match="integrity"):
        archive.publish(project, identity, client)
    assert not client.objects


def test_corrupt_restore_never_publishes_directory(project):
    identity, document = prepared(project)
    client = MemoryS3()
    archive.publish(project, identity, client)
    row = document["files"][0]
    client.objects[f"pilot/v1/{identity}/" + row["path"]] = b"x" * row["bytes"]
    with pytest.raises(ValueError, match="integrity"):
        archive.restore(project, identity, client)
    assert not (project / "data/storage/restored" / identity).exists()
    assert not list((project / "data/storage").glob("restore-*"))


def test_untrusted_manifest_cannot_escape_restore(project):
    client = MemoryS3()
    document = {
        "schema": archive.SCHEMA,
        "files": [{"path": "data/../../escape", "bytes": 0, "sha256": "0" * 64}],
    }
    data = archive.encoded(document)
    identity = hashlib.sha256(data).hexdigest()
    client.objects[f"pilot/v1/{identity}/manifest.json"] = data
    with pytest.raises(ValueError, match="normalized"):
        archive.restore(project, identity, client)
    assert not (project / "escape").exists()


@pytest.mark.parametrize(
    "value",
    [
        "../data/x",
        "/data/x",
        "data/../x",
        "data//x",
        "data/storage/x",
        "data/./x",
        "data\\x",
        "data",
        123,
    ],
)
def test_unsafe_paths_rejected(value):
    with pytest.raises((TypeError, ValueError)):
        archive.relative_path(value)


@pytest.mark.parametrize(
    "name",
    [
        "checkpoint.sqlite",
        "mlflow.db",
        "cache.duckdb",
        "checkpoint.sqlite-wal",
        "checkpoint.sqlite-shm",
    ],
)
def test_database_files_require_consistent_backup_stage(project, name):
    (project / "data" / name).write_bytes(b"database")
    with pytest.raises(ValueError, match="consistent snapshot"):
        archive.prepare(project, ["data/" + name])


def test_symlinks_are_not_archived_or_followed_by_inventory(project):
    (project / "data/releases/link").symlink_to(
        project / "data/releases/example", target_is_directory=True
    )
    with pytest.raises(ValueError, match="Symlinks"):
        archive.prepare(project, ["data/releases/link"])
    result = archive.inventory(project)
    assert result["complete"] is False and result["issues"] == 1
    assert result["files"] == 2


def test_inventory_all_families_excludes_own_reports_and_does_not_open_content(
    project, monkeypatch
):
    for group in ("raw", "interim", "processed", "tracking", "replay", "experiments"):
        path = project / "data" / group / "opaque.bin"
        path.parent.mkdir()
        path.write_bytes(b"abc")
    (project / "data/storage").mkdir()
    (project / "data/storage/prior-report.json").write_bytes(b"excluded")
    original = Path.open

    def guarded(path, *args, **kwargs):
        if path.name == "opaque.bin":
            pytest.fail("inventory read a data payload")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded)
    result = archive.inventory(project)
    assert result["complete"] and result["files"] == 8
    assert set(result["families"]) == {
        "bronze",
        "silver",
        "gold_and_variants",
        "mlflow",
        "replay_and_measurements",
        "other_review_required",
        "releases",
    }
    assert archive.inventory(project)["files"] == 8


def test_unreadable_directory_is_never_marked_complete(project, monkeypatch):
    def walk(path, *, followlinks, onerror):
        onerror(PermissionError(13, "fixture access error", str(path / "unreadable")))
        return iter(())

    monkeypatch.setattr(archive.os, "walk", walk)
    result = archive.inventory(project)
    assert result["issues"] == 1 and not result["complete"]


def test_large_file_rejected_before_payload_copy(project):
    path = project / "data/large.bin"
    with path.open("wb") as f:
        f.truncate(archive.MAX_BYTES + 1)
    with pytest.raises(ValueError, match="Pilot limit"):
        archive.prepare(project, ["data/large.bin"])
    assert not (project / "data/storage").exists()


def test_file_count_limit(project):
    for i in range(65):
        (project / "data/releases/example" / f"{i}.txt").touch()
    with pytest.raises(ValueError, match="Pilot limit"):
        prepared(project)


def test_low_disk_space_preserves_originals(project, monkeypatch):
    monkeypatch.setattr(archive.shutil, "disk_usage", lambda _: SimpleNamespace(free=0))
    with pytest.raises(ValueError, match="Insufficient"):
        prepared(project)
    assert (project / "data/releases/example/model.bin").is_file()


@pytest.mark.parametrize("mutation", ["duplicate", "negative", "oversize", "bad_hash", "schema"])
def test_invalid_manifest_contract(project, mutation):
    _, document = prepared(project)
    if mutation == "duplicate":
        document["files"].append(document["files"][0])
    elif mutation == "negative":
        document["files"][0]["bytes"] = -1
    elif mutation == "oversize":
        document["files"][0]["bytes"] = archive.MAX_BYTES + 1
    elif mutation == "bad_hash":
        document["files"][0]["sha256"] = "etag-is-not-sha256"
    else:
        document["schema"] = "other"
    with pytest.raises(ValueError):
        archive.validate_manifest(document)


@pytest.mark.parametrize(
    "stderr,exception",
    [
        ("An error occurred (404) when calling HeadObject", archive.MissingObject),
        ("An error occurred (AccessDenied)", RuntimeError),
        ("connection refused", RuntimeError),
    ],
)
def test_cli_adapter_does_not_treat_access_or_transport_failure_as_missing(
    project, monkeypatch, stderr, exception
):
    (project / "infra/observability").mkdir(parents=True)
    (project / "infra/observability/.env").write_text("ignored test configuration")
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=1, stdout="", stderr=stderr)

    monkeypatch.setattr(archive.subprocess, "run", run)
    client = archive.ComposeS3(project)
    with pytest.raises(exception):
        client.head("key")
    assert calls[0][calls[0].index("run") + 1 : calls[0].index("storage-client")] == [
        "--rm",
        "--no-deps",
        "-T",
    ]
    assert "http://rustfs:9000" in calls[0]
    assert not any("AWS_SECRET_ACCESS_KEY" in arg for arg in calls[0])


def test_empty_or_missing_data_is_not_a_native_validation(tmp_path):
    with pytest.raises(ValueError, match="does not exist"):
        archive.inventory(tmp_path)
    (tmp_path / "data/empty").mkdir(parents=True)
    with pytest.raises(ValueError, match="No files"):
        archive.prepare(tmp_path, ["data/empty"])


def test_managed_workspace_cannot_redirect_reports_outside_project(project, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    work = archive.workspace(project)
    (work / "inventory").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="Symlinks"):
        archive.inventory(project)
    assert not list(outside.iterdir())
