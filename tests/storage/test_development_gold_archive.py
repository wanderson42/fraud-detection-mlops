"""Development dataset recovery without its upstream data, Optuna database or models."""

import hashlib
import json
from pathlib import Path
import pickle
import shutil
import sys

import pandas as pd
import pyarrow.parquet as pq
import pytest
from scripts.storage import archive
from scripts.storage import bronze_s3_worker as transport
from scripts.storage import development_gold_archive as storage
from tests.storage.test_bronze_transfer import MemoryClient

from fraud_detection_mlops.modeling.experiments import temporal_development_data as development


@pytest.fixture(scope="module")
def built_development(silver_fixture, tmp_path_factory):
    root = tmp_path_factory.mktemp("archive-development-source")
    (root / "references").mkdir()
    shutil.copy2(silver_fixture / "inventory.json", root / archive.INVENTORY_PATH)
    shutil.copy2(development.DEFAULT_POLICY, root / archive.DEVELOPMENT_POLICY_PATH)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            development,
            "committed_inputs",
            lambda p: {"git_revision": "test", "files": {"test": "d" * 64}},
        )
        result = development.prepare_data(
            silver_fixture / "silver",
            root / "data/processed/handbook",
            policy_path=root / archive.DEVELOPMENT_POLICY_PATH,
            inventory_path=root / archive.INVENTORY_PATH,
        )
    return root, Path(result["development_path"]).relative_to(root)


@pytest.fixture
def source(built_development, tmp_path, monkeypatch):
    built, relative = built_development
    for directory in ["data", "references"]:
        shutil.copytree(built / directory, tmp_path / directory)
    (tmp_path / "pyproject.toml").touch()
    snapshot = tmp_path / relative
    study = snapshot / "study"
    study.mkdir()
    (study / "study.db").write_bytes(b"UNREADABLE STUDY SENTINEL")
    (study / ".writer.lock").touch()
    (study / "model.skops").write_bytes(b"UNREADABLE MODEL SENTINEL")
    original_open = Path.open

    def guarded_open(path, *args, **kwargs):
        if path.is_relative_to(study):
            pytest.fail("Study database, lock or models opened")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    monkeypatch.setattr(pickle, "load", lambda *a, **k: pytest.fail("Pickle loaded"))
    monkeypatch.setattr(pickle, "loads", lambda *a, **k: pytest.fail("Pickle loaded"))
    monkeypatch.setattr(
        development, "compute_features", lambda *a, **k: pytest.fail("Features recomputed")
    )
    return tmp_path, snapshot


def prepared(source):
    root, snapshot = source
    result = storage.prepare_development(root, snapshot.name)
    bundle = Path(result["manifest"]).parent
    document = json.loads((bundle / "manifest.json").read_bytes())
    return root, snapshot, result, bundle, document


def job(client):
    return lambda root, command, identity, *, schema: (
        transport.publish if command == "publish" else transport.download
    )(archive.workspace(root), identity, client, schema)


def test_prepare_keeps_identity_policy_bytes_and_excludes_study(source):
    root, snapshot = source
    original = (snapshot / "manifest.json").read_bytes()
    policy = (root / archive.DEVELOPMENT_POLICY_PATH).read_bytes()
    result = storage.prepare_development(root, snapshot.name)
    assert result["verified_partitions"] == 63 and result["rows"] == 189
    assert result["feature_count"] == 19 and result["archive_files"] == 66
    assert result["study_included"] is False and result["study_read"] is False
    assert result["s3_accessed"] is False and result["features_recomputed"] is False
    assert result["confirmation_evaluated"] is False
    bundle = Path(result["manifest"]).parent
    assert (bundle / snapshot.relative_to(root) / "manifest.json").read_bytes() == original
    assert (bundle / archive.DEVELOPMENT_POLICY_PATH).read_bytes() == policy
    assert not list(bundle.rglob("study"))
    assert result["development_manifest_sha256"] == hashlib.sha256(original).hexdigest()
    assert storage.prepare_development(root, snapshot.name)["bundle_id"] == result["bundle_id"]
    shutil.rmtree(snapshot.parent)
    shutil.rmtree(root / "references")
    assert storage.verify_bundle(root, result["bundle_id"])["rows"] == 189


@pytest.mark.parametrize(
    "problem", ["missing", "corrupt", "symlink", "extra", "database", "policy", "run_id"]
)
def test_invalid_source_never_publishes_a_local_bundle(source, problem):
    root, snapshot = source
    partition = next(snapshot.glob("transactions/*.parquet"))
    if problem == "missing":
        partition.unlink()
    elif problem == "corrupt":
        partition.write_bytes(b"broken")
    elif problem == "symlink":
        partition.unlink()
        partition.symlink_to(root / archive.DEVELOPMENT_POLICY_PATH)
    elif problem == "extra":
        (snapshot / "unknown.json").write_text("{}")
    elif problem == "database":
        (snapshot / "transactions/live.db").touch()
    elif problem == "policy":
        (root / archive.DEVELOPMENT_POLICY_PATH).write_text("{}")
    else:
        path = snapshot / "manifest.json"
        data = json.loads(path.read_bytes())
        data["run_id"] = "b" * 64
        path.write_bytes(archive.encoded(data))
    with pytest.raises((ValueError, KeyError, OSError)):
        storage.prepare_development(root, snapshot.name)
    assert not list((root / "data/storage").glob("bundles/*"))


def test_staging_detects_dataset_file_set_changes(source, monkeypatch):
    root, snapshot = source
    original = storage.selections
    calls = 0

    def changed(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            (snapshot / "transactions/extra.parquet").write_bytes(b"new")
        return original(*args)

    monkeypatch.setattr(storage, "selections", changed)
    with pytest.raises(ValueError, match="changed during staging"):
        storage.prepare_development(root, snapshot.name)
    assert not list((root / "data/storage").glob("bundles/*"))


def test_memory_s3_roundtrip_after_removing_original_policy_study_and_local_bundle(source):
    root, snapshot, result, bundle, document = prepared(source)
    client = MemoryClient()
    first = storage.publish(root, result["bundle_id"], worker=job(client))
    assert first["uploaded_objects"] == first["verified_objects"] == 67
    assert (
        client.puts[-1]
        == transport.prefix(result["bundle_id"], archive.DEVELOPMENT_SCHEMA) + "manifest.json"
    )
    again = storage.publish(root, result["bundle_id"], worker=job(client))
    assert again["uploaded_objects"] == 0 and again["reused_objects"] == 67
    shutil.rmtree(root / "references")
    shutil.rmtree(snapshot.parent.parent)
    shutil.rmtree(bundle)
    result = storage.restore(root, result["bundle_id"], worker=job(client))
    assert result["status"] == "restored_verified" and result["complete"]
    assert result["files"] == 66 and result["rows"] == 189 and result["verified_partitions"] == 63
    assert result["policy_included"] and result["inventory_included"]
    assert (
        not result["study_included"]
        and not result["study_read"]
        and not result["primary_storage_migrated"]
    )
    assert storage.verify_directory(Path(result["restored_directory"]), document)["rows"] == 189
    with pytest.raises(ValueError, match="destination exists"):
        storage.restore(
            root, result["bundle_id"], worker=lambda *a, **k: pytest.fail("Unexpected job")
        )


@pytest.mark.parametrize(
    "problem", ["schema", "date", "feature_values", "duplicate_ids", "rows", "policy_hash"]
)
def test_semantics_fail_even_with_refreshed_archive_and_parquet_hashes(source, problem):
    root, snapshot, _, bundle, document = prepared(source)
    snapshot = bundle / snapshot.relative_to(root)
    manifest_path = snapshot / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    record = manifest["files"][0]
    path = snapshot / record["path"]
    if problem in {"schema", "date", "feature_values", "duplicate_ids"}:
        frame = pq.ParquetFile(path).read().to_pandas()
        if problem == "schema":
            frame = frame.drop(columns="TX_AMOUNT")
        elif problem == "date":
            frame["TX_DATETIME"] += pd.Timedelta(days=1)
        elif problem == "feature_values":
            frame["TERMINAL_KNOWN_FRAUD_RATE_1D"] = -1.0
        else:
            frame["TRANSACTION_ID"] = [1, 1, 1]
        frame.to_parquet(path, index=False)
        record.update(size_bytes=path.stat().st_size, sha256=archive.digest(path))
    elif problem == "rows":
        record["rows"] += 1
    else:
        manifest["policy_sha256"] = "f" * 64
    manifest_path.write_bytes(archive.encoded(manifest))
    for row in document["files"]:
        file = bundle / row["path"]
        row.update(bytes=file.stat().st_size, sha256=archive.digest(file))
    document["provenance"]["development_manifest_sha256"] = archive.digest(manifest_path)
    archive.validate_manifest(document)
    with pytest.raises(ValueError):
        storage.verify_directory(bundle, document)


def test_profile_rejects_study_and_keeps_old_reference_allowlists(source):
    root, snapshot, result, _, document = prepared(source)
    for schema in [
        archive.SCHEMA,
        archive.BRONZE_SCHEMA,
        archive.SILVER_SCHEMA,
        archive.GOLD_SCHEMA,
    ]:
        with pytest.raises(ValueError):
            archive.profile_path(archive.DEVELOPMENT_POLICY_PATH, schema)
    assert transport.prefix(result["bundle_id"], archive.DEVELOPMENT_SCHEMA).startswith(
        "development-gold/v1/"
    )
    with pytest.raises(ValueError):
        transport.publish(
            archive.workspace(root), result["bundle_id"], object(), archive.GOLD_SCHEMA
        )
    extra = {
        "path": (snapshot / "study/study.db").relative_to(root).as_posix(),
        "bytes": 1,
        "sha256": "a" * 64,
    }
    document["files"] = sorted([*document["files"], extra], key=lambda row: row["path"])
    with pytest.raises(ValueError, match="unrelated path or study"):
        archive.validate_manifest(document)


def test_partial_publish_resume_and_semantic_failure_quarantine(source, monkeypatch):
    root, _, result, _, _ = prepared(source)
    identity = result["bundle_id"]
    client = MemoryClient()
    client.fail_at = 2
    with pytest.raises(OSError):
        storage.publish(root, identity, worker=job(client))
    assert (
        transport.prefix(identity, archive.DEVELOPMENT_SCHEMA) + "manifest.json"
        not in client.objects
    )
    client.fail_at = None
    assert storage.publish(root, identity, worker=job(client))["reused_objects"] == 2

    def reject(*args):
        raise ValueError("injected semantic failure")

    monkeypatch.setattr(storage, "verify_directory", reject)
    with pytest.raises(ValueError, match="semantic failure"):
        storage.restore(root, identity, worker=job(client))
    assert not (archive.workspace(root) / "restored" / identity).exists()
    assert len(list((archive.workspace(root) / "downloads").glob("development-gold-*"))) == 1


def test_cli_requires_explicit_dataset_identity_and_preserves_receipt(source, monkeypatch, capsys):
    root, snapshot = source
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "development_gold_archive",
            "--project-root",
            str(root),
            "prepare",
            "--run-id",
            snapshot.name,
        ],
    )
    storage.main()
    result = json.loads(capsys.readouterr().out)
    assert result["archive_files"] == 66 and Path(result["report"]).is_file()
    for command in ["prepare", "restore"]:
        monkeypatch.setattr(
            sys, "argv", ["development_gold_archive", "--project-root", str(root), command]
        )
        with pytest.raises(SystemExit) as exc:
            storage.main()
        assert exc.value.code == 2
