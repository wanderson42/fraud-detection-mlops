"""Credentialed bootstrap/readback worker, executed only in the dedicated namespace."""

import hashlib
import json
import os
import re
import sys

BUCKET = "fraud-kind-mlflow"
MARKER = "platform-owner.json"


def require(ok, message):
    if not ok:
        raise ValueError(message)


def execute(action, record=None):
    import boto3
    from botocore.config import Config
    from botocore.exceptions import ClientError

    owner = os.environ["TRACKING_OWNER_ID"]
    require(bool(re.fullmatch(r"[a-f0-9]{32}", owner)), "Invalid owner identity")
    client = boto3.client(
        "s3",
        endpoint_url="http://fraud-platform-rustfs:9000",
        region_name="us-east-1",
        config=Config(
            connect_timeout=5,
            read_timeout=15,
            retries={"max_attempts": 1},
            s3={"addressing_style": "path"},
        ),
    )
    marker = (json.dumps({"owner_id": owner}, sort_keys=True) + "\n").encode()
    try:
        client.head_bucket(Bucket=BUCKET)
    except ClientError as exc:
        require(
            action == "prepare"
            and str(exc.response["Error"]["Code"]) in {"404", "NoSuchBucket", "NotFound"},
            "Bucket unavailable",
        )
        client.create_bucket(Bucket=BUCKET)
        client.put_object(Bucket=BUCKET, Key=MARKER, Body=marker)
    actual = client.get_object(Bucket=BUCKET, Key=MARKER)["Body"].read()
    require(actual == marker, "Bucket owner marker differs; do not adopt it")
    if action == "prepare":
        return {"status": "bucket_owned", "bucket": BUCKET}
    require(action == "verify" and isinstance(record, dict), "Invalid storage action")
    uri = record["model_artifact_uri"]
    require(
        bool(re.fullmatch(r"mlflow-artifacts:/[0-9]+/models/m-[a-f0-9]+/artifacts", uri)),
        "Unexpected model artifact location",
    )
    key = "v1/" + uri.removeprefix("mlflow-artifacts:/") + "/model.skops"
    data = client.get_object(Bucket=BUCKET, Key=key)["Body"].read()
    require(hashlib.sha256(data).hexdigest() == record["model_sha256"], "S3 model hash differs")
    return {
        "status": "s3_model_verified",
        "bucket": BUCKET,
        "key": key,
        "model_sha256": record["model_sha256"],
        "bytes": len(data),
    }


if __name__ == "__main__":
    try:
        result = execute(sys.argv[1], json.loads(sys.argv[2]) if len(sys.argv) == 3 else None)
    except Exception as exc:  # noqa: BLE001 — subprocess JSON boundary, no credential traceback
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__}), flush=True)
        raise SystemExit(1) from None
    print(json.dumps(result), flush=True)
