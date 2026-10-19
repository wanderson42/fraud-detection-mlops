"""Self-contained Silver recovery: byte integrity plus existing schema and SQL gates."""

import json
from pathlib import Path
import pickle
import shutil
import sys

import pyarrow.parquet as pq
import pytest
from scripts.storage import archive, bronze_archive, silver_archive
from scripts.storage import bronze_s3_worker as transport
from tests.data.datasets.test_silver_dataset import source as silver_source  # noqa: F401
from tests.storage.test_bronze_transfer import MemoryClient

from fraud_detection_mlops.data.contracts.dataset_errors import SilverError
from fraud_detection_mlops.data.datasets import silver_dataset as silver
from fraud_detection_mlops.data.ingestion import handbook_bronze as bronze


@pytest.fixture
def source(silver_source, tmp_path, monkeypatch):  # noqa: F811 - shared pytest fixture
    bronze_root, _, inventory = silver_source()
    (tmp_path / "pyproject.toml").touch()
    refs = tmp_path / "references"
    refs.mkdir()
    shutil.copyfile(inventory, tmp_path / archive.INVENTORY_PATH)
    shutil.copyfile(silver.DEFAULT_CONTRACT, tmp_path / archive.SILVER_CONTRACT_PATH)
    silver.build_silver(
        bronze_root,
        tmp_path / "data/interim/handbook",
        inventory_path=tmp_path / archive.INVENTORY_PATH,
        contract_path=tmp_path / archive.SILVER_CONTRACT_PATH,
    )
    # A second audit is outside silver_v1 and must also survive the archive.
    snapshot = tmp_path / "data/interim/handbook" / ("a" * 40) / "silver_v1"
    (snapshot.parent / "runs" / ("silver_" + "b" * 32 + ".json")).write_text(
        '{"status":"historical fixture"}\n'
    )
    (snapshot.parent / "eda_v1").mkdir()
    (snapshot.parent / "eda_v1/reserved.parquet").write_bytes(b"OTHER FAMILY: DO NOT ARCHIVE")
    monkeypatch.setattr(bronze, "urlopen", lambda *a, **k: pytest.fail("Network accessed"))
    monkeypatch.setattr(pickle, "load", lambda *a, **k: pytest.fail("Pickle deserialized"))
    monkeypatch.setattr(pickle, "loads", lambda *a, **k: pytest.fail("Pickle deserialized"))
    shutil.rmtree(bronze_root)
    return tmp_path, snapshot


def report(source):
    root, _ = source
    result = silver_archive.prepare_silver(root)
    bundle = Path(result["manifest"]).parent
    document = json.loads((bundle / "manifest.json").read_bytes())
    return root, result, bundle, document


def test_preparation_includes_external_audits_and_contract_and_is_idempotent(source):
    root, snapshot = source
    before = {
        p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()
    }
    result = silver_archive.prepare_silver(root)
    assert result["schema"] == archive.SILVER_SCHEMA
    assert result["verified_partitions"] == 2
    assert result["rows"] == result["distinct_transaction_ids"] == 4
    assert result["fraud_count"] == result["genuine_count"] == result["zero_amounts"] == 2
    assert result["included_audits"] == 2 and result["archive_files"] == 7
    assert result["inventory_included"] and result["contract_included"]
    assert result["s3_accessed"] is False and result["bronze_read"] is False
    assert silver_archive.prepare_silver(root)["bundle_id"] == result["bundle_id"]
    bundle = Path(result["manifest"]).parent
    assert all((root / name).read_bytes() == raw for name, raw in before.items())
    for name in [
        archive.INVENTORY_PATH,
        archive.SILVER_CONTRACT_PATH,
        (snapshot / "manifest.json").relative_to(root).as_posix(),
    ]:
        assert (bundle / name).read_bytes() == before[name]
    assert not list(bundle.rglob("*reserved*"))
    shutil.rmtree(root / "data/interim")
    shutil.rmtree(root / "references")
    assert silver_archive.verify_bundle(root, result["bundle_id"])["rows"] == 4


@pytest.mark.parametrize(
    "problem", ["missing", "corrupt", "lock", "missing_audit", "contract", "database", "symlink"]
)
def test_preparation_rejects_damage_and_active_writers(source, problem):
    root, snapshot = source
    path = next(snapshot.rglob("*.parquet"))
    if problem == "missing":
        path.unlink()
    elif problem == "corrupt":
        path.write_bytes(b"corrupt")
    elif problem == "lock":
        (snapshot.parent / ".silver_v1.lock").touch()
    elif problem == "missing_audit":
        manifest = json.loads((snapshot / "manifest.json").read_text())
        (snapshot.parent / "runs" / f"silver_{manifest['run_id']}.json").unlink()
    elif problem == "contract":
        (root / archive.SILVER_CONTRACT_PATH).write_text("{}")
    elif problem == "database":
        (snapshot / "live.sqlite").touch()
    else:
        path.unlink()
        path.symlink_to(root / archive.INVENTORY_PATH)
    with pytest.raises((ValueError, SilverError)):
        silver_archive.prepare_silver(root)
    assert not list((root / "data/storage").glob("bundles/*"))


def test_audit_arriving_during_staging_is_detected(source, monkeypatch):
    root, snapshot = source
    original = silver_archive.selections
    calls = 0

    def changed(root):
        nonlocal calls
        calls += 1
        if calls == 2:
            (snapshot.parent / "runs" / ("silver_" + "c" * 32 + ".json")).write_text("{}")
        return original(root)

    monkeypatch.setattr(silver_archive, "selections", changed)
    with pytest.raises(ValueError, match="source changed"):
        silver_archive.prepare_silver(root)
    assert not list((root / "data/storage").glob("bundles/*"))


def test_roundtrip_reuses_transport_and_recovers_without_any_originals(source):
    root, result, bundle, document = report(source)
    identity = result["bundle_id"]
    client = MemoryClient()
    calls = []

    def job(root, command, identity, *, schema):
        calls.append((command, schema))
        return (transport.publish if command == "publish" else transport.download)(
            archive.workspace(root), identity, client, schema
        )

    first = silver_archive.publish(root, identity, worker=job)
    assert first["uploaded_objects"] == len(document["files"]) + 1
    assert client.puts[-1] == transport.prefix(identity, archive.SILVER_SCHEMA) + "manifest.json"
    again = silver_archive.publish(root, identity, worker=job)
    assert again["uploaded_objects"] == 0 and again["reused_objects"] == 8
    shutil.rmtree(root / "data/interim")
    shutil.rmtree(root / "references")
    shutil.rmtree(bundle)
    restored = silver_archive.restore(root, identity, worker=job)
    assert calls == [
        ("publish", archive.SILVER_SCHEMA),
        ("publish", archive.SILVER_SCHEMA),
        ("download", archive.SILVER_SCHEMA),
    ]
    assert restored["status"] == "restored_verified" and restored["complete"]
    assert restored["rows"] == 4 and restored["verified_partitions"] == 2
    assert restored["contract_included"] and restored["original_source_read"] is False
    assert restored["primary_storage_migrated"] is False
    assert (
        silver_archive.verify_directory(Path(restored["restored_directory"]), document)["rows"]
        == 4
    )
    with pytest.raises(ValueError, match="destination exists"):
        silver_archive.restore(
            root, identity, worker=lambda *a, **k: pytest.fail("Unexpected job")
        )


@pytest.mark.parametrize("problem", ["schema", "counts", "duplicate_ids", "inventory", "audit"])
def test_semantic_forgery_is_rejected_even_after_archive_hashes_are_updated(source, problem):
    root, result, bundle, document = report(source)
    snapshot = bundle / Path(result["source_snapshot"]).relative_to(root)
    manifest_path = snapshot / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    path = snapshot / manifest["files"][0]["path"]
    if problem in {"schema", "duplicate_ids"}:
        frame = pq.ParquetFile(path).read().to_pandas()
        if problem == "schema":
            frame = frame.drop(columns="CUSTOMER_ID")
        else:
            frame["TRANSACTION_ID"] = [1, 1]
        frame.to_parquet(path, index=False)
        manifest["files"][0]["size_bytes"] = path.stat().st_size
        manifest["files"][0]["sha256"] = archive.digest(path)
    elif problem == "counts":
        manifest["counts"]["rows"] += 1
    elif problem == "inventory":
        manifest["source"]["commit"] = "b" * 40
    else:
        (
            bundle
            / "data/interim/handbook"
            / ("a" * 40)
            / "runs"
            / f"silver_{manifest['run_id']}.json"
        ).unlink()
    manifest_path.write_bytes(archive.encoded(manifest))
    for row in document["files"]:
        file = bundle / row["path"]
        if file.exists():
            row.update(bytes=file.stat().st_size, sha256=archive.digest(file))
    document["provenance"]["silver_manifest_sha256"] = archive.digest(manifest_path)
    archive.validate_manifest(document)
    with pytest.raises((ValueError, SilverError)):
        silver_archive.verify_directory(bundle, document)


def test_cross_profile_and_path_boundaries_are_closed(source):
    root, result, bundle, document = report(source)
    with pytest.raises(ValueError, match="Bronze"):
        transport.publish(archive.workspace(root), result["bundle_id"], object())
    with pytest.raises(ValueError):
        bronze_archive.verify_directory(bundle, document)
    with pytest.raises(ValueError):
        archive.relative_path(archive.SILVER_CONTRACT_PATH)
    with pytest.raises(ValueError):
        archive.profile_path(archive.SILVER_CONTRACT_PATH, archive.BRONZE_SCHEMA)
    extra = dict(
        document["files"][0], path=f"data/interim/handbook/{'a' * 40}/eda_v1/unrelated.json"
    )
    document["files"] = sorted([*document["files"], extra], key=lambda row: row["path"])
    with pytest.raises(ValueError, match="unrelated"):
        archive.validate_manifest(document)


def test_silver_profile_covers_native_scale_without_relaxing_pilot(source):
    _, _, _, document = report(source)
    parent = f"data/interim/handbook/{'a' * 40}/silver_v1/"
    document["files"] = sorted(
        [
            *document["files"],
            *[
                {"path": parent + f"auxiliary/{i:03d}.bin", "bytes": 0, "sha256": "0" * 64}
                for i in range(190)
            ],
        ],
        key=lambda row: row["path"],
    )
    archive.validate_manifest(document)
    document["files"][-1]["bytes"] = archive.BRONZE_MAX_BYTES + 1
    with pytest.raises(ValueError, match="byte limit"):
        archive.validate_manifest(document)
    assert archive.budget(archive.SCHEMA) == (64, 64 * 1024 * 1024)


def test_cli_reports_prepare_and_requires_identity(source, monkeypatch, capsys):
    root, _ = source
    monkeypatch.setattr(sys, "argv", ["silver_archive", "--project-root", str(root), "prepare"])
    silver_archive.main()
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "prepared_verified_locally" and Path(result["report"]).is_file()
    monkeypatch.setattr(sys, "argv", ["silver_archive", "--project-root", str(root), "restore"])
    with pytest.raises(SystemExit) as error:
        silver_archive.main()
    assert error.value.code == 2


def test_silver_partial_publication_resumes_and_semantic_failure_stays_quarantined(
    source, monkeypatch
):
    root, result, _, _ = report(source)
    identity, client = result["bundle_id"], MemoryClient()
    work = archive.workspace(root)
    client.fail_at = 3
    with pytest.raises(OSError):
        transport.publish(work, identity, client, archive.SILVER_SCHEMA)
    assert (
        transport.prefix(identity, archive.SILVER_SCHEMA) + "manifest.json" not in client.objects
    )
    client.fail_at = None
    assert transport.publish(work, identity, client, archive.SILVER_SCHEMA)["reused_objects"] == 3

    def reject(*args):
        raise SilverError("injected semantic failure")

    monkeypatch.setattr(silver_archive, "verify_directory", reject)
    with pytest.raises(SilverError, match="semantic failure"):
        silver_archive.restore(
            root,
            identity,
            worker=lambda root, command, identity, *, schema: transport.download(
                work, identity, client, schema
            ),
        )
    assert not (work / "restored" / identity).exists()
    assert len(list((work / "downloads").glob("silver-*"))) == 1
