"""Bounded read-only PostgreSQL/S3 capture worker; no MLflow initialization."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

SCHEMA = "fraud_tracking_capture_v1"
BUCKET = "fraud-tracking-lab"
PREFIX = "v1/"
MAX_OBJECTS = 1024
MAX_BYTES = 256 * 1024**2
MAX_METADATA = 64 * 1024**2
MAX_DUMP = 64 * 1024**2


class CaptureError(ValueError):
    """An explicitly authored diagnostic safe for a receipt."""


def require(condition, message):
    if not condition:
        raise CaptureError(message)


def encoded(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def save(path, value):
    with path.open("xb") as stream:
        os.chmod(path, 0o600)
        stream.write(encoded(value))


def key_path(uri):
    parts = urlsplit(uri)
    require(
        parts.scheme == "mlflow-artifacts"
        and not parts.netloc
        and not parts.query
        and not parts.fragment
        and parts.path.startswith("/"),
        "Artifact location outside the reviewed proxy namespace",
    )
    path = parts.path[1:]
    require(
        path
        and all(
            re.fullmatch(r"[A-Za-z0-9_.-]+", p) and p not in {".", ".."} for p in path.split("/")
        ),
        "Invalid proxy artifact path",
    )
    return PREFIX + path.rstrip("/") + "/"


def database_inventory(connection):
    """Hash every public table row, including failed/deleted runs and metric history."""
    from psycopg import sql

    connection.read_only = True
    connection.isolation_level = __import__("psycopg").IsolationLevel.REPEATABLE_READ
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL statement_timeout = '15000ms'")
        cursor.execute(
            "SELECT current_database(), current_user, current_setting('server_version_num')::int"
        )
        database, role, version = cursor.fetchone()
        require(
            database == "mlflow_lab" and role == "mlflow" and 170000 <= version < 180000,
            "Unexpected PostgreSQL database, role or major version",
        )
        cursor.execute(
            "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid() AND backend_type='client backend'"
        )
        require(cursor.fetchone()[0] == 0, "Other PostgreSQL clients remain connected")
        cursor.execute(
            "SELECT n.nspname,c.relname,c.relkind,pg_get_userbyid(c.relowner) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%' AND n.nspname NOT LIKE 'pg_temp%' ORDER BY 1,2"
        )
        relations = cursor.fetchall()
        require(
            all(n == "public" and k in {"r", "S", "i"} for n, _, k, _ in relations),
            "Unreviewed schemas or relation types",
        )
        tables, sequences, total = {}, {}, 0
        for namespace, name, kind, owner in relations:
            require(owner == "mlflow", "Unreviewed relation owner")
            identifier = sql.Identifier(namespace, name)
            if kind == "r":
                hashes, count = [], 0
                with connection.cursor(name="capture_rows") as rows:
                    rows.execute(sql.SQL("SELECT to_jsonb(t)::text FROM {} t").format(identifier))
                    for (text,) in rows:
                        raw = text.encode()
                        total += len(raw)
                        require(total <= MAX_METADATA, "Metadata byte budget exceeded")
                        hashes.append(digest(raw))
                        count += 1
                tables[name] = {
                    "rows": count,
                    "sha256": digest(encoded(sorted(hashes))),
                    "owner": owner,
                }
            elif kind == "S":
                cursor.execute(sql.SQL("SELECT last_value,is_called FROM {}").format(identifier))
                value, called = cursor.fetchone()
                sequences[name] = {"last_value": value, "is_called": called, "owner": owner}
        require(
            {
                "experiments",
                "runs",
                "logged_models",
                "metrics",
                "params",
                "tags",
                "alembic_version",
            }
            <= tables.keys(),
            "Incomplete MLflow schema",
        )
        cursor.execute(
            "SELECT table_name,column_name,data_type,udt_name,is_nullable,column_default,ordinal_position FROM information_schema.columns WHERE table_schema='public' ORDER BY table_name,ordinal_position"
        )
        columns = [list(row) for row in cursor.fetchall()]
        cursor.execute(
            "SELECT tablename,indexname,indexdef FROM pg_indexes WHERE schemaname='public' ORDER BY tablename,indexname"
        )
        indexes = [list(row) for row in cursor.fetchall()]
        cursor.execute(
            "SELECT conrelid::regclass::text,conname,contype,pg_get_constraintdef(oid,true) FROM pg_constraint WHERE connamespace='public'::regnamespace ORDER BY 1,2"
        )
        constraints = [list(row) for row in cursor.fetchall()]
        locations = []
        for table, identity, field in [
            ("experiments", "experiment_id", "artifact_location"),
            ("runs", "run_uuid", "artifact_uri"),
            ("logged_models", "model_id", "artifact_location"),
        ]:
            cursor.execute(
                sql.SQL("SELECT {},{} FROM {} ORDER BY {}::text").format(
                    sql.Identifier(identity),
                    sql.Identifier(field),
                    sql.Identifier("public", table),
                    sql.Identifier(identity),
                )
            )
            for key, uri in cursor.fetchall():
                key_path(uri)  # Fail before starting the object copy.
                locations.append({"table": table, "id": str(key), "uri": uri})
        cursor.execute("SELECT version_num FROM public.alembic_version ORDER BY version_num")
        revisions = [row[0] for row in cursor.fetchall()]
    return {
        "database": database,
        "role": role,
        "postgres_major": 17,
        "tables": tables,
        "sequences": sequences,
        "columns": columns,
        "indexes": indexes,
        "constraints": constraints,
        "locations": locations,
        "alembic_revisions": revisions,
        "logical_bytes_read": total,
    }


def object_listing(client):
    result, seen = [], set()
    token = None
    tokens = set()
    while True:
        args = {"Bucket": BUCKET, "Prefix": PREFIX, "MaxKeys": 1000}
        if token is not None:
            args["ContinuationToken"] = token
        response = client.list_objects_v2(**args)
        for row in response.get("Contents", []):
            key, size = row["Key"], row["Size"]
            require(
                isinstance(key, str) and key.startswith(PREFIX) and key not in seen,
                "Invalid or duplicate S3 key",
            )
            require(type(size) is int and size >= 0, "Invalid S3 size")
            seen.add(key)
            result.append(
                {
                    "key": key,
                    "bytes": size,
                    "etag": row["ETag"],
                    "last_modified": row["LastModified"].isoformat(),
                }
            )
            require(
                len(result) <= MAX_OBJECTS and sum(x["bytes"] for x in result) <= MAX_BYTES,
                "Object count or byte budget exceeded",
            )
        if not response.get("IsTruncated", False):
            break
        next_token = response.get("NextContinuationToken")
        require(
            isinstance(next_token, str) and next_token and next_token not in tokens,
            "Invalid S3 pagination",
        )
        tokens.add(next_token)
        token = next_token
    require(result, "No objects in the authorized prefix")
    return sorted(result, key=lambda row: row["key"])


def coverage(metadata, listing):
    keys = {row["key"] for row in listing}
    for location in metadata["locations"]:
        prefix = key_path(location["uri"])
        # An experiment or artifact-less run can legitimately have no objects.
        if location["table"] == "logged_models":
            require(
                {
                    prefix + name
                    for name in [
                        "MLmodel",
                        "model.skops",
                        "python_env.yaml",
                        "conda.yaml",
                        "requirements.txt",
                    ]
                }
                <= keys,
                "A logged model is not fully covered",
            )
    run_prefixes = [key_path(x["uri"]) for x in metadata["locations"] if x["table"] == "runs"]
    model_prefixes = [
        key_path(x["uri"]) for x in metadata["locations"] if x["table"] == "logged_models"
    ]
    require(
        all(any(key.startswith(p) for p in run_prefixes + model_prefixes) for key in keys),
        "S3 objects without a corresponding run or logged model",
    )


def read_object(client, row, stream=None):
    response = client.get_object(Bucket=BUCKET, Key=row["key"], IfMatch=row["etag"])
    body, size, sha = response["Body"], 0, hashlib.sha256()
    try:
        require(response["ContentLength"] == row["bytes"], "S3 content length changed")
        while chunk := body.read(1024 * 1024):
            size += len(chunk)
            require(size <= row["bytes"], "S3 stream exceeds declared size")
            sha.update(chunk)
            if stream is not None:
                stream.write(chunk)
    finally:
        body.close()
    require(size == row["bytes"], "S3 stream truncated")
    return sha.hexdigest()


def capture_objects(directory, client, metadata):
    listing = object_listing(client)
    coverage(metadata, listing)
    objects = directory / "objects"
    objects.mkdir(mode=0o700)
    records = []
    for index, row in enumerate(listing):
        # S3 keys are metadata, never filesystem paths.
        filename = f"objects/{index:04d}.bin"
        with (directory / filename).open("xb") as stream:
            os.chmod(directory / filename, 0o600)
            sha = read_object(client, row, stream)
        records.append({**row, "file": filename, "sha256": sha})
    require(object_listing(client) == listing, "S3 inventory changed during capture")
    for row in records:
        require(read_object(client, row) == row["sha256"], "S3 bytes changed during capture")
    require(object_listing(client) == listing, "S3 inventory changed during readback")
    return records


def pg_connect():
    import psycopg

    return psycopg.connect(
        host="postgres",
        dbname="mlflow_lab",
        user="mlflow",
        password=os.environ["TRACKING_PG_PASSWORD"],
        connect_timeout=5,
    )


def s3_connect():
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url="http://rustfs:9000",
        region_name="us-east-1",
        config=Config(
            connect_timeout=5,
            read_timeout=15,
            retries={"total_max_attempts": 2},
            s3={"addressing_style": "path"},
        ),
    )


def execute(action, directory):
    require(directory.is_dir() and directory == directory.resolve(), "Unsafe capture directory")
    if action == "check":
        import importlib.metadata

        versions = {
            name: importlib.metadata.version(name)
            for name in ["boto3", "botocore", "psycopg", "mlflow"]
        }
        require(
            versions
            == {
                "boto3": "1.43.114",
                "botocore": "1.43.114",
                "psycopg": "3.3.6",
                "mlflow": "3.17.0",
            },
            "Worker runtime version differs",
        )
        return {"status": "ready", "versions": versions}
    with pg_connect() as connection:
        metadata = database_inventory(connection)
    if action == "inventory":
        save(directory / "metadata.json", metadata)
        return {"status": "inventoried", "metadata_sha256": digest(encoded(metadata))}
    require(
        metadata == json.loads((directory / "metadata.json").read_bytes()),
        "PostgreSQL contents changed before object capture",
    )
    records = capture_objects(directory, s3_connect(), metadata)
    with pg_connect() as connection:
        require(
            database_inventory(connection) == metadata,
            "PostgreSQL contents changed during capture",
        )
    save(directory / "objects.json", records)
    return {
        "status": "objects_captured",
        "objects": len(records),
        "bytes": sum(x["bytes"] for x in records),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["check", "inventory", "objects"])
    args = parser.parse_args()
    try:
        result = execute(args.action, Path("/capture"))
    except Exception as exc:  # noqa: BLE001 - redact credential-bearing SDK failures at CLI boundary
        # SDK/connection errors can carry signed URLs or credentials.
        failure = {
            "status": "failed",
            "action": args.action,
            "error_type": type(exc).__name__,
            "reason": str(exc)
            if isinstance(exc, CaptureError)
            else "SDK/connection details withheld",
        }
        save(Path("/capture/worker-failure.json"), failure)
        print(
            f"Capture worker stopped ({type(exc).__name__}); no source writes requested",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    result["worker_code_sha256"] = digest(Path(__file__).read_bytes())
    print(json.dumps(result, allow_nan=False))


if __name__ == "__main__":
    main()
