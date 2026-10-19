"""Actual pinned botocore parameter validation; Stubber forbids S3 network access."""

import base64
from contextlib import closing
import hashlib
from io import BytesIO

import pytest

boto3 = pytest.importorskip("boto3", reason="Run the isolated bronze-sdk tox environment")
from botocore.exceptions import ClientError
from botocore.response import StreamingBody
from botocore.stub import ANY, Stubber
from scripts.storage import bronze_s3_worker as worker


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "fixture-access")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "fixture-secret")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    with closing(worker.make_client()) as client:
        yield client


def test_pinned_sdk_accepts_conditional_checksum_put_and_readback(client, tmp_path):
    data = b"opaque bytes"
    digest = hashlib.sha256(data).hexdigest()
    path = tmp_path / "payload"
    path.write_bytes(data)
    params = {"Bucket": worker.BUCKET, "Key": "bronze/v1/fixture/payload"}
    with Stubber(client) as stub:
        stub.add_client_error("head_object", "404", http_status_code=404, expected_params=params)
        stub.add_response(
            "put_object",
            {},
            params
            | {
                "Body": ANY,
                "ContentLength": len(data),
                "ChecksumSHA256": base64.b64encode(bytes.fromhex(digest)).decode(),
                "IfNoneMatch": "*",
            },
        )
        stub.add_response("head_object", {"ContentLength": len(data)}, params)
        stub.add_response(
            "get_object",
            {"ContentLength": len(data), "Body": StreamingBody(BytesIO(data), len(data))},
            params,
        )
        assert worker.put_verified(client, params["Key"], path, len(data), digest) == 1
        stub.assert_no_pending_responses()


def test_sdk_403_is_never_interpreted_as_absence(client):
    with Stubber(client) as stub:
        stub.add_client_error(
            "head_object",
            "AccessDenied",
            http_status_code=403,
            expected_params={"Bucket": worker.BUCKET, "Key": "key"},
        )
        with pytest.raises(ClientError):
            worker.head(client, "key")
        stub.assert_no_pending_responses()


def test_real_paginator_reads_every_page(client):
    prefix = "bronze/v1/fixture/"
    expected = {prefix + "one": 1, prefix + "two": 2}
    with Stubber(client) as stub:
        stub.add_response(
            "list_objects_v2",
            {
                "IsTruncated": True,
                "NextContinuationToken": "next",
                "Contents": [{"Key": prefix + "one", "Size": 1}],
            },
            {"Bucket": worker.BUCKET, "Prefix": prefix},
        )
        stub.add_response(
            "list_objects_v2",
            {"IsTruncated": False, "Contents": [{"Key": prefix + "two", "Size": 2}]},
            {"Bucket": worker.BUCKET, "Prefix": prefix, "ContinuationToken": "next"},
        )
        worker.check_listing(client, prefix, expected)
        stub.assert_no_pending_responses()


def test_client_uses_local_path_style_and_bounded_retries(client):
    assert client.meta.endpoint_url == "http://rustfs:9000"
    config = client.meta.config
    assert config.s3["addressing_style"] == "path" and config.signature_version == "s3v4"
    assert config.connect_timeout == 5 and config.read_timeout == 30
    assert config.retries["total_max_attempts"] == 3


@pytest.mark.parametrize("schema", [worker.archive.HGB_STUDY_SCHEMA, worker.mlflow_backup.SCHEMA])
def test_artifact_bucket_put_readback_and_listing_use_explicit_bucket(client, tmp_path, schema):
    data = b"study snapshot"
    path = tmp_path / "snapshot"
    path.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    bucket = worker.bucket_for(schema)
    assert bucket == "fraud-artifacts"
    prefix = worker.prefix("a" * 64, schema)
    key = prefix + "snapshot"
    params = {"Bucket": bucket, "Key": key}
    with Stubber(client) as stub:
        stub.add_client_error("head_object", "404", http_status_code=404, expected_params=params)
        stub.add_response(
            "put_object",
            {},
            params
            | {
                "Body": ANY,
                "ContentLength": len(data),
                "ChecksumSHA256": base64.b64encode(bytes.fromhex(digest)).decode(),
                "IfNoneMatch": "*",
            },
        )
        stub.add_response("head_object", {"ContentLength": len(data)}, params)
        stub.add_response(
            "get_object",
            {"ContentLength": len(data), "Body": StreamingBody(BytesIO(data), len(data))},
            params,
        )
        assert worker.put_verified(client, key, path, len(data), digest, bucket=bucket) == 1
        stub.add_response(
            "list_objects_v2",
            {"IsTruncated": False, "Contents": [{"Key": key, "Size": len(data)}]},
            {"Bucket": bucket, "Prefix": prefix},
        )
        worker.check_listing(client, prefix, {key: len(data)}, bucket=bucket)
        stub.assert_no_pending_responses()
