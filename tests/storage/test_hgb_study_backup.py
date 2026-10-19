"""Consistent SQLite and fit-artifact recovery with a pinned immutable dataset dependency."""

from contextlib import closing
import fcntl
import json
from pathlib import Path
import shutil
import sqlite3
import sys

import pytest
from scripts.storage import archive
from scripts.storage import development_gold_archive as data_storage
from scripts.storage import hgb_study_backup as storage

from fraud_detection_mlops.integrations import mlflow_tracking as tracking
from fraud_detection_mlops.modeling.experiments import candidate_training
from fraud_detection_mlops.modeling.experiments import hgb_optimization as hgb
from fraud_detection_mlops.modeling.experiments import temporal_development_data as development


@pytest.fixture(scope="module")
def built_study(silver_fixture, tmp_path_factory):
    root = tmp_path_factory.mktemp("study-backup-source")
    (root / "references").mkdir()
    shutil.copy2(silver_fixture / "inventory.json", root / archive.INVENTORY_PATH)
    policy = development.load_policy()
    policy["budget"].update(max_trials=2, max_fit_attempts=9)
    (root / archive.DEVELOPMENT_POLICY_PATH).write_bytes(archive.encoded(policy))
    with pytest.MonkeyPatch.context() as patch:
        provenance = lambda p: {"git_revision": "test", "files": {"test": "d" * 64}}
        patch.setattr(development, "committed_inputs", provenance)
        patch.setattr(hgb, "committed_inputs", provenance)
        patch.setattr(
            tracking,
            "log_candidate",
            lambda *a, **k: {"run_id": "offline-fit", "model_uri": "offline-only"},
        )
        prepared = development.prepare_data(
            silver_fixture / "silver",
            root / "data/processed/handbook",
            policy_path=root / archive.DEVELOPMENT_POLICY_PATH,
            inventory_path=root / archive.INVENTORY_PATH,
        )
        directory = Path(prepared["development_path"])
        result = hgb.optimize_hgb(
            directory,
            policy_path=root / archive.DEVELOPMENT_POLICY_PATH,
            tracking_root=root / "tracking",
            new_trials=2,
        )
        assert result["budget_finished"] and result["fit_attempts"] == 9
    return root, directory.relative_to(root)


@pytest.fixture
def source(built_study, tmp_path, monkeypatch):
    original, relative = built_study
    for name in ("data", "references"):
        shutil.copytree(original / name, tmp_path / name)
    (tmp_path / "pyproject.toml").touch()
    study = tmp_path / relative / "study"
    path = study / "report.json"
    report = json.loads(path.read_bytes())
    report["study_path"] = str(study.resolve())
    path.write_bytes(archive.encoded(report))
    data = data_storage.prepare_development(tmp_path, relative.name)
    monkeypatch.setattr(
        candidate_training, "fit_candidate", lambda *a, **k: pytest.fail("Training repeated")
    )
    monkeypatch.setattr(tracking, "log_candidate", lambda *a, **k: pytest.fail("MLflow accessed"))
    monkeypatch.setattr(
        development, "compute_features", lambda *a, **k: pytest.fail("Features recomputed")
    )
    return tmp_path, study, data


def prepare(source):
    root, study, data = source
    report = storage.prepare_study(root, data["bundle_id"])
    bundle = Path(report["manifest"]).parent
    document = json.loads((bundle / "manifest.json").read_bytes())
    return root, study, data, report, bundle, document


def test_preparation_preserves_originals_report_links_and_repeatable_identity(source):
    root, study, data = source
    before = {
        p.relative_to(root).as_posix(): p.read_bytes() for p in study.rglob("*") if p.is_file()
    }
    result = storage.prepare_study(root, data["bundle_id"])
    assert result["sqlite_online_backup"] and result["sqlite_integrity_verified"]
    assert result["trials_used"] == result["completed_trials"] == 2 and result["fit_attempts"] == 9
    assert result["dataset_rows"] == 189 and result["dataset_partitions"] == 63
    assert result["dataset_bundle_id"] == data["bundle_id"]
    assert result["development_manifest_sha256"] == data["development_manifest_sha256"]
    assert result["s3_accessed"] is False and result["transfer_implemented"] is True
    bundle = Path(result["manifest"]).parent
    copy_study = bundle / study.relative_to(root)
    assert (copy_study / "report.json").read_bytes() == (study / "report.json").read_bytes()
    assert not (copy_study / ".writer.lock").exists() and not list(copy_study.glob("*-wal"))
    assert all((root / name).read_bytes() == raw for name, raw in before.items())
    assert storage.prepare_study(root, data["bundle_id"])["bundle_id"] == result["bundle_id"]


def test_offline_verification_after_originals_removed_with_relocated_dependency(
    source, tmp_path_factory
):
    root, study, data, report, bundle, _ = prepare(source)
    isolated = tmp_path_factory.mktemp("isolated-study-recovery")
    work = archive.workspace(isolated)
    shutil.copytree(bundle, work / "bundles" / report["bundle_id"])
    shutil.copytree(Path(data["manifest"]).parent, work / "restored" / data["bundle_id"])
    shutil.rmtree(root / "data/processed")
    shutil.rmtree(root / "references")
    verified = storage.verify_bundle(isolated, report["bundle_id"])
    assert verified["trials_used"] == 2 and verified["fit_attempts"] == 9
    assert verified["historical_study_path"] == str(study.resolve())
    assert verified["sqlite_logical_sha256"] == report["sqlite_logical_sha256"]
    assert not (isolated / "data/processed").exists()


def test_live_writer_lock_is_respected_without_modifying_source(source):
    root, study, data = source
    before = (study / "study.db").read_bytes()
    with (study / ".writer.lock").open("rb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match="writer is active"):
            storage.prepare_study(root, data["bundle_id"])
    assert (study / "study.db").read_bytes() == before
    assert not list((archive.workspace(root) / "bundles").glob("*/*study*"))


@pytest.mark.parametrize(
    "problem",
    [
        "missing_lock",
        "missing_report",
        "corrupt_database",
        "unknown_file",
        "nested_database",
        "nested_sidecar",
        "unfinished_trial",
    ],
)
def test_invalid_or_incomplete_study_never_publishes_bundle(source, problem):
    root, study, data = source
    existing = set((archive.workspace(root) / "bundles").iterdir())
    if problem == "missing_lock":
        (study / ".writer.lock").unlink()
    elif problem == "missing_report":
        (study / "report.json").unlink()
    elif problem == "corrupt_database":
        (study / "study.db").write_bytes(b"corrupted SQLite")
    elif problem == "unknown_file":
        (study / "unreviewed.bin").write_bytes(b"unknown")
    elif problem in {"nested_database", "nested_sidecar"}:
        name = "study.db" if problem == "nested_database" else "study.db-wal"
        (study / "models" / name).write_bytes(b"unreviewed nested state")
    else:
        with closing(sqlite3.connect(study / "study.db")) as connection:
            connection.execute("UPDATE trials SET state='RUNNING' WHERE number=0")
            connection.commit()
    with pytest.raises((ValueError, sqlite3.Error)):
        storage.prepare_study(root, data["bundle_id"])
    assert set((archive.workspace(root) / "bundles").iterdir()) == existing
    assert not list(archive.workspace(root).glob("hgb-snapshot-*"))


def test_snapshot_api_includes_committed_wal_and_leaves_standalone_database(tmp_path):
    source = tmp_path / "source.db"
    target = tmp_path / "snapshot.db"
    with closing(sqlite3.connect(source)) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("CREATE TABLE changes(id INTEGER PRIMARY KEY, value TEXT)")
        writer.execute("INSERT INTO changes VALUES(1,'committed WAL row')")
        writer.commit()
        assert Path(str(source) + "-wal").stat().st_size > 0
        logical = storage.backup_sqlite(source, target)
        with closing(storage.readonly(target)) as copy:
            assert copy.execute("SELECT * FROM changes").fetchall() == [(1, "committed WAL row")]
            assert storage.logical_digest(copy) == logical
    assert not Path(str(target) + "-wal").exists()


def test_noncooperative_artifact_mutation_aborts_snapshot(source, monkeypatch):
    root, study, data = source
    original = storage.backup_sqlite

    def changed(*args):
        result = original(*args)
        (study / "protocol.json").write_bytes((study / "protocol.json").read_bytes() + b" ")
        return result

    monkeypatch.setattr(storage, "backup_sqlite", changed)
    with pytest.raises(ValueError, match="changed during snapshot"):
        storage.prepare_study(root, data["bundle_id"])
    assert len(list((archive.workspace(root) / "bundles").iterdir())) == 1


def refresh(bundle, document):
    study = next(bundle.glob("data/processed/handbook/*/development_gold_v2/*/study"))
    marker_path = study / storage.SNAPSHOT_NAME
    marker = json.loads(marker_path.read_bytes())
    with closing(storage.readonly(study / "study.db")) as connection:
        marker["sqlite_logical_sha256"] = storage.logical_digest(connection)
    marker["study_report_sha256"] = archive.digest(study / "report.json")
    marker_path.write_bytes(archive.encoded(marker))
    for row in document["files"]:
        path = bundle / row["path"]
        row.update(bytes=path.stat().st_size, sha256=archive.digest(path))
    document["provenance"].update(
        study_snapshot_sha256=archive.digest(study / "study.db"),
        snapshot_receipt_sha256=archive.digest(marker_path),
    )
    archive.validate_manifest(document)
    return study


@pytest.mark.parametrize(
    "problem", ["trial_value", "saved_metrics", "model_bytes", "report_decision", "dataset_link"]
)
def test_semantic_corruption_rejected_after_snapshot_and_archive_hashes_refreshed(source, problem):
    root, study, data, _, bundle, document = prepare(source)
    copy_study = bundle / study.relative_to(root)
    if problem == "trial_value":
        with closing(sqlite3.connect(copy_study / "study.db")) as connection:
            connection.execute(
                "UPDATE trial_values SET value=value+0.25 WHERE trial_id=(SELECT trial_id FROM trials WHERE number=0)"
            )
            connection.commit()
    elif problem == "saved_metrics":
        path = next(copy_study.glob("models/trial-*/fold_1/metrics.json"))
        metrics = json.loads(path.read_bytes())
        metrics["average_precision"] += 0.1
        path.write_bytes(archive.encoded(metrics))
    elif problem == "model_bytes":
        next(copy_study.glob("models/trial-*/fold_1/model.skops")).write_bytes(b"changed model")
    elif problem == "report_decision":
        path = copy_study / "report.json"
        report = json.loads(path.read_bytes())
        report["decision"] = "tampered"
        path.write_bytes(archive.encoded(report))
    else:
        document["provenance"]["dataset_bundle_id"] = "a" * 64
    refresh(bundle, document)
    with pytest.raises(ValueError):
        storage.verify_snapshot(bundle, document, storage.dataset_bundle(root, data["bundle_id"]))


def test_profile_does_not_enable_arbitrary_database_or_transport(source):
    root, _, _, report, _, document = prepare(source)
    from scripts.storage import bronze_s3_worker

    assert bronze_s3_worker.layer(archive.HGB_STUDY_SCHEMA) == "hgb-study"
    assert bronze_s3_worker.bucket_for(archive.HGB_STUDY_SCHEMA) == "fraud-artifacts"
    with pytest.raises(ValueError, match="requires semantic verification"):
        archive.prepare(
            root, [], schema=archive.HGB_STUDY_SCHEMA, provenance=document["provenance"]
        )
    assert storage.verify_bundle(root, report["bundle_id"])["status"] == "verified_locally"
    extra = {"path": "data/other.db", "bytes": 1, "sha256": "a" * 64}
    document["files"] = sorted([*document["files"], extra], key=lambda row: row["path"])
    with pytest.raises(ValueError, match="unrelated path"):
        archive.validate_manifest(document)


def test_cli_requires_dependency_and_creates_receipt(source, monkeypatch, capsys):
    root, _, data = source
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "hgb_study_backup",
            "--project-root",
            str(root),
            "prepare",
            "--dataset-bundle-id",
            data["bundle_id"],
        ],
    )
    storage.main()
    result = json.loads(capsys.readouterr().out)
    assert Path(result["report"]).is_file() and result["refit"] is False
    monkeypatch.setattr(sys, "argv", ["hgb_study_backup", "--project-root", str(root), "prepare"])
    with pytest.raises(SystemExit) as exc:
        storage.main()
    assert exc.value.code == 2
