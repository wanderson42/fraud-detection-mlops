"""Read source via explicit forwards; restore only on a private target network."""

import argparse
from contextlib import redirect_stdout
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from scripts.platform.tracking_backup import archive as a
from scripts.tracking import capture_worker as shared
from scripts.tracking import restore_worker as restored


def connect():
    import psycopg

    return psycopg.connect(
        host=os.environ["BACKUP_PG_HOST"],
        port=int(os.environ.get("BACKUP_PG_PORT", "5432")),
        dbname=a.DATABASE,
        user=a.ROLE,
        password=os.environ["BACKUP_PG_PASSWORD"],
        connect_timeout=5,
    )


def s3():
    import boto3
    from botocore.config import Config

    url = os.environ["BACKUP_S3_ENDPOINT"]
    parsed = urlsplit(url)
    a.require(
        parsed.scheme == "http"
        and parsed.hostname in {"127.0.0.1", "rustfs"}
        and not parsed.username
        and not parsed.password
        and parsed.path in ("", "/"),
        "Unreviewed S3 endpoint",
    )
    return boto3.client(
        "s3",
        endpoint_url=url,
        region_name="us-east-1",
        config=Config(
            connect_timeout=5,
            read_timeout=15,
            retries={"total_max_attempts": 2},
            s3={"addressing_style": "path"},
        ),
    )


def metadata():
    with connect() as conn:
        return shared.database_inventory(conn, database_name=a.DATABASE, role_name=a.ROLE)


def owned_listing(client, owner):
    body = client.get_object(Bucket=a.BUCKET, Key="platform-owner.json")["Body"]
    try:
        marker = body.read(4097)
        a.require(marker == shared.encoded({"owner_id": owner}), "Tracking bucket owner differs")
    finally:
        body.close()
    entire = shared.object_listing(client, bucket=a.BUCKET, prefix="")
    a.require(
        all(r["key"] == "platform-owner.json" or r["key"].startswith("v1/") for r in entire),
        "Unexpected keys outside tracking prefix",
    )
    return entire


def verify_data(directory, client):
    expected = a.document(directory / "metadata.json")
    a.require(
        restored.canonical_metadata(metadata()) == restored.canonical_metadata(expected),
        "Restored PostgreSQL metadata/schema/sequences differ",
    )
    rows = shared.object_listing(client, bucket=a.BUCKET)
    objects = a.document(directory / "objects.json")
    a.require(
        {r["key"]: r["bytes"] for r in rows} == {r["key"]: r["bytes"] for r in objects},
        "Restored S3 key set differs",
    )
    records = {r["key"]: r for r in objects}
    for row in rows:
        a.require(
            shared.read_object(client, row, bucket=a.BUCKET) == records[row["key"]]["sha256"],
            "Restored S3 bytes differ",
        )
    owner = a.document(directory / "owner-marker.json")["owner_id"]
    owned_listing(client, owner)
    return {
        "status": "restored_data_verified",
        "objects": len(rows),
        "tables": len(expected["tables"]),
    }


def client_check(proof):
    from mlflow import MlflowClient

    a.require(
        not any(k.startswith(("AWS_", "BACKUP_PG", "TARGET_PG", "POSTGRES_")) for k in os.environ),
        "Client received infrastructure credentials",
    )
    uri = "http://mlflow:5000"
    a.require(
        os.environ.get("MLFLOW_TRACKING_URI") == uri, "Explicit isolated target URI required"
    )
    client = MlflowClient(tracking_uri=uri, registry_uri=uri)
    control = a.document(proof / "control.json")
    counts = restored.inspect_api(client, a.document(proof / "expected-api.json"))
    version = client.get_model_version_by_alias(control["registered_model"], "champion")
    a.require(
        str(version.version) == control["registry_version"]
        and version.run_id == control["run_id"]
        and version.source == control["model_uri"]
        and version.tags.get("fraud.model_sha256") == control["model_sha256"],
        "Restored champion Registry identity differs",
    )
    with TemporaryDirectory(dir="/tmp") as tmp:
        scores = restored.reload_model(client, control, Path(tmp))
    return {
        "status": "target_client_verified",
        **counts,
        **scores,
        "registry_alias_verified": True,
        "direct_storage_credentials": False,
    }


def execute(action, directory, proof, digest=None):
    if action == "client":
        return client_check(proof)
    if action == "runtime-check":
        import importlib.metadata

        versions = {
            n: importlib.metadata.version(n) for n in ("boto3", "botocore", "psycopg", "mlflow")
        }
        a.require(
            versions
            == {
                "boto3": "1.43.114",
                "botocore": "1.43.114",
                "psycopg": "3.3.6",
                "mlflow": "3.17.0",
            },
            "Worker runtime differs",
        )
        return {"status": "runtime_ready", "versions": versions}
    if action == "preflight":
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT current_database(),current_user,pg_database_size(current_database()),current_setting('server_version_num')::int"
            )
            db, role, size, version = cur.fetchone()
        a.require(
            db == a.DATABASE and role == a.ROLE and 170000 <= version < 180000,
            "PostgreSQL identity/version differs",
        )
        listing = owned_listing(s3(), os.environ["BACKUP_TRACKING_OWNER"])
        return {
            "status": "source_preflight_verified",
            "database": db,
            "database_bytes": size,
            "bucket": a.BUCKET,
            "objects_including_marker": len(listing),
            "object_bytes": sum(r["bytes"] for r in listing),
            "capture_executed": False,
        }
    if action == "inventory":
        shared.save(directory / "metadata.json", metadata())
        return {"status": "inventoried"}
    if action == "objects":
        expected = a.document(directory / "metadata.json")
        a.require(metadata() == expected, "PostgreSQL changed before capture")
        client = s3()
        before = owned_listing(client, os.environ["BACKUP_TRACKING_OWNER"])
        records = shared.capture_objects(directory, client, expected, bucket=a.BUCKET)
        a.require(
            metadata() == expected
            and owned_listing(client, os.environ["BACKUP_TRACKING_OWNER"]) == before,
            "Source changed during capture",
        )
        shared.save(directory / "objects.json", records)
        shared.save(
            directory / "owner-marker.json", {"owner_id": os.environ["BACKUP_TRACKING_OWNER"]}
        )
        return {
            "status": "objects_captured",
            "objects": len(records),
            "bytes": sum(r["bytes"] for r in records),
        }
    a.verify(directory, digest)
    client = s3()
    if action == "seed":
        a.require(
            not client.list_buckets().get("Buckets"), "Isolated target object store is not empty"
        )
        client.create_bucket(Bucket=a.BUCKET)
        for row in a.document(directory / "objects.json"):
            with (directory / row["file"]).open("rb") as stream:
                client.put_object(
                    Bucket=a.BUCKET,
                    Key=row["key"],
                    Body=stream,
                    ContentLength=row["bytes"],
                    IfNoneMatch="*",
                )
        client.put_object(
            Bucket=a.BUCKET,
            Key="platform-owner.json",
            Body=(directory / "owner-marker.json").read_bytes(),
            IfNoneMatch="*",
        )
        result = verify_data(directory, client)
        with connect() as conn:
            shared.save(proof / "expected-api.json", restored.expected_api(conn))
        shared.save(proof / "control.json", a.document(directory / "control.json"))
        return result
    a.require(action == "verify", "Unknown worker action")
    return verify_data(directory, client)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=("runtime-check", "preflight", "inventory", "objects", "seed", "verify", "client"),
    )
    args = parser.parse_args()
    try:
        with redirect_stdout(__import__("sys").stderr):
            result = execute(
                args.action,
                Path("/capture"),
                Path("/proof"),
                os.environ.get("BACKUP_MANIFEST_SHA256"),
            )
    except Exception as exc:  # noqa: BLE001 - never print SDK/driver credential-bearing details
        result = {
            "status": "failed",
            "error_type": type(exc).__name__,
            "reason": str(exc)
            if isinstance(exc, shared.CaptureError)
            else "Private SDK/driver details withheld",
        }
    result["worker_sha256"] = a.sha(Path(__file__))
    print(json.dumps(result, allow_nan=False))
    if result["status"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
