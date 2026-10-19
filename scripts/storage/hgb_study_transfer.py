"""Publish or restore a consistent HGB study with an explicit archived dataset dependency."""

import argparse
import json
from pathlib import Path
import sqlite3
import tempfile

from scripts.storage import archive, bronze_transfer, hgb_study_backup
from scripts.storage import bronze_s3_worker as transport


def publish(root, identity, worker=bronze_transfer.run_worker):
    identity = archive.bundle_id(identity)
    verified = hgb_study_backup.verify_bundle(root, identity)
    result = worker(root, "publish", identity, schema=archive.HGB_STUDY_SCHEMA)
    if (
        result.get("status") != "success"
        or result.get("bundle_id") != identity
        or result.get("bucket") != transport.bucket_for(archive.HGB_STUDY_SCHEMA)
        or result.get("prefix") != transport.prefix(identity, archive.HGB_STUDY_SCHEMA)
    ):
        raise ValueError("Storage worker did not confirm HGB study publication")
    return (
        result
        | {k: v for k, v in verified.items() if k not in {"status", "scope"}}
        | {
            "transfer_implemented": True,
            "refit": False,
            "models_loaded": False,
            "mlflow_accessed": False,
            "production_promotion": False,
            "primary_storage_migrated": False,
        }
    )


def restore(root, identity, dataset_id, worker=bronze_transfer.run_worker):
    identity, dataset_id = archive.bundle_id(identity), archive.bundle_id(dataset_id)
    work = archive.workspace(root)
    target = archive.checked_path(work, Path("restored") / identity)
    if target.exists():
        raise ValueError("Restore destination exists; preserve it before another restore")
    # No implicit dataset download: fail before S3 if the declared dependency is unavailable.
    dependency = hgb_study_backup.dataset_bundle(root, dataset_id)
    result = worker(root, "download", identity, schema=archive.HGB_STUDY_SCHEMA)
    relative = Path(result.get("candidate", ""))
    if (
        result.get("status") != "downloaded_quarantined"
        or result.get("bundle_id") != identity
        or result.get("bucket") != transport.bucket_for(archive.HGB_STUDY_SCHEMA)
        or result.get("prefix") != transport.prefix(identity, archive.HGB_STUDY_SCHEMA)
        or len(relative.parts) != 2
        or relative.parts[0] != "downloads"
        or not relative.parts[1].startswith("hgb-study-")
    ):
        raise ValueError("Unexpected HGB study quarantine destination")
    candidate = archive.checked_path(work, relative)
    document, _ = transport.manifest_at(candidate, identity, archive.HGB_STUDY_SCHEMA)
    if document["provenance"]["dataset_bundle_id"] != dataset_id:
        raise ValueError("Downloaded study references another dataset; quarantine preserved")
    verified = hgb_study_backup.verify_snapshot(candidate, document, dependency)
    target.parent.mkdir(exist_ok=True)
    if target.exists():
        raise ValueError("Restore destination appeared; quarantine preserved")
    candidate.rename(target)
    return (
        {k: v for k, v in result.items() if k != "candidate"}
        | verified
        | {
            "status": "restored_verified",
            "restored_directory": str(target),
            "files": len(document["files"]),
            "bytes": sum(row["bytes"] for row in document["files"]),
            "complete": True,
            "sqlite_integrity_verified": True,
            "sqlite_foreign_keys_verified": True,
            "report_bytes_preserved": True,
            "tracking_references_preserved": True,
            "verification_path_adaptation": "study_path_in_disposable_report_copy_only",
            "original_source_read": False,
            "refit": False,
            "models_loaded": False,
            "mlflow_accessed": False,
            "confirmation_evaluated": False,
            "production_promotion": False,
            "primary_storage_migrated": False,
            "training_resumption_validated": False,
            "scope": "s3_consistent_hgb_study_roundtrip_with_verified_local_dataset_dependency_not_training_resumption_or_mlflow_migration",
        }
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("command", choices=["publish", "restore"])
    parser.add_argument("--bundle-id", required=True)
    parser.add_argument("--dataset-bundle-id")
    args = parser.parse_args()
    root = args.project_root.resolve()
    if not (root / "pyproject.toml").is_file():
        parser.error("Run at project root")
    if args.command == "restore" and not args.dataset_bundle_id:
        parser.error("--dataset-bundle-id is required for restore")
    try:
        result = (
            publish(root, args.bundle_id)
            if args.command == "publish"
            else restore(root, args.bundle_id, args.dataset_bundle_id)
        )
        receipts = archive.workspace(root) / "receipts"
        receipts.mkdir(exist_ok=True)
        with tempfile.NamedTemporaryFile(
            prefix="hgb-study-" + args.command + "-", suffix=".json", dir=receipts, delete=False
        ) as stream:
            stream.write(archive.encoded(result))
            result["report"] = stream.name
        print(json.dumps(result, indent=2, allow_nan=False))
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error) as error:
        parser.exit(1, f"HGB study transfer stopped: {error}\n")


if __name__ == "__main__":
    main()
