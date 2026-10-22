"""Read the verified immutable snapshot; never initialize its SQLite databases."""

from contextlib import closing
import hashlib
import json
import sqlite3

from scripts.storage import archive
from scripts.storage import mlflow_backup as backup
from scripts.storage import mlflow_recovery as recovery

PREFIX = "fraud.history."
SUPPORTED = {
    "alembic_version",
    "workspaces",
    "experiments",
    "experiment_tags",
    "runs",
    "params",
    "tags",
    "metrics",
    "latest_metrics",
    "logged_models",
    "logged_model_params",
    "logged_model_tags",
    "logged_model_metrics",
    "inputs",
    "input_tags",
    "datasets",
}


def require(value, reason):
    if not value:
        raise ValueError(reason)


def fingerprint(value):
    return hashlib.sha256(archive.encoded(value)).hexdigest()


def rows(path):
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        connection.execute("PRAGMA query_only=ON")
        connection.row_factory = sqlite3.Row
        names = [
            r[0]
            for r in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        result = {
            name: [
                dict(r)
                for r in connection.execute('SELECT * FROM "' + name.replace('"', '""') + '"')
            ]
            for name in names
        }
    for name, values in result.items():
        require(
            not values or name in SUPPORTED,
            "Populated metadata requires explicit API mapping: " + name,
        )
    return result


def pairs(table, key, identity, field="key", value="value"):
    return {row[field]: row[value] for row in table if row[key] == identity}


def metric(value, nan=False):
    return float("nan" if nan else value).hex()


def artifacts(snapshot, files, location):
    if not location or location["kind"] != "local_artifacts":
        return {}
    prefix = location["local_path"].rstrip("/") + "/"
    return {
        name[len(prefix) :]: {"source": name, "bytes": row["bytes"], "sha256": row["sha256"]}
        for name, row in files.items()
        if name.startswith(prefix)
    }


def prepare(root, bundle_id, release_bundle_id):
    snapshot, document, checked = recovery.restored_snapshot(root, bundle_id)
    control = recovery.release_control(root, release_bundle_id, recovery.REFERENCE)
    inventory = json.loads((snapshot / backup.INVENTORY).read_bytes())
    files = {row["path"]: row for row in document["files"]}
    stores = []
    used = set()
    for tracking, saved in backup.store_inventories(inventory).items():
        data = rows(snapshot / tracking / "mlflow.db")
        experiments, runs, models = [], [], []
        for row in data["experiments"]:
            require(
                row["lifecycle_stage"] == "active",
                "Deleted source experiment requires explicit lifecycle mapping",
            )
            old = row["experiment_id"]
            tags = pairs(data.get("experiment_tags", []), "experiment_id", old)
            require(not any(k.startswith(PREFIX) for k in tags), "Reserved source experiment tag")
            name = row["name"]
            if tracking != backup.TRACKING:
                name = "interface-smoke::" + name
            elif name == "Default":
                name = "historical-main::Default"
            experiments.append(
                {"id": str(old), "target_name": name, "metadata": row, "tags": tags}
            )
        for row in data["runs"]:
            identity = row["run_uuid"]
            tags = pairs(data.get("tags", []), "run_uuid", identity)
            require(not any(k.startswith(PREFIX) for k in tags), "Reserved source run tag")
            location = next(
                r["artifact_uri"] for r in saved["database"]["runs"] if r["run_uuid"] == identity
            )
            items = artifacts(snapshot, files, location)
            require(len(items) == len(set(items)), "Artifact path collision")
            used.update(v["source"] for v in items.values())
            metrics = [
                {
                    "key": v["key"],
                    "value": metric(v["value"], v.get("is_nan")),
                    "timestamp": v["timestamp"],
                    "step": v["step"],
                }
                for v in data.get("metrics", [])
                if v["run_uuid"] == identity
            ]
            latest = {
                v["key"]: metric(v["value"], v.get("is_nan"))
                for v in data.get("latest_metrics", [])
                if v["run_uuid"] == identity
            }
            require(
                set(latest) == {m["key"] for m in metrics}, "Latest/history metric keys differ"
            )
            runs.append(
                {
                    "id": identity,
                    "experiment_id": str(row["experiment_id"]),
                    "metadata": row,
                    "tags": tags,
                    "params": pairs(data.get("params", []), "run_uuid", identity),
                    "metrics": metrics,
                    "latest": latest,
                    "artifacts": items,
                }
            )
        for row in data.get("logged_models", []):
            require(
                row.get("lifecycle_stage", "active") == "active",
                "Deleted logged model requires explicit lifecycle mapping",
            )
            identity = row["model_id"]
            tags = pairs(
                data.get("logged_model_tags", []), "model_id", identity, "tag_key", "tag_value"
            )
            require(not any(k.startswith(PREFIX) for k in tags), "Reserved source model tag")
            location = next(
                m["artifact_location"]
                for m in saved["database"]["logged_models"]
                if m["model_id"] == identity
            )
            items = artifacts(snapshot, files, location)
            used.update(v["source"] for v in items.values())
            metrics = [
                {
                    "key": m["metric_name"],
                    "value": metric(m["metric_value"], m["metric_value"] is None),
                    "timestamp": m["metric_timestamp_ms"],
                    "step": m["metric_step"],
                    "run_id": m["run_id"],
                    "dataset_name": m["dataset_name"],
                    "dataset_digest": m["dataset_digest"],
                }
                for m in data.get("logged_model_metrics", [])
                if m["model_id"] == identity
            ]
            models.append(
                {
                    "id": identity,
                    "experiment_id": str(row["experiment_id"]),
                    "metadata": row,
                    "tags": tags,
                    "params": pairs(
                        data.get("logged_model_params", []),
                        "model_id",
                        identity,
                        "param_key",
                        "param_value",
                    ),
                    "metrics": metrics,
                    "artifacts": items,
                }
            )
        for edge in data.get("inputs", []):
            if (edge["source_type"], edge["destination_type"]) == ("RUN_OUTPUT", "MODEL_OUTPUT"):
                edge.update(source_type="RUN", destination_type="MODEL")
            elif (edge["source_type"], edge["destination_type"]) == ("RUN_INPUT", "MODEL_INPUT"):
                edge.update(
                    source_type="MODEL",
                    destination_type="RUN",
                    source_id=edge["destination_id"],
                    destination_id=edge["source_id"],
                )
        for edge in data.get("inputs", []):
            require(
                (edge["source_type"], edge["destination_type"])
                in {("RUN", "MODEL"), ("MODEL", "RUN"), ("DATASET", "RUN")},
                "Unsupported lineage edge",
            )
        stores.append(
            {
                "tracking_root": tracking,
                "experiments": experiments,
                "runs": runs,
                "models": models,
                "edges": data.get("inputs", []),
                "input_tags": data.get("input_tags", []),
                "datasets": data.get("datasets", []),
                "source_table_counts": {k: len(v) for k, v in data.items()},
            }
        )
    extras = {
        n: {"source": n, "bytes": v["bytes"], "sha256": v["sha256"]}
        for n, v in files.items()
        if n not in used
    }
    # Source entity IDs are unique across the coordinated stores (backup verifier enforces it).
    all_runs = {v["id"] for s in stores for v in s["runs"]}
    all_models = {v["id"] for s in stores for v in s["models"]}
    for store in stores:
        for run in store["runs"]:
            parent = run["tags"].get("mlflow.parentRunId")
            require(not parent or parent in all_runs, "Parent run outside reviewed snapshot")
            uri = run["tags"].get("logged_model_uri", "")
            require(
                not uri.startswith("models:/") or uri[8:] in all_models,
                "Logged-model tag outside reviewed snapshot",
            )
        for model in store["models"]:
            require(
                model["metadata"]["source_run_id"] in all_runs,
                "Unresolved logged model source run",
            )
            for m in model["metrics"]:
                source = next(v for s in stores for v in s["runs"] if v["id"] == m["run_id"])
                require(
                    {k: m[k] for k in ("key", "value", "timestamp", "step")} in source["metrics"],
                    "Model metric absent from originating run",
                )
        for edge in store["edges"]:
            require(
                edge["source_type"] != "MODEL" or edge.get("step", 0) == 0,
                "Model input step cannot be represented by the public API",
            )
            for side in ("source", "destination"):
                if edge[side + "_type"] == "RUN":
                    require(edge[side + "_id"] in all_runs, "Unresolved lineage run")
                elif edge[side + "_type"] == "MODEL":
                    require(edge[side + "_id"] in all_models, "Unresolved lineage model")
        dataset_edges = {e["input_uuid"] for e in store["edges"] if e["source_type"] == "DATASET"}
        referenced_datasets = {
            e["source_id"] for e in store["edges"] if e["source_type"] == "DATASET"
        }
        require(
            referenced_datasets == {d["dataset_uuid"] for d in store["datasets"]},
            "Unresolved or unreferenced dataset needs explicit API mapping",
        )
        require(
            all(t["input_uuid"] in dataset_edges for t in store["input_tags"]),
            "Non-dataset input tags need explicit API mapping",
        )
    return {
        "schema": "fraud_history_plan_v1",
        "snapshot_bundle_id": bundle_id,
        "release_bundle_id": release_bundle_id,
        "snapshot_directory": str(snapshot),
        "snapshot_manifest_sha256": archive.digest(snapshot / "manifest.json"),
        "stores": stores,
        "extras": extras,
        "source_counts": {
            "runs": checked["total_runs"],
            "logged_models": checked["total_logged_models"],
            "artifact_files": checked["artifact_files"],
        },
        "control": control,
        "refit": False,
        "original_source_read": False,
    }


def identity_tags(plan, store, kind, identity, metadata):
    return {
        PREFIX + "bundle": plan["snapshot_bundle_id"],
        PREFIX + "store": store,
        PREFIX + "kind": kind,
        PREFIX + "id": identity,
        PREFIX + "source_metadata": json.dumps(metadata, sort_keys=True, separators=(",", ":")),
    }
