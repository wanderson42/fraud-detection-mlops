"""Single-process S3 transport for prepared dataset/study archives; no scientific imports."""

import argparse
import base64
from contextlib import closing
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time

from scripts.storage import archive, mlflow_backup

BUCKET = "fraud-datasets"
TRANSPORT_SCHEMAS = (*archive.PROVENANCE_SCHEMAS, mlflow_backup.SCHEMA)
CHUNK = 1024 * 1024


def code_sha256():
    digest = hashlib.sha256()
    for name in ("archive.py", "bronze_s3_worker.py", "mlflow_inventory.py", "mlflow_backup.py"):
        digest.update(name.encode() + b"\0" + Path(__file__).with_name(name).read_bytes())
    return digest.hexdigest()


def absent(error):
    response = getattr(error, "response", {})
    return response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 404 and str(
        response.get("Error", {}).get("Code")
    ) in {"404", "NoSuchKey", "NoSuchBucket", "NotFound"}


def head(client, key, *, bucket=BUCKET):
    try:
        return client.head_object(Bucket=bucket, Key=key)["ContentLength"]
    except Exception as error:
        if absent(error):
            return None
        raise


def stream_object(client, key, size, expected_hash, destination=None, *, bucket=BUCKET):
    if head(client, key, bucket=bucket) != size:
        raise ValueError("Remote object missing or size differs from manifest")
    response = client.get_object(Bucket=bucket, Key=key)
    body = response["Body"]
    count, digest = 0, hashlib.sha256()
    try:
        if response["ContentLength"] != size:
            raise ValueError("Remote GET size differs from manifest")
        while chunk := body.read(min(CHUNK, size - count + 1)):
            count += len(chunk)
            if count > size:
                raise ValueError("Remote object exceeds declared size")
            digest.update(chunk)
            if destination is not None:
                destination.write(chunk)
    finally:
        body.close()
    if count != size or digest.hexdigest() != expected_hash:
        raise ValueError("Remote object SHA-256 or size mismatch")


def manifest_at(directory, identity, schema=archive.BRONZE_SCHEMA):
    path = archive.checked_path(directory, Path("manifest.json"))
    if not 0 < path.stat().st_size <= archive.BRONZE_MAX_MANIFEST_BYTES:
        raise ValueError("Dataset manifest exceeds metadata budget")
    raw = path.read_bytes()
    document = json.loads(raw)
    validate_document(document, raw, identity, schema)
    return document, raw


def validate_document(document, raw, identity, schema):
    if (
        not isinstance(document, dict)
        or document.get("schema") != schema
        or raw != archive.encoded(document)
    ):
        raise ValueError(f"Expected canonical {layer(schema).capitalize()} manifest")
    if schema == mlflow_backup.SCHEMA:
        mlflow_backup.manifest_id(document, identity)
    else:
        archive.validate_manifest(document, identity)


def verify_payload(directory, document):
    if document["schema"] != mlflow_backup.SCHEMA:
        archive.verify_files(directory, document)
        return
    # Byte transport only; host verifier checks each archived SQLite DB and its metadata.
    for row in document["files"]:
        path = archive.checked_path(directory, mlflow_backup.path_value(row["path"]))
        if (
            not path.is_file()
            or path.stat().st_size != row["bytes"]
            or archive.digest(path) != row["sha256"]
        ):
            raise ValueError("File integrity mismatch: " + row["path"])


def layer(schema):
    if schema not in TRANSPORT_SCHEMAS:
        raise ValueError("Unsupported dataset transport profile")
    return {
        archive.BRONZE_SCHEMA: "bronze",
        archive.SILVER_SCHEMA: "silver",
        archive.GOLD_SCHEMA: "gold",
        archive.DEVELOPMENT_SCHEMA: "development-gold",
        archive.HGB_STUDY_SCHEMA: "hgb-study",
        mlflow_backup.SCHEMA: "mlflow-tracking",
    }[schema]


def bucket_for(schema):
    layer(schema)
    return (
        "fraud-artifacts" if schema in {archive.HGB_STUDY_SCHEMA, mlflow_backup.SCHEMA} else BUCKET
    )


def prefix(identity, schema=archive.BRONZE_SCHEMA):
    return f"{layer(schema)}/v1/{archive.bundle_id(identity)}/"


def ensure_bucket(client, *, bucket=BUCKET):
    try:
        client.head_bucket(Bucket=bucket)
    except Exception as error:
        if not absent(error):
            raise
        try:
            client.create_bucket(Bucket=bucket)
        except Exception as race:
            if (
                getattr(race, "response", {}).get("Error", {}).get("Code")
                != "BucketAlreadyOwnedByYou"
            ):
                raise


def put_verified(client, key, path, size, digest, *, bucket=BUCKET):
    uploaded = 0
    if head(client, key, bucket=bucket) is None:
        with path.open("rb") as body:
            try:
                client.put_object(
                    Bucket=bucket,
                    Key=key,
                    Body=body,
                    ContentLength=size,
                    ChecksumSHA256=base64.b64encode(bytes.fromhex(digest)).decode(),
                    IfNoneMatch="*",
                )
                uploaded = 1
            except Exception as error:
                if (
                    getattr(error, "response", {})
                    .get("ResponseMetadata", {})
                    .get("HTTPStatusCode")
                    != 412
                ):
                    raise
                # Another writer won the conditional PUT; verify its bytes below.
    stream_object(client, key, size, digest, bucket=bucket)
    return uploaded


def check_listing(client, key_prefix, expected, *, bucket=BUCKET):
    observed = {}
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=key_prefix):
        for row in page.get("Contents", []):
            key = row["Key"]
            if key in observed or key not in expected or row["Size"] != expected[key]:
                raise ValueError("Unexpected object or size in dataset prefix")
            observed[key] = row["Size"]
    if observed != expected:
        raise ValueError("Incomplete dataset prefix")


def progress(done, total, schema=archive.BRONZE_SCHEMA):
    if done % 25 == 0 or done == total:
        print(
            f"{layer(schema).capitalize()}: {done}/{total} payloads verified",
            file=sys.stderr,
            flush=True,
        )


def publish(work, identity, client, schema=archive.BRONZE_SCHEMA):
    directory = archive.checked_path(work, Path("bundles") / identity)
    document, raw = manifest_at(directory, identity, schema)
    verify_payload(directory, document)
    bucket = bucket_for(schema)
    ensure_bucket(client, bucket=bucket)
    key_prefix, uploaded = prefix(identity, schema), 0
    expected = {key_prefix + row["path"]: row["bytes"] for row in document["files"]}
    for i, row in enumerate(document["files"], 1):
        uploaded += put_verified(
            client,
            key_prefix + row["path"],
            directory / row["path"],
            row["bytes"],
            row["sha256"],
            bucket=bucket,
        )
        progress(i, len(document["files"]), schema)
    # The completion marker is written only after every payload has been read back.
    # Existing markers are also verified, so retries cannot silently accept corruption.
    marker = key_prefix + "manifest.json"
    existing = head(client, marker, bucket=bucket)
    before_marker = expected | ({marker: len(raw)} if existing is not None else {})
    check_listing(client, key_prefix, before_marker, bucket=bucket)
    uploaded += put_verified(
        client,
        marker,
        directory / "manifest.json",
        len(raw),
        hashlib.sha256(raw).hexdigest(),
        bucket=bucket,
    )
    check_listing(client, key_prefix, expected | {marker: len(raw)}, bucket=bucket)
    return {
        "status": "success",
        "bundle_id": identity,
        "bucket": bucket,
        "prefix": key_prefix,
        "uploaded_objects": uploaded,
        "reused_objects": len(expected) + 1 - uploaded,
        "verified_objects": len(expected) + 1,
        "bytes": sum(row["bytes"] for row in document["files"]),
        "scope": f"s3_{layer(schema)}_publication_with_full_readback_not_primary_storage_migration",
    }


def download(work, identity, client, schema=archive.BRONZE_SCHEMA):
    bucket = bucket_for(schema)
    key_prefix = prefix(identity, schema)
    marker = key_prefix + "manifest.json"
    size = head(client, marker, bucket=bucket)
    if size is None or not 0 < size <= archive.BRONZE_MAX_MANIFEST_BYTES:
        raise ValueError("Dataset completion manifest absent or exceeds metadata budget")
    # Identity hashes canonical manifest bytes; reject other encodings below.
    from io import BytesIO

    manifest = BytesIO()
    stream_object(client, marker, size, identity, manifest, bucket=bucket)
    raw = manifest.getvalue()
    document = json.loads(raw)
    validate_document(document, raw, identity, schema)
    expected = {key_prefix + row["path"]: row["bytes"] for row in document["files"]}
    check_listing(client, key_prefix, expected | {marker: len(raw)}, bucket=bucket)
    required = sum(row["bytes"] for row in document["files"])
    if shutil.disk_usage(work).free < required + 16 * CHUNK:
        raise ValueError("Insufficient free space for dataset download")
    downloads = archive.checked_path(work, Path("downloads"))
    downloads.mkdir(exist_ok=True)
    candidate = Path(tempfile.mkdtemp(prefix=layer(schema) + "-", dir=downloads))
    try:
        for i, row in enumerate(document["files"], 1):
            path = archive.checked_path(candidate, Path(row["path"]))
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as stream:
                if schema == mlflow_backup.SCHEMA:
                    path.chmod(0o600)
                stream_object(
                    client,
                    key_prefix + row["path"],
                    row["bytes"],
                    row["sha256"],
                    stream,
                    bucket=bucket,
                )
            progress(i, len(document["files"]), schema)
        (candidate / "manifest.json").write_bytes(raw)
        if schema == mlflow_backup.SCHEMA:
            (candidate / "manifest.json").chmod(0o600)
        verify_payload(candidate, document)
    except BaseException:
        shutil.rmtree(candidate)
        raise
    return {
        "status": "downloaded_quarantined",
        "bundle_id": identity,
        "bucket": bucket,
        "prefix": key_prefix,
        "candidate": candidate.relative_to(work).as_posix(),
        "files": len(document["files"]),
        "bytes": required,
        "verified_objects": len(expected) + 1,
        "scope": f"byte_integrity_only_pending_host_{layer(schema)}_verifier",
    }


def make_client():
    # Operational dependency only: the scientific Poetry environment never imports it.
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url="http://rustfs:9000",
        region_name="us-east-1",
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            connect_timeout=5,
            read_timeout=30,
            retries={"mode": "standard", "total_max_attempts": 3},
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        ),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["publish", "download"])
    parser.add_argument("--bundle-id", required=True, type=archive.bundle_id)
    parser.add_argument("--expected-code-sha256", required=True)
    parser.add_argument("--schema", choices=TRANSPORT_SCHEMAS, default=archive.BRONZE_SCHEMA)
    args = parser.parse_args()
    try:
        fingerprint = code_sha256()
        if args.expected_code_sha256 != fingerprint:
            raise ValueError("Storage image differs from host scripts; rebuild storage-transfer")
        started = time.monotonic()
        with closing(make_client()) as client:
            result = (publish if args.command == "publish" else download)(
                Path("/work"), args.bundle_id, client, args.schema
            )
        result.update(
            worker_code_sha256=fingerprint,
            boto3_version=version("boto3"),
            botocore_version=version("botocore"),
            elapsed_seconds=time.monotonic() - started,
        )
        print(json.dumps(result, allow_nan=False))
    except (OSError, TypeError, ValueError) as error:
        parser.exit(1, f"Dataset transfer stopped: {error}\n")
    except Exception as error:  # noqa: BLE001 - sanitized CLI boundary
        # SDK errors can include service bodies or request details. Keep credentials out of logs.
        parser.exit(
            1,
            f"Dataset S3 request failed ({type(error).__name__}); check RustFS readiness and credentials.\n",
        )


if __name__ == "__main__":
    main()
