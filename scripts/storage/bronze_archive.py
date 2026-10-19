"""Prepare and verify an entire Bronze snapshot offline; S3 transfer is a separate stage."""

import argparse
import json
from pathlib import Path
import tempfile

from scripts.storage import archive

from fraud_detection_mlops.data.contracts.dataset_errors import BronzeError
from fraud_detection_mlops.data.ingestion.handbook_bronze import verify_bronze
from fraud_detection_mlops.data.ingestion.handbook_inventory import load_inventory


def source_state(root, snapshot):
    state = {}
    for path in sorted(snapshot.rglob("*")):
        path = archive.checked_path(root, path.relative_to(root))
        if path.name == ".extract.lock":
            raise ValueError("Bronze writer is active; stop it before archiving")
        if path.is_file():
            info = path.stat()
            state[path.relative_to(root).as_posix()] = (
                info.st_size,
                info.st_mtime_ns,
                info.st_ino,
            )
        elif not path.is_dir():
            raise ValueError("Bronze contains a missing or nonregular file")
    return state


def verify_directory(directory, document):
    """Verify original Git identities and Bronze SHA-256 using the archived inventory."""
    archive.validate_manifest(document)
    if document["schema"] != archive.BRONZE_SCHEMA:
        raise ValueError("Expected a Bronze archive")
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
        raise ValueError("Unexpected or missing files in Bronze archive")
    inventory_path = archive.checked_path(directory, Path(archive.INVENTORY_PATH))
    inventory = load_inventory(inventory_path)
    if inventory["source"]["commit"] != document["provenance"]["source_commit"]:
        raise ValueError("Archived inventory commit differs from provenance")
    return verify_bronze(
        directory / "data/raw/handbook", inventory_path=inventory_path, require_complete=True
    )


def prepare_bronze(root):
    root = root.resolve()
    inventory_path = archive.checked_path(root, Path(archive.INVENTORY_PATH))
    inventory = load_inventory(inventory_path)
    snapshot_relative = Path("data/raw/handbook") / inventory["source"]["commit"]
    snapshot = archive.checked_path(root, snapshot_relative)
    # Reuse the frozen verifier; it hashes opaque bytes and never unpickles data.
    verify_bronze(root / "data/raw/handbook", inventory_path=inventory_path, require_complete=True)
    before = source_state(root, snapshot)
    provenance = {
        "source_commit": inventory["source"]["commit"],
        "inventory_sha256": archive.digest(inventory_path),
        "bronze_manifest_sha256": archive.digest(snapshot / "manifest.json"),
    }
    verified = {}

    def check(staging, document):
        if source_state(root, snapshot) != before:
            raise ValueError("Bronze source changed during staging; stop writers and retry")
        verified.update(verify_directory(staging, document))

    identity = archive.prepare(
        root,
        [snapshot_relative.as_posix(), archive.INVENTORY_PATH],
        schema=archive.BRONZE_SCHEMA,
        provenance=provenance,
        verify_staged=check,
    )
    bundle = archive.workspace(root) / "bundles" / identity
    document = json.loads((bundle / "manifest.json").read_bytes())
    return {
        "status": "prepared_verified_locally",
        "schema": archive.BRONZE_SCHEMA,
        "bundle_id": identity,
        "manifest": str(bundle / "manifest.json"),
        "source_snapshot": str(snapshot),
        "source_commit": provenance["source_commit"],
        "source_files": len(before),
        "archive_files": len(document["files"]),
        "bytes": sum(row["bytes"] for row in document["files"]),
        "verified_source_partitions": verified["verified_file_count"],
        "complete": verified["complete"],
        "inventory_included": True,
        "source_manifest_bytes_preserved": True,
        "budget": {"max_files": archive.BRONZE_MAX_FILES, "max_bytes": archive.BRONZE_MAX_BYTES},
        "scope": "offline_bronze_archive_preparation_not_s3_transfer",
        "pickle_deserialized": False,
        "s3_accessed": False,
    }


def verify_bundle(root, identity):
    identity = archive.bundle_id(identity)
    bundle = archive.checked_path(archive.workspace(root), Path("bundles") / identity)
    path = archive.checked_path(bundle, Path("manifest.json"))
    if not 0 < path.stat().st_size <= archive.BRONZE_MAX_MANIFEST_BYTES:
        raise ValueError("Bronze archive manifest exceeds its metadata budget")
    document = json.loads(path.read_bytes())
    archive.validate_manifest(document, identity)
    result = verify_directory(bundle, document)
    return {
        "status": "verified_locally",
        "bundle_id": identity,
        "verified_source_partitions": result["verified_file_count"],
        "complete": result["complete"],
        "scope": "offline_bronze_archive_verification_not_s3_transfer",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("prepare")
    verify = commands.add_parser("verify")
    verify.add_argument("--bundle-id", required=True)
    args = parser.parse_args()
    root = args.project_root.resolve()
    if not (root / "pyproject.toml").is_file():
        parser.error("Run at the project root")
    try:
        result = (
            prepare_bronze(root)
            if args.command == "prepare"
            else verify_bundle(root, args.bundle_id)
        )
        receipts = archive.checked_path(archive.workspace(root), Path("receipts"))
        receipts.mkdir(exist_ok=True)
        with tempfile.NamedTemporaryFile(
            prefix="bronze-" + args.command + "-", suffix=".json", dir=receipts, delete=False
        ) as f:
            f.write(archive.encoded(result))
            result["report"] = f.name
        print(json.dumps(result, indent=2, allow_nan=False))
    except (OSError, TypeError, ValueError, BronzeError) as error:
        parser.exit(1, f"Bronze archive stopped: {error}\n")


if __name__ == "__main__":
    main()
