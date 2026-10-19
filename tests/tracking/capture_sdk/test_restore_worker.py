"""Restore integrity and full API history contracts; no real PostgreSQL or S3."""

import json
from types import SimpleNamespace as Item

import pytest

pytest.importorskip("psycopg", reason="Run tracking-capture-sdk")
pytest.importorskip("mlflow", reason="Run tracking-capture-sdk")
from scripts.tracking import capture_worker as archive
from scripts.tracking import restore_worker as worker


def api_fixture():
    expected = {
        "experiments": [
            {
                "experiment_id": 1,
                "name": "exp",
                "artifact_location": "mlflow-artifacts:/1",
                "lifecycle_stage": "active",
            }
        ],
        "runs": [
            {
                "run_uuid": "run",
                "status": "FAILED",
                "experiment_id": 1,
                "artifact_uri": "mlflow-artifacts:/1/run/artifacts",
                "lifecycle_stage": "deleted",
            }
        ],
        "params": [{"run_uuid": "run", "key": "depth", "value": "1"}],
        "tags": [{"run_uuid": "run", "key": "owner", "value": "fixture"}],
        "metrics": [
            {
                "run_uuid": "run",
                "key": "AP",
                "value": v,
                "timestamp": step + 1,
                "step": step,
                "is_nan": False,
            }
            for step, v in enumerate([0.2, 0.6])
        ],
        "logged_models": [],
    }

    class Client:
        def get_experiment(self, _):
            return Item(
                name="exp", artifact_location="mlflow-artifacts:/1", lifecycle_stage="active"
            )

        def get_run(self, _):
            return Item(
                info=Item(
                    status="FAILED",
                    experiment_id="1",
                    artifact_uri="mlflow-artifacts:/1/run/artifacts",
                    lifecycle_stage="deleted",
                ),
                data=Item(params={"depth": "1"}, tags={"owner": "fixture"}),
            )

        def get_metric_history(self, *args):
            return [
                Item(value=r["value"], timestamp=r["timestamp"], step=r["step"])
                for r in expected["metrics"]
            ]

    return expected, Client()


def test_full_history_preserves_failed_deleted_run_and_earlier_metric():
    expected, client = api_fixture()
    assert worker.inspect_api(client, expected) == {
        "runs": 1,
        "logged_models": 0,
        "metric_history_rows": 2,
    }
    client.get_metric_history = lambda *a: [Item(value=0.6, timestamp=2, step=1)]
    with pytest.raises(ValueError, match="history"):
        worker.inspect_api(client, expected)


@pytest.mark.parametrize("bad", ["keys", "bytes", "metadata"])
def test_target_readback_detects_source_mismatch(tmp_path, monkeypatch, bad):
    records = [{"key": "v1/key", "bytes": 1, "sha256": "sha"}]
    archive.save(tmp_path / "objects.json", records)
    archive.save(tmp_path / "metadata.json", {"tables": {"metrics": {"rows": 44}}})
    monkeypatch.setattr(
        archive,
        "object_listing",
        lambda client: [{**records[0], "etag": "e"}] if bad != "keys" else [],
    )
    monkeypatch.setattr(archive, "read_object", lambda *a: "wrong" if bad == "bytes" else "sha")

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    monkeypatch.setattr(worker, "connect", Connection)
    monkeypatch.setattr(
        archive, "database_inventory", lambda c: {"tables": {"metrics": {"rows": 43}}}
    )
    with pytest.raises(ValueError):
        worker.verify_data(tmp_path, object())


def test_client_rejects_infrastructure_credentials_before_prediction(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "not-for-client")
    with pytest.raises(ValueError, match="credentials"):
        worker.reload_model(None, {}, tmp_path)


@pytest.mark.parametrize("safe", [True, False])
def test_worker_failure_protocol_is_json_redacted_and_never_client_success(
    monkeypatch, capsys, safe
):
    import sys

    error = (
        archive.CaptureError("Restored PostgreSQL logical/schema/sequence contents differ")
        if safe
        else RuntimeError("password=secret signed-url")
    )

    def fail(proof):
        worker.stage("postgres_comparison")
        worker.DETAILS["metadata_difference"] = {"tables": {"changed_names": ["metrics"]}}
        raise error

    monkeypatch.setattr(worker, "client_check", fail)
    monkeypatch.setattr(sys, "argv", ["restore_worker", "client"])
    worker.main()
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "failed" and result["step"] == "postgres_comparison"
    assert result["worker_code_sha256"]
    assert "secret" not in json.dumps(result) and "signed-url" not in json.dumps(result)
    assert ("contents differ" in result["reason"]) == safe
    assert result["details"]["metadata_difference"]["tables"]["changed_names"] == ["metrics"]


def test_metadata_diagnostics_do_not_copy_data_values():
    expected = {"tables": {"metrics": {"sha256": "a", "rows": 44}}, "columns": [["secret-value"]]}
    actual = {"tables": {"metrics": {"sha256": "b", "rows": 43}}, "columns": [["new-password"]]}
    delta = worker.metadata_difference(expected, actual)
    assert delta["tables"]["changed_names"] == ["metrics"]
    assert delta["columns"]["expected_items"] == delta["columns"]["actual_items"] == 1
    assert "secret-value" not in json.dumps(delta) and "new-password" not in json.dumps(delta)
