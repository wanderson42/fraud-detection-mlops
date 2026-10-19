"""Archive one immutable development dataset and its policy; exclude Optuna study state."""

import argparse
import json
from pathlib import Path
import re
import tempfile

from scripts.storage import archive, bronze_s3_worker, bronze_transfer

from fraud_detection_mlops.data.ingestion.handbook_inventory import load_inventory
from fraud_detection_mlops.features.feature_schema import FEATURE_COLUMNS
from fraud_detection_mlops.modeling.experiments import temporal_development_data as development


def run_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise ValueError("Expected a 64-character development run ID")
    return value


def selections(root, identity):
    """Read only dataset inputs; do not enter, stat or open the study subtree."""
    identity = run_id(identity)
    inventory = archive.checked_path(root, Path(archive.INVENTORY_PATH))
    policy = archive.checked_path(root, Path(archive.DEVELOPMENT_POLICY_PATH))
    commit = load_inventory(inventory)["source"]["commit"]
    if development.load_policy(policy)["source_commit"] != commit:
        raise ValueError("Development policy and inventory source differ")
    snapshot = archive.checked_path(
        root, Path("data/processed/handbook") / commit / "development_gold_v2" / identity
    )
    if (
        json.loads(archive.checked_path(snapshot, Path("manifest.json")).read_bytes()).get(
            "run_id"
        )
        != identity
    ):
        raise ValueError("Development directory and manifest run ID differ")
    # The known study is a separate recovery unit. Unknown siblings are not silently lost.
    if {p.name for p in snapshot.iterdir()} - {"manifest.json", "transactions", "study"}:
        raise ValueError("Unexpected development dataset member; review coverage")
    transactions = archive.checked_path(snapshot, Path("transactions"))
    paths = [inventory, policy, snapshot / "manifest.json", transactions]
    paths.extend(transactions.rglob("*"))
    state = {}
    for path in paths:
        path = archive.checked_path(root, path.relative_to(root))
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError("Missing or nonregular development dataset input")
        info = path.stat()
        state[path.relative_to(root).as_posix()] = (info.st_size, info.st_mtime_ns, info.st_ino)
    return snapshot, state


def verify_directory(directory, document):
    archive.validate_manifest(document)
    if document["schema"] != archive.DEVELOPMENT_SCHEMA:
        raise ValueError("Expected a development Gold archive")
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
        raise ValueError("Unexpected or missing files in development Gold archive")
    provenance = document["provenance"]
    snapshot, _ = selections(directory, provenance["development_run_id"])
    if snapshot.parent.parent.name != provenance["source_commit"]:
        raise ValueError("Development Gold source differs from archive provenance")
    verified = development.verify_data(
        snapshot, policy_path=directory / archive.DEVELOPMENT_POLICY_PATH
    )
    return {k: v for k, v in verified.items() if k not in {"development_path", "status"}} | {
        "development_run_id": provenance["development_run_id"],
        "development_manifest_sha256": provenance["development_manifest_sha256"],
        "feature_count": len(FEATURE_COLUMNS),
    }


def prepare_development(root, identity):
    root = root.resolve()
    snapshot, before = selections(root, identity)
    verified = development.verify_data(
        snapshot, policy_path=root / archive.DEVELOPMENT_POLICY_PATH
    )
    provenance = {
        "source_commit": snapshot.parent.parent.name,
        "development_run_id": snapshot.name,
        "inventory_sha256": archive.digest(root / archive.INVENTORY_PATH),
        "policy_sha256": archive.digest(root / archive.DEVELOPMENT_POLICY_PATH),
        "development_manifest_sha256": archive.digest(snapshot / "manifest.json"),
    }

    def check(staging, document):
        if selections(root, identity)[1] != before:
            raise ValueError("Development dataset changed during staging; stop writers and retry")
        restored = verify_directory(staging, document)
        for key in ("verified_partitions", "rows", "confirmation_evaluated"):
            if restored[key] != verified[key]:
                raise ValueError("Development staging counts differ from source")

    bundle_id = archive.prepare(
        root,
        sorted(before),
        schema=archive.DEVELOPMENT_SCHEMA,
        provenance=provenance,
        verify_staged=check,
    )
    bundle = archive.workspace(root) / "bundles" / bundle_id
    document, _ = bronze_s3_worker.manifest_at(bundle, bundle_id, archive.DEVELOPMENT_SCHEMA)
    return verify_directory(bundle, document) | {
        "status": "prepared_verified_locally",
        "schema": archive.DEVELOPMENT_SCHEMA,
        "bundle_id": bundle_id,
        "manifest": str(bundle / "manifest.json"),
        "source_snapshot": str(snapshot),
        "source_commit": provenance["source_commit"],
        "dataset_version": "development_gold_v2",
        "archive_files": len(document["files"]),
        "bytes": sum(row["bytes"] for row in document["files"]),
        "complete": True,
        "inventory_included": True,
        "policy_included": True,
        "source_manifest_bytes_preserved": True,
        "study_included": False,
        "study_read": False,
        "study_backup_validated": False,
        "features_recomputed": False,
        "bronze_read": False,
        "silver_read": False,
        "pickle_deserialized": False,
        "s3_accessed": False,
        "budget": {"max_files": archive.BRONZE_MAX_FILES, "max_bytes": archive.BRONZE_MAX_BYTES},
        "scope": "offline_development_gold_dataset_preparation_not_study_backup_or_s3_transfer",
    }


def verify_bundle(root, identity):
    identity = archive.bundle_id(identity)
    bundle = archive.checked_path(archive.workspace(root), Path("bundles") / identity)
    document, _ = bronze_s3_worker.manifest_at(bundle, identity, archive.DEVELOPMENT_SCHEMA)
    return verify_directory(bundle, document) | {
        "status": "verified_locally",
        "bundle_id": identity,
        "complete": True,
        "scope": "offline_development_gold_dataset_verification_not_study_recovery",
    }


def publish(root, identity, worker=bronze_transfer.run_worker):
    identity = archive.bundle_id(identity)
    verified = verify_bundle(root, identity)
    result = worker(root, "publish", identity, schema=archive.DEVELOPMENT_SCHEMA)
    if (
        result.get("status") != "success"
        or result.get("bucket") != bronze_s3_worker.BUCKET
        or result.get("prefix") != bronze_s3_worker.prefix(identity, archive.DEVELOPMENT_SCHEMA)
    ):
        raise ValueError("Storage worker did not confirm development Gold publication")
    return (
        result
        | {k: v for k, v in verified.items() if k not in {"status", "scope"}}
        | {
            "primary_storage_migrated": False,
            "study_included": False,
        }
    )


def restore(root, identity, worker=bronze_transfer.run_worker):
    identity = archive.bundle_id(identity)
    work = archive.workspace(root)
    target = archive.checked_path(work, Path("restored") / identity)
    if target.exists():
        raise ValueError("Restore destination exists; preserve it before another restore")
    result = worker(root, "download", identity, schema=archive.DEVELOPMENT_SCHEMA)
    relative = Path(result.get("candidate", ""))
    if (
        result.get("status") != "downloaded_quarantined"
        or result.get("bucket") != bronze_s3_worker.BUCKET
        or result.get("prefix") != bronze_s3_worker.prefix(identity, archive.DEVELOPMENT_SCHEMA)
        or len(relative.parts) != 2
        or relative.parts[0] != "downloads"
        or not relative.parts[1].startswith("development-gold-")
    ):
        raise ValueError("Unexpected development Gold quarantine destination")
    candidate = archive.checked_path(work, relative)
    document, _ = bronze_s3_worker.manifest_at(candidate, identity, archive.DEVELOPMENT_SCHEMA)
    verified = verify_directory(candidate, document)
    target.parent.mkdir(exist_ok=True)
    if target.exists():
        raise ValueError("Restore destination appeared during download; quarantine preserved")
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
            "inventory_included": True,
            "policy_included": True,
            "original_source_read": False,
            "bronze_read": False,
            "silver_read": False,
            "study_read": False,
            "study_included": False,
            "study_backup_validated": False,
            "features_recomputed": False,
            "pickle_deserialized": False,
            "primary_storage_migrated": False,
            "scope": "s3_development_gold_dataset_roundtrip_with_archived_policy_not_study_recovery",
        }
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("command", choices=["prepare", "verify", "publish", "restore"])
    parser.add_argument("--run-id")
    parser.add_argument("--bundle-id")
    args = parser.parse_args()
    root = args.project_root.resolve()
    if not (root / "pyproject.toml").is_file():
        parser.error("Run at the project root")
    if args.command == "prepare" and not args.run_id:
        parser.error("--run-id is required for prepare")
    if args.command != "prepare" and not args.bundle_id:
        parser.error("--bundle-id is required for verify/publish/restore")
    try:
        result = (
            prepare_development(root, args.run_id)
            if args.command == "prepare"
            else {"verify": verify_bundle, "publish": publish, "restore": restore}[args.command](
                root, args.bundle_id
            )
        )
        receipts = archive.checked_path(archive.workspace(root), Path("receipts"))
        receipts.mkdir(exist_ok=True)
        with tempfile.NamedTemporaryFile(
            prefix="development-gold-" + args.command + "-",
            suffix=".json",
            dir=receipts,
            delete=False,
        ) as stream:
            stream.write(archive.encoded(result))
            result["report"] = stream.name
        print(json.dumps(result, indent=2, allow_nan=False))
    except (OSError, TypeError, ValueError, KeyError) as error:
        parser.exit(1, f"Development Gold archive stopped: {error}\n")


if __name__ == "__main__":
    main()
