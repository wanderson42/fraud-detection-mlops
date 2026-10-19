"""Study S3 recovery uses artifact bucket and a locally verified dataset dependency."""

import json
from pathlib import Path
import shutil
import sys

import pytest
from scripts.storage import archive
from scripts.storage import bronze_s3_worker as worker
from scripts.storage import hgb_study_backup as backup
from scripts.storage import hgb_study_transfer as transfer
from tests.storage import test_hgb_study_backup as fixtures
from tests.storage.test_bronze_transfer import MemoryClient

built_study = fixtures.built_study
source = fixtures.source


def prepared(source):
    root, study, data = source
    report = backup.prepare_study(root, data["bundle_id"])
    return root, study, data, report


def job(client):
    return lambda root, command, identity, *, schema: (
        worker.publish if command == "publish" else worker.download
    )(archive.workspace(root), identity, client, schema)


def test_publication_repeat_and_complete_recovery_without_originals_or_prepared_study(source):
    root, study, data, report = prepared(source)
    identity = report["bundle_id"]
    client = MemoryClient(expected_bucket="fraud-artifacts")
    before = (study / "report.json").read_bytes()
    first = transfer.publish(root, identity, worker=job(client))
    assert first["bucket"] == "fraud-artifacts" and first["prefix"] == f"hgb-study/v1/{identity}/"
    assert first["uploaded_objects"] == first["verified_objects"] == 53
    assert client.puts[-1] == first["prefix"] + "manifest.json"
    assert transfer.publish(root, identity, worker=job(client))["uploaded_objects"] == 0
    dep = Path(data["manifest"]).parent
    restored_dep = archive.workspace(root) / "restored" / data["bundle_id"]
    restored_dep.parent.mkdir()
    shutil.move(dep, restored_dep)
    shutil.rmtree(root / "data/processed")
    shutil.rmtree(root / "references")
    shutil.rmtree(Path(report["manifest"]).parent)
    restored = transfer.restore(root, identity, data["bundle_id"], worker=job(client))
    assert restored["status"] == "restored_verified" and restored["files"] == 52
    assert (
        restored["trials_used"] == restored["completed_trials"] == 2
        and restored["fit_attempts"] == 9
    )
    assert restored["dataset_rows"] == 189 and restored["dataset_bundle_id"] == data["bundle_id"]
    assert restored["sqlite_logical_sha256"] == report["sqlite_logical_sha256"]
    directory = Path(restored["restored_directory"])
    archived_study = directory / study.relative_to(root)
    assert (archived_study / "report.json").read_bytes() == before
    assert (
        restored["original_source_read"] is False
        and restored["training_resumption_validated"] is False
    )
    assert (
        backup.verify_snapshot(
            directory,
            json.loads((directory / "manifest.json").read_bytes()),
            backup.dataset_bundle(root, data["bundle_id"]),
        )["fit_attempts"]
        == 9
    )
    with pytest.raises(ValueError, match="destination exists"):
        transfer.restore(
            root,
            identity,
            data["bundle_id"],
            worker=lambda *a, **k: pytest.fail("Unexpected network"),
        )


def test_missing_dependency_fails_before_s3(source):
    root, _, data, report = prepared(source)
    shutil.rmtree(Path(data["manifest"]).parent)
    with pytest.raises(ValueError, match="required locally"):
        transfer.restore(
            root,
            report["bundle_id"],
            data["bundle_id"],
            worker=lambda *a, **k: pytest.fail("Unexpected network"),
        )


def test_wrong_dependency_preserves_quarantine(source, monkeypatch):
    root, _, data, report = prepared(source)
    client = MemoryClient(expected_bucket="fraud-artifacts")
    transfer.publish(root, report["bundle_id"], worker=job(client))
    # A valid local dependency can still differ from the dependency declared by the study.
    dependency = backup.dataset_bundle(root, data["bundle_id"])
    monkeypatch.setattr(backup, "dataset_bundle", lambda *a, **k: dependency)
    with pytest.raises(ValueError, match="another dataset"):
        transfer.restore(root, report["bundle_id"], "a" * 64, worker=job(client))
    assert len(list((archive.workspace(root) / "downloads").glob("hgb-study-*"))) == 1
    assert not (archive.workspace(root) / "restored" / report["bundle_id"]).exists()


def test_partial_publication_resume_and_download_corruption_cleanup(source):
    root, _, data, report = prepared(source)
    identity = report["bundle_id"]
    client = MemoryClient(expected_bucket="fraud-artifacts")
    client.fail_at = 3
    with pytest.raises(OSError):
        transfer.publish(root, identity, worker=job(client))
    assert (
        worker.prefix(identity, archive.HGB_STUDY_SCHEMA) + "manifest.json" not in client.objects
    )
    client.fail_at = None
    assert transfer.publish(root, identity, worker=job(client))["reused_objects"] == 3
    client.body_override = b"corrupt response"
    with pytest.raises(ValueError):
        transfer.restore(root, identity, data["bundle_id"], worker=job(client))
    assert not list((archive.workspace(root) / "downloads").glob("hgb-study-*"))


def test_semantic_failure_preserves_download_for_review(source, monkeypatch):
    root, _, data, report = prepared(source)
    client = MemoryClient(expected_bucket="fraud-artifacts")
    transfer.publish(root, report["bundle_id"], worker=job(client))

    def reject(*a, **k):
        raise ValueError("semantic rejection")

    monkeypatch.setattr(backup, "verify_snapshot", reject)
    with pytest.raises(ValueError, match="semantic rejection"):
        transfer.restore(root, report["bundle_id"], data["bundle_id"], worker=job(client))
    assert len(list((archive.workspace(root) / "downloads").glob("hgb-study-*"))) == 1
    assert not (archive.workspace(root) / "restored" / report["bundle_id"]).exists()


@pytest.mark.parametrize(
    "field,value",
    [
        ("bucket", "fraud-datasets"),
        ("prefix", "other/v1/"),
        ("candidate", "downloads/bronze-fake"),
    ],
)
def test_wrong_worker_receipt_rejected(source, field, value):
    root, _, data, report = prepared(source)
    receipt = {
        "status": "downloaded_quarantined",
        "bundle_id": report["bundle_id"],
        "bucket": "fraud-artifacts",
        "prefix": worker.prefix(report["bundle_id"], archive.HGB_STUDY_SCHEMA),
        "candidate": "downloads/hgb-study-fake",
    }
    receipt[field] = value
    with pytest.raises(ValueError, match="Unexpected HGB study quarantine"):
        transfer.restore(
            root, report["bundle_id"], data["bundle_id"], worker=lambda *a, **k: receipt
        )


def test_cli_requires_declared_dataset_for_restore(source, monkeypatch):
    root, _, _, report = prepared(source)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "hgb_study_transfer",
            "--project-root",
            str(root),
            "restore",
            "--bundle-id",
            report["bundle_id"],
        ],
    )
    with pytest.raises(SystemExit) as exc:
        transfer.main()
    assert exc.value.code == 2
    assert worker.bucket_for(archive.HGB_STUDY_SCHEMA) == "fraud-artifacts"
    assert all(worker.bucket_for(schema) == "fraud-datasets" for schema in archive.DATASET_SCHEMAS)
