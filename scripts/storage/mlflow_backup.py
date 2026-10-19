"""Prepare and verify a quiesced MLflow SQLite/artifact snapshot; no S3 or migration."""

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import sys
import tempfile
import time
from urllib.parse import unquote, urlsplit

from scripts.storage import archive
from scripts.storage import mlflow_inventory as inv

SCHEMA = "fraud_mlflow_tracking_bundle_v1"
SNAPSHOT_SCHEMA = "fraud_mlflow_tracking_snapshot_v1"
TRACKING = "data/tracking"
DATABASE = TRACKING + "/mlflow.db"
INVENTORY = TRACKING + "/storage-inventory.json"
CAPTURE = TRACKING + "/storage-snapshot.json"
MAX_FILES = 1024
MAX_BYTES = 256 * 1024 * 1024
MAX_MANIFEST_BYTES = 256 * 1024
AUXILIARY = TRACKING + "/interface-smoke"
SUPPORTED_STORES = (TRACKING, AUXILIARY)


def producer_sha256():
    digest = hashlib.sha256()
    for name in ("archive.py", "mlflow_inventory.py", "mlflow_backup.py"):
        digest.update(name.encode() + b"\0" + Path(__file__).with_name(name).read_bytes())
    return digest.hexdigest()


def path_value(value):
    if not isinstance(value, str) or "\\" in value or "\x00" in value:
        raise ValueError("Invalid tracking snapshot path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != value:
        raise ValueError("Noncanonical tracking snapshot path")
    if value != TRACKING and not value.startswith(TRACKING + "/"):
        raise ValueError("Tracking snapshot path escapes its unit")
    return Path(value)


def other_database(path):
    return path.suffix.lower() in {".db", ".sqlite", ".sqlite3", ".duckdb"} or path.name.endswith(
        ("-wal", "-shm", "-journal")
    )


def tree(root):
    """Inventory the entire managed tracking unit, including ancillary and empty directories."""
    tracking = archive.checked_path(root, Path(TRACKING))
    files, directories = {}, []

    def failed(error):
        raise error

    for directory, children, names in os.walk(tracking, followlinks=False, onerror=failed):
        base = Path(directory)
        directories.append(base.relative_to(root).as_posix())
        for name in sorted(children + names):
            archive.checked_path(root, (base / name).relative_to(root))
        for name in sorted(names):
            path = base / name
            files[path.relative_to(root).as_posix()] = inv.signature(path)
    return files, sorted(directories)


def store_inventories(inventory):
    additional = inventory.get("additional_stores", {})
    if not isinstance(additional, dict) or set(additional) - {AUXILIARY}:
        raise ValueError("Unsupported tracking store coverage")
    return {TRACKING: inventory, **additional}


def normalized_inventory(root, work):
    stores = {}
    for tracking in SUPPORTED_STORES:
        if tracking != TRACKING and not (root / tracking / "mlflow.db").exists():
            continue
        result = inv.inventory(
            root, tracking_root=root / tracking, temporary_directory=work, scan_references=False
        )
        result.pop("created_at_utc")
        stores[tracking] = result
    # Resolve project IDs against the coordinated set, never against one store alone.
    runs = [r for result in stores.values() for r in result["database"]["runs"]]
    models = [m for result in stores.values() for m in result["database"]["logged_models"]]
    for key, rows in (("run_uuid", runs), ("model_id", models)):
        ids = [r[key] for r in rows]
        if len(ids) != len(set(ids)):
            raise ValueError("Ambiguous tracking IDs across stores require review")
    references, sources = inv.project_references(root, root / TRACKING, runs, models)
    main = stores.pop(TRACKING)
    main.update(
        references=references,
        reference_json_files=len(sources),
        reference_files=[{"path": name, **entry} for name, (entry, _) in sources.items()],
        unresolved_references=sum(r.get("resolved") is False for r in references),
        project_references_scanned=True,
    )
    if main["unresolved_references"]:
        main["issues"].append(
            "Project references to run/model IDs missing from coordinated stores"
        )
    main["status"] = "review_required" if main["issues"] else "success"
    if stores:
        main["additional_stores"] = stores
    return main


def plain_uri(value, root):
    parsed = urlsplit(value)
    if parsed.scheme not in {"", "file"} or parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError("Nonlocal URI requires explicit tracking backup coverage")
    path = PurePosixPath(unquote(parsed.path))
    if not path.is_absolute():
        path = PurePosixPath(root) / path
    return os.path.normpath(str(path))


def coverage(inventory, historical_root, available, *, source_root=None, tracking=TRACKING):
    """Resolve local coverage from archived observations; verify never probes original paths."""
    if not isinstance(historical_root, str) or not Path(historical_root).is_absolute():
        raise ValueError("Missing absolute historical project root")
    data = inventory["database"]
    if inventory["unresolved_references"] or inventory["missing_local_artifact_locations"]:
        raise ValueError("Resolve missing tracking references/locations before backup")
    if any(row["status"] not in {"FINISHED", "FAILED", "KILLED"} for row in data["runs"]):
        raise ValueError("MLflow runs are not all terminal; stop writers and review")
    if any(row["status"] not in {2, 3} for row in data["logged_models"]):
        raise ValueError("MLflow logged models are not all terminal")
    # Their source/URI fields are not yet mapped by this inventory profile.
    if data["tables"].get("model_versions", {}).get("rows", 0):
        raise ValueError("Registered model versions require explicit source URI coverage")
    for name, table in data["tables"].items():
        extra_uri = set(table["columns"]) & {
            "artifact_uri",
            "artifact_location",
            "storage_location",
        }
        if table["rows"] and extra_uri and name not in {"experiments", "runs", "logged_models"}:
            raise ValueError("Additional artifact metadata requires coverage: " + name)
    reviews = []
    for table, field in (
        ("experiments", "artifact_location"),
        ("runs", "artifact_uri"),
        ("logged_models", "artifact_location"),
    ):
        for row in data[table]:
            location = row[field]
            if not location:
                continue
            if location["kind"] == "local_artifacts":
                relative = path_value(location["local_path"]).as_posix()
                if (
                    relative != tracking + "/artifacts"
                    and not relative.startswith(tracking + "/artifacts/")
                ) or relative not in available:
                    raise ValueError("Artifact location is not represented in the snapshot")
                if plain_uri(location["value"], historical_root) != str(
                    Path(historical_root) / relative
                ):
                    raise ValueError("Artifact URI disagrees with archived relative path")
            else:
                expected = str(Path(historical_root) / "mlruns/0")
                empty_default = (
                    table == "experiments"
                    and str(row["experiment_id"]) == "0"
                    and row["name"] == "Default"
                    and plain_uri(location["value"], historical_root) == expected
                    and not any(str(r["experiment_id"]) == "0" for r in data["runs"])
                    and not any(str(m["experiment_id"]) == "0" for m in data["logged_models"])
                )
                if not empty_default:
                    raise ValueError("External tracking location needs coverage before backup")
                if (
                    source_root is not None
                    and archive.checked_path(source_root, Path("mlruns/0")).exists()
                ):
                    raise ValueError("Default path now exists; review its contents before backup")
                reviews.append(
                    {
                        "experiment_id": 0,
                        "artifact_location": location["value"],
                        "path_exists": False,
                    }
                )
    return reviews


def manifest_id(document, expected=None):
    if (
        not isinstance(document, dict)
        or set(document) != {"schema", "files", "provenance"}
        or document["schema"] != SCHEMA
    ):
        raise ValueError("Unsupported MLflow backup manifest")
    files = document["files"]
    if not isinstance(files, list) or not 1 <= len(files) <= MAX_FILES:
        raise ValueError("MLflow backup file budget exceeded")
    seen, total = set(), 0
    for row in files:
        if not isinstance(row, dict) or set(row) != {"path", "bytes", "sha256"}:
            raise ValueError("Invalid MLflow backup file entry")
        path = path_value(row["path"]).as_posix()
        if (
            path in seen
            or path == TRACKING
            or (
                path not in {t + "/mlflow.db" for t in SUPPORTED_STORES}
                and other_database(Path(path))
            )
        ):
            raise ValueError("Duplicate path or unmanaged database in tracking snapshot")
        if (
            type(row["bytes"]) is not int
            or row["bytes"] < 0
            or not isinstance(row["sha256"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", row["sha256"])
        ):
            raise ValueError("Invalid tracking file size/hash")
        seen.add(path)
        total += row["bytes"]
    if total > MAX_BYTES or files != sorted(files, key=lambda r: r["path"]):
        raise ValueError("Tracking backup byte budget or canonical order invalid")
    if any(p.as_posix() in seen for name in seen for p in PurePosixPath(name).parents):
        raise ValueError("Conflicting tracking snapshot paths")
    provenance = document["provenance"]
    required = {
        DATABASE: "database_sha256",
        INVENTORY: "inventory_sha256",
        CAPTURE: "capture_sha256",
    }
    if AUXILIARY + "/mlflow.db" in seen:
        required.update(
            {
                AUXILIARY + "/mlflow.db": "interface_smoke_database_sha256",
                AUXILIARY + "/storage-inventory.json": "interface_smoke_inventory_sha256",
            }
        )
    if not isinstance(provenance, dict) or set(provenance) != set(required.values()):
        raise ValueError("Invalid tracking backup provenance")
    rows = {r["path"]: r for r in files}
    if any(
        name not in rows or rows[name]["sha256"] != provenance[key]
        for name, key in required.items()
    ):
        raise ValueError("Tracking provenance does not match payload")
    raw = archive.encoded(document)
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ValueError("Tracking manifest metadata budget exceeded")
    identity = hashlib.sha256(raw).hexdigest()
    if expected is not None and identity != expected:
        raise ValueError("Tracking manifest identity mismatch")
    return identity


def copy_file(source, target, remaining, *, database=False):
    before = inv.signature(source)
    count = 0
    digest = hashlib.sha256()
    target.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as incoming, target.open("xb") as outgoing:
        target.chmod(0o600)
        while block := incoming.read(1024 * 1024):
            if count == 0 and not database and block.startswith(b"SQLite format 3\x00"):
                raise ValueError("Additional SQLite payload requires a consistent snapshot policy")
            count += len(block)
            if count > remaining:
                raise ValueError("Tracking source exceeds byte budget during capture")
            digest.update(block)
            outgoing.write(block)
    if inv.signature(source) != before:
        raise ValueError("Tracking source changed during capture; pause all writers")
    return {"bytes": count, "sha256": digest.hexdigest()}


def online_backup(source, destination):
    deadline = time.monotonic() + 30
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as original:
        original.execute("PRAGMA query_only=ON")
        original.execute("BEGIN")
        page_size = original.execute("PRAGMA page_size").fetchone()[0]

        def progress(status, remaining, total):
            if time.monotonic() > deadline or total * page_size > MAX_BYTES:
                raise ValueError("SQLite backup time/page budget exceeded")

        with closing(sqlite3.connect(destination)) as target:
            original.backup(target, pages=256, progress=progress, sleep=0.05)
            target.execute("PRAGMA journal_mode=DELETE")
            target.commit()
        original.rollback()
    destination.chmod(0o600)


def verify_store(directory, tracking, inventory, rows, historical_root, available):
    with closing(
        sqlite3.connect((directory / (tracking + "/mlflow.db")).as_uri() + "?mode=ro", uri=True)
    ) as connection:
        connection.execute("PRAGMA query_only=ON")
        if connection.execute("PRAGMA journal_mode").fetchone()[
            0
        ] != "delete" or connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise ValueError("Tracking SQLite integrity/journal check failed")
    data = inv.inspect_database(directory / (tracking + "/mlflow.db"))
    saved = inventory["database"]
    if (
        data["tables"] != saved["tables"]
        or data["table_fingerprint_sha256"] != saved["table_fingerprint_sha256"]
    ):
        raise ValueError("Tracking database logical content differs from archived inventory")
    for table, field in (
        ("experiments", "artifact_location"),
        ("runs", "artifact_uri"),
        ("logged_models", "artifact_location"),
    ):
        plain = []
        for row in saved[table]:
            item = dict(row)
            if item[field]:
                item[field] = item[field]["value"]
            plain.append(item)
        if data[table] != plain:
            raise ValueError("Archived inventory entities disagree with database")
    for row in inventory["artifact_files"]:
        if rows.get(row["path"]) != row:
            raise ValueError("Artifact bytes disagree with archived inventory")
    artifacts = {row["path"] for row in inventory["artifact_files"]}
    if artifacts != {name for name in rows if name.startswith(tracking + "/artifacts/")}:
        raise ValueError("Artifact set differs from archived inventory")
    reviews = coverage(inventory, historical_root, available, tracking=tracking)
    return (
        {
            "tracking_root": tracking,
            "experiments": len(data["experiments"]),
            "runs": len(data["runs"]),
            "logged_models": len(data["logged_models"]),
            "metadata_rows": {
                name: r["rows"]
                for name, r in data["tables"].items()
                if name in {"params", "metrics", "latest_metrics", "tags"}
            },
            "database_table_fingerprint_sha256": data["table_fingerprint_sha256"],
            "database_bytes": rows[tracking + "/mlflow.db"]["bytes"],
            "sqlite_snapshot_sha256": rows[tracking + "/mlflow.db"]["sha256"],
            "artifact_files": len(artifacts),
            "artifact_bytes": sum(rows[p]["bytes"] for p in artifacts),
            "default_reviews": len(reviews),
        },
        artifacts,
        reviews,
    )


def verify_snapshot(directory, document):
    identity = manifest_id(document)
    expected = {row["path"] for row in document["files"]}
    actual = []
    for path in inv.files_under(directory, directory):
        relative = path.relative_to(directory).as_posix()
        if relative != "manifest.json":
            actual.append(relative)
    if set(actual) != expected:
        raise ValueError("Tracking backup contains missing or unexpected files")
    for row in document["files"]:
        path = archive.checked_path(directory, path_value(row["path"]))
        if path.stat().st_size != row["bytes"]:
            raise ValueError("Tracking file size differs from manifest")
        entry, _ = inv.read_file(path)
        if entry != {"bytes": row["bytes"], "sha256": row["sha256"]}:
            raise ValueError("Tracking file integrity mismatch: " + row["path"])
        with path.open("rb") as source:
            if (
                row["path"] not in {t + "/mlflow.db" for t in SUPPORTED_STORES}
                and source.read(16) == b"SQLite format 3\x00"
            ):
                raise ValueError("Additional SQLite database in tracking payload")
    inventory = json.loads((directory / INVENTORY).read_bytes())
    marker = json.loads((directory / CAPTURE).read_bytes())
    if marker["schema"] != SNAPSHOT_SCHEMA or inventory["schema"] != inv.SCHEMA:
        raise ValueError("Unsupported tracking snapshot metadata")
    if (
        marker.get("tracking_root") != TRACKING
        or marker.get("method") != "sqlite_online_backup_from_disposable_file_capture"
        or marker.get("writer_quiescence") != "operator_declared_and_source_mutation_checks"
        or marker.get("shared_writer_lock_acquired") is not False
        or not isinstance(marker.get("producer_code_sha256"), str)
        or not re.fullmatch(r"[a-f0-9]{64}", marker["producer_code_sha256"])
    ):
        raise ValueError("Invalid tracking snapshot capture method")
    directories = marker["directories"]
    if not isinstance(directories, list) or directories != sorted(set(directories)):
        raise ValueError("Invalid archived tracking directories")
    for value in directories:
        path_value(value)
    actual_files, actual_directories = tree(directory)
    if actual_directories != directories:
        raise ValueError("Tracking snapshot directory set differs")
    stores = store_inventories(inventory)
    if set(stores) - set(SUPPORTED_STORES) or marker.get("additional_stores", []) != sorted(
        set(stores) - {TRACKING}
    ):
        raise ValueError("Unsupported tracking store coverage")
    rows = {row["path"]: row for row in document["files"]}
    represented = {t + "/mlflow.db" for t in stores}
    if {name for name in rows if other_database(Path(name))} != represented:
        raise ValueError("Database set differs from coordinated store inventories")
    artifacts, reviews, summaries = set(), [], []
    metadata = {CAPTURE}
    for tracking, store in stores.items():
        if store["tracking_root"] != tracking or store["schema"] != inv.SCHEMA:
            raise ValueError("Store inventory root/schema mismatch")
        inventory_path = tracking + "/storage-inventory.json"
        if tracking != TRACKING and json.loads((directory / inventory_path).read_bytes()) != store:
            raise ValueError("Auxiliary inventory differs from coordinated metadata")
        summary, store_artifacts, store_reviews = verify_store(
            directory,
            tracking,
            store,
            rows,
            marker["historical_project_root"],
            set(actual_files) | set(actual_directories),
        )
        summaries.append(summary)
        artifacts.update(store_artifacts)
        reviews.extend(store_reviews)
        metadata.update({tracking + "/mlflow.db", inventory_path})
    ancillary = expected - artifacts - metadata
    if sorted(ancillary) != marker["ancillary_files"]:
        raise ValueError("Ancillary tracking file coverage differs")
    if reviews != marker["default_reviews"]:
        raise ValueError("Default coverage observation differs")
    return {
        "bundle_id": identity,
        "producer_code_sha256": marker["producer_code_sha256"],
        **{k: v for k, v in summaries[0].items() if k != "tracking_root"},
        "stores": summaries,
        "store_count": len(stores),
        "total_runs": sum(r["runs"] for r in summaries),
        "total_logged_models": sum(r["logged_models"] for r in summaries),
        "artifact_files": len(artifacts),
        "artifact_bytes": sum(rows[p]["bytes"] for p in artifacts),
        "ancillary_files": len(ancillary),
        "archive_files": len(rows),
        "bytes": sum(row["bytes"] for row in rows.values()),
        "default_reviews": len(reviews),
        "sqlite_integrity_verified": True,
        "sqlite_foreign_keys_verified": True,
        "logical_table_contents_verified": True,
        "historical_uris_preserved": True,
        "original_source_read": False,
        "models_loaded": False,
        "mlflow_initialized": False,
        "refit": False,
        "s3_accessed": False,
        "transfer_implemented": False,
        "migration_implemented": False,
        "primary_storage_migrated": False,
        "model_reload_validated": False,
        "project_wide_storage_inventory_complete": False,
    }


def prepare(root, *, writers_stopped=False):
    root = root.resolve()
    if not writers_stopped:
        raise ValueError(
            "Declare stopped MLflow UI/training/evaluation writers with --writers-stopped"
        )
    if not archive.checked_path(root, Path(DATABASE)).is_file():
        raise ValueError("Existing MLflow database required; no store will be initialized")
    work = archive.workspace(root)
    before = normalized_inventory(root, work)
    files, directories = tree(root)
    stores = store_inventories(before)
    metadata = {CAPTURE} | {t + "/storage-inventory.json" for t in stores}
    if metadata & set(files):
        raise ValueError("Backup metadata path collides with an original tracking file")
    databases = {t + "/mlflow.db" for t in stores}
    sidecars = {
        t + "/" + name for t in stores for name in inv.DATABASE_FILES if name != "mlflow.db"
    }
    payload = set(files) - sidecars - databases
    if (
        len(payload) + len(databases) + len(metadata) > MAX_FILES
        or sum(stamp[2] for stamp in files.values()) > MAX_BYTES
    ):
        raise ValueError("Tracking preflight exceeds 1024 files/256 MiB; originals untouched")
    if any(other_database(Path(name)) for name in payload):
        raise ValueError("Other databases/sidecars need explicit snapshot coverage")
    reviews = []
    for tracking, store in stores.items():
        reviews.extend(
            coverage(
                store,
                str(root),
                set(files) | set(directories),
                source_root=root,
                tracking=tracking,
            )
        )
    if shutil.disk_usage(work).free < sum(s[2] for s in files.values()) * 4 + 16 * 1024 * 1024:
        raise ValueError("Insufficient free space for tracking capture and verification")
    bundles = archive.checked_path(work, Path("bundles"))
    bundles.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mlflow-backup-", dir=work) as temporary:
        base = Path(temporary)
        raw = base / "raw"
        raw.mkdir()
        stage = base / "payload"
        stage.mkdir(mode=0o700)
        for value in directories:
            (stage / path_value(value)).mkdir(parents=True, exist_ok=True)
        used = 0
        for tracking, store in stores.items():
            copied = raw / path_value(tracking)
            copied.mkdir(parents=True)
            for item in store["source_database_files"]:
                name = Path(item["path"]).name
                if name == "mlflow.db-shm":
                    continue
                entry = copy_file(root / item["path"], copied / name, MAX_BYTES, database=True)
                if entry != {"bytes": item["bytes"], "sha256": item["sha256"]}:
                    raise ValueError("Database changed since source inventory")
            destination_db = stage / tracking / "mlflow.db"
            online_backup(copied / "mlflow.db", destination_db)
            used += destination_db.stat().st_size
        for name in sorted(payload):
            entry = copy_file(root / name, stage / path_value(name), MAX_BYTES - used)
            used += entry["bytes"]
        for tracking, store in stores.items():
            target_inventory = stage / tracking / "storage-inventory.json"
            target_inventory.write_bytes(archive.encoded(store))
            target_inventory.chmod(0o600)
        marker = {
            "schema": SNAPSHOT_SCHEMA,
            "method": "sqlite_online_backup_from_disposable_file_capture",
            "historical_project_root": str(root),
            "tracking_root": TRACKING,
            "directories": directories,
            "default_reviews": reviews,
            "ancillary_files": sorted(
                payload - {r["path"] for store in stores.values() for r in store["artifact_files"]}
            ),
            "writer_quiescence": "operator_declared_and_source_mutation_checks",
            "shared_writer_lock_acquired": False,
            "producer_code_sha256": producer_sha256(),
            "producer_python": sys.version.split()[0],
        }
        if len(stores) > 1:
            marker["additional_stores"] = sorted(set(stores) - {TRACKING})
        (stage / CAPTURE).write_bytes(archive.encoded(marker))
        for path in (stage / INVENTORY, stage / CAPTURE):
            path.chmod(0o600)
        rows = []
        for path in inv.files_under(stage, stage):
            entry, _ = inv.read_file(path)
            rows.append({"path": path.relative_to(stage).as_posix(), **entry})
        rows.sort(key=lambda row: row["path"])
        required = {
            DATABASE: "database_sha256",
            INVENTORY: "inventory_sha256",
            CAPTURE: "capture_sha256",
        }
        if AUXILIARY in stores:
            required.update(
                {
                    AUXILIARY + "/mlflow.db": "interface_smoke_database_sha256",
                    AUXILIARY + "/storage-inventory.json": "interface_smoke_inventory_sha256",
                }
            )
        document = {
            "schema": SCHEMA,
            "files": rows,
            "provenance": {
                required[row["path"]]: row["sha256"] for row in rows if row["path"] in required
            },
        }
        identity = manifest_id(document)
        (stage / "manifest.json").write_bytes(archive.encoded(document))
        (stage / "manifest.json").chmod(0o600)
        result = verify_snapshot(stage, document)
        after = normalized_inventory(root, work)
        if tree(root) != (files, directories) or after != before:
            raise ValueError("Tracking source/reference set changed; pause writers and retry")
        for tracking, store in store_inventories(after).items():
            coverage(
                store,
                str(root),
                set(files) | set(directories),
                source_root=root,
                tracking=tracking,
            )
        destination = archive.checked_path(bundles, Path(identity))
        status = "prepared_verified_locally"
        if destination.exists():
            verify(root, identity)
            status = "reused_verified_locally"
        else:
            stage.rename(destination)
    return result | {
        "status": status,
        "schema": SCHEMA,
        "manifest": str(destination / "manifest.json"),
        "complete": True,
        "sqlite_online_backup": True,
        "source_database_opened": False,
        "original_source_read": True,
        "source_database_connection_opened": False,
        "original_database_rows_modified": False,
        "shared_writer_lock_acquired": False,
        "writer_quiescence": "operator_declared_and_source_mutation_checks",
        "budget": {"max_files": MAX_FILES, "max_bytes": MAX_BYTES},
        "scope": "offline_quiesced_mlflow_metadata_and_artifact_snapshot_not_model_reload_or_migration",
    }


def verify(root, identity):
    identity = archive.bundle_id(identity)
    target = archive.checked_path(archive.workspace(root), Path("bundles") / identity)
    manifest = archive.checked_path(target, Path("manifest.json"))
    if manifest.stat().st_size > MAX_MANIFEST_BYTES:
        raise ValueError("Tracking manifest metadata budget exceeded")
    raw = manifest.read_bytes()
    document = json.loads(raw)
    manifest_id(document, identity)
    if raw != archive.encoded(document):
        raise ValueError("Noncanonical tracking manifest")
    return verify_snapshot(target, document) | {
        "status": "verified_locally",
        "complete": True,
        "scope": "offline_mlflow_snapshot_integrity_and_metadata_verification_not_model_reload_or_migration",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "verify"])
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--writers-stopped", action="store_true")
    parser.add_argument("--bundle-id")
    args = parser.parse_args(argv)
    if args.command == "verify" and not args.bundle_id:
        parser.error("--bundle-id is required")
    try:
        root = args.project_root.resolve()
        result = (
            prepare(root, writers_stopped=args.writers_stopped)
            if args.command == "prepare"
            else verify(root, args.bundle_id)
        )
        receipts = archive.checked_path(archive.workspace(root), Path("receipts"))
        receipts.mkdir(exist_ok=True)
        with tempfile.NamedTemporaryFile(
            prefix="mlflow-backup-" + args.command + "-",
            suffix=".json",
            dir=receipts,
            delete=False,
        ) as output:
            output.write(archive.encoded(result))
            result["report"] = output.name
        print(json.dumps(result, indent=2, allow_nan=False))
        return 0
    except (ValueError, OSError, KeyError, TypeError, sqlite3.Error) as error:
        parser.exit(1, f"MLflow backup stopped: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
