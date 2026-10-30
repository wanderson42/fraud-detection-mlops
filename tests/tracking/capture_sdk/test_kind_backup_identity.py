"""Exercise explicit KinD identities against the pinned SDK and shared SQL reader."""

from io import BytesIO

import pytest

boto3 = pytest.importorskip("boto3", reason="Run tracking-capture-sdk")
pytest.importorskip("psycopg", reason="Run tracking-capture-sdk")
from botocore.response import StreamingBody
from botocore.stub import Stubber
from scripts.platform.tracking_backup import archive as a
from scripts.tracking import capture_worker as shared
from tests.tracking.capture_sdk.test_capture_worker import Cursor, Database, listing


class KindCursor(Cursor):
    def execute(self, query):
        super().execute(query)
        text = self.owner.queries[-1]
        if text.startswith("SELECT current_database"):
            self.rows = [(self.owner.database, self.owner.role, 170011)]
        elif "FROM pg_class" in text:
            self.rows = [(ns, name, kind, self.owner.role) for ns, name, kind, _ in self.rows]


class KindDatabase(Database):
    database = role = a.DATABASE

    def cursor(self, name=None):
        return KindCursor(self)


def test_explicit_kind_database_and_role_preserve_full_inventory():
    connection = KindDatabase()
    result = shared.database_inventory(connection, database_name=a.DATABASE, role_name=a.ROLE)
    assert result["database"] == result["role"] == a.DATABASE
    assert result["tables"]["metrics"]["rows"] == 2
    assert result["sequences"]["sequence"]["last_value"] == 5
    assert connection.read_only
    with pytest.raises(ValueError, match="Unexpected PostgreSQL"):
        shared.database_inventory(connection)


@pytest.mark.parametrize("field", ["database", "role"])
def test_wrong_identity_is_not_adopted(field):
    connection = KindDatabase()
    setattr(connection, field, "other")
    with pytest.raises(ValueError, match="Unexpected PostgreSQL"):
        shared.database_inventory(connection, database_name=a.DATABASE, role_name=a.ROLE)


def test_kind_bucket_copy_readback_never_falls_back_to_lab_bucket(tmp_path):
    client = boto3.client(
        "s3",
        endpoint_url="http://127.0.0.1:1",
        region_name="us-east-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )
    data = b"payload"
    row = listing(data=data)
    args = {"Bucket": a.BUCKET, "Prefix": "v1/", "MaxKeys": 1000}
    get = {"Bucket": a.BUCKET, "Key": row["Key"], "IfMatch": row["ETag"]}
    with Stubber(client) as stub:
        stub.add_response("list_objects_v2", {"Contents": [row], "IsTruncated": False}, args)
        stub.add_response(
            "get_object",
            {"ContentLength": len(data), "Body": StreamingBody(BytesIO(data), len(data))},
            get,
        )
        stub.add_response("list_objects_v2", {"Contents": [row], "IsTruncated": False}, args)
        stub.add_response(
            "get_object",
            {"ContentLength": len(data), "Body": StreamingBody(BytesIO(data), len(data))},
            get,
        )
        stub.add_response("list_objects_v2", {"Contents": [row], "IsTruncated": False}, args)
        records = shared.capture_objects(
            tmp_path,
            client,
            {
                "locations": [
                    {"table": "runs", "id": "run", "uri": "mlflow-artifacts:/1/run/artifacts"}
                ]
            },
            bucket=a.BUCKET,
        )
        stub.assert_no_pending_responses()
    assert records[0]["sha256"] == shared.digest(data)
    assert (tmp_path / records[0]["file"]).read_bytes() == data
