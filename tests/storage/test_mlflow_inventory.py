"""Read-only MLflow inventory against the pinned SDK's real SQLite schema."""

from contextlib import closing
import hashlib
import json
from pathlib import Path
import socket
import sqlite3

import pytest
from scripts.storage import mlflow_inventory as inv


def contents(root):
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file()
    }


def test_real_store_all_tables_history_deleted_runs_and_no_source_connection(store, monkeypatch):
    root, tracking, _, _, model = store
    before = contents(root)
    connect = inv.sqlite3.connect
    calls = []

    def guarded(path, *args, **kwargs):
        assert str(tracking) not in str(path)
        calls.append(path)
        return connect(path, *args, **kwargs)

    monkeypatch.setattr(inv.sqlite3, "connect", guarded)
    monkeypatch.setattr(socket.socket, "connect", lambda *args: pytest.fail("network used"))
    result = inv.inventory(root)
    assert calls and contents(root) == before
    assert len(result["database"]["runs"]) == 2
    assert result["run_statuses"] == {"FINISHED": 1, "FAILED": 1}
    assert {r["lifecycle_stage"] for r in result["database"]["runs"]} == {"active", "deleted"}
    assert result["database"]["tables"]["metrics"]["rows"] == 2
    assert result["database"]["tables"]["latest_metrics"]["rows"] == 1
    assert result["database"]["logged_models"][0]["model_id"] == model.model_id
    assert result["database"]["logged_models"][0]["status"] == 2
    assert all(r["resolved"] for r in result["references"])
    assert result["unresolved_references"] == 0
    assert not result["source_database_opened"] and not result["mlflow_initialized"]
    assert not result["models_loaded"] and not result["backup_validated"]
    assert "do-not-emit-parameter-value" not in json.dumps(result)


def test_repeat_fingerprints_and_all_artifact_bytes(store):
    root, _, _, _, _ = store
    first, second = inv.inventory(root), inv.inventory(root)
    assert first["artifact_manifest_sha256"] == second["artifact_manifest_sha256"]
    assert (
        first["database"]["table_fingerprint_sha256"]
        == second["database"]["table_fingerprint_sha256"]
    )
    assert first["artifact_files_count"] == 2
    assert first["artifact_bytes"] == len(b"not-a-deserializable-model") + len(b'{"ap":0.6}\n')


def test_committed_wal_is_included_without_touching_source_sidecars(store):
    root, tracking, *_ = store
    with closing(sqlite3.connect(tracking / "mlflow.db")) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("CREATE TABLE inventory_wal_sentinel(value TEXT)")
        writer.execute("INSERT INTO inventory_wal_sentinel VALUES ('committed-in-wal')")
        writer.commit()
        assert (tracking / "mlflow.db-wal").stat().st_size
        before = contents(root)
        result = inv.inventory(root)
        assert result["database"]["tables"]["inventory_wal_sentinel"]["rows"] == 1
        assert contents(root) == before


@pytest.mark.parametrize(
    "change", ["database", "artifact", "new_artifact", "sidecar", "reference"]
)
def test_source_mutation_rejected_without_report(store, monkeypatch, change):
    root, tracking, *_ = store
    original = inv.project_references
    triggered = False

    def mutate(*args, **kwargs):
        nonlocal triggered
        result = original(*args, **kwargs)
        if not triggered:
            triggered = True
            if change == "database":
                with sqlite3.connect(tracking / "mlflow.db") as connection:
                    connection.execute("CREATE TABLE new_write(value TEXT)")
            elif change == "artifact":
                next((tracking / "artifacts").rglob("pipeline.skops")).write_bytes(b"changed")
            elif change == "new_artifact":
                (tracking / "artifacts/new.txt").write_bytes(b"new")
            elif change == "sidecar":
                (tracking / "mlflow.db-wal").write_bytes(b"changed")
            else:
                (root / "data/processed/fixture/manifest.json").write_text("{}")
        return result

    monkeypatch.setattr(inv, "project_references", mutate)
    with pytest.raises(inv.InventoryError, match="changed"):
        inv.inventory(root)
    assert not (root / "data/storage").exists()


def test_missing_store_does_not_initialize(tmp_path):
    assert inv.main(["--repo", str(tmp_path)]) == 1
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("target", ["database", "artifact", "reference", "output"])
def test_symlinks_rejected_before_read_or_report(store, tmp_path, target):
    root, tracking, *_ = store
    external = tmp_path / "external"
    external.mkdir()
    if target == "database":
        path = tracking / "mlflow.db"
        destination = external / "db"
        path.rename(destination)
        path.symlink_to(destination)
    elif target == "artifact":
        (external / "secret").write_text("do-not-read")
        (tracking / "artifacts/link").symlink_to(external, target_is_directory=True)
    elif target == "reference":
        (external / "secret.json").write_text("do-not-read")
        (root / "data/processed/link").symlink_to(external, target_is_directory=True)
    else:
        (root / "data/storage").symlink_to(external, target_is_directory=True)
    before = contents(external)
    assert inv.main(["--repo", str(root)]) == 1
    assert contents(external) == before


def test_outside_remote_credentials_and_missing_references_are_reported_without_access(store):
    root, tracking, _, _, _ = store
    with sqlite3.connect(tracking / "mlflow.db") as connection:
        connection.execute(
            "UPDATE logged_models SET artifact_location=?",
            ("s3://user:password@bucket/models?token=secret",),
        )
    path = root / "data/processed/fixture/manifest.json"
    path.write_text(
        json.dumps(
            {
                "mlflow_run_id": "f" * 32,
                "model_uri": "models:/m-" + "f" * 32,
                "artifact_uri": "https://user:password@example.invalid/a?token=secret",
            }
        )
    )
    result = inv.inventory(root)
    assert result["status"] == "review_required"
    assert result["unresolved_references"] == 2
    assert "external_or_sensitive" in result["uri_kinds"]
    assert "password" not in json.dumps(result) and "token=secret" not in json.dumps(result)
    assert not result["network_accessed"]


def test_active_runs_require_review_and_registered_models_are_counted(store):
    root, _, client, *_ = store
    experiment = client.get_experiment_by_name("inventory-fixture")
    client.create_run(experiment.experiment_id)
    client.create_registered_model("inventory-registry-fixture")
    result = inv.inventory(root)
    assert result["run_statuses"]["RUNNING"] == 1
    assert result["database"]["tables"]["registered_models"]["rows"] == 1
    assert any("Unfinished" in issue for issue in result["issues"])


@pytest.mark.parametrize("diagnostics_report", [False, True])
def test_local_execution_uuid_is_not_mistaken_for_missing_mlflow_run(store, diagnostics_report):
    root, _, _, run, model = store
    path = root / "data/processed/fixture/manifest.json"
    data = {"run_id": "e" * 32, "tracking": {"run_id": run.info.run_id}}
    if diagnostics_report:
        data.update(model_uri=model.model_uri, mlflow_run_id=run.info.run_id)
    path.write_text(json.dumps(data))
    result = inv.inventory(root)
    assert result["unresolved_references"] == 0
    unknown = [r for r in result["references"] if r["kind"] == "unclassified_run_id"]
    assert len(unknown) == 1 and unknown[0]["resolved"] is None
    assert all(r["resolved"] for r in result["references"] if r["kind"] != "unclassified_run_id")


def test_diagnostics_audit_uuid_and_explicit_missing_tracking_still_detected(store):
    root, _, _, _, model = store
    path = root / "data/processed/fixture/manifest.json"
    local_id = "d" * 32
    path.write_text(json.dumps({"run_id": local_id, "model_uri": model.model_uri}))
    audit = root / "data/processed/runs" / f"diagnostics_{local_id}.json"
    audit.parent.mkdir()
    audit.write_bytes(path.read_bytes())
    result = inv.inventory(root)
    assert result["unresolved_references"] == 0
    assert sum(r["kind"] == "unclassified_run_id" for r in result["references"]) == 2
    # Explicit MLflow contexts still fail resolution when IDs are absent.
    audit.write_text(json.dumps({"tracking": {"run_id": "a" * 32}, "mlflow_run_id": "b" * 32}))
    result = inv.inventory(root)
    assert result["unresolved_references"] == 2


def test_hot_journal_and_invalid_database_refused(store):
    root, tracking, *_ = store
    journal = tracking / "mlflow.db-journal"
    journal.write_bytes(b"not-a-safe-rollback-state")
    before = contents(root)
    with pytest.raises(inv.InventoryError, match="journal"):
        inv.inventory(root)
    assert before == contents(root)
    journal.unlink()
    (tracking / "mlflow.db").write_bytes(b"invalid SQLite")
    with pytest.raises(sqlite3.DatabaseError):
        inv.inventory(root)


def test_cli_private_report_only_and_archive_copies_not_scanned(store, capsys):
    root, *_ = store
    archived = root / "data/storage/restored/old/invalid.json"
    archived.parent.mkdir(parents=True)
    archived.write_text("not-json")
    before = contents(root)
    assert inv.main(["--repo", str(root)]) == 0
    summary = json.loads(capsys.readouterr().out)
    report = Path(summary["report"])
    assert report.stat().st_mode & 0o777 == 0o600
    result = json.loads(report.read_text())
    assert result["reference_json_files"] == 1
    assert summary["runs"] == 2 and summary["logged_models"] == 1
    assert not summary["migration_implemented"]
    after = contents(root)
    assert set(after) - set(before) == {report.relative_to(root).as_posix()}
    assert all(before[k] == after[k] for k in before)


def test_unknown_schema_and_corrupt_manifest_are_explicit(store):
    root, tracking, *_ = store
    path = root / "data/processed/fixture/manifest.json"
    path.write_text("invalid json")
    with pytest.raises(inv.InventoryError, match="Invalid project JSON"):
        inv.inventory(root)
    path.write_text("{}")
    with sqlite3.connect(tracking / "mlflow.db") as connection:
        connection.execute("ALTER TABLE runs RENAME COLUMN artifact_uri TO moved_uri")
    with pytest.raises(inv.InventoryError, match="Unsupported MLflow columns"):
        inv.inventory(root)
