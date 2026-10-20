"""Transfer prepared dataset bundles to the owned KinD RustFS, without scientific imports."""

import argparse
from contextlib import closing
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import re

from scripts.storage import archive
from scripts.storage import bronze_s3_worker as transport

MARKER = "kind-datasets-owner.json"


def owned_bucket(client, owner, *, create):
    if not re.fullmatch(r"[a-f0-9]{32}", owner):
        raise ValueError("Invalid dataset owner")
    raw = archive.encoded({"schema": "fraud_kind_datasets_owner_v1", "owner_id": owner})
    try:
        client.head_bucket(Bucket=transport.BUCKET)
    except Exception as error:
        if not create or not transport.absent(error):
            raise
        client.create_bucket(Bucket=transport.BUCKET)
        # A failed/interrupted bootstrap is preserved; an unmarked existing bucket is refused.
        transport.put_verified(
            client, MARKER, BytesPath(raw), len(raw), hashlib.sha256(raw).hexdigest()
        )
    content = BytesIO()
    transport.stream_object(client, MARKER, len(raw), hashlib.sha256(raw).hexdigest(), content)
    if content.getvalue() != raw:
        raise ValueError("Dataset bucket belongs to another flow")


class BytesPath:
    """Small immutable marker exposed through the existing file transport interface."""

    def __init__(self, raw):
        self.raw = raw

    def open(self, mode):
        if mode != "rb":
            raise ValueError("Read-only marker")
        return BytesIO(self.raw)


def execute(action, identity, schema, client, work, owner):
    archive.bundle_id(identity)
    if schema not in archive.DATASET_SCHEMAS:
        raise ValueError("Only Bronze, Silver, Gold and development Gold are supported")
    if action not in {"publish", "download"}:
        raise ValueError("Unknown dataset action")
    owned_bucket(client, owner, create=action == "publish")
    result = (transport.publish if action == "publish" else transport.download)(
        work, identity, client, schema
    )
    return result | {"endpoint_scope": "owned_kind_rustfs", "dataset_owner_id": owner}


def make_client(endpoint):
    import boto3
    from botocore.config import Config

    if not re.fullmatch(r"http://127\.0\.0\.1:[0-9]{1,5}", endpoint):
        raise ValueError("Expected the explicit loopback KinD port-forward")
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
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
    parser.add_argument("action", choices=["publish", "download"])
    parser.add_argument("--bundle-id", required=True)
    parser.add_argument("--schema", choices=archive.DATASET_SCHEMAS, required=True)
    args = parser.parse_args()
    try:
        with closing(make_client(os.environ["KIND_DATASETS_ENDPOINT"])) as client:
            result = execute(
                args.action,
                args.bundle_id,
                args.schema,
                client,
                Path("/work"),
                os.environ["KIND_DATASETS_OWNER"],
            )
        print(json.dumps(result, allow_nan=False))
    except Exception as exc:  # noqa: BLE001 — credentialed subprocess boundary
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
