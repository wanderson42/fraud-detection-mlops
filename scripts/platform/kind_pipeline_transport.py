"""Credentialed init/final containers for the Helm data Jobs; no scientific imports."""

import argparse
from contextlib import closing, redirect_stderr
import hashlib
from io import StringIO
import json
from pathlib import Path

from scripts.platform.kind_datasets_worker import BytesPath, owned_bucket
from scripts.platform.kind_pipeline_compute import INPUT_SCHEMAS, OUTPUT_SCHEMAS, identity, require
from scripts.storage import archive
from scripts.storage import bronze_s3_worker as transport


def execute(action, stage, input_id, run_id, owner_id, client, work=Path("/work")):
    identity(run_id, stage, input_id)
    require(action in {"download", "publish"}, "Unknown transport action")
    owned_bucket(client, owner_id, create=False)
    if action == "download":
        value = transport.download(work, input_id, client, INPUT_SCHEMAS[stage])
        value.update(stage=stage, run_id=run_id, input_bundle_id=input_id)
        with (work / "input.json").open("x") as stream:
            json.dump(value, stream, allow_nan=False)
        return value
    path = archive.checked_path(work, Path("compute.json"))
    require(path.stat().st_size <= 2 * 1024 * 1024, "Computation receipt exceeds metadata budget")
    computed = json.loads(path.read_text())
    require(
        computed.get("schema") == "fraud_kind_pipeline_computation_v1"
        and computed.get("status") == "computed_verified"
        and computed.get("stage") == stage
        and computed.get("run_id") == run_id
        and computed.get("input_bundle_id") == input_id,
        "Computation receipt does not belong to this Job",
    )
    output_id = archive.bundle_id(computed["output_bundle_id"])
    expected = "project/data/storage/bundles/" + output_id
    require(computed["bundle_relative"] == expected, "Unexpected producer bundle path")
    directory = archive.checked_path(work, Path(expected))
    document, _ = transport.manifest_at(directory, output_id, OUTPUT_SCHEMAS[stage])
    require(
        computed["files"] == len(document["files"])
        and computed["bytes"] == sum(r["bytes"] for r in document["files"]),
        "Producer receipt counts differ",
    )
    value = transport.publish(
        work / "project/data/storage", output_id, client, OUTPUT_SCHEMAS[stage]
    )
    result = {
        "schema": "fraud_kind_pipeline_stage_v1",
        "status": "published_verified",
        "stage": stage,
        "run_id": run_id,
        "input_bundle_id": input_id,
        "output_bundle_id": output_id,
        "dataset_owner_id": owner_id,
        "computation": computed,
        "publication": value,
    }
    raw = archive.encoded(result)
    key = f"platform-jobs/v1/{run_id}/{stage}/receipt.json"
    digest = hashlib.sha256(raw).hexdigest()
    transport.put_verified(client, key, BytesPath(raw), len(raw), digest)
    # Completion envelope is immutable and read back after the payload manifest.
    return result | {"remote_receipt_key": key, "remote_receipt_sha256": digest}


def make_client():
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url="http://fraud-platform-rustfs:9000",
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
    parser.add_argument("action", choices=["download", "publish"])
    parser.add_argument("--stage", required=True, choices=INPUT_SCHEMAS)
    parser.add_argument("--input-bundle-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--owner-id", required=True)
    args = parser.parse_args()
    try:
        with redirect_stderr(StringIO()), closing(make_client()) as client:
            result = execute(
                args.action, args.stage, args.input_bundle_id, args.run_id, args.owner_id, client
            )
        print(json.dumps(result, allow_nan=False))
    except Exception as exc:  # noqa: BLE001 — never expose SDK error bodies or credentials
        print(
            json.dumps({"status": "failed", "step": args.action, "error_type": type(exc).__name__})
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
