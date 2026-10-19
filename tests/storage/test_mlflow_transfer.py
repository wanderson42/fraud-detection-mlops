"""Prepared MLflow snapshot transport and isolated recovery, without Docker or S3."""

from contextlib import closing
import hashlib
import json
from pathlib import Path
import shutil
import socket
import sqlite3

import pytest
from scripts.storage import archive
from scripts.storage import bronze_s3_worker as transport
from scripts.storage import mlflow_backup as backup
from scripts.storage import mlflow_transfer as transfer
from tests.storage import test_mlflow_backup as fixtures
from tests.storage.test_bronze_transfer import MemoryClient, ServiceError

tracking_store = fixtures.tracking_store
two_stores = fixtures.two_stores
source_contents = fixtures.source_contents


def job(client):
    return lambda root, command, identity, *, schema: (
        transport.publish if command == "publish" else transport.download
    )(archive.workspace(root), identity, client, schema)


@pytest.fixture
def prepared(two_stores):
    root, *_ = two_stores
    (root / "pyproject.toml").write_text("# test project root")
    return root, backup.prepare(root, writers_stopped=True)


def test_full_roundtrip_idempotent_manifest_last_and_independent_of_all_sources(
    prepared, monkeypatch
):
    root, report = prepared
    identity = report["bundle_id"]
    work, client = archive.workspace(root), MemoryClient(expected_bucket="fraud-artifacts")
    before = source_contents(root)
    monkeypatch.setattr(socket.socket, "connect", lambda *args: pytest.fail("network used"))
    result = transfer.publish(root, identity, worker=job(client))
    assert result["uploaded_objects"] == result["verified_objects"] == report["archive_files"] + 1
    assert result["store_count"] == 2 and result["total_runs"] == 3
    assert result["prefix"] == f"mlflow-tracking/v1/{identity}/"
    assert (
        result["transfer_implemented"]
        and result["s3_accessed"]
        and not result["original_source_read"]
    )
    assert client.puts[-1] == result["prefix"] + "manifest.json"
    assert transfer.publish(root, identity, worker=job(client))["uploaded_objects"] == 0
    assert source_contents(root) == before
    expected = {
        p.relative_to(Path(report["manifest"]).parent).as_posix(): p.read_bytes()
        for p in Path(report["manifest"]).parent.rglob("*")
        if p.is_file()
    }
    shutil.rmtree(root / "data/tracking")
    shutil.rmtree(root / "data/processed")
    shutil.rmtree(root / "data/examples")
    shutil.rmtree(work / "bundles")
    restored = transfer.restore(root, identity, worker=job(client))
    assert restored["status"] == "restored_verified"
    assert restored["files"] == report["archive_files"]
    assert restored["total_logged_models"] == 2
    assert restored["logical_table_contents_verified"] and restored["sqlite_foreign_keys_verified"]
    assert not restored["original_source_read"] and not restored["mlflow_initialized"]
    assert not restored["model_reload_validated"] and not restored["migration_implemented"]
    target = Path(restored["restored_directory"])
    assert (target / "data/tracking/artifacts/empty-directory").is_dir()
    assert (target / backup.AUXILIARY / "artifacts/empty").is_dir()
    assert {
        p.relative_to(target).as_posix(): p.read_bytes() for p in target.rglob("*") if p.is_file()
    } == expected
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in target.rglob("*") if p.is_file())
    with pytest.raises(ValueError, match="destination exists"):
        transfer.restore(root, identity, worker=lambda *a, **k: pytest.fail("Unexpected network"))


def test_partial_publication_resumes_and_reuses_verified_objects(prepared):
    root, report = prepared
    client = MemoryClient(expected_bucket="fraud-artifacts")
    client.fail_at = 3
    with pytest.raises(OSError):
        transfer.publish(root, report["bundle_id"], worker=job(client))
    assert len(client.objects) == 3
    assert not any(key.endswith("/manifest.json") for key in client.objects)
    client.fail_at = None
    result = transfer.publish(root, report["bundle_id"], worker=job(client))
    assert result["reused_objects"] == 3
    assert result["uploaded_objects"] == report["archive_files"] + 1 - 3


@pytest.mark.parametrize("problem", ["corrupt", "missing", "extra", "forbidden"])
def test_bad_remote_set_never_becomes_restored_snapshot(prepared, problem):
    root, report = prepared
    identity = report["bundle_id"]
    client = MemoryClient(expected_bucket="fraud-artifacts")
    transfer.publish(root, identity, worker=job(client))
    key = next(iter(client.objects))
    if problem == "corrupt":
        client.objects[key] = b"x" * len(client.objects[key])
    elif problem == "missing":
        del client.objects[key]
    elif problem == "extra":
        client.objects[transport.prefix(identity, backup.SCHEMA) + "extra"] = b"x"
    else:
        client.fail_status = (403, "AccessDenied")
    with pytest.raises((ValueError, ServiceError)):
        transfer.restore(root, identity, worker=job(client))
    assert not (archive.workspace(root) / "restored" / identity).exists()
    assert not list((archive.workspace(root) / "downloads").glob("mlflow-tracking-*"))


def test_corrupt_local_snapshot_stops_before_worker(prepared):
    root, report = prepared
    target = Path(report["manifest"]).parent / backup.AUXILIARY / "mlflow.db"
    with sqlite3.connect(target) as connection:
        connection.execute("UPDATE metrics SET value=99")
    with pytest.raises(ValueError, match="integrity mismatch"):
        transfer.publish(
            root, report["bundle_id"], worker=lambda *a, **k: pytest.fail("Unexpected network")
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("bucket", "fraud-datasets"),
        ("prefix", "other/"),
        ("candidate", "downloads/bronze-fake"),
        ("candidate", "../escape"),
        ("bundle_id", "a" * 64),
    ],
)
def test_mismatched_download_receipt_rejected_before_file_access(prepared, field, value):
    root, report = prepared
    identity = report["bundle_id"]
    result = {
        "status": "downloaded_quarantined",
        "bundle_id": identity,
        "bucket": "fraud-artifacts",
        "prefix": transport.prefix(identity, backup.SCHEMA),
        "candidate": "downloads/mlflow-tracking-fake",
    }
    result[field] = value
    with pytest.raises(ValueError, match="Unexpected MLflow"):
        transfer.restore(root, identity, worker=lambda *a, **k: result)


def test_semantic_failure_preserves_quarantine_and_never_publishes(prepared, monkeypatch):
    root, report = prepared
    client = MemoryClient(expected_bucket="fraud-artifacts")
    transfer.publish(root, report["bundle_id"], worker=job(client))

    def reject(*a, **k):
        raise ValueError("independent store verification failed")

    monkeypatch.setattr(backup, "verify_snapshot", reject)
    with pytest.raises(ValueError, match="independent store"):
        transfer.restore(root, report["bundle_id"], worker=job(client))
    work = archive.workspace(root)
    assert len(list((work / "downloads").glob("mlflow-tracking-*"))) == 1
    assert not (work / "restored" / report["bundle_id"]).exists()


def test_remote_sqlite_semantic_corruption_rejected_even_with_valid_object_hashes(prepared):
    root, report = prepared
    directory = Path(report["manifest"]).parent
    db = directory / backup.AUXILIARY / "mlflow.db"
    with closing(sqlite3.connect(db)) as connection:
        connection.execute("UPDATE metrics SET value=99")
        connection.commit()
    document = json.loads(Path(report["manifest"]).read_bytes())
    for row in document["files"]:
        if row["path"] == backup.AUXILIARY + "/mlflow.db":
            row.update(bytes=db.stat().st_size, sha256=archive.digest(db))
            document["provenance"]["interface_smoke_database_sha256"] = row["sha256"]
    identity = backup.manifest_id(document)
    raw = archive.encoded(document)
    # Build remote bytes directly to exercise host semantic verification, bypassing publisher.
    client = MemoryClient(expected_bucket="fraud-artifacts")
    prefix = transport.prefix(identity, backup.SCHEMA)
    client.objects = {
        prefix + row["path"]: (directory / row["path"]).read_bytes() for row in document["files"]
    }
    client.objects[prefix + "manifest.json"] = raw
    assert hashlib.sha256(raw).hexdigest() == identity
    with pytest.raises(ValueError, match="logical content"):
        transfer.restore(root, identity, worker=job(client))
    assert not (archive.workspace(root) / "restored" / identity).exists()
    assert len(list((archive.workspace(root) / "downloads").glob("mlflow-tracking-*"))) == 1


def test_transport_profile_accepts_above_512_files_without_widening_dataset_budget(two_stores):
    root, *_ = two_stores
    for number in range(513):
        (root / "data/tracking/artifacts" / f"payload-{number:04d}").write_bytes(b"x")
    report = backup.prepare(root, writers_stopped=True)
    assert report["archive_files"] > 512
    client = MemoryClient(expected_bucket="fraud-artifacts")
    result = transfer.publish(root, report["bundle_id"], worker=job(client))
    assert result["verified_objects"] == report["archive_files"] + 1
    assert archive.budget(archive.BRONZE_SCHEMA)[0] == 512


def test_cli_receipt_uses_managed_path_and_restrictive_permissions(prepared, monkeypatch, capsys):
    root, report = prepared
    client = MemoryClient(expected_bucket="fraud-artifacts")
    publish = transfer.publish
    monkeypatch.setattr(
        transfer, "publish", lambda root, identity: publish(root, identity, worker=job(client))
    )
    assert (
        transfer.main(["publish", "--project-root", str(root), "--bundle-id", report["bundle_id"]])
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    receipt = Path(result["report"])
    assert receipt.is_relative_to(root / "data/storage/receipts")
    assert receipt.stat().st_mode & 0o777 == 0o600
    assert result["transfer_implemented"]
