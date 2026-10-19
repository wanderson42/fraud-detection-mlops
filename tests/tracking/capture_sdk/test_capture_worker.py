"""Use the real pinned botocore request/streaming contract, without a service."""

from copy import deepcopy
from datetime import UTC, datetime
from io import BytesIO

import pytest

boto3 = pytest.importorskip("boto3", reason="Run tracking-capture-sdk")
pytest.importorskip("psycopg", reason="Run tracking-capture-sdk")
from botocore.response import StreamingBody
from botocore.stub import Stubber
from scripts.tracking import capture_worker as worker


@pytest.fixture
def client():
    return boto3.client(
        "s3",
        endpoint_url="http://127.0.0.1:1",
        region_name="us-east-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )


def listing(key="v1/1/run/artifacts/probe.json", data=b"payload"):
    return {
        "Key": key,
        "Size": len(data),
        "ETag": '"etag"',
        "LastModified": datetime(2026, 10, 19, tzinfo=UTC),
    }


def list_call(stub, rows, token=None, next_token=None):
    args = {"Bucket": worker.BUCKET, "Prefix": worker.PREFIX, "MaxKeys": 1000}
    if token:
        args["ContinuationToken"] = token
    response = {"Contents": rows, "IsTruncated": bool(next_token)}
    if next_token:
        response["NextContinuationToken"] = next_token
    stub.add_response("list_objects_v2", response, args)


def get_call(stub, row, data):
    stub.add_response(
        "get_object",
        {"ContentLength": row["Size"], "Body": StreamingBody(BytesIO(data), len(data))},
        {"Bucket": worker.BUCKET, "Key": row["Key"], "IfMatch": row["ETag"]},
    )


def test_full_read_copy_relist_readback_and_closed_key_mapping(client, tmp_path):
    row, data = listing(), b"payload"
    metadata = {
        "locations": [{"table": "runs", "id": "run", "uri": "mlflow-artifacts:/1/run/artifacts"}]
    }
    with Stubber(client) as stub:
        list_call(stub, [row])
        get_call(stub, row, data)
        list_call(stub, [row])
        get_call(stub, row, data)
        list_call(stub, [row])
        records = worker.capture_objects(tmp_path, client, metadata)
        stub.assert_no_pending_responses()
    assert len(records) == 1 and records[0]["sha256"] == worker.digest(data)
    assert (tmp_path / records[0]["file"]).read_bytes() == data
    assert (tmp_path / records[0]["file"]).stat().st_mode & 0o777 == 0o600


def test_pagination_and_limits_are_not_silent_truncation(client, monkeypatch):
    with Stubber(client) as stub:
        list_call(stub, [listing()], next_token="page2")
        list_call(stub, [listing("v1/1/run/artifacts/other")], token="page2")
        assert len(worker.object_listing(client)) == 2
        stub.assert_no_pending_responses()
    monkeypatch.setattr(worker, "MAX_OBJECTS", 1)
    with Stubber(client) as stub:
        list_call(stub, [listing(), listing("v1/1/run/artifacts/other")])
        with pytest.raises(ValueError, match="budget"):
            worker.object_listing(client)


@pytest.mark.parametrize("bad", ["duplicate", "foreign", "bytes", "empty", "pagination"])
def test_bad_object_inventories_fail_closed(client, monkeypatch, bad):
    rows = [listing()]
    if bad == "duplicate":
        rows *= 2
    if bad == "foreign":
        rows[0]["Key"] = "v2/private"
    if bad == "bytes":
        monkeypatch.setattr(worker, "MAX_BYTES", 1)
    if bad == "empty":
        rows = []
    with Stubber(client) as stub:
        if bad == "pagination":
            stub.add_response(
                "list_objects_v2",
                {"Contents": rows, "IsTruncated": True},
                {"Bucket": worker.BUCKET, "Prefix": worker.PREFIX, "MaxKeys": 1000},
            )
        else:
            list_call(stub, rows)
        with pytest.raises(ValueError):
            worker.object_listing(client)


@pytest.mark.parametrize(
    "uri",
    [
        "s3://fraud-tracking-lab/v1",
        "file:///tmp/artifacts",
        "mlflow-artifacts://other/1",
        "mlflow-artifacts:/../1",
        "mlflow-artifacts:/1?secret=a",
        "mlflow-artifacts:/1/%2e%2e",
        "mlflow-artifacts:/1//model",
    ],
)
def test_uri_boundary_is_explicit(uri):
    with pytest.raises(ValueError):
        worker.key_path(uri)


def test_missing_model_dependencies_and_unowned_objects_are_rejected():
    metadata = {
        "locations": [
            {
                "table": "logged_models",
                "id": "m-a",
                "uri": "mlflow-artifacts:/1/models/m-a/artifacts",
            }
        ]
    }
    with pytest.raises(ValueError, match="fully covered"):
        worker.coverage(metadata, [{"key": "v1/1/models/m-a/artifacts/model.skops"}])
    with pytest.raises(ValueError, match="corresponding"):
        worker.coverage({"locations": []}, [{"key": "v1/unowned"}])


def test_short_stream_and_changed_object_cannot_succeed(client, tmp_path):
    row = listing()
    observed = {"key": row["Key"], "bytes": row["Size"], "etag": row["ETag"]}
    with Stubber(client) as stub:
        get_call(stub, row, b"bad")
        with pytest.raises(ValueError, match="truncated"):
            worker.read_object(client, observed)
    metadata = {"locations": [{"table": "runs", "uri": "mlflow-artifacts:/1/run/artifacts"}]}
    with Stubber(client) as stub:
        list_call(stub, [row])
        get_call(stub, row, b"payload")
        changed = deepcopy(row)
        changed["ETag"] = '"changed"'
        list_call(stub, [changed])
        with pytest.raises(ValueError, match="changed"):
            worker.capture_objects(tmp_path, client, metadata)
    assert not (tmp_path / "objects.json").exists()


def test_database_change_before_and_after_copy_is_detected(tmp_path, monkeypatch):
    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(worker, "pg_connect", Connection)
    worker.save(tmp_path / "metadata.json", {"version": "before"})
    monkeypatch.setattr(worker, "database_inventory", lambda c: {"version": "changed"})
    monkeypatch.setattr(worker, "s3_connect", lambda: pytest.fail("must not connect"))
    with pytest.raises(ValueError, match="before object"):
        worker.execute("objects", tmp_path)
    values = iter([{"version": "before"}, {"version": "changed"}])
    monkeypatch.setattr(worker, "database_inventory", lambda c: next(values))
    monkeypatch.setattr(worker, "s3_connect", lambda: None)
    monkeypatch.setattr(worker, "capture_objects", lambda *a: [])
    with pytest.raises(ValueError, match="during capture"):
        worker.execute("objects", tmp_path)
    assert not (tmp_path / "objects.json").exists()


class Cursor:
    """Record real psycopg SQL objects; fake only result transport, not SQL quoting."""

    def __init__(self, owner):
        self.owner = owner
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, query):
        from psycopg import sql

        text = query.as_string() if isinstance(query, sql.Composable) else query
        self.owner.queries.append(text)
        tables = [
            "experiments",
            "runs",
            "logged_models",
            "metrics",
            "params",
            "tags",
            "alembic_version",
        ]
        if text.startswith("SELECT current_database"):
            self.rows = [(self.owner.database, "mlflow", 170011)]
        elif "pg_stat_activity" in text:
            self.rows = [(self.owner.clients,)]
        elif "FROM pg_class" in text:
            self.rows = [("public", t, "r", self.owner.role) for t in tables] + [
                ("public", "sequence", "S", "mlflow")
            ]
        elif text.startswith("SELECT to_jsonb"):
            self.rows = [(row,) for row in self.owner.rows]
        elif "SELECT last_value" in text:
            self.rows = [(5, True)]
        elif "information_schema.columns" in text:
            self.rows = [("runs", "run_uuid", "character varying", "varchar", "NO", None, 1)]
        elif "SELECT version_num" in text:
            self.rows = [("dc11669786a5",)]
        elif '"artifact_location"' in text or '"artifact_uri"' in text:
            self.rows = [("1", self.owner.uri)]
        else:
            self.rows = []

    def fetchone(self):
        return self.rows[0]

    def fetchall(self):
        return self.rows

    def __iter__(self):
        return iter(self.rows)


class Database:
    database = "mlflow_lab"
    role = "mlflow"
    clients = 0
    uri = "mlflow-artifacts:/1/run/artifacts"
    rows = (
        '{"key": "metric", "step": 0, "value": 0.25}',
        '{"key": "metric", "step": 1, "value": 0.5}',
    )

    def __init__(self):
        self.queries = []

    def transaction(self):
        from contextlib import nullcontext

        return nullcontext()

    def cursor(self, name=None):
        return Cursor(self)


def test_full_table_fingerprints_and_history_are_order_independent_without_mlflow():
    import psycopg

    connection = Database()
    first = worker.database_inventory(connection)
    connection.rows = list(reversed(connection.rows))
    assert worker.database_inventory(connection) == first
    connection.rows = connection.rows[:1]
    assert worker.database_inventory(connection)["tables"]["metrics"] != first["tables"]["metrics"]
    assert first["tables"]["metrics"]["rows"] == 2
    assert first["sequences"]["sequence"] == {
        "last_value": 5,
        "is_called": True,
        "owner": "mlflow",
    }
    assert (
        connection.read_only
        and connection.isolation_level == psycopg.IsolationLevel.REPEATABLE_READ
    )
    assert all(
        not q.startswith(("INSERT", "UPDATE", "DELETE", "ALTER", "CREATE"))
        for q in connection.queries
    )
    assert any('"public"."metrics"' in q for q in connection.queries)


@pytest.mark.parametrize("case", ["database", "clients", "role", "uri", "budget"])
def test_database_boundary_and_metadata_budget_reject_unsupported_source(case, monkeypatch):
    connection = Database()
    if case == "budget":
        monkeypatch.setattr(worker, "MAX_METADATA", 1)
    else:
        setattr(
            connection,
            case,
            {"database": "historical", "clients": 1, "role": "admin", "uri": "file:///tmp/mlruns"}[
                case
            ],
        )
    with pytest.raises(ValueError):
        worker.database_inventory(connection)


@pytest.mark.parametrize("safe", [False, True])
def test_worker_cli_preserves_useful_private_failure_without_sdk_secrets(
    tmp_path, monkeypatch, safe, capsys
):
    from pathlib import Path
    import sys

    error = (
        worker.CaptureError("Other PostgreSQL clients remain connected")
        if safe
        else RuntimeError("password=secret signed-url")
    )
    monkeypatch.setattr(worker, "execute", lambda *a: (_ for _ in ()).throw(error))
    monkeypatch.setattr(
        worker,
        "Path",
        lambda value: (
            tmp_path
            if value == "/capture"
            else (tmp_path / "worker-failure.json")
            if value == "/capture/worker-failure.json"
            else Path(value)
        ),
    )
    monkeypatch.setattr(sys, "argv", ["capture_worker", "objects"])
    with pytest.raises(SystemExit) as exit_code:
        worker.main()
    assert exit_code.value.code == 1
    failure = (tmp_path / "worker-failure.json").read_text()
    assert "secret" not in failure and "signed-url" not in failure
    assert ("Other PostgreSQL clients" in failure) == safe
    output = capsys.readouterr()
    assert output.out == "" and "secret" not in output.err
