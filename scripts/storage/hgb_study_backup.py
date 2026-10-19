"""Prepare and verify an offline consistent HGB study backup; no S3 transfer or training."""

import argparse
from contextlib import closing, contextmanager
import fcntl
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import time

import optuna
from scripts.storage import archive, bronze_s3_worker, development_gold_archive

from fraud_detection_mlops.modeling.experiments import hgb_optimization as hgb

SNAPSHOT_NAME = "storage-snapshot.json"
SNAPSHOT_SCHEMA = "fraud_hgb_study_snapshot_v1"
SIDECARS = {"study.db-wal", "study.db-shm", "study.db-journal"}


def dataset_bundle(root, identity):
    identity = archive.bundle_id(identity)
    work = archive.workspace(root)
    for family in ("bundles", "restored"):
        directory = archive.checked_path(work, Path(family) / identity)
        if directory.exists():
            document, _ = bronze_s3_worker.manifest_at(
                directory, identity, archive.DEVELOPMENT_SCHEMA
            )
            verified = development_gold_archive.verify_directory(directory, document)
            return directory, document, verified
    raise ValueError("Verified development bundle/restore is required locally before study backup")


def dataset_path(document):
    p = document["provenance"]
    return (
        Path("data/processed/handbook")
        / p["source_commit"]
        / "development_gold_v2"
        / p["development_run_id"]
    )


@contextmanager
def writer_lock(study):
    path = archive.checked_path(study, Path(".writer.lock"))
    if not path.is_file():
        raise ValueError("Existing study writer lock is required; do not create or bypass it")
    with path.open("rb") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("Study writer is active; stop optimization before backup") from error
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def source_files(root, study):
    state = {}
    for path in study.rglob("*"):
        path = archive.checked_path(root, path.relative_to(root))
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError("Study contains a missing or nonregular input")
        if path.parent == study and path.name in {".writer.lock", "study.db-shm"}:
            continue
        info = path.stat()
        state[path.relative_to(root).as_posix()] = (info.st_size, info.st_mtime_ns, info.st_ino)
    required = {
        str((study / name).relative_to(root))
        for name in ("study.db", "protocol.json", "report.json")
    }
    if not required <= state.keys() or (study / SNAPSHOT_NAME).exists():
        raise ValueError("Study metadata missing or unexpected snapshot receipt in original study")
    return state


def readonly(path):
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=3)


def logical_digest(connection):
    if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise ValueError("SQLite integrity check failed")
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise ValueError("SQLite foreign key check failed")
    digest = hashlib.sha256()
    for line in connection.iterdump():
        digest.update(line.encode() + b"\n")
    return digest.hexdigest()


def backup_sqlite(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise ValueError("SQLite backup destination already exists")
    deadline = time.monotonic() + 30

    def progress(status, remaining, total):
        if time.monotonic() > deadline or total * page_size > archive.BRONZE_MAX_BYTES:
            raise ValueError("SQLite snapshot exceeded its time or page budget")

    with closing(readonly(source)) as original:
        page_size = original.execute("PRAGMA page_size").fetchone()[0]
        original.execute("BEGIN")
        before = logical_digest(original)
        with closing(sqlite3.connect(destination)) as copy:
            original.backup(copy, pages=256, progress=progress, sleep=0.05)
            copy.execute("PRAGMA journal_mode=DELETE")
            copy.commit()
            if logical_digest(copy) != before:
                raise ValueError("SQLite logical content differs after online backup")
        original.rollback()
    with closing(readonly(source)) as original:
        if logical_digest(original) != before:
            raise ValueError("SQLite source changed during backup; preserve inputs and retry")
    return before


def verify_snapshot(directory, document, dependency):
    """Reconcile saved trials/metrics with the frozen verifier in a disposable relocated view."""
    archive.validate_manifest(document)
    if document["schema"] != archive.HGB_STUDY_SCHEMA:
        raise ValueError("Expected an HGB study snapshot bundle")
    archive.verify_files(directory, document)
    expected = {row["path"] for row in document["files"]}
    if (directory / "manifest.json").exists():
        expected.add("manifest.json")
    observed = set()
    for path in directory.rglob("*"):
        path = archive.checked_path(directory, path.relative_to(directory))
        if path.is_file():
            observed.add(path.relative_to(directory).as_posix())
    if observed != expected:
        raise ValueError("Unexpected or missing study backup files")
    dep_dir, dep_document, verified_data = dependency
    p = document["provenance"]
    if p["dataset_bundle_id"] != archive.validate_manifest(dep_document):
        raise ValueError("Study backup refers to another dataset bundle")
    for key in (
        "source_commit",
        "development_run_id",
        "inventory_sha256",
        "policy_sha256",
        "development_manifest_sha256",
    ):
        if p[key] != dep_document["provenance"][key]:
            raise ValueError("Study backup and dataset dependency differ")
    relative = dataset_path(dep_document)
    study = directory / relative / "study"
    marker = json.loads((study / SNAPSHOT_NAME).read_bytes())
    if marker != {
        "schema": SNAPSHOT_SCHEMA,
        "method": "sqlite_online_backup_with_study_writer_lock",
        "dataset_bundle_id": p["dataset_bundle_id"],
        "development_run_id": p["development_run_id"],
        "development_manifest_sha256": p["development_manifest_sha256"],
        "sqlite_logical_sha256": marker.get("sqlite_logical_sha256"),
        "original_study_path": marker.get("original_study_path"),
        "study_report_sha256": archive.digest(study / "report.json"),
    }:
        raise ValueError("Study snapshot receipt differs from archived content")
    logical = marker["sqlite_logical_sha256"]
    archive.bundle_id(logical)
    original_path = marker["original_study_path"]
    if (
        not isinstance(original_path, str)
        or not Path(original_path).is_absolute()
        or not original_path.endswith("/" + (relative / "study").as_posix())
    ):
        raise ValueError("Invalid historical study path")
    saved_report = json.loads((study / "report.json").read_bytes())
    if (
        saved_report.get("study_path") != original_path
        or saved_report.get("budget_finished") is not True
    ):
        raise ValueError("Only finished historical studies with matching origin can be archived")
    with closing(readonly(study / "study.db")) as connection:
        if logical_digest(connection) != logical:
            raise ValueError("SQLite snapshot logical digest differs")
    # Optuna may initialize SQLAlchemy metadata; never run it on original or archived SQLite.
    with tempfile.TemporaryDirectory(prefix="verify-hgb-", dir=directory.parent) as temp:
        temp = Path(temp)
        view = temp / relative
        shutil.copytree(dep_dir / relative, view)
        shutil.copytree(study, view / "study")
        policy = temp / archive.DEVELOPMENT_POLICY_PATH
        policy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(directory / archive.DEVELOPMENT_POLICY_PATH, policy)
        expected_report = dict(saved_report, study_path=str((view / "study").resolve()))
        (view / "study/report.json").write_bytes(archive.encoded(expected_report))
        loaded = optuna.load_study(
            study_name=hgb.EXPERIMENT,
            storage="sqlite:///" + str((view / "study/study.db").resolve()),
        )
        if any(not trial.state.is_finished() for trial in loaded.trials):
            raise ValueError("Study has unfinished trials; no coherent completed snapshot")
        report = hgb.verify_hgb_optimization(view / "study", policy_path=policy)
    # Only the disposable view's study_path was adapted; archived report and tracking stay unchanged.
    report["study_path"] = original_path
    return {
        "trials_used": report["trials_used"],
        "completed_trials": report["completed_trials"],
        "failed_trials": report["failed_trials"],
        "fit_attempts": report["fit_attempts"],
        "budget_finished": report["budget_finished"],
        "decision": report["decision"],
        "sqlite_logical_sha256": logical,
        "dataset_rows": verified_data["rows"],
        "dataset_partitions": verified_data["verified_partitions"],
        "dataset_bundle_id": p["dataset_bundle_id"],
        "development_run_id": p["development_run_id"],
        "development_manifest_sha256": p["development_manifest_sha256"],
        "historical_study_path": original_path,
    }


def prepare_study(root, dataset_id):
    root = root.resolve()
    dataset_id = archive.bundle_id(dataset_id)
    dependency = dataset_bundle(root, dataset_id)
    dep_dir, dep_document, _ = dependency
    relative = dataset_path(dep_document)
    study = archive.checked_path(root, relative / "study")
    work = archive.workspace(root)
    with writer_lock(study):
        before = source_files(root, study)
        sources = [
            name
            for name in before
            if Path(name).parent != study.relative_to(root) or Path(name).name not in SIDECARS
        ]
        total = sum(before[name][0] for name in sources)
        if len(sources) + 4 > archive.BRONZE_MAX_FILES or total > archive.BRONZE_MAX_BYTES:
            raise ValueError("Study exceeds the existing snapshot budget")
        data_bytes = sum(row["bytes"] for row in dep_document["files"])
        if shutil.disk_usage(work).free < total * 4 + data_bytes * 2 + 16 * 1024 * 1024:
            raise ValueError(
                "Insufficient space for snapshot, staging and disposable verification view"
            )
        with tempfile.TemporaryDirectory(prefix="hgb-snapshot-", dir=work) as temp:
            temp = Path(temp)
            copy_study = temp / relative / "study"
            logical = backup_sqlite(study / "study.db", copy_study / "study.db")
            for name in sources:
                if Path(name) == (study / "study.db").relative_to(root):
                    continue
                src = archive.checked_path(root, Path(name))
                target = temp / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, target)
            for name in [
                archive.INVENTORY_PATH,
                archive.DEVELOPMENT_POLICY_PATH,
                (relative / "manifest.json").as_posix(),
            ]:
                target = temp / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(dep_dir / name, target)
            marker = {
                "schema": SNAPSHOT_SCHEMA,
                "method": "sqlite_online_backup_with_study_writer_lock",
                "dataset_bundle_id": dataset_id,
                "development_run_id": dep_document["provenance"]["development_run_id"],
                "development_manifest_sha256": dep_document["provenance"][
                    "development_manifest_sha256"
                ],
                "sqlite_logical_sha256": logical,
                "original_study_path": str(study.resolve()),
                "study_report_sha256": archive.digest(copy_study / "report.json"),
            }
            (copy_study / SNAPSHOT_NAME).write_bytes(archive.encoded(marker))
            provenance = dict(
                dep_document["provenance"],
                dataset_bundle_id=dataset_id,
                study_snapshot_sha256=archive.digest(copy_study / "study.db"),
                snapshot_receipt_sha256=archive.digest(copy_study / SNAPSHOT_NAME),
            )
            selected = [p.relative_to(temp).as_posix() for p in temp.rglob("*") if p.is_file()]
            verified = None

            def check(staging, document):
                nonlocal verified
                if source_files(root, study) != before:
                    raise ValueError(
                        "Study files changed during snapshot; preserve source and retry"
                    )
                verified = verify_snapshot(staging, document, dependency)

            identity = archive.prepare(
                temp,
                selected,
                schema=archive.HGB_STUDY_SCHEMA,
                provenance=provenance,
                verify_staged=check,
            )
            local = temp / "data/storage/bundles" / identity
            document = json.loads((local / "manifest.json").read_bytes())
            target = archive.checked_path(work, Path("bundles") / identity)
            if target.exists():
                old = json.loads((target / "manifest.json").read_bytes())
                archive.validate_manifest(old, identity)
                verified = verify_snapshot(target, old, dependency)
            else:
                target.parent.mkdir(exist_ok=True)
                local.rename(target)
    return verified | {
        "status": "prepared_verified_locally",
        "schema": archive.HGB_STUDY_SCHEMA,
        "bundle_id": identity,
        "manifest": str(target / "manifest.json"),
        "archive_files": len(document["files"]),
        "bytes": sum(row["bytes"] for row in document["files"]),
        "complete": True,
        "sqlite_integrity_verified": True,
        "sqlite_foreign_keys_verified": True,
        "sqlite_online_backup": True,
        "study_writer_lock_acquired": True,
        "report_bytes_preserved": True,
        "tracking_references_preserved": True,
        "verification_path_adaptation": "study_path_in_disposable_report_copy_only",
        "original_database_rows_modified": False,
        "models_loaded": False,
        "refit": False,
        "mlflow_accessed": False,
        "confirmation_evaluated": False,
        "production_promotion": False,
        "s3_accessed": False,
        "transfer_implemented": True,
        "primary_storage_migrated": False,
        "budget": {"max_files": archive.BRONZE_MAX_FILES, "max_bytes": archive.BRONZE_MAX_BYTES},
        "scope": "offline_consistent_finished_hgb_study_snapshot_with_archived_dataset_dependency_not_s3_transfer",
    }


def verify_bundle(root, identity):
    identity = archive.bundle_id(identity)
    target = archive.checked_path(archive.workspace(root), Path("bundles") / identity)
    document = json.loads((target / "manifest.json").read_bytes())
    archive.validate_manifest(document, identity)
    dependency = dataset_bundle(root, document["provenance"]["dataset_bundle_id"])
    return verify_snapshot(target, document, dependency) | {
        "status": "verified_locally",
        "bundle_id": identity,
        "complete": True,
        "scope": "offline_study_snapshot_verification_not_s3_transfer_or_training_resumption",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("command", choices=["prepare", "verify"])
    parser.add_argument("--dataset-bundle-id")
    parser.add_argument("--bundle-id")
    args = parser.parse_args()
    if not (args.project_root / "pyproject.toml").is_file():
        parser.error("Run at project root")
    if args.command == "prepare" and not args.dataset_bundle_id:
        parser.error("--dataset-bundle-id is required")
    if args.command == "verify" and not args.bundle_id:
        parser.error("--bundle-id is required")
    try:
        result = (
            prepare_study(args.project_root, args.dataset_bundle_id)
            if args.command == "prepare"
            else verify_bundle(args.project_root, args.bundle_id)
        )
        receipts = archive.workspace(args.project_root) / "receipts"
        receipts.mkdir(exist_ok=True)
        with tempfile.NamedTemporaryFile(
            prefix="hgb-study-" + args.command + "-", suffix=".json", dir=receipts, delete=False
        ) as stream:
            stream.write(archive.encoded(result))
            result["report"] = stream.name
        print(json.dumps(result, indent=2, allow_nan=False))
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error) as error:
        parser.exit(1, f"HGB study backup stopped: {error}\n")


if __name__ == "__main__":
    main()
