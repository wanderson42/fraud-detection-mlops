"""Archive complete Gold with its contract, inventory and audits; reuse dataset S3 transport."""

import argparse
import json
from pathlib import Path
import re
import tempfile

from scripts.storage import archive, bronze_s3_worker, bronze_transfer

from fraud_detection_mlops.data.contracts.dataset_errors import GoldError
from fraud_detection_mlops.data.contracts.gold_contract import load_gold_contract
from fraud_detection_mlops.data.datasets.gold_dataset import verify_gold
from fraud_detection_mlops.data.ingestion.handbook_inventory import load_inventory


def selections(root):
    inventory = archive.checked_path(root, Path(archive.INVENTORY_PATH))
    contract = archive.checked_path(root, Path(archive.GOLD_CONTRACT_PATH))
    silver_contract = archive.checked_path(root, Path(archive.SILVER_CONTRACT_PATH))
    protocol = archive.checked_path(root, Path(archive.TEMPORAL_PROTOCOL_PATH))
    commit = load_inventory(inventory)["source"]["commit"]
    version = load_gold_contract(contract)["version"]
    parent = archive.checked_path(root, Path("data/processed/handbook") / commit)
    if archive.checked_path(parent, Path(f".{version}.lock")).exists():
        raise ValueError("Gold writer is active; stop it before archiving")
    snapshot = archive.checked_path(parent, Path(version))
    manifest = json.loads(archive.checked_path(snapshot, Path("manifest.json")).read_bytes())
    run_id = manifest.get("run_id")
    if not isinstance(run_id, str) or not re.fullmatch(r"[a-f0-9]{32}", run_id):
        raise ValueError("Gold manifest lacks its creating run identity")
    audits = sorted(archive.checked_path(parent, Path("runs")).glob("gold_*.json"))
    if parent / "runs" / f"gold_{run_id}.json" not in audits:
        raise ValueError("Creating Gold audit is missing; preserve it with the dataset")
    paths = [inventory, contract, silver_contract, protocol, *audits]
    paths.extend(snapshot.rglob("*"))
    state = {}
    for path in paths:
        path = archive.checked_path(root, path.relative_to(root))
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError("Gold contains a missing or nonregular file")
        info = path.stat()
        state[path.relative_to(root).as_posix()] = (info.st_size, info.st_mtime_ns, info.st_ino)
    return snapshot, state, len(audits)


def verify_directory(directory, document):
    """Use only archived metadata and the existing schema/features/temporal-splits/IDs verifier."""
    archive.validate_manifest(document)
    if document["schema"] != archive.GOLD_SCHEMA:
        raise ValueError("Expected a Gold archive")
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
        raise ValueError("Unexpected or missing files in Gold archive")
    snapshot, _, _ = selections(directory)
    inventory = directory / archive.INVENTORY_PATH
    contract = directory / archive.GOLD_CONTRACT_PATH
    if snapshot.parent.name != document["provenance"]["source_commit"]:
        raise ValueError("Archived Gold commit differs from provenance")
    return verify_gold(
        directory / "data/processed/handbook",
        inventory_path=inventory,
        silver_contract=directory / archive.SILVER_CONTRACT_PATH,
        protocol_path=directory / archive.TEMPORAL_PROTOCOL_PATH,
        gold_contract=contract,
    )


def prepare_gold(root):
    root = root.resolve()
    snapshot, before, audit_count = selections(root)
    verified = verify_gold(
        root / "data/processed/handbook",
        inventory_path=root / archive.INVENTORY_PATH,
        silver_contract=root / archive.SILVER_CONTRACT_PATH,
        protocol_path=root / archive.TEMPORAL_PROTOCOL_PATH,
        gold_contract=root / archive.GOLD_CONTRACT_PATH,
    )
    provenance = {
        "source_commit": snapshot.parent.name,
        "inventory_sha256": archive.digest(root / archive.INVENTORY_PATH),
        "gold_contract_sha256": archive.digest(root / archive.GOLD_CONTRACT_PATH),
        "silver_contract_sha256": archive.digest(root / archive.SILVER_CONTRACT_PATH),
        "protocol_sha256": archive.digest(root / archive.TEMPORAL_PROTOCOL_PATH),
        "gold_manifest_sha256": archive.digest(snapshot / "manifest.json"),
    }

    def check(staging, document):
        if selections(root)[1] != before:
            raise ValueError("Gold source changed during staging; stop writers and retry")
        restored = verify_directory(staging, document)
        if {k: v for k, v in restored.items() if k != "gold_path"} != {
            k: v for k, v in verified.items() if k != "gold_path"
        }:
            raise ValueError("Gold staging counts differ from source")

    identity = archive.prepare(
        root,
        sorted(before),
        schema=archive.GOLD_SCHEMA,
        provenance=provenance,
        verify_staged=check,
    )
    bundle = archive.workspace(root) / "bundles" / identity
    document, _ = bronze_s3_worker.manifest_at(bundle, identity, archive.GOLD_SCHEMA)
    return {
        "status": "prepared_verified_locally",
        "schema": archive.GOLD_SCHEMA,
        "bundle_id": identity,
        "manifest": str(bundle / "manifest.json"),
        "source_snapshot": str(snapshot),
        "source_commit": snapshot.parent.name,
        "contract_version": "gold_v1",
        "archive_files": len(document["files"]),
        "bytes": sum(row["bytes"] for row in document["files"]),
        "included_audits": audit_count,
        **{k: v for k, v in verified.items() if k != "gold_path"},
        "complete": True,
        "inventory_included": True,
        "contract_included": True,
        "silver_contract_included": True,
        "temporal_protocol_included": True,
        "source_manifest_bytes_preserved": True,
        "budget": {"max_files": archive.BRONZE_MAX_FILES, "max_bytes": archive.BRONZE_MAX_BYTES},
        "scope": "offline_gold_archive_preparation_with_schema_features_temporal_splits_and_ids_not_s3_transfer",
        "pickle_deserialized": False,
        "bronze_read": False,
        "silver_read": False,
        "features_recomputed": False,
        "s3_accessed": False,
    }


def verify_bundle(root, identity):
    identity = archive.bundle_id(identity)
    bundle = archive.checked_path(archive.workspace(root), Path("bundles") / identity)
    document, _ = bronze_s3_worker.manifest_at(bundle, identity, archive.GOLD_SCHEMA)
    verified = verify_directory(bundle, document)
    return {k: v for k, v in verified.items() if k != "gold_path"} | {
        "status": "verified_locally",
        "bundle_id": identity,
        "complete": True,
        "scope": "offline_gold_archive_verification_not_s3_transfer",
    }


def publish(root, identity, worker=bronze_transfer.run_worker):
    identity = archive.bundle_id(identity)
    verified = verify_bundle(root, identity)
    result = worker(root, "publish", identity, schema=archive.GOLD_SCHEMA)
    if (
        result.get("status") != "success"
        or result.get("bucket") != bronze_s3_worker.BUCKET
        or result.get("prefix") != bronze_s3_worker.prefix(identity, archive.GOLD_SCHEMA)
    ):
        raise ValueError("Storage worker did not confirm Gold publication")
    return (
        result
        | {k: v for k, v in verified.items() if k not in {"status", "scope"}}
        | {
            "primary_storage_migrated": False,
        }
    )


def restore(root, identity, worker=bronze_transfer.run_worker):
    identity = archive.bundle_id(identity)
    work = archive.workspace(root)
    target = archive.checked_path(work, Path("restored") / identity)
    if target.exists():
        raise ValueError(
            "Restore destination exists; preserve it and review before another restore"
        )
    result = worker(root, "download", identity, schema=archive.GOLD_SCHEMA)
    relative = Path(result.get("candidate", ""))
    if (
        result.get("status") != "downloaded_quarantined"
        or result.get("bucket") != bronze_s3_worker.BUCKET
        or result.get("prefix") != bronze_s3_worker.prefix(identity, archive.GOLD_SCHEMA)
        or len(relative.parts) != 2
        or relative.parts[0] != "downloads"
        or not relative.parts[1].startswith("gold-")
    ):
        raise ValueError("Unexpected Gold quarantine destination")
    candidate = archive.checked_path(work, relative)
    document, _ = bronze_s3_worker.manifest_at(candidate, identity, archive.GOLD_SCHEMA)
    verified = verify_directory(candidate, document)
    target.parent.mkdir(exist_ok=True)
    if target.exists():
        raise ValueError("Restore destination appeared during download; quarantine preserved")
    candidate.rename(target)
    return (
        {k: v for k, v in result.items() if k != "candidate"}
        | {k: v for k, v in verified.items() if k != "gold_path"}
        | {
            "status": "restored_verified",
            "restored_directory": str(target),
            "files": len(document["files"]),
            "bytes": sum(row["bytes"] for row in document["files"]),
            "complete": True,
            "inventory_included": True,
            "contract_included": True,
            "silver_contract_included": True,
            "temporal_protocol_included": True,
            "original_source_read": False,
            "bronze_read": False,
            "silver_read": False,
            "features_recomputed": False,
            "pickle_deserialized": False,
            "primary_storage_migrated": False,
            "scope": "s3_gold_roundtrip_with_archived_references_schema_features_temporal_splits_and_ids",
        }
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("command", choices=["prepare", "verify", "publish", "restore"])
    parser.add_argument("--bundle-id")
    args = parser.parse_args()
    root = args.project_root.resolve()
    if not (root / "pyproject.toml").is_file():
        parser.error("Run at the project root")
    if args.command != "prepare" and not args.bundle_id:
        parser.error("--bundle-id is required for verify/publish/restore")
    try:
        result = (
            prepare_gold(root)
            if args.command == "prepare"
            else {"verify": verify_bundle, "publish": publish, "restore": restore}[args.command](
                root, args.bundle_id
            )
        )
        receipts = archive.checked_path(archive.workspace(root), Path("receipts"))
        receipts.mkdir(exist_ok=True)
        with tempfile.NamedTemporaryFile(
            prefix="gold-" + args.command + "-", suffix=".json", dir=receipts, delete=False
        ) as stream:
            stream.write(archive.encoded(result))
            result["report"] = stream.name
        print(json.dumps(result, indent=2, allow_nan=False))
    except (OSError, TypeError, ValueError, GoldError) as error:
        parser.exit(1, f"Gold archive stopped: {error}\n")


if __name__ == "__main__":
    main()
