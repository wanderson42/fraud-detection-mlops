"""Transport failure boundaries and complete Bronze recovery, with no Docker or S3."""

from io import BytesIO
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
from scripts.storage import archive, bronze_archive, bronze_transfer
from scripts.storage import bronze_s3_worker as worker
from tests.storage.test_bronze_archive import source  # noqa: F401


class ServiceError(Exception):
    def __init__(self, status, code):
        self.response = {"ResponseMetadata": {"HTTPStatusCode": status}, "Error": {"Code": code}}


class MemoryClient:
    def __init__(self, *, expected_bucket=worker.BUCKET):
        self.expected_bucket = expected_bucket
        self.objects = {}
        self.puts = []
        self.bucket = False
        self.fail_at = None
        self.fail_status = None
        self.race = False
        self.body_override = None

    def head_bucket(self, **kwargs):
        assert kwargs == {"Bucket": self.expected_bucket}
        if not self.bucket:
            raise ServiceError(404, "NoSuchBucket")

    def create_bucket(self, **kwargs):
        assert kwargs == {"Bucket": self.expected_bucket}
        self.bucket = True

    def head_object(self, *, Bucket, Key):
        assert Bucket == self.expected_bucket
        if self.fail_status:
            raise ServiceError(*self.fail_status)
        if Key not in self.objects:
            raise ServiceError(404, "404")
        return {"ContentLength": len(self.objects[Key])}

    def put_object(self, **kwargs):
        import base64
        import hashlib

        key = kwargs["Key"]
        assert kwargs["IfNoneMatch"] == "*" and kwargs["Bucket"] == self.expected_bucket
        if self.fail_at is not None and len(self.puts) == self.fail_at:
            raise OSError("injected connection interruption")
        data = kwargs["Body"].read()
        assert len(data) == kwargs["ContentLength"]
        assert kwargs["ChecksumSHA256"] == base64.b64encode(hashlib.sha256(data).digest()).decode()
        assert key not in self.objects
        self.objects[key] = data
        self.puts.append(key)
        if self.race:
            raise ServiceError(412, "PreconditionFailed")

    def get_object(self, *, Bucket, Key):
        assert Bucket == self.expected_bucket
        data = self.objects[Key]
        return {"ContentLength": len(data), "Body": BytesIO(self.body_override or data)}

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        return self

    def paginate(self, *, Bucket, Prefix):
        assert Bucket == self.expected_bucket
        rows = [
            {"Key": key, "Size": len(value)}
            for key, value in self.objects.items()
            if key.startswith(Prefix)
        ]
        # Exercise pagination, including an empty last page.
        yield {"Contents": rows[:30]}
        yield {"Contents": rows[30:]}
        yield {}


@pytest.fixture
def prepared(source):  # noqa: F811 - shared pytest fixture
    root, snapshot, inventory = source
    result = bronze_archive.prepare_bronze(root)
    return root, snapshot, inventory, result["bundle_id"]


def test_roundtrip_manifest_last_idempotent_and_independent_of_originals(prepared):
    root, snapshot, inventory, identity = prepared
    work, client = archive.workspace(root), MemoryClient()
    original = {
        p.relative_to(root).as_posix(): p.read_bytes() for p in snapshot.rglob("*") if p.is_file()
    }
    first = worker.publish(work, identity, client)
    assert first["uploaded_objects"] == first["verified_objects"] == 74
    assert client.puts[-1] == worker.prefix(identity) + "manifest.json"
    again = worker.publish(work, identity, client)
    assert again["uploaded_objects"] == 0 and again["reused_objects"] == 74
    assert original == {
        p.relative_to(root).as_posix(): p.read_bytes() for p in snapshot.rglob("*") if p.is_file()
    }
    shutil.rmtree(snapshot)
    inventory.unlink()
    shutil.rmtree(work / "bundles")
    calls = []

    def job(root, command, bundle):
        calls.append((command, bundle))
        return worker.download(work, bundle, client)

    restored = bronze_transfer.restore(root, identity, worker=job)
    assert calls == [("download", identity)]
    assert restored["status"] == "restored_verified" and restored["complete"]
    assert restored["files"] == 73 and restored["verified_source_partitions"] == 70
    assert restored["original_source_read"] is False
    assert restored["primary_storage_migrated"] is False
    for name, raw in original.items():
        assert (Path(restored["restored_directory"]) / name).read_bytes() == raw
    with pytest.raises(ValueError, match="destination exists"):
        bronze_transfer.restore(
            root, identity, worker=lambda *a: pytest.fail("Unexpected network")
        )


def test_partial_publication_retries_reusing_verified_payloads(prepared):
    root, _, _, identity = prepared
    work, client = archive.workspace(root), MemoryClient()
    client.fail_at = 3
    with pytest.raises(OSError):
        worker.publish(work, identity, client)
    assert len(client.objects) == 3
    assert worker.prefix(identity) + "manifest.json" not in client.objects
    client.fail_at = None
    result = worker.publish(work, identity, client)
    assert result["uploaded_objects"] == 71 and result["reused_objects"] == 3


@pytest.mark.parametrize("problem", ["corrupt", "extra", "403", "timeout"])
def test_remote_conflicts_and_errors_abort_without_overwrite(prepared, problem):
    root, _, _, identity = prepared
    client, work = MemoryClient(), archive.workspace(root)
    worker.publish(work, identity, client)
    key = next(iter(client.objects))
    if problem == "corrupt":
        client.objects[key] = bytes([client.objects[key][0] ^ 1]) + client.objects[key][1:]
    elif problem == "extra":
        client.objects[worker.prefix(identity) + "extra.bin"] = b"extra"
    else:
        client.fail_status = (403, "AccessDenied") if problem == "403" else (504, "GatewayTimeout")
    before, puts = client.objects.copy(), len(client.puts)
    with pytest.raises((ValueError, ServiceError)):
        worker.publish(work, identity, client)
    assert client.objects == before and len(client.puts) == puts


def test_conditional_put_race_is_reverified(prepared):
    root, _, _, identity = prepared
    client = MemoryClient()
    client.race = True
    result = worker.publish(archive.workspace(root), identity, client)
    assert result["uploaded_objects"] == 0 and result["reused_objects"] == 74


@pytest.mark.parametrize(
    "problem",
    [
        "marker_missing",
        "marker_large",
        "marker_corrupt",
        "payload_missing",
        "payload_corrupt",
        "overrun",
        "truncated",
    ],
)
def test_download_rejects_invalid_remote_and_never_publishes_restore(prepared, problem):
    root, _, _, identity = prepared
    client, work = MemoryClient(), archive.workspace(root)
    worker.publish(work, identity, client)
    marker = worker.prefix(identity) + "manifest.json"
    payload = next(iter(client.objects))
    if problem == "marker_missing":
        del client.objects[marker]
    elif problem == "marker_large":
        client.objects[marker] = b"x" * (archive.BRONZE_MAX_MANIFEST_BYTES + 1)
    elif problem == "marker_corrupt":
        client.objects[marker] = b"x" * len(client.objects[marker])
    elif problem == "payload_missing":
        del client.objects[payload]
    elif problem == "payload_corrupt":
        client.objects[payload] = b"x" * len(client.objects[payload])
    else:
        client.body_override = client.objects[marker] + b"x" if problem == "overrun" else b"x"
    with pytest.raises(ValueError):
        bronze_transfer.restore(
            root, identity, worker=lambda *a: worker.download(work, identity, client)
        )
    assert not (work / "restored" / identity).exists()
    assert not list((work / "downloads").glob("bronze-*"))


def test_semantic_failure_stays_quarantined(prepared, monkeypatch):
    root, _, _, identity = prepared
    client, work = MemoryClient(), archive.workspace(root)
    worker.publish(work, identity, client)

    def reject(*args):
        raise ValueError("injected Bronze verification failure")

    monkeypatch.setattr(bronze_archive, "verify_directory", reject)
    with pytest.raises(ValueError, match="Bronze verification"):
        bronze_transfer.restore(
            root, identity, worker=lambda *a: worker.download(work, identity, client)
        )
    assert not (work / "restored" / identity).exists()
    assert len(list((work / "downloads").glob("bronze-*"))) == 1


def test_publish_checks_local_bronze_before_single_job(prepared):
    root, _, _, identity = prepared
    calls = []

    def job(root, command, bundle):
        calls.append((command, bundle))
        return {"status": "success", "bucket": worker.BUCKET, "bundle_id": bundle}

    result = bronze_transfer.publish(root, identity, worker=job)
    assert result["verified_source_partitions"] == 70
    assert calls == [("publish", identity)]
    (archive.workspace(root) / "bundles" / identity / archive.INVENTORY_PATH).write_text("{}")
    with pytest.raises(ValueError):
        bronze_transfer.publish(
            root, identity, worker=lambda *a: pytest.fail("Unexpected network")
        )


def test_compose_invoked_once_with_code_handshake(prepared, monkeypatch):
    root, _, _, identity = prepared
    env = root / "infra/observability/.env"
    env.parent.mkdir(parents=True)
    env.write_text("ignored test credentials")
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(
            command,
            0,
            json.dumps({"bundle_id": identity, "worker_code_sha256": worker.code_sha256()}),
        )

    monkeypatch.setattr(subprocess, "run", run)
    bronze_transfer.run_worker(root, "publish", identity)
    assert len(calls) == 1 and calls[0].count("storage-transfer") == 1
    assert "ignored test credentials" not in str(calls)
    assert "--expected-code-sha256" in calls[0] and "--no-deps" in calls[0]


def test_stale_image_fails_before_sdk_import_or_network(monkeypatch, capsys):
    monkeypatch.setattr(
        sys,
        "argv",
        ["worker", "publish", "--bundle-id", "a" * 64, "--expected-code-sha256", "old-image"],
    )
    monkeypatch.setattr(worker, "make_client", lambda: pytest.fail("SDK created before handshake"))
    with pytest.raises(SystemExit) as error:
        worker.main()
    assert error.value.code == 1 and "rebuild" in capsys.readouterr().err


def test_transport_rejects_pilot_manifest_before_network(tmp_path):
    (tmp_path / "data/releases").mkdir(parents=True)
    (tmp_path / "data/releases/file").write_bytes(b"opaque")
    identity = archive.prepare(tmp_path, ["data/releases"])
    with pytest.raises(ValueError, match="Bronze"):
        worker.publish(archive.workspace(tmp_path), identity, object())
