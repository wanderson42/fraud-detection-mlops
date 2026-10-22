"""Storage ownership and byte parity with an in-memory S3 fake; no service calls."""

import hashlib
from io import BytesIO
import json
import sys
from types import ModuleType, SimpleNamespace

import pytest
from scripts.platform import kind_tracking_storage as storage


@pytest.fixture
def fake(monkeypatch):
    class ClientError(Exception):
        def __init__(self, code):
            self.response = {"Error": {"Code": code}}

    objects = {}
    state = {"exists": False}

    class Client:
        def head_bucket(self, **kw):
            if not state["exists"]:
                raise ClientError("404")

        def create_bucket(self, **kw):
            state["exists"] = True

        def put_object(self, Bucket, Key, Body):
            objects[Key] = Body

        def get_object(self, Bucket, Key):
            if Key not in objects:
                raise ClientError("NoSuchKey")
            return {"Body": BytesIO(objects[Key])}

    boto = ModuleType("boto3")
    boto.client = lambda *a, **kw: Client()
    config = ModuleType("botocore.config")
    config.Config = lambda **kw: kw
    exceptions = ModuleType("botocore.exceptions")
    exceptions.ClientError = ClientError
    monkeypatch.setitem(sys.modules, "boto3", boto)
    monkeypatch.setitem(sys.modules, "botocore", ModuleType("botocore"))
    monkeypatch.setitem(sys.modules, "botocore.config", config)
    monkeypatch.setitem(sys.modules, "botocore.exceptions", exceptions)
    monkeypatch.setenv("TRACKING_OWNER_ID", "a" * 32)
    return SimpleNamespace(objects=objects, state=state, error=ClientError)


def test_prepare_is_idempotent_with_owned_marker(fake):
    assert storage.execute("prepare")["status"] == "bucket_owned"
    before = dict(fake.objects)
    storage.execute("prepare")
    assert fake.objects == before
    assert json.loads(fake.objects[storage.MARKER]) == {"owner_id": "a" * 32}


def test_existing_bucket_without_marker_is_not_adopted(fake):
    fake.state["exists"] = True
    with pytest.raises(fake.error):
        storage.execute("prepare")
    assert not fake.objects


def test_existing_foreign_marker_is_not_replaced(fake):
    fake.state["exists"] = True
    fake.objects[storage.MARKER] = b"foreign"
    with pytest.raises(ValueError, match="owner marker differs"):
        storage.execute("prepare")
    assert fake.objects[storage.MARKER] == b"foreign"


def test_direct_model_readback_compares_http_hash(fake):
    storage.execute("prepare")
    data = b"the exact synthetic model bytes"
    fake.objects["v1/1/models/m-abc/artifacts/model.skops"] = data
    record = {
        "model_artifact_uri": "mlflow-artifacts:/1/models/m-abc/artifacts",
        "model_sha256": hashlib.sha256(data).hexdigest(),
    }
    assert storage.execute("verify", record)["bytes"] == len(data)
    record["model_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="hash differs"):
        storage.execute("verify", record)


@pytest.mark.parametrize(
    "uri",
    [
        "file:/tmp/model",
        "s3://foreign/model",
        "mlflow-artifacts:/1/models/m-abc/../x",
        "mlflow-artifacts://foreign/1/models/m-abc/artifacts",
    ],
)
def test_unreviewed_model_locations_refused(fake, uri):
    storage.execute("prepare")
    with pytest.raises(ValueError, match="artifact location"):
        storage.execute("verify", {"model_artifact_uri": uri, "model_sha256": "0" * 64})
