"""Full-snapshot Bronze preparation, with opaque fixture bytes and offline provenance."""

from datetime import date, timedelta
import hashlib
from io import BytesIO
import json
from pathlib import Path
import pickle
import shutil
import sys

import pytest
from scripts.storage import archive, bronze_archive

from fraud_detection_mlops.data.ingestion import handbook_bronze as bronze


@pytest.fixture
def source(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").touch()
    inventory_path = tmp_path / archive.INVENTORY_PATH
    inventory_path.parent.mkdir()
    payloads = {}
    records = []
    # More than 64 payloads proves this is a complete snapshot beyond the pilot.
    for i in range(70):
        day = str(date(2018, 4, 1) + timedelta(days=i))
        name = day + ".pkl"
        payload = b"opaque bytes; deliberately not a pickle: " + name.encode()
        payloads[name] = payload
        records.append(
            {
                "date": day,
                "filename": name,
                "source_path": "data/" + name,
                "size_bytes": len(payload),
                "git_blob_sha1": hashlib.sha1(
                    f"blob {len(payload)}\0".encode() + payload
                ).hexdigest(),
            }
        )
    inventory_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source": {"repository": bronze.SOURCE_REPOSITORY, "commit": "a" * 40},
                "files": records,
            }
        )
    )
    monkeypatch.setattr(
        bronze,
        "urlopen",
        lambda request, timeout: BytesIO(payloads[request.full_url.rsplit("/", 1)[-1]]),
    )
    bronze.extract_bronze(tmp_path / "data/raw/handbook", inventory_path=inventory_path)
    monkeypatch.setattr(
        bronze, "urlopen", lambda *a, **k: pytest.fail("Preparation accessed the network")
    )
    monkeypatch.setattr(pickle, "load", lambda *a, **k: pytest.fail("Pickle was deserialized"))
    monkeypatch.setattr(pickle, "loads", lambda *a, **k: pytest.fail("Pickle was deserialized"))
    return tmp_path, tmp_path / "data/raw/handbook" / ("a" * 40), inventory_path


def test_complete_bronze_archive_is_idempotent_and_self_contained(source):
    root, snapshot, inventory_path = source
    original = {
        p.relative_to(root).as_posix(): p.read_bytes() for p in snapshot.rglob("*") if p.is_file()
    }
    result = bronze_archive.prepare_bronze(root)
    assert result["complete"] and result["verified_source_partitions"] == 70
    assert result["archive_files"] == 73 and result["source_files"] == 72
    assert result["schema"] == archive.BRONZE_SCHEMA and result["s3_accessed"] is False
    assert bronze_archive.prepare_bronze(root)["bundle_id"] == result["bundle_id"]
    assert {
        p.relative_to(root).as_posix(): p.read_bytes() for p in snapshot.rglob("*") if p.is_file()
    } == original
    bundle = Path(result["manifest"]).parent
    assert (bundle / archive.INVENTORY_PATH).read_bytes() == inventory_path.read_bytes()
    for name, data in original.items():
        assert (bundle / name).read_bytes() == data
    shutil.rmtree(snapshot)
    inventory_path.unlink()
    assert bronze_archive.verify_bundle(root, result["bundle_id"])["complete"] is True


def test_bronze_profile_accepts_more_bytes_than_pilot_but_has_own_budget(source):
    root, _, _ = source
    result = bronze_archive.prepare_bronze(root)
    document = json.loads(Path(result["manifest"]).read_bytes())
    row = next(row for row in document["files"] if row["path"].endswith(".pkl"))
    row["bytes"] = 80 * 1024 * 1024
    archive.validate_manifest(document)
    row["bytes"] = archive.BRONZE_MAX_BYTES + 1
    with pytest.raises(ValueError, match="byte limit"):
        archive.validate_manifest(document)


def test_pilot_stays_bounded_and_does_not_accept_inventory_or_bronze_publish(source):
    root, snapshot, _ = source
    with pytest.raises(ValueError, match="Pilot limit"):
        archive.prepare(root, [snapshot.relative_to(root).as_posix()])
    with pytest.raises(ValueError):
        archive.relative_path(archive.INVENTORY_PATH)
    result = bronze_archive.prepare_bronze(root)
    with pytest.raises(ValueError, match="Dataset transfer"):
        archive.publish(root, result["bundle_id"], object())


def test_bronze_metadata_budget_matches_the_verifier(source):
    root, snapshot, _ = source
    result = bronze_archive.prepare_bronze(root)
    document = json.loads(Path(result["manifest"]).read_bytes())
    metadata = [
        row
        for row in document["files"]
        if row["path"] == archive.INVENTORY_PATH or row["path"].endswith("/manifest.json")
    ]
    prefix = snapshot.relative_to(root).as_posix() + "/" + "nested/" * 100
    document["files"] = sorted(
        metadata
        + [
            {"path": prefix + f"file-{i:04d}", "bytes": 0, "sha256": "0" * 64}
            for i in range(archive.BRONZE_MAX_FILES - len(metadata))
        ],
        key=lambda row: row["path"],
    )
    with pytest.raises(ValueError, match="metadata budget"):
        archive.validate_manifest(document)


@pytest.mark.parametrize("problem", ["corruption", "missing_partition", "writer_lock", "database"])
def test_invalid_or_active_source_is_not_published(source, problem):
    root, snapshot, _ = source
    path = snapshot / "2018-04-01.pkl"
    if problem == "corruption":
        path.write_bytes(b"corrupt")
    elif problem == "missing_partition":
        path.unlink()
    elif problem == "writer_lock":
        (snapshot / ".extract.lock").touch()
    else:
        (snapshot / "live.sqlite").touch()
    with pytest.raises((ValueError, bronze.BronzeError)):
        bronze_archive.prepare_bronze(root)
    assert not list((root / "data/storage").glob("bundles/*"))


def test_audit_set_change_during_staging_is_rejected(source, monkeypatch):
    root, _snapshot, _ = source
    original_state = bronze_archive.source_state
    calls = 0

    def changed(root, snapshot):
        nonlocal calls
        calls += 1
        if calls == 2:
            (snapshot / "runs/new.json").write_text("{}")
        return original_state(root, snapshot)

    monkeypatch.setattr(bronze_archive, "source_state", changed)
    with pytest.raises(ValueError, match="source changed"):
        bronze_archive.prepare_bronze(root)
    assert not list((root / "data/storage").glob("bundles/*"))


@pytest.mark.parametrize("location", ["payload", "inventory", "extra_file"])
def test_prepared_bundle_tamper_is_rejected(source, location):
    root, snapshot, _ = source
    result = bronze_archive.prepare_bronze(root)
    bundle = Path(result["manifest"]).parent
    if location == "inventory":
        (bundle / archive.INVENTORY_PATH).write_text("{}")
    elif location == "payload":
        (bundle / snapshot.relative_to(root) / "2018-04-01.pkl").write_bytes(b"corrupt")
    else:
        (bundle / "unexpected.json").write_text("{}")
    with pytest.raises(ValueError):
        bronze_archive.verify_bundle(root, result["bundle_id"])
    if location == "extra_file":
        with pytest.raises(ValueError, match="Unexpected"):
            bronze_archive.prepare_bronze(root)


@pytest.mark.parametrize("corrupt", [False, True])
def test_cli_reports_success_or_stops_on_bronze_error(source, monkeypatch, capsys, corrupt):
    root, snapshot, _ = source
    monkeypatch.setattr(sys, "argv", ["bronze_archive", "--project-root", str(root), "prepare"])
    if corrupt:
        (snapshot / "2018-04-01.pkl").write_bytes(b"corrupt")
        with pytest.raises(SystemExit) as error:
            bronze_archive.main()
        assert error.value.code == 1
        assert "Bronze archive stopped" in capsys.readouterr().err
        return
    bronze_archive.main()
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "prepared_verified_locally"
    assert Path(result["report"]).is_file()
    assert json.loads(Path(result["report"]).read_bytes())["complete"]
