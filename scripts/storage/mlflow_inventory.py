"""Offline inventory of the existing MLflow store; never initialize or migrate it."""

import argparse
from collections import Counter
from contextlib import closing
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import tempfile
from urllib.parse import unquote, urlsplit

SCHEMA = "fraud_mlflow_inventory_v1"
ROOT = Path(__file__).resolve().parents[2]
DATABASE_FILES = ("mlflow.db", "mlflow.db-wal", "mlflow.db-shm", "mlflow.db-journal")
REFERENCE_KEYS = {
    "model_uri",
    "logged_model_uri",
    "run_id",
    "mlflow_run_id",
    "tracking_uri",
    "tracking_root",
    "artifact_uri",
    "artifact_location",
}


class InventoryError(ValueError):
    """The inventory cannot establish a stable, bounded local source."""


def encoded(value):
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode()


def safe(root, path):
    """Do not follow source or destination symlinks, including parent components."""
    path = Path(os.path.abspath(path))
    if not path.is_relative_to(root):
        raise InventoryError("Path outside project; inventory requires a local project source")
    for part in (path, *path.parents):
        if part == root:
            break
        if part.is_symlink():
            raise InventoryError("Symlink in inventory path: " + str(part.relative_to(root)))
    return path


def signature(path):
    info = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(info.st_mode):
        raise InventoryError("Expected a regular inventory file")
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def read_file(path, destination=None):
    """Stream a fingerprint and optionally copy bytes, checking for source mutation."""
    before = signature(path)
    digest = hashlib.sha256()
    with path.open("rb") as source:
        if destination is None:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        else:
            with destination.open("xb") as target:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
                    target.write(chunk)
    if signature(path) != before:
        raise InventoryError("Source changed during inventory; pause MLflow writers and retry")
    return {"bytes": before[2], "sha256": digest.hexdigest()}, before


def files_under(root, directory):
    safe(root, directory)
    if not directory.exists():
        return []
    if not directory.is_dir():
        raise InventoryError("Expected an artifact directory")
    result = []
    for current, directories, files in os.walk(directory, followlinks=False):
        for name in sorted(directories + files):
            safe(root, Path(current) / name)
        for name in sorted(files):
            path = Path(current) / name
            signature(path)
            result.append(path)
    return sorted(result)


def database_files(root, tracking):
    paths = [safe(root, tracking / name) for name in DATABASE_FILES]
    return [path for path in paths if path.exists()]


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def cell(value):
    if isinstance(value, bytes):
        return ["bytes", value.hex()]
    if isinstance(value, float):
        return ["float", value.hex()]
    return [type(value).__name__, value]


def inspect_database(path):
    """Only connect to the disposable copy; no MLflow/SQLAlchemy initialization."""
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        connection.execute("BEGIN")
        if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise InventoryError("SQLite quick_check failed on disposable copy")
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise InventoryError("SQLite foreign keys failed on disposable copy")
        names = [
            r[0]
            for r in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        tables = {}
        for name in names:
            columns = [r[1] for r in connection.execute(f"PRAGMA table_info({quote(name)})")]
            if not columns:
                raise InventoryError("Unsupported empty SQLite table schema")
            digest = hashlib.sha256(encoded(columns))
            count = 0
            order = ",".join(quote(c) for c in columns)
            for row in connection.execute(f"SELECT * FROM {quote(name)} ORDER BY {order}"):
                digest.update(encoded([cell(v) for v in row]))
                count += 1
            tables[name] = {
                "columns": columns,
                "rows": count,
                "content_sha256": digest.hexdigest(),
            }

        def select(table, columns, required=True):
            if table not in tables:
                if required:
                    raise InventoryError("Missing MLflow table: " + table)
                return []
            if not set(columns).issubset(tables[table]["columns"]):
                raise InventoryError("Unsupported MLflow columns: " + table)
            fields = ",".join(quote(c) for c in columns)
            return [
                dict(zip(columns, r, strict=True))
                for r in connection.execute(
                    f"SELECT {fields} FROM {quote(table)} ORDER BY {fields}"
                )
            ]

        experiments = select(
            "experiments", ["experiment_id", "name", "artifact_location", "lifecycle_stage"]
        )
        runs = select(
            "runs", ["run_uuid", "experiment_id", "status", "lifecycle_stage", "artifact_uri"]
        )
        models = select(
            "logged_models",
            ["model_id", "experiment_id", "source_run_id", "status", "artifact_location"],
            False,
        )
        revisions = select("alembic_version", ["version_num"])
        return {
            "tables": tables,
            "table_fingerprint_sha256": hashlib.sha256(encoded(tables)).hexdigest(),
            "alembic_revisions": [r["version_num"] for r in revisions],
            "experiments": experiments,
            "runs": runs,
            "logged_models": models,
            "quick_check": "ok",
            "foreign_keys_verified": True,
        }


def uri_info(root, tracking, value):
    """Classify URIs without fetching or traversing locations outside artifacts/."""
    parsed = urlsplit(value)
    if parsed.scheme in {"runs", "models"}:
        return {
            "value": value
            if not (parsed.query or parsed.fragment or parsed.netloc)
            else "redacted",
            "kind": parsed.scheme,
            "local_path": None,
        }
    if parsed.scheme not in {"", "file"} or parsed.netloc or parsed.query or parsed.fragment:
        return {
            "value": f"{parsed.scheme or 'unknown'}:<redacted>",
            "kind": "external_or_sensitive",
            "local_path": None,
        }
    path = Path(unquote(parsed.path))
    if not path.is_absolute():
        path = root / path
    path = Path(os.path.abspath(path))
    if not path.is_relative_to(tracking / "artifacts"):
        return {"value": value, "kind": "local_outside_artifacts_not_read", "local_path": None}
    safe(root, path)
    return {
        "value": value,
        "kind": "local_artifacts",
        "local_path": path.relative_to(root).as_posix(),
        "exists": path.exists(),
    }


def project_references(root, tracking, runs, models):
    """Inspect JSON manifests/reports in data/, excluding tracking and archive copies."""
    references, sources = [], {}
    run_ids = {r["run_uuid"] for r in runs}
    model_ids = {m["model_id"] for m in models}
    for current, directories, files in os.walk(root / "data", followlinks=False):
        current = Path(current)
        directories[:] = sorted(
            d
            for d in directories
            if current / d
            not in {
                root / "data/storage",
                tracking,
            }
        )
        for d in directories:
            safe(root, current / d)
        for name in sorted(files):
            if not name.endswith(".json"):
                continue
            path = safe(root, current / name)
            entry, stamp = read_file(path)
            try:
                data = json.loads(path.read_bytes())
            except (ValueError, UnicodeError) as exc:
                raise InventoryError(
                    "Invalid project JSON: " + path.relative_to(root).as_posix()
                ) from exc
            if signature(path) != stamp:
                raise InventoryError("Project reference changed during inventory")
            relative = path.relative_to(root).as_posix()
            sources[relative] = (entry, stamp)

            def visit(value, pointer="", relative=relative):
                if isinstance(value, dict):
                    for key, item in sorted(value.items()):
                        location = pointer + "/" + key.replace("~", "~0").replace("/", "~1")
                        if key in REFERENCE_KEYS and isinstance(item, str) and item:
                            if key in {"run_id", "mlflow_run_id"}:
                                if re.fullmatch(r"[0-9a-f]{32}", item):
                                    identified = (
                                        key == "mlflow_run_id"
                                        or item in run_ids
                                        or any(
                                            p in {"tracking", "mlflow"} for p in pointer.split("/")
                                        )
                                        or Path(relative).name == "mlflow.json"
                                    )
                                    references.append(
                                        {
                                            "file": relative,
                                            "pointer": location,
                                            "kind": "run_id"
                                            if identified
                                            else "unclassified_run_id",
                                            "value": item,
                                            "resolved": item in run_ids if identified else None,
                                        }
                                    )
                            else:
                                info = uri_info(root, tracking, item)
                                if info["kind"] == "models":
                                    identifier = urlsplit(item).path.lstrip("/")
                                    info["resolved"] = (
                                        identifier in model_ids
                                        if identifier.startswith("m-")
                                        else None
                                    )
                                if info["kind"] == "runs":
                                    info["resolved"] = (
                                        urlsplit(item).path.lstrip("/").split("/")[0] in run_ids
                                    )
                                references.append({"file": relative, "pointer": location, **info})
                        visit(item, location)
                elif isinstance(value, list):
                    for i, item in enumerate(value):
                        visit(item, pointer + "/" + str(i))

            visit(data)
    return references, sources


def inventory(root=ROOT, tracking_root=None, *, temporary_directory=None, scan_references=True):
    root = Path(root).resolve()
    tracking = safe(root, tracking_root or root / "data/tracking")
    if not (tracking / "mlflow.db").is_file():
        raise InventoryError("Existing data/tracking/mlflow.db required; nothing was initialized")
    initial_db = database_files(root, tracking)
    database_state = {}
    if temporary_directory is not None:
        temporary_directory = safe(root, temporary_directory)
        if not temporary_directory.is_dir():
            raise InventoryError("Managed inventory staging directory must exist")
    with tempfile.TemporaryDirectory(
        prefix="fraud-mlflow-inventory-", dir=temporary_directory
    ) as temporary:
        copied = Path(temporary)
        for path in initial_db:
            destination = copied / path.name if path.name != "mlflow.db-shm" else None
            database_state[path] = read_file(path, destination)
        if (copied / "mlflow.db-journal").exists() and (
            copied / "mlflow.db-journal"
        ).stat().st_size:
            raise InventoryError(
                "Rollback journal present; stop writers and resolve source state before inventory"
            )
        database = inspect_database(copied / "mlflow.db")
        artifacts = files_under(root, tracking / "artifacts")
        entries, artifact_state = [], {}
        for path in artifacts:
            entry, stamp = read_file(path)
            artifact_state[path] = stamp
            entries.append({"path": path.relative_to(root).as_posix(), **entry})
        references, reference_state = (
            project_references(root, tracking, database["runs"], database["logged_models"])
            if scan_references
            else ([], {})
        )
        for table, field in (
            ("experiments", "artifact_location"),
            ("runs", "artifact_uri"),
            ("logged_models", "artifact_location"),
        ):
            for row in database[table]:
                if row[field]:
                    row[field] = uri_info(root, tracking, row[field])
        # Re-enumerate reference inputs too: newly created JSON must not be silently omitted.
        again, final_references = (
            project_references(root, tracking, database["runs"], database["logged_models"])
            if scan_references
            else ([], {})
        )
        if final_references != reference_state or again != references:
            raise InventoryError("Project references changed; pause writers and retry")
        if database_files(root, tracking) != initial_db:
            raise InventoryError("Database sidecar set changed; pause writers and retry")
        for path, previous in database_state.items():
            if read_file(path) != previous:
                raise InventoryError("Database changed; pause writers and retry")
        if files_under(root, tracking / "artifacts") != artifacts:
            raise InventoryError("Artifact set changed; pause writers and retry")
        if any(signature(p) != stamp for p, stamp in artifact_state.items()):
            raise InventoryError("Artifact changed; pause writers and retry")
    statuses = dict(Counter(r["status"] for r in database["runs"]))
    uris = [
        row[field]
        for table, field in (
            ("experiments", "artifact_location"),
            ("runs", "artifact_uri"),
            ("logged_models", "artifact_location"),
        )
        for row in database[table]
        if row[field]
    ]
    issues = []
    if statuses.get("RUNNING", 0) or any(
        m["status"] not in {2, 3} for m in database["logged_models"]
    ):
        issues.append("Unfinished runs/models require review before a coordinated backup")
    unresolved = sum(r.get("resolved") is False for r in references)
    if unresolved:
        issues.append("Project references to run/model IDs missing from this store")
    missing = sum(u.get("exists") is False for u in uris)
    if missing:
        issues.append(
            "Some local artifact locations do not exist; distinguish empty/failed runs from lost artifacts"
        )
    outside = sum(u["kind"] != "local_artifacts" for u in uris)
    if outside:
        issues.append(
            "Artifact locations outside managed local artifacts require explicit coverage"
        )
    return {
        "schema": SCHEMA,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "status": "review_required" if issues else "success",
        "complete": True,
        "scope": "offline_local_tracking_inventory_not_backup_or_migration",
        "project_references_scanned": scan_references,
        "tracking_root": tracking.relative_to(root).as_posix(),
        "backend": "sqlite",
        "database": database,
        "source_database_files": [
            {"path": p.relative_to(root).as_posix(), **entry}
            for p, (entry, _) in database_state.items()
        ],
        "artifact_files": entries,
        "artifact_files_count": len(entries),
        "artifact_bytes": sum(e["bytes"] for e in entries),
        "artifact_manifest_sha256": hashlib.sha256(encoded(entries)).hexdigest(),
        "run_statuses": statuses,
        "uri_kinds": dict(Counter(u["kind"] for u in uris)),
        "references": references,
        "reference_json_files": len(reference_state),
        "reference_files": [
            {"path": name, **entry} for name, (entry, _) in reference_state.items()
        ],
        "unresolved_references": unresolved,
        "missing_local_artifact_locations": missing,
        "issues": issues,
        "source_stable_during_observation": True,
        "source_database_opened": False,
        "mlflow_initialized": False,
        "models_loaded": False,
        "network_accessed": False,
        "backup_validated": False,
        "migration_implemented": False,
        "primary_storage_migrated": False,
        "limits": [
            "Pause MLflow UI, training and evaluation writers before running; mutation checks do not replace a shared writer lock or coordinated backup",
            "Only data/tracking/artifacts bytes are inventoried; outside/remote locations are classified without access",
            "Project reference scan covers JSON under data/ except tracking/storage; opaque tags, MLmodel YAML, notebooks and historical evidence are not rewritten or exhaustively linked",
            "Table fingerprints describe the SQLite snapshot, not a PostgreSQL cross-dialect equivalence proof",
            "Unreferenced artifacts are not declared disposable; file contents are hashed without model deserialization",
        ],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    try:
        root = args.repo.resolve()
        result = inventory(root)
        directory = safe(root, root / "data/storage/inventory")
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", prefix="mlflow-", suffix=".json", dir=directory, delete=False
        ) as target:
            os.chmod(target.name, 0o600)
            json.dump(result, target, indent=2, allow_nan=False)
            target.write("\n")
        summary = {
            key: result[key]
            for key in (
                "status",
                "complete",
                "scope",
                "backend",
                "artifact_files_count",
                "artifact_bytes",
                "run_statuses",
                "uri_kinds",
                "reference_json_files",
                "unresolved_references",
                "missing_local_artifact_locations",
                "issues",
                "source_database_opened",
                "mlflow_initialized",
                "models_loaded",
                "network_accessed",
                "backup_validated",
                "migration_implemented",
            )
        }
        summary.update(
            alembic_revisions=result["database"]["alembic_revisions"],
            metadata_rows={
                name: result["database"]["tables"].get(name, {}).get("rows", 0)
                for name in ("params", "metrics", "latest_metrics", "tags", "logged_models")
            },
            experiments=len(result["database"]["experiments"]),
            runs=len(result["database"]["runs"]),
            logged_models=len(result["database"]["logged_models"]),
            registered_models=result["database"]["tables"]
            .get("registered_models", {})
            .get("rows", 0),
            model_versions=result["database"]["tables"].get("model_versions", {}).get("rows", 0),
            report=str(Path(target.name)),
        )
        print(json.dumps(summary, indent=2, allow_nan=False))
        return 0
    except (InventoryError, OSError, sqlite3.Error) as exc:
        print(f"MLflow inventory stopped: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
