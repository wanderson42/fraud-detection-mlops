"""Publish and recover an already prepared MLflow snapshot; no live tracking or migration."""

import argparse
import json
from pathlib import Path
import sqlite3
import tempfile

from scripts.storage import archive, bronze_transfer, mlflow_backup
from scripts.storage import bronze_s3_worker as transport


def receipt_matches(result, identity, status):
    return (
        result.get("status") == status
        and result.get("bundle_id") == identity
        and result.get("bucket") == transport.bucket_for(mlflow_backup.SCHEMA)
        and result.get("prefix") == transport.prefix(identity, mlflow_backup.SCHEMA)
    )


def publish(root, identity, worker=bronze_transfer.run_worker):
    identity = archive.bundle_id(identity)
    verified = mlflow_backup.verify(root, identity)
    result = worker(root, "publish", identity, schema=mlflow_backup.SCHEMA)
    if (
        not receipt_matches(result, identity, "success")
        or result.get("verified_objects") != verified["archive_files"] + 1
    ):
        raise ValueError("Storage worker did not confirm complete MLflow snapshot publication")
    return (
        result
        | {k: v for k, v in verified.items() if k not in {"status", "scope"}}
        | {
            "transfer_implemented": True,
            "s3_accessed": True,
            "operational_tracking_restore_validated": False,
            "production_promotion": False,
        }
    )


def restore(root, identity, worker=bronze_transfer.run_worker):
    identity = archive.bundle_id(identity)
    work = archive.workspace(root)
    target = archive.checked_path(work, Path("restored") / identity)
    if target.exists():
        raise ValueError("Restore destination exists; preserve it before another restore")
    result = worker(root, "download", identity, schema=mlflow_backup.SCHEMA)
    relative = Path(result.get("candidate", ""))
    if (
        not receipt_matches(result, identity, "downloaded_quarantined")
        or len(relative.parts) != 2
        or relative.parts[0] != "downloads"
        or not relative.parts[1].startswith("mlflow-tracking-")
    ):
        raise ValueError("Unexpected MLflow snapshot quarantine destination")
    candidate = archive.checked_path(work, relative)
    document, _ = transport.manifest_at(candidate, identity, mlflow_backup.SCHEMA)
    transport.verify_payload(candidate, document)
    if result.get("verified_objects") != len(document["files"]) + 1:
        raise ValueError("Worker did not verify the complete MLflow object set")
    # S3 has objects, not empty filesystem directories. Recover only the archived directory set.
    marker = json.loads((candidate / mlflow_backup.CAPTURE).read_bytes())
    directories = marker["directories"]
    if not isinstance(directories, list) or directories != sorted(set(directories)):
        raise ValueError("Invalid archived MLflow directories; quarantine preserved")
    for name in directories:
        path = archive.checked_path(candidate, mlflow_backup.path_value(name))
        path.mkdir(parents=True, exist_ok=True)
    verified = mlflow_backup.verify_snapshot(candidate, document)
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
            "complete": True,
            "transfer_implemented": True,
            "s3_accessed": True,
            "operational_tracking_restore_validated": False,
            "production_promotion": False,
            "scope": "s3_mlflow_snapshot_roundtrip_with_independent_store_metadata_and_artifact_verification_not_operational_tracking_restore_or_migration",
        }
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["publish", "restore"])
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--bundle-id", required=True)
    args = parser.parse_args(argv)
    root = args.project_root.resolve()
    if not (root / "pyproject.toml").is_file():
        parser.error("Run at project root")
    try:
        result = (publish if args.command == "publish" else restore)(root, args.bundle_id)
        receipts = archive.checked_path(archive.workspace(root), Path("receipts"))
        receipts.mkdir(exist_ok=True)
        with tempfile.NamedTemporaryFile(
            prefix="mlflow-" + args.command + "-", suffix=".json", dir=receipts, delete=False
        ) as stream:
            stream.write(archive.encoded(result))
            result["report"] = stream.name
        print(json.dumps(result, indent=2, allow_nan=False))
        return 0
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error) as error:
        parser.exit(1, f"MLflow transfer stopped: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
