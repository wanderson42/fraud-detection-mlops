"""Gold recovery reuses its frozen semantic verifier and the shared dataset transport."""

import json
from pathlib import Path
import pickle
import shutil
import sys

import pandas as pd
import pyarrow.parquet as pq
import pytest
from scripts.storage import archive, gold_archive, silver_archive
from scripts.storage import bronze_s3_worker as transport
from tests.data.datasets.test_gold_dataset import prepared as prepared_gold_source  # noqa: F401
from tests.storage.test_bronze_transfer import MemoryClient

from fraud_detection_mlops.data.contracts.dataset_errors import GoldError
from fraud_detection_mlops.data.datasets import gold_dataset as gold
from fraud_detection_mlops.data.ingestion import handbook_bronze as bronze

REFERENCES = (
    archive.INVENTORY_PATH,
    archive.SILVER_CONTRACT_PATH,
    archive.GOLD_CONTRACT_PATH,
    archive.TEMPORAL_PROTOCOL_PATH,
)


@pytest.fixture(scope="module")
def built_gold(prepared_gold_source, tmp_path_factory):  # noqa: F811 - shared fixture
    original, protocol = prepared_gold_source
    root = tmp_path_factory.mktemp("archive-gold-source")
    (root / "references").mkdir()
    shutil.copy2(original / "inventory.json", root / archive.INVENTORY_PATH)
    shutil.copy2(gold.DEFAULT_CONTRACT, root / archive.SILVER_CONTRACT_PATH)
    shutil.copy2(gold.DEFAULT_GOLD_CONTRACT, root / archive.GOLD_CONTRACT_PATH)
    (root / archive.TEMPORAL_PROTOCOL_PATH).write_text(json.dumps(protocol))
    gold.build_gold(
        original / "silver",
        root / "data/processed/handbook",
        inventory_path=root / archive.INVENTORY_PATH,
        silver_contract=root / archive.SILVER_CONTRACT_PATH,
        protocol_path=root / archive.TEMPORAL_PROTOCOL_PATH,
        gold_contract=root / archive.GOLD_CONTRACT_PATH,
    )
    return root


@pytest.fixture
def source(built_gold, tmp_path, monkeypatch):
    for directory in ["data", "references"]:
        shutil.copytree(built_gold / directory, tmp_path / directory)
    (tmp_path / "pyproject.toml").touch()
    snapshot = tmp_path / "data/processed/handbook" / ("a" * 40) / "gold_v1"
    (snapshot.parent / "runs" / ("gold_" + "b" * 32 + ".json")).write_text(
        '{"status":"historical fixture"}\n'
    )
    (snapshot.parent / "development_gold_v2").mkdir()
    (snapshot.parent / "development_gold_v2/study.db").write_bytes(b"NOT THIS DATASET")
    (snapshot.parent / "runs" / ("baseline_" + "c" * 32 + ".json")).write_bytes(b"OTHER AUDIT")
    monkeypatch.setattr(bronze, "urlopen", lambda *a, **k: pytest.fail("Network accessed"))
    monkeypatch.setattr(pickle, "load", lambda *a, **k: pytest.fail("Pickle deserialized"))
    monkeypatch.setattr(pickle, "loads", lambda *a, **k: pytest.fail("Pickle deserialized"))
    monkeypatch.setattr(
        gold, "compute_features", lambda *a, **k: pytest.fail("Features recomputed")
    )
    return tmp_path, snapshot


def report(source):
    root, _ = source
    result = gold_archive.prepare_gold(root)
    bundle = Path(result["manifest"]).parent
    return root, result, bundle, json.loads((bundle / "manifest.json").read_bytes())


def test_local_prepare_preserves_all_references_audits_and_identity(source):
    root, snapshot = source
    before = {
        p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()
    }
    result = gold_archive.prepare_gold(root)
    assert result["schema"] == archive.GOLD_SCHEMA and result["complete"]
    assert result["verified_partitions"] == 4 and result["feature_count"] == 19
    assert result["rows"] == 12 and result["split_rows"] == {
        "train": 6,
        "validation": 3,
        "test": 3,
    }
    assert result["archive_files"] == 11 and result["included_audits"] == 2
    assert result["temporal_protocol_included"] and result["silver_contract_included"]
    assert result["s3_accessed"] is False and result["features_recomputed"] is False
    bundle = Path(result["manifest"]).parent
    for name in REFERENCES:
        assert (bundle / name).read_bytes() == before[name]
    assert all((root / name).read_bytes() == raw for name, raw in before.items())
    assert not list(bundle.rglob("*.db")) and not list(bundle.rglob("baseline_*.json"))
    assert gold_archive.prepare_gold(root)["bundle_id"] == result["bundle_id"]
    shutil.rmtree(root / "references")
    shutil.rmtree(snapshot.parent)
    assert gold_archive.verify_bundle(root, result["bundle_id"])["rows"] == 12


@pytest.mark.parametrize(
    "problem",
    [
        "missing_partition",
        "corrupt",
        "lock",
        "missing_audit",
        "protocol",
        "gold_contract",
        "database",
        "symlink",
    ],
)
def test_prepare_rejects_invalid_or_active_source(source, problem):
    root, snapshot = source
    path = next(snapshot.rglob("*.parquet"))
    if problem == "missing_partition":
        path.unlink()
    elif problem == "corrupt":
        path.write_bytes(b"corrupt")
    elif problem == "lock":
        (snapshot.parent / ".gold_v1.lock").touch()
    elif problem == "missing_audit":
        run = json.loads((snapshot / "manifest.json").read_bytes())["run_id"]
        (snapshot.parent / "runs" / f"gold_{run}.json").unlink()
    elif problem in {"protocol", "gold_contract"}:
        relative = (
            archive.TEMPORAL_PROTOCOL_PATH if problem == "protocol" else archive.GOLD_CONTRACT_PATH
        )
        (root / relative).write_text("{}")
    elif problem == "database":
        (snapshot / "live.db").touch()
    else:
        path.unlink()
        path.symlink_to(root / archive.INVENTORY_PATH)
    with pytest.raises((ValueError, GoldError)):
        gold_archive.prepare_gold(root)
    assert not list((root / "data/storage").glob("bundles/*"))


def test_source_audit_mutation_aborts_staging(source, monkeypatch):
    root, snapshot = source
    original = gold_archive.selections
    calls = 0

    def changed(root):
        nonlocal calls
        calls += 1
        if calls == 2:
            (snapshot.parent / "runs" / ("gold_" + "c" * 32 + ".json")).write_text("{}")
        return original(root)

    monkeypatch.setattr(gold_archive, "selections", changed)
    with pytest.raises(ValueError, match="source changed"):
        gold_archive.prepare_gold(root)
    assert not list((root / "data/storage").glob("bundles/*"))


def test_roundtrip_without_gold_silver_bronze_references_or_prepared_bundle(source):
    root, result, bundle, document = report(source)
    client = MemoryClient()
    calls = []
    identity = result["bundle_id"]

    def job(root, command, identity, *, schema):
        calls.append((command, schema))
        return (transport.publish if command == "publish" else transport.download)(
            archive.workspace(root), identity, client, schema
        )

    first = gold_archive.publish(root, identity, worker=job)
    assert first["uploaded_objects"] == 12 and first["verified_objects"] == 12
    assert client.puts[-1] == transport.prefix(identity, archive.GOLD_SCHEMA) + "manifest.json"
    again = gold_archive.publish(root, identity, worker=job)
    assert again["uploaded_objects"] == 0 and again["reused_objects"] == 12
    shutil.rmtree(root / "references")
    shutil.rmtree(root / "data/processed")
    shutil.rmtree(bundle)
    restored = gold_archive.restore(root, identity, worker=job)
    assert calls == [
        ("publish", archive.GOLD_SCHEMA),
        ("publish", archive.GOLD_SCHEMA),
        ("download", archive.GOLD_SCHEMA),
    ]
    assert restored["status"] == "restored_verified" and restored["complete"]
    assert (
        restored["verified_partitions"] == 4
        and restored["rows"] == 12
        and restored["feature_count"] == 19
    )
    assert restored["split_rows"] == result["split_rows"] and restored["files"] == 11
    assert restored["original_source_read"] is False and restored["features_recomputed"] is False
    assert restored["primary_storage_migrated"] is False
    assert (
        gold_archive.verify_directory(Path(restored["restored_directory"]), document)["rows"] == 12
    )
    with pytest.raises(ValueError, match="destination exists"):
        gold_archive.restore(root, identity, worker=lambda *a, **k: pytest.fail("Unexpected job"))


@pytest.mark.parametrize(
    "problem",
    ["schema", "date", "feature_values", "duplicate_ids", "split_counts", "protocol_identity"],
)
def test_semantics_rejected_after_archive_and_parquet_hashes_are_refreshed(source, problem):
    root, result, bundle, document = report(source)
    snapshot = bundle / Path(result["source_snapshot"]).relative_to(root)
    manifest_path = snapshot / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    record = manifest["files"][0]
    path = snapshot / record["path"]
    if problem in {"schema", "date", "feature_values", "duplicate_ids"}:
        frame = pq.ParquetFile(path).read().to_pandas()
        if problem == "schema":
            frame = frame.drop(columns="TX_AMOUNT")
        elif problem == "date":
            frame["TX_DATETIME"] = frame["TX_DATETIME"] + pd.Timedelta(days=1)
        elif problem == "feature_values":
            frame["TERMINAL_KNOWN_FRAUD_RATE_1D"] = -1.0
        else:
            frame["TRANSACTION_ID"] = [1, 1, 1]
        frame.to_parquet(path, index=False)
        record.update(size_bytes=path.stat().st_size, sha256=archive.digest(path))
    elif problem == "split_counts":
        manifest["split_rows"]["train"] += 1
    else:
        manifest["protocol"]["label_delay_days"] = 0
    manifest_path.write_bytes(archive.encoded(manifest))
    for row in document["files"]:
        file = bundle / row["path"]
        row.update(bytes=file.stat().st_size, sha256=archive.digest(file))
    document["provenance"]["gold_manifest_sha256"] = archive.digest(manifest_path)
    archive.validate_manifest(document)
    with pytest.raises(GoldError):
        gold_archive.verify_directory(bundle, document)


def test_profile_and_reference_boundaries_preserve_other_datasets(source):
    root, result, bundle, document = report(source)
    with pytest.raises(ValueError, match="Bronze"):
        transport.publish(archive.workspace(root), result["bundle_id"], object())
    with pytest.raises(ValueError):
        silver_archive.verify_directory(bundle, document)
    for path in REFERENCES[2:]:
        for schema in [archive.SCHEMA, archive.BRONZE_SCHEMA, archive.SILVER_SCHEMA]:
            with pytest.raises(ValueError):
                archive.profile_path(path, schema)
    extra = dict(
        document["files"][0],
        path=f"data/processed/handbook/{'a' * 40}/development_gold_v2/unrelated.json",
    )
    document["files"] = sorted([*document["files"], extra], key=lambda r: r["path"])
    with pytest.raises(ValueError, match="unrelated"):
        archive.validate_manifest(document)


def test_partial_publication_reuses_verified_payloads_and_quarantines_semantic_failure(
    source, monkeypatch
):
    root, result, _, _ = report(source)
    work = archive.workspace(root)
    identity = result["bundle_id"]
    client = MemoryClient()
    client.fail_at = 2
    with pytest.raises(OSError):
        transport.publish(work, identity, client, archive.GOLD_SCHEMA)
    assert transport.prefix(identity, archive.GOLD_SCHEMA) + "manifest.json" not in client.objects
    client.fail_at = None
    assert transport.publish(work, identity, client, archive.GOLD_SCHEMA)["reused_objects"] == 2

    def reject(*args):
        raise GoldError("injected semantic failure")

    monkeypatch.setattr(gold_archive, "verify_directory", reject)
    with pytest.raises(GoldError, match="semantic failure"):
        gold_archive.restore(
            root,
            identity,
            worker=lambda root, command, identity, *, schema: transport.download(
                work, identity, client, schema
            ),
        )
    assert (
        not (work / "restored" / identity).exists()
        and len(list((work / "downloads").glob("gold-*"))) == 1
    )


def test_cli_prepare_reports_counts_and_missing_bundle_id(source, monkeypatch, capsys):
    root, _ = source
    monkeypatch.setattr(sys, "argv", ["gold_archive", "--project-root", str(root), "prepare"])
    gold_archive.main()
    result = json.loads(capsys.readouterr().out)
    assert (
        result["status"] == "prepared_verified_locally"
        and result["feature_count"] == 19
        and Path(result["report"]).is_file()
    )
    monkeypatch.setattr(sys, "argv", ["gold_archive", "--project-root", str(root), "restore"])
    with pytest.raises(SystemExit) as error:
        gold_archive.main()
    assert error.value.code == 2
