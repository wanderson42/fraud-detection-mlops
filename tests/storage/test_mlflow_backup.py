"""Quiesced tracking capture, complete local unit, and verification without originals."""

from contextlib import closing
import json
from pathlib import Path
import shutil
import socket
import sqlite3

from mlflow import MlflowClient
import pytest
from scripts.storage import archive
from scripts.storage import mlflow_backup as backup
from tests.storage.test_mlflow_inventory import contents


@pytest.fixture
def tracking_store(store):
    root, tracking, client, run, model = store
    # SDK's default location follows cwd; native project uses its own checkout.
    with sqlite3.connect(tracking / "mlflow.db") as connection:
        connection.execute(
            "UPDATE experiments SET artifact_location=? WHERE experiment_id=0",
            ((root / "mlruns/0").as_posix(),),
        )
        for (uri,) in connection.execute("SELECT artifact_uri FROM runs"):
            Path(uri.removeprefix("file://")).mkdir(parents=True, exist_ok=True)
    (tracking / "notes.json").write_text('{"scope":"ancillary tracking metadata"}\n')
    (tracking / "artifacts/empty-directory").mkdir()
    return root, tracking, client, run, model


def source_contents(root):
    return {
        name: digest
        for name, digest in contents(root).items()
        if not name.startswith("data/storage/")
    }


def prepare(root):
    return backup.prepare(root, writers_stopped=True)


def test_complete_unit_preserves_source_ids_and_reuses_snapshot(tracking_store, monkeypatch):
    root, tracking, _, run, model = tracking_store
    before = source_contents(root)
    connect = backup.sqlite3.connect

    def guarded(path, *args, **kwargs):
        assert str(tracking) not in str(path), "Source database connection forbidden"
        return connect(path, *args, **kwargs)

    monkeypatch.setattr(backup.sqlite3, "connect", guarded)
    monkeypatch.setattr(socket.socket, "connect", lambda *args: pytest.fail("network used"))
    result = prepare(root)
    assert result["status"] == "prepared_verified_locally"
    assert result["archive_files"] == 6  # 2 artifacts + note + normalized DB + 2 metadata files.
    assert result["artifact_files"] == 2 and result["ancillary_files"] == 1
    assert result["experiments"] == 2 and result["runs"] == 2 and result["logged_models"] == 1
    assert (
        result["metadata_rows"]["metrics"] == 2 and result["metadata_rows"]["latest_metrics"] == 1
    )
    assert result["default_reviews"] == 1
    assert result["sqlite_online_backup"] and not result["shared_writer_lock_acquired"]
    assert result["original_source_read"] and not result["source_database_connection_opened"]
    target = Path(result["manifest"]).parent
    assert (target / "data/tracking/artifacts/empty-directory").is_dir()
    with closing(
        connect((target / backup.DATABASE).as_uri() + "?mode=ro", uri=True)
    ) as connection:
        assert (
            connection.execute("SELECT run_uuid FROM runs WHERE status='FINISHED'").fetchone()[0]
            == run.info.run_id
        )
        assert (
            connection.execute("SELECT model_id FROM logged_models").fetchone()[0]
            == model.model_id
        )
    repeated = prepare(root)
    assert repeated["bundle_id"] == result["bundle_id"]
    assert repeated["status"] == "reused_verified_locally"
    assert source_contents(root) == before
    assert not list((root / "data/storage").glob("mlflow-backup-*"))
    assert not list((root / "data/storage").glob("fraud-mlflow-inventory-*"))


def test_verify_after_source_and_project_references_removed(tracking_store):
    root, tracking, *_ = tracking_store
    result = prepare(root)
    shutil.rmtree(tracking)
    shutil.rmtree(root / "data/processed")
    verified = backup.verify(root, result["bundle_id"])
    assert verified["status"] == "verified_locally"
    assert verified["sqlite_integrity_verified"] and verified["logical_table_contents_verified"]
    assert not verified["original_source_read"] and not verified["models_loaded"]
    assert not verified["mlflow_initialized"] and not verified["s3_accessed"]


def test_committed_wal_is_normalized_and_original_wal_unchanged(tracking_store):
    root, tracking, *_ = tracking_store
    with closing(sqlite3.connect(tracking / "mlflow.db")) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("CREATE TABLE wal_proof(value TEXT)")
        writer.execute("INSERT INTO wal_proof VALUES ('committed')")
        writer.commit()
        before = source_contents(root)
        result = prepare(root)
        assert source_contents(root) == before
        target = Path(result["manifest"]).parent
        with closing(
            sqlite3.connect((target / backup.DATABASE).as_uri() + "?mode=ro", uri=True)
        ) as connection:
            assert connection.execute("SELECT value FROM wal_proof").fetchone() == ("committed",)
            assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert not list((target / "data/tracking").glob("*-wal"))
        assert not list((target / "data/tracking").glob("*-shm"))


@pytest.mark.parametrize(
    "issue",
    [
        "default_exists",
        "default_owned",
        "outside_uri",
        "active_run",
        "pending_model",
        "other_database",
        "hidden_sqlite",
        "metadata_collision",
        "unresolved_reference",
    ],
)
def test_unreviewed_or_unstable_tracking_refused(tracking_store, issue):
    root, tracking, client, run, model = tracking_store
    if issue == "default_exists":
        (root / "mlruns/0").mkdir(parents=True)
    elif issue == "default_owned":
        client.create_run("0")
    elif issue == "outside_uri":
        with sqlite3.connect(tracking / "mlflow.db") as connection:
            connection.execute("UPDATE runs SET artifact_uri='s3://uncovered/path'")
    elif issue == "active_run":
        client.create_run(run.info.experiment_id)
    elif issue == "pending_model":
        client.create_logged_model(run.info.experiment_id)
    elif issue == "other_database":
        (tracking / "unknown.sqlite").write_bytes(b"opaque")
    elif issue == "hidden_sqlite":
        (tracking / "unknown.bin").write_bytes(b"SQLite format 3\x00" + b"opaque")
    elif issue == "metadata_collision":
        (root / backup.CAPTURE).write_text("{}")
    else:
        (root / "data/processed/fixture/manifest.json").write_text(
            json.dumps({"mlflow_run_id": "a" * 32, "model_uri": model.model_uri})
        )
    before = source_contents(root)
    with pytest.raises(ValueError):
        prepare(root)
    assert source_contents(root) == before
    assert not list((root / "data/storage/bundles").glob("*/manifest.json"))
    assert not list((root / "data/storage").glob("mlflow-backup-*"))


@pytest.mark.parametrize("kind", ["artifact", "database", "new_file", "reference", "default"])
def test_mutation_during_capture_is_not_published(tracking_store, monkeypatch, kind):
    root, tracking, *_ = tracking_store
    original = backup.copy_file
    mutated = False

    def mutate(source, target, remaining, **kwargs):
        nonlocal mutated
        result = original(source, target, remaining, **kwargs)
        if not mutated and not kwargs.get("database"):
            mutated = True
            if kind == "artifact":
                next((tracking / "artifacts").rglob("pipeline.skops")).write_bytes(b"changed")
            elif kind == "database":
                with sqlite3.connect(tracking / "mlflow.db") as connection:
                    connection.execute("CREATE TABLE concurrent_write(value TEXT)")
            elif kind == "new_file":
                (tracking / "new.json").write_text("{}")
            elif kind == "reference":
                (root / "data/processed/fixture/manifest.json").write_text("{}")
            else:
                (root / "mlruns/0").mkdir(parents=True)
        return result

    monkeypatch.setattr(backup, "copy_file", mutate)
    with pytest.raises(ValueError, match="changed|inventory|integrity|Default"):
        prepare(root)
    assert not list((root / "data/storage/bundles").glob("*/manifest.json"))


@pytest.mark.parametrize("budget", ["files", "bytes", "disk"])
def test_preflight_budget_does_not_modify_originals(tracking_store, monkeypatch, budget):
    root, *_ = tracking_store
    if budget == "files":
        monkeypatch.setattr(backup, "MAX_FILES", 3)
    elif budget == "bytes":
        monkeypatch.setattr(backup, "MAX_BYTES", 16)
    else:
        monkeypatch.setattr(backup.shutil, "disk_usage", lambda p: type("Disk", (), {"free": 0})())
    before = source_contents(root)
    with pytest.raises(ValueError):
        prepare(root)
    assert source_contents(root) == before


def rehash(root, result):
    target = Path(result["manifest"]).parent
    document = json.loads((target / "manifest.json").read_bytes())
    for row in document["files"]:
        path = target / row["path"]
        row.update(bytes=path.stat().st_size, sha256=archive.digest(path))
    keys = {
        backup.DATABASE: "database_sha256",
        backup.INVENTORY: "inventory_sha256",
        backup.CAPTURE: "capture_sha256",
    }
    if backup.AUXILIARY + "/mlflow.db" in {r["path"] for r in document["files"]}:
        keys.update(
            {
                backup.AUXILIARY + "/mlflow.db": "interface_smoke_database_sha256",
                backup.AUXILIARY + "/storage-inventory.json": "interface_smoke_inventory_sha256",
            }
        )
    document["provenance"] = {
        keys[r["path"]]: r["sha256"] for r in document["files"] if r["path"] in keys
    }
    identity = backup.manifest_id(document)
    (target / "manifest.json").write_bytes(archive.encoded(document))
    final = root / "data/storage/bundles" / identity
    target.rename(final)
    return identity


def test_semantic_corruption_rejected_even_after_file_hashes_refreshed(tracking_store):
    root, *_ = tracking_store
    result = prepare(root)
    db = Path(result["manifest"]).parent / backup.DATABASE
    with sqlite3.connect(db) as connection:
        connection.execute("UPDATE params SET value='tampered' WHERE key='private'")
    identity = rehash(root, result)
    with pytest.raises(ValueError, match="logical content"):
        backup.verify(root, identity)


@pytest.mark.parametrize(
    "corrupt", ["unexpected", "file", "symlink", "manifest_link", "directory"]
)
def test_corrupt_payload_and_existing_invalid_bundle_never_overwritten(
    tracking_store, corrupt, tmp_path
):
    root, *_ = tracking_store
    result = prepare(root)
    target = Path(result["manifest"]).parent
    if corrupt == "unexpected":
        (target / "extra").write_bytes(b"extra")
    elif corrupt == "file":
        next((target / "data/tracking/artifacts").rglob("pipeline.skops")).write_bytes(b"corrupt")
    elif corrupt == "symlink":
        path = target / backup.CAPTURE
        path.unlink()
        path.symlink_to(tmp_path / "outside")
    elif corrupt == "manifest_link":
        path = target / "manifest.json"
        saved = tmp_path / "outside.json"
        path.rename(saved)
        path.symlink_to(saved)
    else:
        (target / "data/tracking/extra-empty").mkdir()
    with pytest.raises((ValueError, OSError)):
        backup.verify(root, result["bundle_id"])
    with pytest.raises((ValueError, OSError)):
        prepare(root)


def test_cli_requires_writer_declaration_and_emits_managed_receipt(tracking_store, capsys):
    root, *_ = tracking_store
    with pytest.raises(SystemExit):
        backup.main(["prepare", "--project-root", str(root)])
    capsys.readouterr()
    assert backup.main(["prepare", "--writers-stopped", "--project-root", str(root)]) == 0
    result = json.loads(capsys.readouterr().out)
    receipt = Path(result["report"])
    assert receipt.is_relative_to(root / "data/storage/receipts")
    assert receipt.stat().st_mode & 0o777 == 0o600
    assert (
        backup.main(["verify", "--bundle-id", result["bundle_id"], "--project-root", str(root)])
        == 0
    )
    assert json.loads(capsys.readouterr().out)["status"] == "verified_locally"


def test_missing_source_never_initializes_tracking(tmp_path):
    with pytest.raises(ValueError):
        prepare(tmp_path)
    assert not list(tmp_path.iterdir())


def test_complete_tracking_unit_can_exceed_old_512_file_profiles(tracking_store):
    root, tracking, *_ = tracking_store
    for number in range(513):
        (tracking / "artifacts" / f"small-{number:04}.txt").write_bytes(b"x")
    result = prepare(root)
    assert result["artifact_files"] == 515 and result["archive_files"] == 519
    assert backup.verify(root, result["bundle_id"])["artifact_files"] == 515


@pytest.mark.parametrize(
    "problem", ["traversal", "absolute", "duplicate", "bool_size", "hash", "schema", "provenance"]
)
def test_unsafe_manifests_fail_before_any_file_access(tracking_store, problem):
    root, *_ = tracking_store
    result = prepare(root)
    document = json.loads(Path(result["manifest"]).read_bytes())
    if problem == "traversal":
        document["files"][0]["path"] = "data/tracking/../../escape"
    elif problem == "absolute":
        document["files"][0]["path"] = "/tmp/escape"
    elif problem == "duplicate":
        document["files"].append(document["files"][0])
    elif problem == "bool_size":
        document["files"][0]["bytes"] = True
    elif problem == "hash":
        document["files"][0]["sha256"] = "invalid"
    elif problem == "schema":
        document["schema"] = archive.SCHEMA
    else:
        document["provenance"]["database_sha256"] = "0" * 64
    with pytest.raises(ValueError):
        backup.manifest_id(document)


def test_unmapped_artifact_tables_and_registry_versions_need_coverage(tracking_store):
    root, tracking, client, run, model = tracking_store
    with sqlite3.connect(tracking / "mlflow.db") as connection:
        connection.execute("CREATE TABLE uncovered_artifacts(artifact_uri TEXT)")
        connection.execute("INSERT INTO uncovered_artifacts VALUES ('s3://not-covered/path')")
    with pytest.raises(ValueError, match="Additional artifact metadata"):
        prepare(root)
    with sqlite3.connect(tracking / "mlflow.db") as connection:
        connection.execute("DROP TABLE uncovered_artifacts")
    client.create_registered_model("coverage-fixture")
    client.create_model_version(
        "coverage-fixture", source=model.artifact_location, run_id=run.info.run_id
    )
    with pytest.raises(ValueError, match="model versions"):
        prepare(root)


@pytest.fixture
def two_stores(tracking_store):
    root, *_ = tracking_store
    tracking = root / backup.AUXILIARY
    artifacts = tracking / "artifacts"
    artifacts.mkdir(parents=True)
    client = MlflowClient(tracking_uri="sqlite:///" + str(tracking / "mlflow.db"))
    experiment = client.create_experiment(
        "fraud-model-interface-smoke", artifact_location=artifacts.as_uri()
    )
    run = client.create_run(experiment)
    client.log_metric(run.info.run_id, "smoke_metric", 0.75)
    model = client.create_logged_model(experiment, name="smoke", source_run_id=run.info.run_id)
    model_path = Path(model.artifact_location.removeprefix("file://"))
    model_path.mkdir(parents=True)
    (model_path / "pipeline.skops").write_bytes(b"auxiliary-model-not-loaded")
    run_path = Path(run.info.artifact_uri.removeprefix("file://"))
    run_path.mkdir(parents=True)
    for number in range(5):
        (run_path / f"metadata-{number}.json").write_text("{}")
    client.finalize_logged_model(model.model_id, "READY")
    client.set_terminated(run.info.run_id)
    with sqlite3.connect(tracking / "mlflow.db") as connection:
        connection.execute(
            "UPDATE experiments SET artifact_location=? WHERE experiment_id=0",
            (str(root / "mlruns/0"),),
        )
    reference = root / "data/examples/interface-smoke.json"
    reference.parent.mkdir(parents=True)
    reference.write_text(
        json.dumps({"tracking": {"run_id": run.info.run_id, "model_uri": model.model_uri}})
    )
    (artifacts / "empty").mkdir()
    return root, tracking, client, run, model


def test_two_stores_preserve_ids_artifacts_references_and_reuse_without_source_connections(
    two_stores, monkeypatch
):
    root, _tracking, _, run, model = two_stores
    before = source_contents(root)
    connect = sqlite3.connect

    def guarded(path, *args, **kwargs):
        assert str(root / backup.TRACKING) not in str(path)
        return connect(path, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", guarded)
    monkeypatch.setattr(socket.socket, "connect", lambda *args: pytest.fail("network used"))
    result = prepare(root)
    assert result["store_count"] == 2
    assert result["total_runs"] == 3 and result["total_logged_models"] == 2
    assert result["runs"] == 2 and result["logged_models"] == 1  # Primary stays distinct.
    assert result["artifact_files"] == 8 and result["default_reviews"] == 2
    auxiliary = result["stores"][1]
    assert auxiliary["tracking_root"] == backup.AUXILIARY
    assert auxiliary["runs"] == 1 and auxiliary["logged_models"] == 1
    assert auxiliary["artifact_files"] == 6
    target = Path(result["manifest"]).parent
    inventory = json.loads((target / backup.INVENTORY).read_bytes())
    assert inventory["unresolved_references"] == 0
    assert any(
        r.get("value") == run.info.run_id and r.get("resolved") is True
        for r in inventory["references"]
    )
    assert (target / backup.AUXILIARY / "artifacts/empty").is_dir()
    with closing(
        connect((target / backup.AUXILIARY / "mlflow.db").as_uri() + "?mode=ro", uri=True)
    ) as connection:
        assert connection.execute("SELECT run_uuid FROM runs").fetchone() == (run.info.run_id,)
        assert connection.execute("SELECT model_id FROM logged_models").fetchone() == (
            model.model_id,
        )
    assert prepare(root)["bundle_id"] == result["bundle_id"]
    assert source_contents(root) == before
    shutil.rmtree(root / backup.TRACKING)
    shutil.rmtree(root / "data/examples")
    shutil.rmtree(root / "data/processed")
    verified = backup.verify(root, result["bundle_id"])
    assert verified["store_count"] == 2 and not verified["original_source_read"]
    assert verified["logical_table_contents_verified"] and not verified["models_loaded"]


def test_auxiliary_committed_wal_captured_without_source_mutation(two_stores):
    root, tracking, *_ = two_stores
    with closing(sqlite3.connect(tracking / "mlflow.db")) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("CREATE TABLE auxiliary_wal(value TEXT)")
        writer.execute("INSERT INTO auxiliary_wal VALUES ('committed')")
        writer.commit()
        before = source_contents(root)
        result = prepare(root)
        assert source_contents(root) == before
        target = Path(result["manifest"]).parent / backup.AUXILIARY
        with closing(
            sqlite3.connect((target / "mlflow.db").as_uri() + "?mode=ro", uri=True)
        ) as connection:
            assert connection.execute("SELECT value FROM auxiliary_wal").fetchone() == (
                "committed",
            )
            assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert not list(target.glob("*-wal")) and not list(target.glob("*-shm"))


@pytest.mark.parametrize(
    "issue",
    [
        "active",
        "missing_artifact",
        "outside_uri",
        "unknown_db",
        "metadata_collision",
        "default_owned",
        "duplicate_ids",
    ],
)
def test_auxiliary_coverage_is_mandatory_and_unknown_databases_still_refused(two_stores, issue):
    root, tracking, client, run, _ = two_stores
    if issue == "active":
        client.create_run(run.info.experiment_id)
    elif issue == "missing_artifact":
        shutil.rmtree(Path(run.info.artifact_uri.removeprefix("file://")))
    elif issue == "outside_uri":
        with sqlite3.connect(tracking / "mlflow.db") as connection:
            connection.execute("UPDATE runs SET artifact_uri='s3://uncovered/smoke'")
    elif issue == "unknown_db":
        unknown = root / "data/tracking/unknown/mlflow.db"
        unknown.parent.mkdir()
        unknown.write_bytes(b"not-covered")
    elif issue == "metadata_collision":
        (tracking / "storage-inventory.json").write_text("{}")
    elif issue == "default_owned":
        client.create_run("0")
    else:
        with (
            closing(sqlite3.connect(root / backup.DATABASE)) as source,
            closing(sqlite3.connect(tracking / "mlflow.db")) as target,
        ):
            source.backup(target)
    before = source_contents(root)
    with pytest.raises(ValueError):
        prepare(root)
    assert source_contents(root) == before
    assert not list((root / "data/storage/bundles").glob("*/manifest.json"))


def test_auxiliary_mutation_during_capture_prevents_publication(two_stores, monkeypatch):
    root, tracking, *_ = two_stores
    original = backup.copy_file
    changed = False

    def mutate(source, target, remaining, **kwargs):
        nonlocal changed
        result = original(source, target, remaining, **kwargs)
        if not changed and not kwargs.get("database"):
            changed = True
            with sqlite3.connect(tracking / "mlflow.db") as connection:
                connection.execute("CREATE TABLE concurrent_auxiliary(value TEXT)")
        return result

    monkeypatch.setattr(backup, "copy_file", mutate)
    with pytest.raises(ValueError, match="changed"):
        prepare(root)
    assert not list((root / "data/storage/bundles").glob("*/manifest.json"))


def test_auxiliary_logical_corruption_detected_after_manifest_rehashed(two_stores):
    root, *_ = two_stores
    result = prepare(root)
    target = Path(result["manifest"]).parent
    with sqlite3.connect(target / backup.AUXILIARY / "mlflow.db") as connection:
        connection.execute("UPDATE metrics SET value=99")
    identity = rehash(root, result)
    with pytest.raises(ValueError, match="logical content"):
        backup.verify(root, identity)


def test_aggregate_budget_includes_auxiliary_store(two_stores, monkeypatch):
    root, *_ = two_stores
    files, _ = backup.tree(root)
    total = sum(stamp[2] for stamp in files.values())
    primary = sum(
        stamp[2] for name, stamp in files.items() if not name.startswith(backup.AUXILIARY + "/")
    )
    assert primary < total
    monkeypatch.setattr(backup, "MAX_BYTES", total - 1)
    with pytest.raises(ValueError, match="preflight"):
        prepare(root)
