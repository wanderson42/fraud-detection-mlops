from datetime import date, timedelta
import hashlib
from io import BytesIO
import json
from pathlib import Path
import shutil

import pandas as pd
import pyarrow.parquet as pq
import pytest
from typer.testing import CliRunner

from fraud_detection_mlops import bronze, gold, silver, temporal
from fraud_detection_mlops.features import FEATURE_COLUMNS, METADATA_DTYPES


@pytest.fixture(scope="module")
def prepared(tmp_path_factory):
    root = tmp_path_factory.mktemp("gold-source")
    files, payloads = [], {}
    for i in range(21):
        day = date(2018, 4, 1) + timedelta(days=i)
        stamps = pd.to_datetime([f"{day} 00:00:00", f"{day} 12:00:00", f"{day} 23:59:59"])
        seconds = ((stamps - pd.Timestamp("2018-04-01")).total_seconds()).astype(int)
        frame = pd.DataFrame(
            {
                "TRANSACTION_ID": [3 * i, 3 * i + 1, 3 * i + 2],
                "TX_DATETIME": stamps,
                "CUSTOMER_ID": [1, 1, 2],
                "TERMINAL_ID": [10, 10, 10],
                "TX_AMOUNT": [0.0, 10.0, 250.0],
                "TX_TIME_SECONDS": seconds,
                "TX_TIME_DAYS": seconds // 86400,
                "TX_FRAUD": [0, 0, 1],
                "TX_FRAUD_SCENARIO": [0, 0, 1],
            }
        )
        buffer = BytesIO()
        frame.to_pickle(buffer)
        payload = buffer.getvalue()
        filename = f"{day}.pkl"
        payloads[filename] = payload
        files.append(
            {
                "date": str(day),
                "filename": filename,
                "source_path": f"data/{filename}",
                "size_bytes": len(payload),
                "git_blob_sha1": hashlib.sha1(
                    f"blob {len(payload)}".encode() + b"\0" + payload
                ).hexdigest(),
            }
        )
    inventory = root / "inventory.json"
    inventory.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source": {"repository": bronze.SOURCE_REPOSITORY, "commit": "a" * 40},
                "files": files,
            }
        )
    )
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            bronze,
            "urlopen",
            lambda request, timeout: BytesIO(payloads[request.full_url.rsplit("/", 1)[-1]]),
        )
        bronze.extract_bronze(root / "bronze", inventory_path=inventory)
    silver.build_silver(root / "bronze", root / "silver", inventory_path=inventory)
    protocol = json.loads(temporal.DEFAULT_PROTOCOL.read_text())
    protocol["source_commit"] = "a" * 40
    protocol["windows"] = {
        "train": {"start": "2018-04-01", "end_exclusive": "2018-04-03"},
        "validation": {"start": "2018-04-10", "end_exclusive": "2018-04-11"},
        "test": {"start": "2018-04-19", "end_exclusive": "2018-04-20"},
    }
    return root, protocol


@pytest.fixture
def source(prepared, tmp_path):
    original, protocol = prepared
    shutil.copytree(original / "silver", tmp_path / "silver")
    shutil.copy2(original / "inventory.json", tmp_path / "inventory.json")
    (tmp_path / "protocol.json").write_text(json.dumps(protocol))
    return tmp_path


def build(source):
    return gold.build_gold(
        source / "silver",
        source / "gold",
        inventory_path=source / "inventory.json",
        protocol_path=source / "protocol.json",
    )


def verify(source):
    return gold.verify_gold(
        source / "gold",
        inventory_path=source / "inventory.json",
        protocol_path=source / "protocol.json",
    )


def test_build_preserves_source_split_rows_and_exposes_only_allowlisted_features(source):
    source_dir = source / "silver" / ("a" * 40) / "silver_v1"
    before = {
        str(p): (p.read_bytes(), p.stat().st_mtime_ns)
        for p in source_dir.rglob("*")
        if p.is_file()
    }
    result = build(source)
    assert result["status"] == "success"
    assert result["split_rows"] == {"train": 6, "validation": 3, "test": 3}
    assert result["rows"] == 12
    assert result["verified_partitions"] == 4
    assert result["feature_count"] == 19
    assert verify(source)["split_rows"] == result["split_rows"]
    X, y, metadata = gold.load_gold_split(
        "validation",
        source / "gold",
        inventory_path=source / "inventory.json",
        protocol_path=source / "protocol.json",
    )
    assert list(X) == FEATURE_COLUMNS
    assert list(metadata) == list(METADATA_DTYPES)
    assert not (
        {
            "TX_FRAUD",
            "TX_FRAUD_SCENARIO",
            "CUSTOMER_ID",
            "TERMINAL_ID",
            "TRANSACTION_ID",
            "LABEL_AVAILABLE_AT",
        }
        & set(X)
    )
    assert y.tolist() == [0, 0, 1]
    # Validation retains event and label history from the gap, not just the train split.
    assert X.iloc[0].CUSTOMER_TX_COUNT_1D == 2
    assert X.iloc[0].TERMINAL_KNOWN_LABEL_COUNT_7D == 6
    assert X.iloc[0].TERMINAL_KNOWN_FRAUD_COUNT_7D == 2
    assert before == {
        str(p): (p.read_bytes(), p.stat().st_mtime_ns)
        for p in source_dir.rglob("*")
        if p.is_file()
    }
    manifest = json.loads((Path(result["gold_path"]) / "manifest.json").read_text())
    assert len(manifest["context_inputs"]) == 19
    assert manifest["context_inputs"][-1]["input_filename"] == "2018-04-19.pkl"
    assert json.loads(Path(result["audit_path"]).read_text())["status"] == "success"


def test_repeated_build_verifies_and_reuses_without_rewriting_gold(source):
    first = build(source)
    directory = Path(first["gold_path"])
    before = {
        str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in directory.rglob("*") if p.is_file()
    }
    second = build(source)
    assert second["status"] == "reused"
    assert first["audit_path"] != second["audit_path"]
    assert before == {
        str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in directory.rglob("*") if p.is_file()
    }


def test_corrupted_existing_gold_is_not_overwritten(source):
    first = build(source)
    path = next(Path(first["gold_path"]).rglob("*.parquet"))
    path.write_bytes(b"bad parquet")
    with pytest.raises(gold.GoldError, match="integrity"):
        build(source)
    assert path.read_bytes() == b"bad parquet"


def test_corrupted_silver_prevents_publication_and_records_failure(source):
    path = next((source / "silver").rglob("*.parquet"))
    path.write_bytes(b"bad input")
    with pytest.raises(silver.SilverError, match="integrity"):
        build(source)
    parent = source / "gold" / ("a" * 40)
    assert not (parent / "gold_v1").exists()
    assert not (parent / ".gold_v1.lock").exists()
    assert json.loads(next((parent / "runs").glob("*.json")).read_text())["status"] == "failed"


def test_write_failure_cleans_staging_preserves_source_and_records_audit(source, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("disk failure")

    monkeypatch.setattr(pd.DataFrame, "to_parquet", fail)
    with pytest.raises(OSError, match="disk failure"):
        build(source)
    parent = source / "gold" / ("a" * 40)
    assert not (parent / "gold_v1").exists()
    assert not list(parent.glob(".gold-staging-*"))
    assert not (parent / ".gold_v1.lock").exists()
    assert json.loads(next((parent / "runs").glob("*.json")).read_text())["status"] == "failed"


def test_protocol_changed_during_build_is_not_published(source, monkeypatch):
    original = gold.compute_features

    def changed(connection):
        original(connection)
        path = source / "protocol.json"
        path.write_text(path.read_text() + "\n")

    monkeypatch.setattr(gold, "compute_features", changed)
    with pytest.raises(gold.GoldError, match="Inputs changed"):
        build(source)
    assert not (source / "gold" / ("a" * 40) / "gold_v1").exists()


def test_unknown_contract_fails_before_writes(source):
    contract = source / "gold-contract.json"
    value = json.loads(gold.DEFAULT_GOLD_CONTRACT.read_text())
    value["label_delay_days"] = 0
    contract.write_text(json.dumps(value))
    with pytest.raises(gold.GoldError, match="Unsupported"):
        gold.build_gold(
            source / "silver",
            source / "gold",
            inventory_path=source / "inventory.json",
            protocol_path=source / "protocol.json",
            gold_contract=contract,
        )
    assert not (source / "gold").exists()


def test_existing_lock_prevents_a_second_builder(source):
    parent = source / "gold" / ("a" * 40)
    parent.mkdir(parents=True)
    (parent / ".gold_v1.lock").write_text("active")
    with pytest.raises(gold.GoldError, match="lock"):
        build(source)
    assert (parent / ".gold_v1.lock").read_text() == "active"


@pytest.mark.parametrize(
    "change",
    [
        lambda f: f.__setitem__("CUSTOMER_AVG_AMOUNT_1D", float("nan")),
        lambda f: f.__setitem__("TERMINAL_KNOWN_FRAUD_RATE_1D", 2.0),
        lambda f: f.__setitem__("TX_DATETIME", pd.Timestamp("2018-04-02")),
    ],
)
def test_verify_catches_semantic_tampering_even_if_checksum_is_updated(source, change):
    result = build(source)
    directory = Path(result["gold_path"])
    manifest = json.loads((directory / "manifest.json").read_text())
    record = manifest["files"][0]
    path = directory / record["path"]
    frame = pq.ParquetFile(path).read().to_pandas()
    change(frame)
    frame = frame.astype(gold.DTYPES)
    frame.to_parquet(path, index=False, engine="pyarrow", compression="zstd")
    record["size_bytes"] = path.stat().st_size
    record["sha256"] = gold.sha256(path)
    (directory / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(gold.GoldError, match="semantic"):
        verify(source)


def test_cli_build_and_verify(source):
    runner = CliRunner()
    options = [
        "--output-root",
        str(source / "gold"),
        "--inventory",
        str(source / "inventory.json"),
        "--protocol",
        str(source / "protocol.json"),
    ]
    result = runner.invoke(gold.app, ["build", "--silver-root", str(source / "silver"), *options])
    assert result.exit_code == 0, result.output
    assert runner.invoke(gold.app, ["verify", *options]).exit_code == 0
    with pytest.raises(gold.GoldError, match="Unknown split"):
        gold.load_gold_split("future", source / "gold")
