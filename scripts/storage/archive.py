"""Versioned archive staging, metadata inventory and the validated artifact S3 pilot."""

import argparse
from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tempfile
import time

MAX_BYTES = 64 * 1024 * 1024
MAX_FILES = 64
SCHEMA = "fraud_storage_bundle_v1"
BUCKET = "fraud-artifacts"
BRONZE_SCHEMA = "fraud_bronze_bundle_v1"
BRONZE_MAX_FILES = 512
BRONZE_MAX_BYTES = 256 * 1024 * 1024
BRONZE_MAX_MANIFEST_BYTES = 256 * 1024
INVENTORY_PATH = "references/handbook_source.json"
SILVER_SCHEMA = "fraud_silver_bundle_v1"
SILVER_CONTRACT_PATH = "references/silver_contract_v1.json"
GOLD_SCHEMA = "fraud_gold_bundle_v1"
GOLD_CONTRACT_PATH = "references/gold_contract_v1.json"
TEMPORAL_PROTOCOL_PATH = "references/temporal_protocol_v1.json"
DEVELOPMENT_SCHEMA = "fraud_development_gold_bundle_v1"
DEVELOPMENT_POLICY_PATH = "references/hgb_optuna_protocol_v1.json"
DATASET_SCHEMAS = (BRONZE_SCHEMA, SILVER_SCHEMA, GOLD_SCHEMA, DEVELOPMENT_SCHEMA)
HGB_STUDY_SCHEMA = "fraud_hgb_study_bundle_v1"
PROVENANCE_SCHEMAS = (*DATASET_SCHEMAS, HGB_STUDY_SCHEMA)


def budget(schema):
    if schema == SCHEMA:
        return MAX_FILES, MAX_BYTES
    if schema in PROVENANCE_SCHEMAS:
        return BRONZE_MAX_FILES, BRONZE_MAX_BYTES
    raise ValueError("Unsupported archive schema")


def encoded(value):
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode()


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def relative_path(
    value,
    *,
    allow_inventory=False,
    allow_silver_contract=False,
    allow_gold_references=False,
    allow_development_policy=False,
):
    if not isinstance(value, str):
        raise TypeError("Expected a string path")
    if (
        (allow_inventory and value == INVENTORY_PATH)
        or (allow_silver_contract and value == SILVER_CONTRACT_PATH)
        or (allow_gold_references and value in {GOLD_CONTRACT_PATH, TEMPORAL_PROTOCOL_PATH})
        or (allow_development_policy and value == DEVELOPMENT_POLICY_PATH)
    ):
        return Path(value)
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or value != path.as_posix()
        or ".." in path.parts
        or "\\" in value
        or len(path.parts) < 2
        or path.parts[0] != "data"
        or path.parts[1] == "storage"
    ):
        raise ValueError("Expected a normalized data/ path outside data/storage")
    return Path(value)


def profile_path(value, schema):
    return relative_path(
        value,
        allow_inventory=schema in PROVENANCE_SCHEMAS,
        allow_silver_contract=schema in (SILVER_SCHEMA, GOLD_SCHEMA),
        allow_gold_references=schema == GOLD_SCHEMA,
        allow_development_policy=schema in (DEVELOPMENT_SCHEMA, HGB_STUDY_SCHEMA),
    )


def checked_path(root, relative):
    target = root / relative
    if any(part.is_symlink() for part in [target, *target.parents] if part != root):
        raise ValueError("Symlinks are not supported")
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError("Path escapes its root")
    return target


def workspace(root):
    path = checked_path(root, Path("data/storage"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def family(relative):
    name = relative.parts[1]
    return {
        "raw": "bronze",
        "interim": "silver",
        "processed": "gold_and_variants",
        "tracking": "mlflow",
        "replay": "replay_and_measurements",
        "artifacts": "artifacts",
        "releases": "releases",
    }.get(name, "other_review_required")


def inventory(root):
    """Read filesystem metadata only; incomplete scans are explicitly marked."""
    root = root.resolve()
    data = checked_path(root, Path("data"))
    if not data.is_dir():
        raise ValueError("data/ does not exist; no native inventory can be asserted")
    started = time.monotonic()
    entries, issues = [], []
    totals = defaultdict(lambda: {"files": 0, "bytes": 0})

    def walk_error(error):
        issues.append({"path": str(error.filename), "reason": "unreadable_directory"})

    for directory, directories, files in os.walk(data, followlinks=False, onerror=walk_error):
        base = Path(directory)
        for name in sorted(directories.copy()):
            path = base / name
            if path == data / "storage":
                directories.remove(name)
            elif path.is_symlink():
                directories.remove(name)
                issues.append({"path": path.relative_to(root).as_posix(), "reason": "symlink"})
        directories.sort()
        for name in sorted(files):
            path = base / name
            relative = path.relative_to(root)
            try:
                info = path.lstat()
                if not stat.S_ISREG(info.st_mode):
                    issues.append({"path": relative.as_posix(), "reason": "not_regular_file"})
                    continue
                group = family(relative)
                entries.append(
                    {
                        "path": relative.as_posix(),
                        "bytes": info.st_size,
                        "mtime_ns": info.st_mtime_ns,
                        "family": group,
                    }
                )
                totals[group]["files"] += 1
                totals[group]["bytes"] += info.st_size
            except OSError:
                issues.append({"path": relative.as_posix(), "reason": "stat_failed"})
    disk = shutil.disk_usage(data)
    result = {
        "schema": "fraud_storage_inventory_v1",
        "scope": "data_except_storage_no_content_reads",
        "complete": not issues,
        "elapsed_seconds": time.monotonic() - started,
        "families": dict(totals),
        "files": len(entries),
        "bytes": sum(row["bytes"] for row in entries),
        "disk_free_bytes": disk.free,
        "entries": entries,
        "issues": issues,
        "excluded": ["data/storage/", "Docker named volumes (not inspected)"],
    }
    reports = checked_path(workspace(root), Path("inventory"))
    reports.mkdir(exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix="inventory-", suffix=".json", dir=reports, delete=False
    ) as f:
        f.write(encoded(result))
        report = Path(f.name)
    return {
        key: result[key] for key in ("complete", "files", "bytes", "families", "disk_free_bytes")
    } | {
        "report": str(report),
        "issues": len(issues),
    }


def validate_manifest(document, expected_id=None):
    if not isinstance(document, dict):
        raise TypeError("Manifest must be an object")
    schema = document.get("schema")
    max_files, max_bytes = budget(schema)
    expected_keys = (
        {"schema", "files", "provenance"} if schema in PROVENANCE_SCHEMAS else {"schema", "files"}
    )
    if set(document) != expected_keys:
        raise ValueError("Unsupported manifest")
    files = document["files"]
    if not isinstance(files, list) or not 1 <= len(files) <= max_files:
        raise ValueError("Manifest file limit exceeded or empty")
    seen, total = set(), 0
    for row in files:
        if not isinstance(row, dict) or set(row) != {"path", "bytes", "sha256"}:
            raise ValueError("Invalid file entry")
        path = profile_path(row["path"], schema).as_posix()
        if path in seen or type(row["bytes"]) is not int or row["bytes"] < 0:
            raise ValueError("Duplicate path or invalid size")
        if not isinstance(row["sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", row["sha256"]):
            raise ValueError("Invalid SHA-256")
        seen.add(path)
        total += row["bytes"]
    if total > max_bytes:
        raise ValueError("Manifest exceeds byte limit")
    if files != sorted(files, key=lambda row: row["path"]):
        raise ValueError("Manifest paths are not in canonical string order")
    # File/directory collisions must fail before any download.
    if any(parent.as_posix() in seen for path in seen for parent in PurePosixPath(path).parents):
        raise ValueError("Conflicting manifest paths")
    if schema == BRONZE_SCHEMA:
        provenance = document["provenance"]
        if not isinstance(provenance, dict) or set(provenance) != {
            "source_commit",
            "inventory_sha256",
            "bronze_manifest_sha256",
        }:
            raise ValueError("Invalid Bronze provenance")
        if not isinstance(provenance["source_commit"], str) or not re.fullmatch(
            r"[a-f0-9]{40}", provenance["source_commit"]
        ):
            raise ValueError("Invalid Bronze source commit")
        for key in ("inventory_sha256", "bronze_manifest_sha256"):
            if not isinstance(provenance[key], str) or not re.fullmatch(
                r"[a-f0-9]{64}", provenance[key]
            ):
                raise ValueError("Invalid Bronze provenance digest")
        snapshot = f"data/raw/handbook/{provenance['source_commit']}/"
        rows = {row["path"]: row for row in files}
        for path, expected in [
            (INVENTORY_PATH, provenance["inventory_sha256"]),
            (snapshot + "manifest.json", provenance["bronze_manifest_sha256"]),
        ]:
            if path not in rows or rows[path]["sha256"] != expected:
                raise ValueError("Bronze provenance does not match archived files")
        if any(path != INVENTORY_PATH and not path.startswith(snapshot) for path in seen):
            raise ValueError("Bronze bundle contains an unrelated path")
    if schema == SILVER_SCHEMA:
        provenance = document["provenance"]
        if not isinstance(provenance, dict) or set(provenance) != {
            "source_commit",
            "inventory_sha256",
            "contract_sha256",
            "silver_manifest_sha256",
        }:
            raise ValueError("Invalid Silver provenance")
        if not isinstance(provenance["source_commit"], str) or not re.fullmatch(
            r"[a-f0-9]{40}", provenance["source_commit"]
        ):
            raise ValueError("Invalid Silver source commit")
        for key in ("inventory_sha256", "contract_sha256", "silver_manifest_sha256"):
            if not isinstance(provenance[key], str) or not re.fullmatch(
                r"[a-f0-9]{64}", provenance[key]
            ):
                raise ValueError("Invalid Silver provenance digest")
        parent = f"data/interim/handbook/{provenance['source_commit']}/"
        snapshot = parent + "silver_v1/"
        rows = {row["path"]: row for row in files}
        for path, expected in [
            (INVENTORY_PATH, provenance["inventory_sha256"]),
            (SILVER_CONTRACT_PATH, provenance["contract_sha256"]),
            (snapshot + "manifest.json", provenance["silver_manifest_sha256"]),
        ]:
            if path not in rows or rows[path]["sha256"] != expected:
                raise ValueError("Silver provenance does not match archived files")
        if any(
            path not in {INVENTORY_PATH, SILVER_CONTRACT_PATH}
            and not path.startswith(snapshot)
            and not re.fullmatch(re.escape(parent) + r"runs/silver_[a-f0-9]{32}\.json", path)
            for path in seen
        ):
            raise ValueError("Silver bundle contains an unrelated path")
    if schema == GOLD_SCHEMA:
        provenance = document["provenance"]
        digests = {
            "inventory_sha256",
            "silver_contract_sha256",
            "protocol_sha256",
            "gold_contract_sha256",
            "gold_manifest_sha256",
        }
        if not isinstance(provenance, dict) or set(provenance) != {"source_commit", *digests}:
            raise ValueError("Invalid Gold provenance")
        if not isinstance(provenance["source_commit"], str) or not re.fullmatch(
            r"[a-f0-9]{40}", provenance["source_commit"]
        ):
            raise ValueError("Invalid Gold source commit")
        if any(
            not isinstance(provenance[key], str)
            or not re.fullmatch(r"[a-f0-9]{64}", provenance[key])
            for key in digests
        ):
            raise ValueError("Invalid Gold provenance digest")
        parent = f"data/processed/handbook/{provenance['source_commit']}/"
        snapshot = parent + "gold_v1/"
        references = {
            INVENTORY_PATH: "inventory_sha256",
            SILVER_CONTRACT_PATH: "silver_contract_sha256",
            TEMPORAL_PROTOCOL_PATH: "protocol_sha256",
            GOLD_CONTRACT_PATH: "gold_contract_sha256",
        }
        rows = {row["path"]: row for row in files}
        for path, key in [
            *references.items(),
            (snapshot + "manifest.json", "gold_manifest_sha256"),
        ]:
            if path not in rows or rows[path]["sha256"] != provenance[key]:
                raise ValueError("Gold provenance does not match archived files")
        if any(
            path not in references
            and not path.startswith(snapshot)
            and not re.fullmatch(re.escape(parent) + r"runs/gold_[a-f0-9]{32}\.json", path)
            for path in seen
        ):
            raise ValueError("Gold bundle contains an unrelated path")
    if schema == DEVELOPMENT_SCHEMA:
        provenance = document["provenance"]
        digests = {"inventory_sha256", "policy_sha256", "development_manifest_sha256"}
        if not isinstance(provenance, dict) or set(provenance) != {
            "source_commit",
            "development_run_id",
            *digests,
        }:
            raise ValueError("Invalid development Gold provenance")
        for key, length in [
            ("source_commit", 40),
            ("development_run_id", 64),
            *((key, 64) for key in digests),
        ]:
            if not isinstance(provenance[key], str) or not re.fullmatch(
                rf"[a-f0-9]{{{length}}}", provenance[key]
            ):
                raise ValueError("Invalid development Gold provenance identity")
        snapshot = (
            f"data/processed/handbook/{provenance['source_commit']}/development_gold_v2/"
            f"{provenance['development_run_id']}/"
        )
        references = {
            INVENTORY_PATH: "inventory_sha256",
            DEVELOPMENT_POLICY_PATH: "policy_sha256",
            snapshot + "manifest.json": "development_manifest_sha256",
        }
        rows = {row["path"]: row for row in files}
        if any(
            path not in rows or rows[path]["sha256"] != provenance[key]
            for path, key in references.items()
        ):
            raise ValueError("Development Gold provenance does not match archived files")
        if any(
            path not in references
            and not re.fullmatch(
                re.escape(snapshot) + r"transactions/\d{4}-\d{2}-\d{2}\.parquet", path
            )
            for path in seen
        ):
            raise ValueError("Development Gold bundle contains an unrelated path or study")
    if schema == HGB_STUDY_SCHEMA:
        provenance = document["provenance"]
        keys = {
            "source_commit",
            "development_run_id",
            "dataset_bundle_id",
            "inventory_sha256",
            "policy_sha256",
            "development_manifest_sha256",
            "study_snapshot_sha256",
            "snapshot_receipt_sha256",
        }
        if not isinstance(provenance, dict) or set(provenance) != keys:
            raise ValueError("Invalid HGB study provenance")
        for key in keys:
            length = 40 if key == "source_commit" else 64
            if not isinstance(provenance[key], str) or not re.fullmatch(
                rf"[a-f0-9]{{{length}}}", provenance[key]
            ):
                raise ValueError("Invalid HGB study provenance identity")
        snapshot = (
            f"data/processed/handbook/{provenance['source_commit']}/development_gold_v2/"
            f"{provenance['development_run_id']}/"
        )
        required = {
            INVENTORY_PATH: "inventory_sha256",
            DEVELOPMENT_POLICY_PATH: "policy_sha256",
            snapshot + "manifest.json": "development_manifest_sha256",
            snapshot + "study/study.db": "study_snapshot_sha256",
            snapshot + "study/storage-snapshot.json": "snapshot_receipt_sha256",
        }
        rows = {row["path"]: row for row in files}
        if any(
            path not in rows or rows[path]["sha256"] != provenance[key]
            for path, key in required.items()
        ):
            raise ValueError("HGB study provenance does not match archived files")
        if any(
            path not in required
            and not (
                path in {snapshot + "study/protocol.json", snapshot + "study/report.json"}
                or re.fullmatch(
                    re.escape(snapshot)
                    + r"study/(?:attempts/(?:reference|trial-\d{3})/fold_[123]\.json|models/(?:reference|trial-\d{3})/fold_[123]/(?:model\.skops|metrics\.json|result\.json|validation_predictions\.parquet))",
                    path,
                )
            )
            for path in seen
        ):
            raise ValueError("HGB study bundle contains an unrelated path")
    serialized = encoded(document)
    if schema in PROVENANCE_SCHEMAS and len(serialized) > BRONZE_MAX_MANIFEST_BYTES:
        raise ValueError("Dataset archive manifest exceeds its metadata budget")
    identity = hashlib.sha256(serialized).hexdigest()
    if expected_id is not None and identity != expected_id:
        raise ValueError("Manifest identity mismatch")
    return identity


def verify_files(directory, document):
    for row in document["files"]:
        path = checked_path(
            directory,
            profile_path(row["path"], document["schema"]),
        )
        if (
            not path.is_file()
            or path.stat().st_size != row["bytes"]
            or digest(path) != row["sha256"]
        ):
            raise ValueError("File integrity mismatch: " + row["path"])


def prepare(root, selections, *, schema=SCHEMA, provenance=None, verify_staged=None):
    """Stage selected bytes under a declared profile, then validate before publishing locally."""
    root = root.resolve()
    max_files, max_bytes = budget(schema)
    if (schema == SCHEMA and provenance is not None) or (
        schema in PROVENANCE_SCHEMAS and provenance is None
    ):
        raise ValueError("Provenance does not match the archive profile")
    if schema == HGB_STUDY_SCHEMA and verify_staged is None:
        raise ValueError("Study snapshot requires semantic verification before publication")
    selected = set()
    total = 0
    for value in selections:
        path = checked_path(root, profile_path(value, schema))
        candidates = [path]
        if path.is_dir():
            candidates = path.rglob("*")
        for source in candidates:
            relative = source.relative_to(root)
            source = checked_path(root, relative)
            if source.is_dir():
                continue
            if not source.is_file():
                raise ValueError("Missing or nonregular input")
            database = source.suffix in {
                ".sqlite",
                ".sqlite3",
                ".db",
                ".duckdb",
            } or source.name.endswith(("-wal", "-shm", "-journal"))
            if database and (schema != HGB_STUDY_SCHEMA or source.name != "study.db"):
                raise ValueError("Database backup requires a separate consistent snapshot stage")
            if source in selected:
                continue
            selected.add(source)
            total += source.stat().st_size
            if len(selected) > max_files or total > max_bytes:
                if schema == SCHEMA:
                    raise ValueError("Pilot limit: 64 files and 64 MiB; originals untouched")
                raise ValueError(
                    f"Archive profile limit: {max_files} files and {max_bytes} bytes; originals untouched"
                )
    if not selected:
        raise ValueError("No files selected")
    work = workspace(root)
    if shutil.disk_usage(work).free < total * 3 + 16 * 1024 * 1024:
        raise ValueError("Insufficient free space for bounded staging and verification")
    bundles = checked_path(work, Path("bundles"))
    bundles.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="prepare-", dir=work) as temporary:
        staging = Path(temporary)
        rows = []
        copied = 0
        for source in sorted(selected, key=lambda path: path.relative_to(root).as_posix()):
            before = source.stat()
            relative = source.relative_to(root)
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            # Bound reads even if a source grows after the initial stat.
            with source.open("rb") as stream, target.open("xb") as output:
                while block := stream.read(1024 * 1024):
                    copied += len(block)
                    if copied > max_bytes:
                        raise ValueError("Source grew beyond the archive profile limit")
                    output.write(block)
            after = source.stat()
            if (before.st_size, before.st_mtime_ns, before.st_ino) != (
                after.st_size,
                after.st_mtime_ns,
                after.st_ino,
            ):
                raise ValueError("Source changed during copy; retry with writer stopped")
            rows.append(
                {
                    "path": relative.as_posix(),
                    "bytes": target.stat().st_size,
                    "sha256": digest(target),
                }
            )
        document = {"schema": schema, "files": rows}
        if schema in PROVENANCE_SCHEMAS:
            document["provenance"] = provenance
        identity = validate_manifest(document)
        if verify_staged is not None:
            verify_staged(staging, document)
        (staging / "manifest.json").write_bytes(encoded(document))
        target = checked_path(work, Path("bundles") / identity)
        if target.exists():
            old = json.loads((target / "manifest.json").read_bytes())
            validate_manifest(old, identity)
            verify_files(target, old)
            if verify_staged is not None:
                verify_staged(target, old)
            return identity
        staging.rename(target)
    return identity


class MissingObject(Exception):
    """Only a confirmed S3 404; authentication/transport failures are not absence."""


class ComposeS3:
    """Official pinned AWS CLI; credentials stay in the ignored Compose environment."""

    def __init__(self, root):
        self.root = root.resolve()
        self.work = workspace(self.root)
        if not (self.root / "infra/observability/.env").is_file():
            raise ValueError(
                "Initialize local_stack first; no credentials supplied on the command line"
            )
        # Explicit path-style routing prevents virtual-host DNS assumptions for RustFS.
        checked_path(self.work, Path("aws-config")).write_text(
            "[default]\nregion = us-east-1\ns3 =\n    addressing_style = path\n"
        )

    def call(self, operation, *arguments):
        command = [
            "docker",
            "compose",
            "--env-file",
            "infra/observability/.env",
            "-f",
            "infra/observability/compose.yaml",
            "run",
            "--rm",
            "--no-deps",
            "-T",
            "storage-client",
            "--endpoint-url",
            "http://rustfs:9000",
            "--region",
            "us-east-1",
            "--cli-connect-timeout",
            "5",
            "--cli-read-timeout",
            "30",
            "--no-cli-pager",
            "--output",
            "json",
            "s3api",
            operation,
            *arguments,
        ]
        try:
            result = subprocess.run(
                command, cwd=self.root, capture_output=True, text=True, timeout=90, check=False
            )
        except subprocess.TimeoutExpired as error:
            raise RuntimeError(
                "S3 command timed out; preserve staging and inspect Docker"
            ) from error
        if result.returncode:
            if re.search(r"\((?:404|NoSuchKey|NotFound|NoSuchBucket)\)", result.stderr):
                raise MissingObject(operation)
            # Do not echo expanded configuration or raw server responses with credentials.
            raise RuntimeError(
                f"S3 {operation} failed (exit {result.returncode}); no completion recorded"
            )
        return json.loads(result.stdout or "{}")

    def container_path(self, path):
        path = checked_path(self.work, path.relative_to(self.work))
        return "/work/" + path.relative_to(self.work).as_posix()

    def ensure_bucket(self):
        try:
            self.call("head-bucket", "--bucket", BUCKET)
        except MissingObject:
            self.call("create-bucket", "--bucket", BUCKET)

    def head(self, key):
        return self.call("head-object", "--bucket", BUCKET, "--key", key)["ContentLength"]

    def put(self, key, path):
        self.call(
            "put-object",
            "--bucket",
            BUCKET,
            "--key",
            key,
            "--body",
            self.container_path(path),
            "--if-none-match",
            "*",
            "--checksum-algorithm",
            "SHA256",
        )

    def get(self, key, path):
        self.call("get-object", "--bucket", BUCKET, "--key", key, self.container_path(path))

    def listed(self, prefix):
        result = self.call("list-objects-v2", "--bucket", BUCKET, "--prefix", prefix)
        return {row["Key"] for row in result.get("Contents", [])}


def bundle_id(value):
    if not re.fullmatch(r"[a-f0-9]{64}", value):
        raise ValueError("Expected a SHA-256 bundle ID")
    return value


def publish(root, identity, client):
    identity = bundle_id(identity)
    work = workspace(root)
    bundle = checked_path(work, Path("bundles") / identity)
    document = json.loads((bundle / "manifest.json").read_bytes())
    validate_manifest(document, identity)
    if document["schema"] != SCHEMA:
        raise ValueError(
            "Dataset transfer is a separate stage; pilot publish is not used for Bronze"
        )
    verify_files(bundle, document)
    client.ensure_bucket()
    prefix = f"pilot/v1/{identity}/"
    keys, uploaded, reused = set(), 0, 0
    with tempfile.TemporaryDirectory(prefix="verify-", dir=work) as temporary:
        for relative in [row["path"] for row in document["files"]] + ["manifest.json"]:
            source = bundle / relative
            key = prefix + relative
            size = source.stat().st_size
            try:
                remote_size = client.head(key)
                exists = True
            except MissingObject:
                exists = False
                client.put(key, source)
                uploaded += 1
                remote_size = client.head(key)
            if remote_size != size:
                raise ValueError("Remote object size conflict; nothing overwritten")
            check = Path(temporary) / "object"
            client.get(key, check)
            if check.stat().st_size != size or digest(check) != digest(source):
                raise ValueError("Remote SHA-256 conflict; nothing overwritten")
            check.unlink()
            reused += int(exists)
            keys.add(key)
        if not keys.issubset(client.listed(prefix)):
            raise ValueError("S3 listing does not contain the complete bundle")
    return {
        "status": "success",
        "bundle_id": identity,
        "bucket": BUCKET,
        "uploaded_objects": uploaded,
        "reused_objects": reused,
        "verified_objects": len(keys),
    }


def restore(root, identity, client):
    identity = bundle_id(identity)
    work = workspace(root)
    restores = checked_path(work, Path("restored"))
    restores.mkdir(exist_ok=True)
    target = checked_path(restores, Path(identity))
    if target.exists():
        raise ValueError("Restore destination already exists; no files overwritten")
    prefix = f"pilot/v1/{identity}/"
    with tempfile.TemporaryDirectory(prefix="restore-", dir=work) as temporary:
        staging = Path(temporary)
        key = prefix + "manifest.json"
        size = client.head(key)
        if not 0 < size <= 64 * 1024:
            raise ValueError("Manifest size limit")
        client.get(key, staging / "manifest.json")
        document = json.loads((staging / "manifest.json").read_bytes())
        validate_manifest(document, identity)
        if document["schema"] != SCHEMA:
            raise ValueError("Pilot restore does not accept a Bronze archive")
        if (
            shutil.disk_usage(work).free
            < sum(row["bytes"] for row in document["files"]) + 16 * 1024 * 1024
        ):
            raise ValueError("Insufficient space for restore")
        for row in document["files"]:
            if client.head(prefix + row["path"]) != row["bytes"]:
                raise ValueError("Remote file size mismatch")
            destination = staging / relative_path(row["path"])
            destination.parent.mkdir(parents=True, exist_ok=True)
            client.get(prefix + row["path"], destination)
        verify_files(staging, document)
        staging.rename(target)
    return {
        "status": "success",
        "bundle_id": identity,
        "restored_directory": str(target),
        "files": len(document["files"]),
        "bytes": sum(row["bytes"] for row in document["files"]),
        "scope": "byte_integrity_only_not_model_reload_or_historical_path_migration",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("inventory")
    prepare_command = commands.add_parser("prepare")
    prepare_command.add_argument("--include", action="append", required=True)
    for name in ("publish", "restore"):
        command = commands.add_parser(name)
        command.add_argument("--bundle-id", required=True)
    args = parser.parse_args()
    root = args.project_root.resolve()
    if not (root / "pyproject.toml").is_file():
        parser.error("Run at the project root")
    try:
        if args.command == "inventory":
            result = inventory(root)
        elif args.command == "prepare":
            identity = prepare(root, args.include)
            result = {
                "status": "prepared_locally",
                "bundle_id": identity,
                "manifest": str(workspace(root) / "bundles" / identity / "manifest.json"),
            }
        else:
            result = {"publish": publish, "restore": restore}[args.command](
                root, args.bundle_id, ComposeS3(root)
            )
            reports = checked_path(workspace(root), Path("receipts"))
            reports.mkdir(exist_ok=True)
            with tempfile.NamedTemporaryFile(
                prefix=args.command + "-", suffix=".json", dir=reports, delete=False
            ) as f:
                f.write(encoded(result))
                result["report"] = f.name
        print(json.dumps(result, indent=2, allow_nan=False))
    except (OSError, TypeError, ValueError, RuntimeError, MissingObject) as error:
        parser.exit(1, f"Storage operation stopped: {error}\n")


if __name__ == "__main__":
    main()
