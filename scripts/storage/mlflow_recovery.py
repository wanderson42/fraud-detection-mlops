"""Rehearse MLflow history/model reads on disposable copies of two restored archives."""

import argparse
from contextlib import closing
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile

from scripts.storage import archive
from scripts.storage import mlflow_backup as backup
from scripts.storage import mlflow_inventory as inv

RELEASE_PATH = Path("data/serving/reference-v1")
REFERENCE = Path("references/evidence/laboratory_serving_native_validation_2026-10-09.json")
LOCATION_FIELDS = {
    "experiments": ("experiment_id", "artifact_location"),
    "runs": ("run_uuid", "artifact_uri"),
    "logged_models": ("model_id", "artifact_location"),
}


def restored_snapshot(root, identity):
    identity = archive.bundle_id(identity)
    directory = archive.checked_path(archive.workspace(root), Path("restored") / identity)
    path = archive.checked_path(directory, Path("manifest.json"))
    if path.stat().st_size > backup.MAX_MANIFEST_BYTES:
        raise ValueError("Oversized snapshot manifest")
    raw = path.read_bytes()
    document = json.loads(raw)
    backup.manifest_id(document, identity)
    if raw != archive.encoded(document):
        raise ValueError("Noncanonical snapshot manifest")
    return directory, document, backup.verify_snapshot(directory, document)


def release_control(root, identity, reference_path):
    identity = archive.bundle_id(identity)
    directory = archive.checked_path(archive.workspace(root), Path("restored") / identity)
    path = archive.checked_path(directory, Path("manifest.json"))
    if path.stat().st_size > archive.BRONZE_MAX_MANIFEST_BYTES:
        raise ValueError("Oversized release manifest")
    document = json.loads(path.read_bytes())
    archive.validate_manifest(document, identity)
    archive.verify_files(directory, document)
    reference = json.loads(archive.checked_path(root, reference_path).read_bytes())
    release = archive.checked_path(directory, RELEASE_PATH)
    raw = (release / "manifest.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != reference["release"]["manifest_sha256"]:
        raise ValueError("Release manifest differs from external historical pin")
    metadata = json.loads(raw)
    if metadata["smoke_file"] != "smoke.json":
        raise ValueError("Unsupported smoke path")
    smoke_path = archive.checked_path(release, Path("smoke.json"))
    if archive.digest(smoke_path) != metadata["smoke_sha256"]:
        raise ValueError("Release smoke bytes differ from pinned metadata")
    smoke = json.loads(smoke_path.read_bytes())
    expected = reference["wheel_check"]["http_score"]
    if (
        metadata["model_sha256"] != reference["release"]["model_sha256"]
        or metadata["serving_release_id"] != reference["release"]["serving_release_id"]
        or expected["model_sha256"] != metadata["model_sha256"]
        or expected["serving_release_id"] != metadata["serving_release_id"]
        or expected["transaction_id"] != smoke["request"]["transaction_id"]
        or not math.isclose(smoke["expected_score"], expected["score"], rel_tol=0, abs_tol=1e-12)
    ):
        raise ValueError("Historical release identity/score disagrees with smoke")
    return {
        "model_uri": metadata["source"]["model_uri"],
        "model_sha256": metadata["model_sha256"],
        "serving_release_id": metadata["serving_release_id"],
        "expected_score": expected["score"],
        "request": smoke["request"],
        "runtime_environment": metadata["runtime_environment"],
    }


def table_rows(connection, table, changes=()):
    columns = [r[1] for r in connection.execute(f"PRAGMA table_info({inv.quote(table)})")]
    overrides = {(r["id"], r["old"]): r["new"] for r in changes}
    rows = []
    for row in connection.execute(f"SELECT * FROM {inv.quote(table)}"):
        values = list(row)
        if changes:
            key, field = LOCATION_FIELDS[table]
            index = columns.index(field)
            values[index] = overrides.get((row[columns.index(key)], row[index]), row[index])
        rows.append(inv.encoded([inv.cell(v) for v in values]))
    return columns, sorted(rows)


def map_locations(candidate, inventory):
    """Reconcile every row/cell against the declared URI-only transformation."""
    mapping, stores = [], []
    for tracking, saved in backup.store_inventories(inventory).items():
        path = candidate / tracking / "mlflow.db"
        updates = {}
        for table, (key, field) in LOCATION_FIELDS.items():
            updates[table] = []
            for row in saved["database"][table]:
                location = row[field]
                if not location:
                    continue
                relative = (
                    location["local_path"] if location["kind"] == "local_artifacts" else "mlruns/0"
                )
                target = archive.checked_path(candidate, Path(relative))
                change = {
                    "store": tracking,
                    "table": table,
                    "id": row[key],
                    "field": field,
                    "old": location["value"],
                    "new": target.as_uri(),
                }
                updates[table].append(change)
                mapping.append(change)
        with closing(sqlite3.connect(path)) as connection:
            tables = list(saved["database"]["tables"])
            expected = {
                name: table_rows(connection, name, updates.get(name, ())) for name in tables
            }
            for table, changes in updates.items():
                key, field = LOCATION_FIELDS[table]
                for change in changes:
                    count = connection.execute(
                        f"UPDATE {inv.quote(table)} SET {inv.quote(field)}=? WHERE {inv.quote(key)}=? AND {inv.quote(field)}=?",
                        (change["new"], change["id"], change["old"]),
                    ).rowcount
                    if count != 1:
                        raise ValueError("URI mapping did not match exactly one row")
            connection.commit()
            if any(table_rows(connection, name) != expected[name] for name in tables):
                raise ValueError("Database cells changed outside declared URI map")
        stores.append({"tracking_root": tracking, "database": inv.inspect_database(path)})
    return stores, mapping


def rehearse(root, identity, release_id, reference_path=REFERENCE):
    root = root.resolve()
    source, document, verified = restored_snapshot(root, identity)
    control = release_control(root, release_id, reference_path)
    work = archive.workspace(root)
    if shutil.disk_usage(work).free < verified["bytes"] * 2 + 16 * 1024 * 1024:
        raise ValueError("Insufficient space for disposable rehearsal")
    rehearsals = archive.checked_path(work, Path("rehearsals"))
    rehearsals.mkdir(exist_ok=True)
    marker = json.loads((source / backup.CAPTURE).read_bytes())
    with tempfile.TemporaryDirectory(prefix="mlflow-recovery-", dir=rehearsals) as temporary:
        candidate = Path(temporary)
        for name in marker["directories"]:
            (candidate / backup.path_value(name)).mkdir(parents=True, exist_ok=True)
        for row in document["files"]:
            target = candidate / backup.path_value(row["path"])
            backup.copy_file(source / row["path"], target, row["bytes"], database=True)
            if archive.digest(target) != row["sha256"]:
                raise ValueError("Snapshot changed during disposable capture")
        stores, mapping = map_locations(
            candidate, json.loads((candidate / backup.INVENTORY).read_bytes())
        )
        for name in ("tmp", "cache", "downloads"):
            (candidate / name).mkdir()
        request = {
            "candidate": str(candidate),
            "project_root": str(root),
            "historical_root": marker["historical_project_root"],
            "stores": stores,
            "control": control,
        }
        (candidate / "control.json").write_bytes(archive.encoded(request))
        env = {
            name: os.environ[name]
            for name in ("PATH", "LANG", "LC_ALL", "LD_LIBRARY_PATH")
            if name in os.environ
        }
        env.update(
            PYTHONDONTWRITEBYTECODE="1",
            TMPDIR=str(candidate / "tmp"),
            XDG_CACHE_HOME=str(candidate / "cache"),
            MLFLOW_DISABLE_TELEMETRY="true",
            MLFLOW_DISABLE_AGENT_HINT="1",
            MLFLOW_ENABLE_ASYNC_LOGGING="false",
        )
        process = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.storage.mlflow_recovery_worker",
                str(candidate / "control.json"),
            ],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        if process.returncode:
            raise ValueError("Isolated rehearsal failed: " + process.stderr[-3000:])
        result = json.loads((candidate / "result.json").read_bytes())
        if (
            result.get("status") != "success"
            or result.get("model_sha256") != control["model_sha256"]
        ):
            raise ValueError("Worker did not confirm pinned model reload")
        databases = {s["tracking_root"] + "/mlflow.db" for s in stores}
        for row in document["files"]:
            if (
                row["path"] not in databases
                and archive.digest(candidate / row["path"]) != row["sha256"]
            ):
                raise ValueError("Rehearsal modified archived artifact/metadata bytes")
        restored_snapshot(root, identity)
        release_control(root, release_id, reference_path)
    report = result | {
        "snapshot_bundle_id": identity,
        "release_bundle_id": release_id,
        "uri_map": mapping,
        "uri_cells_remapped_in_disposable_copy": len(mapping),
        "uri_map_sha256": hashlib.sha256(archive.encoded(mapping)).hexdigest(),
        "restored_snapshot_unchanged": True,
        "restored_release_unchanged": True,
        "disposable_copy_removed": not candidate.exists(),
        "original_source_read": False,
        "s3_accessed": False,
        "refit": False,
        "production_promotion": False,
        "migration_implemented": False,
        "primary_storage_migrated": False,
        "project_wide_storage_inventory_complete": False,
        "scope": "local_disposable_sqlite_MLflow_API_history_and_pinned_model_reload_not_HTTP_server_PostgreSQL_or_cutover",
    }
    receipts = archive.checked_path(work, Path("receipts"))
    receipts.mkdir(exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix="mlflow-recovery-", suffix=".json", dir=receipts, delete=False
    ) as output:
        output.write(archive.encoded(report))
        report["report"] = output.name
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--bundle-id", required=True)
    parser.add_argument("--release-bundle-id", required=True)
    args = parser.parse_args(argv)
    try:
        report = rehearse(args.project_root.resolve(), args.bundle_id, args.release_bundle_id)
        print(
            json.dumps(
                {k: v for k, v in report.items() if k != "uri_map"}, indent=2, allow_nan=False
            )
        )
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        sqlite3.Error,
        subprocess.TimeoutExpired,
    ) as error:
        parser.exit(1, f"MLflow recovery stopped: {error}\n")


if __name__ == "__main__":
    main()
