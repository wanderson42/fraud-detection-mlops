"""Restore-target jobs and a credential-free model client; source services are unreachable."""

import argparse
from contextlib import redirect_stdout
import importlib.metadata
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

from scripts.tracking import capture
from scripts.tracking import capture_worker as archive

URI = "http://mlflow:5000"
STEP = "startup"
DETAILS = {}


def stage(name):
    global STEP
    STEP = name


# PostgreSQL 17 pg_dump rewrites these reviewed MLflow enum checks by
# distributing the varchar[] -> text[] cast onto the literal elements.
# Accept only these exact pairs; changed values/operators/other rules stay unequal.
CHECK_CAST_EQUIVALENTS = {}
for _table, _name, _column, _values in [
    ("experiments", "experiments_lifecycle_stage", "lifecycle_stage", ("active", "deleted")),
    (
        "logged_models",
        "logged_models_lifecycle_stage_check",
        "lifecycle_stage",
        ("active", "deleted"),
    ),
    ("runs", "runs_lifecycle_stage", "lifecycle_stage", ("active", "deleted")),
    (
        "runs",
        "runs_status_check",
        "status",
        ("SCHEDULED", "FAILED", "FINISHED", "RUNNING", "KILLED"),
    ),
    ("runs", "source_type", "source_type", ("NOTEBOOK", "JOB", "LOCAL", "UNKNOWN", "PROJECT")),
]:
    _literals = [f"'{value}'::character varying" for value in _values]
    _source = f"CHECK ({_column}::text = ANY (ARRAY[{', '.join(_literals)}]::text[]))"
    _target = (
        f"CHECK ({_column}::text = ANY (ARRAY[{', '.join(x + '::text' for x in _literals)}]))"
    )
    CHECK_CAST_EQUIVALENTS[(_table, _name)] = (_source, _target)


def canonical_metadata(metadata):
    """Compare live column order and exact schema, allowing only reviewed PG17 forms.

    Deleted physical column slots are absent from a logical dump. Rank the live
    ordinals within each table; do not discard column order or any other field.
    Inter-table / inter-constraint query order has no schema meaning.
    Archived metadata is never changed, and all non-schema fields remain exact.
    """
    result = dict(metadata)
    if "columns" in metadata:
        grouped = {}
        for row in metadata["columns"]:
            archive.require(
                isinstance(row, list)
                and len(row) == 7
                and isinstance(row[0], str)
                and isinstance(row[1], str)
                and type(row[6]) is int
                and row[6] > 0,
                "Invalid column descriptor",
            )
            grouped.setdefault(row[0], []).append(row)
        columns = []
        for table in sorted(grouped):
            rows = sorted(grouped[table], key=lambda row: row[6])
            archive.require(
                len({row[1] for row in rows}) == len(rows)
                and len({row[6] for row in rows}) == len(rows),
                "Duplicate column name or ordinal",
            )
            columns.extend([*row[:6], rank] for rank, row in enumerate(rows, 1))
        result["columns"] = columns
    if "constraints" in metadata:
        constraints, identities = [], set()
        for row in metadata["constraints"]:
            archive.require(
                isinstance(row, list)
                and len(row) == 4
                and all(isinstance(value, str) for value in row),
                "Invalid constraint descriptor",
            )
            key = (row[0], row[1])
            archive.require(key not in identities, "Duplicate constraint identity")
            identities.add(key)
            pair = CHECK_CAST_EQUIVALENTS.get(key)
            definition = pair[0] if row[2] == "c" and pair and row[3] in pair else row[3]
            constraints.append([*row[:3], definition])
        result["constraints"] = sorted(constraints)
    return result


def metadata_difference(expected, actual):
    """Only field/table names and hashes/counts, never database values or credentials."""
    result = {}
    for field in sorted(expected.keys() | actual.keys()):
        if expected.get(field) != actual.get(field):
            left, right = expected.get(field), actual.get(field)
            item = {
                "expected_sha256": archive.digest(archive.encoded(left)),
                "actual_sha256": archive.digest(archive.encoded(right)),
            }
            if isinstance(left, dict) and isinstance(right, dict):
                item["changed_names"] = sorted(
                    k for k in left.keys() | right.keys() if left.get(k) != right.get(k)
                )
            if isinstance(left, list) and isinstance(right, list):
                item["expected_items"] = len(left)
                item["actual_items"] = len(right)
            result[field] = item
    return result


def safe_failure(exc, action):
    result = {
        "status": "failed",
        "action": action,
        "step": STEP,
        "error_type": type(exc).__name__,
        "reason": str(exc)
        if isinstance(exc, archive.CaptureError)
        else "Private SDK/driver details withheld",
        "details": DETAILS.copy(),
    }
    # SQLSTATE and SDK error code are useful identifiers, not driver messages/URLs.
    code = getattr(exc, "sqlstate", None)
    if isinstance(code, str) and len(code) == 5 and code.isalnum():
        result["sqlstate"] = code
    response = getattr(exc, "response", {})
    code = response.get("Error", {}).get("Code") if isinstance(response, dict) else None
    if isinstance(code, str) and code.isalnum() and len(code) <= 64:
        result["sdk_error_code"] = code
    return result


def connect():
    import psycopg

    return psycopg.connect(
        host="postgres",
        dbname="mlflow_lab",
        user="mlflow",
        password=os.environ["TARGET_PG_PASSWORD"],
        connect_timeout=5,
    )


def expected_api(connection):
    from psycopg import sql
    from psycopg.rows import dict_row

    result = {}
    with connection.transaction(), connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute("SET TRANSACTION READ ONLY")
        for name in ["experiments", "runs", "params", "tags", "metrics", "logged_models"]:
            cursor.execute(sql.SQL("SELECT * FROM public.{}").format(sql.Identifier(name)))
            result[name] = cursor.fetchall()
    # Keep only the API contract, excluding PG-specific types and unused columns.
    fields = {
        "experiments": ["experiment_id", "name", "artifact_location", "lifecycle_stage"],
        "runs": ["run_uuid", "status", "experiment_id", "artifact_uri", "lifecycle_stage"],
        "params": ["run_uuid", "key", "value"],
        "tags": ["run_uuid", "key", "value"],
        "metrics": ["run_uuid", "key", "value", "timestamp", "step", "is_nan"],
        "logged_models": ["model_id", "source_run_id", "artifact_location", "status"],
    }
    return {
        name: [{field: row[field] for field in fields[name]} for row in rows]
        for name, rows in result.items()
    }


def seed(directory, proof, manifest_hash):
    stage("archive_verification")
    capture.verify(directory, manifest_hash)
    stage("s3_connect")
    client = archive.s3_connect()  # Same DNS names, exclusively on the target's private network.
    stage("s3_create_bucket")
    client.create_bucket(Bucket=archive.BUCKET)
    archive.require(
        not client.list_objects_v2(Bucket=archive.BUCKET).get("Contents"),
        "Target bucket is not empty",
    )
    records = json.loads((directory / "objects.json").read_bytes())
    stage("s3_upload")
    for row in records:
        with (directory / row["file"]).open("rb") as stream:
            client.put_object(
                Bucket=archive.BUCKET,
                Key=row["key"],
                Body=stream,
                ContentLength=row["bytes"],
                IfNoneMatch="*",
            )
    verify_data(directory, client)
    metadata = json.loads((directory / "metadata.json").read_bytes())
    stage("api_control_read")
    with connect() as connection:
        api = expected_api(connection)
    stage("api_control_save")
    archive.save(proof / "expected-api.json", api)
    return {
        "status": "restored_verified",
        "objects": len(records),
        "metadata_rows": {name: value["rows"] for name, value in metadata["tables"].items()},
        "logical_metadata_schema_and_sequences_verified": True,
        "s3_full_readback_verified": True,
    }


def verify_data(directory, client=None):
    if client is None:
        client = archive.s3_connect()
    records = json.loads((directory / "objects.json").read_bytes())
    stage("s3_key_readback")
    actual = archive.object_listing(client)
    archive.require(
        {r["key"]: r["bytes"] for r in actual} == {r["key"]: r["bytes"] for r in records},
        "Restored S3 key set or size differs",
    )
    by_key = {r["key"]: r for r in records}
    stage("s3_byte_readback")
    for row in actual:
        archive.require(
            archive.read_object(client, row) == by_key[row["key"]]["sha256"],
            "Restored S3 bytes differ",
        )
    stage("postgres_inventory")
    with connect() as connection:
        metadata = archive.database_inventory(connection)
    stage("postgres_comparison")
    expected = json.loads((directory / "metadata.json").read_bytes())
    left, right = canonical_metadata(expected), canonical_metadata(metadata)
    if left != right:
        DETAILS["metadata_difference"] = metadata_difference(left, right)
    archive.require(
        left == right,
        "Restored PostgreSQL logical/schema/sequence contents differ",
    )
    return {
        "status": "target_unchanged",
        "objects": len(records),
        "database_unchanged": True,
        "schema_comparison": "live_column_order_and_five_exact_pg17_check_cast_pairs_v1",
        "raw_descriptor_fields_differing": sorted(metadata_difference(expected, metadata)),
    }


def inspect_api(client, expected):
    from mlflow.entities import LoggedModelStatus

    for row in expected["experiments"]:
        item = client.get_experiment(str(row["experiment_id"]))
        archive.require(
            (item.name, item.artifact_location, item.lifecycle_stage)
            == (row["name"], row["artifact_location"], row["lifecycle_stage"]),
            "Experiment API differs",
        )
    histories = 0
    for row in expected["runs"]:
        identity = row["run_uuid"]
        run = client.get_run(identity)
        archive.require(
            (
                run.info.status,
                run.info.experiment_id,
                run.info.artifact_uri,
                run.info.lifecycle_stage,
            )
            == (
                row["status"],
                str(row["experiment_id"]),
                row["artifact_uri"],
                row["lifecycle_stage"],
            ),
            "Run API differs",
        )
        for field in ["params", "tags"]:
            values = {r["key"]: r["value"] for r in expected[field] if r["run_uuid"] == identity}
            archive.require(getattr(run.data, field) == values, "Run parameters/tags API differs")
        rows = [r for r in expected["metrics"] if r["run_uuid"] == identity]
        for key in sorted({r["key"] for r in rows}):
            wanted = sorted(
                ("nan" if r["is_nan"] else float(r["value"]).hex(), r["timestamp"], r["step"])
                for r in rows
                if r["key"] == key
            )
            found = sorted(
                (float(r.value).hex(), r.timestamp, r.step)
                for r in client.get_metric_history(identity, key)
            )
            archive.require(wanted == found, "Full metric history API differs")
            histories += len(found)
    for row in expected["logged_models"]:
        model = client.get_logged_model(row["model_id"])
        archive.require(
            (model.source_run_id, model.artifact_location, model.status)
            == (
                row["source_run_id"],
                row["artifact_location"],
                LoggedModelStatus.from_int(row["status"]),
            ),
            "Logged model API differs",
        )
    return {
        "runs": len(expected["runs"]),
        "logged_models": len(expected["logged_models"]),
        "metric_history_rows": histories,
    }


def reload_model(client, control, directory):
    import mlflow
    import numpy as np
    import pandas as pd

    from fraud_detection_mlops.integrations.skops_persistence import TRUSTED_TYPES, load_pipeline

    archive.require(
        not any(
            name.startswith(("AWS_", "TARGET_PG", "TRACKING_PG", "POSTGRES_"))
            for name in os.environ
        ),
        "Client received infrastructure credentials",
    )
    model_info = client.get_logged_model(control["model_uri"].removeprefix("models:/"))
    archive.require(model_info.source_run_id == control["run_id"], "Pinned source run differs")
    archive.key_path(model_info.artifact_location)
    path = Path(
        mlflow.artifacts.download_artifacts(
            artifact_uri=control["model_uri"], dst_path=str(directory)
        )
    )
    archive.require(
        path.resolve().is_relative_to(directory.resolve()),
        "Model download escaped disposable directory",
    )
    metadata = mlflow.models.Model.load(path / "MLmodel")
    flavor = metadata.flavors.get("sklearn", {})
    pyfunc = metadata.flavors.get("python_function", {})
    archive.require(
        flavor.get("serialization_format") == "skops"
        and flavor.get("pickled_model") == "model.skops"
        and set(flavor.get("skops_trusted_types", [])) <= TRUSTED_TYPES
        and pyfunc.get("loader_module") == "mlflow.sklearn"
        and pyfunc.get("predict_fn") == "predict_proba"
        and metadata.signature.inputs.input_names() == control["columns"],
        "Unreviewed model flavor/signature",
    )
    archive.require(
        capture.sha(path / "model.skops") == control["model_sha256"], "Pinned model bytes differ"
    )
    load_pipeline(path / "model.skops", feature_columns=control["columns"])
    frame = pd.DataFrame(control["rows"], columns=control["columns"], dtype="float64")
    probabilities = np.asarray(mlflow.pyfunc.load_model(str(path)).predict(frame))
    expected = np.asarray(control["scores"], dtype=float)
    archive.require(
        probabilities.shape == (len(expected), 2) and np.isfinite(probabilities).all(),
        "Invalid restored probabilities",
    )
    archive.require(
        np.allclose(probabilities[:, 1], expected, rtol=1e-12, atol=1e-12),
        "Restored model score parity differs",
    )
    return {
        "model_reload_validated": True,
        "model_sha256": control["model_sha256"],
        "model_uri": control["model_uri"],
        "scored_rows": len(expected),
        "max_absolute_score_error": float(np.max(np.abs(probabilities[:, 1] - expected))),
    }


def client_check(proof):
    from mlflow import MlflowClient

    archive.require(
        os.environ.get("MLFLOW_TRACKING_URI") == URI, "Explicit target tracking URI required"
    )
    archive.require(
        os.environ.get("MLFLOW_ENABLE_PROXY_MULTIPART_DOWNLOAD") == "false",
        "Proxy streaming required",
    )
    control = json.loads((proof / "control.json").read_bytes())
    expected = json.loads((proof / "expected-api.json").read_bytes())
    client = MlflowClient(tracking_uri=URI, registry_uri=URI)
    result = inspect_api(client, expected)
    with TemporaryDirectory(prefix="restore-download-", dir="/tmp") as temporary:
        result.update(reload_model(client, control, Path(temporary)))
    return {"status": "client_verified", **result, "direct_s3_or_pg_credentials": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["check", "seed", "client", "verify"])
    args = parser.parse_args()
    stage("startup")
    DETAILS.clear()
    try:
        with redirect_stdout(sys.stderr):
            if args.action == "check":
                result = {
                    "status": "ready",
                    "mlflow_version": importlib.metadata.version("mlflow"),
                }
                archive.require(result["mlflow_version"] == "3.17.0", "MLflow version differs")
            elif args.action == "seed":
                result = seed(
                    Path(os.environ["CAPTURE_PATH"]),
                    Path("/proof"),
                    os.environ["CAPTURE_MANIFEST_SHA256"],
                )
            elif args.action == "verify":
                directory = Path(os.environ["CAPTURE_PATH"])
                capture.verify(directory, os.environ["CAPTURE_MANIFEST_SHA256"])
                result = verify_data(directory)
            else:
                result = client_check(Path("/proof"))
    except Exception as exc:  # noqa: BLE001 - serialize only authored messages and safe codes
        result = safe_failure(exc, args.action)
        # The seed bind is writable; the independent client bind remains read-only.
        if args.action in {"seed", "verify"}:
            try:
                archive.save(Path("/proof") / ("worker-failure-" + args.action + ".json"), result)
            except OSError:
                pass  # Structured stdout still preserves the failure in the host receipt.
    result["worker_code_sha256"] = capture.sha(Path(__file__))
    print(json.dumps(result, allow_nan=False))


if __name__ == "__main__":
    main()
