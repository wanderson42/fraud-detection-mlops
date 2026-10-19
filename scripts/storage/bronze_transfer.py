"""Publish or restore a complete Bronze bundle through one isolated Compose job."""

import argparse
import json
from pathlib import Path
import subprocess
import tempfile

from scripts.storage import archive, bronze_archive, bronze_s3_worker

from fraud_detection_mlops.data.contracts.dataset_errors import BronzeError


def run_worker(root, command, identity, *, schema=archive.BRONZE_SCHEMA):
    config = archive.checked_path(root, Path("infra/observability/.env"))
    if not config.is_file():
        raise ValueError("Initialize local_stack before dataset transfer")
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            str(config),
            "-f",
            str(root / "infra/observability/compose.yaml"),
            "--profile",
            "storage-tools",
            "run",
            "--rm",
            "--no-deps",
            "-T",
            "--pull",
            "never",
            "storage-transfer",
            command,
            "--bundle-id",
            identity,
            "--schema",
            schema,
            "--expected-code-sha256",
            bronze_s3_worker.code_sha256(),
        ],
        cwd=root,
        stdout=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode:
        raise ValueError("Storage job failed; inspect the preceding output (no originals changed)")
    document = json.loads(result.stdout)
    if (
        document.get("bundle_id") != identity
        or document.get("worker_code_sha256") != bronze_s3_worker.code_sha256()
    ):
        raise ValueError("Unexpected storage worker receipt")
    return document


def publish(root, identity, worker=run_worker):
    identity = archive.bundle_id(identity)
    verified = bronze_archive.verify_bundle(root, identity)
    result = worker(root, "publish", identity)
    if result.get("status") != "success" or result.get("bucket") != bronze_s3_worker.BUCKET:
        raise ValueError("Storage worker did not confirm publication")
    return result | {
        "verified_source_partitions": verified["verified_source_partitions"],
        "complete": verified["complete"],
        "pickle_deserialized": False,
        "original_source_read": False,
        "primary_storage_migrated": False,
    }


def restore(root, identity, worker=run_worker):
    identity = archive.bundle_id(identity)
    work = archive.workspace(root)
    target = archive.checked_path(work, Path("restored") / identity)
    if target.exists():
        raise ValueError(
            "Restore destination exists; preserve it and review before another restore"
        )
    result = worker(root, "download", identity)
    relative = Path(result.get("candidate", ""))
    if (
        result.get("status") != "downloaded_quarantined"
        or result.get("bucket") != bronze_s3_worker.BUCKET
        or len(relative.parts) != 2
        or relative.parts[0] != "downloads"
        or not relative.parts[1].startswith("bronze-")
    ):
        raise ValueError("Unexpected quarantine destination")
    candidate = archive.checked_path(work, relative)
    document, _ = bronze_s3_worker.manifest_at(candidate, identity)
    # This verification reads only the download and its own archived inventory.
    # A semantic failure leaves quarantine available for diagnosis, never a final restore.
    verified = bronze_archive.verify_directory(candidate, document)
    target.parent.mkdir(exist_ok=True)
    if target.exists():
        raise ValueError("Restore destination appeared during download; quarantine preserved")
    candidate.rename(target)
    result = {key: value for key, value in result.items() if key != "candidate"}
    return result | {
        "status": "restored_verified",
        "restored_directory": str(target),
        "files": len(document["files"]),
        "bytes": sum(row["bytes"] for row in document["files"]),
        "verified_source_partitions": verified["verified_file_count"],
        "complete": verified["complete"],
        "inventory_included": True,
        "original_source_read": False,
        "pickle_deserialized": False,
        "primary_storage_migrated": False,
        "scope": "s3_roundtrip_with_archived_inventory_and_complete_bronze_verification",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("command", choices=["publish", "restore"])
    parser.add_argument("--bundle-id", required=True)
    args = parser.parse_args()
    root = args.project_root.resolve()
    if not (root / "pyproject.toml").is_file():
        parser.error("Run at the project root")
    try:
        result = (publish if args.command == "publish" else restore)(root, args.bundle_id)
        receipts = archive.checked_path(archive.workspace(root), Path("receipts"))
        receipts.mkdir(exist_ok=True)
        with tempfile.NamedTemporaryFile(
            prefix="bronze-" + args.command + "-", suffix=".json", dir=receipts, delete=False
        ) as stream:
            stream.write(archive.encoded(result))
            result["report"] = stream.name
        print(json.dumps(result, indent=2, allow_nan=False))
    except (OSError, TypeError, ValueError, BronzeError) as error:
        parser.exit(1, f"Bronze transfer stopped: {error}\n")


if __name__ == "__main__":
    main()
