from copy import deepcopy
from datetime import date
import hashlib
from io import BytesIO
import json
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq
import pytest
from typer.testing import CliRunner

from fraud_detection_mlops.data.datasets import silver_dataset as silver
from fraud_detection_mlops.data.ingestion import handbook_bronze as bronze


@pytest.fixture
def source(tmp_path, monkeypatch):
    def prepare(change=None):
        files, payloads = [], {}
        for number, day in enumerate(["2018-04-01", "2018-04-02"]):
            dates = pd.to_datetime([f"{day} 01:00:00", f"{day} 02:00:00"])
            seconds = ((dates - pd.Timestamp("2018-04-01")).total_seconds()).astype(int)
            frame = pd.DataFrame(
                {
                    "TRANSACTION_ID": [number * 2 + 1, number * 2 + 2],
                    "TX_DATETIME": dates,
                    "CUSTOMER_ID": pd.Series([0, 1], dtype=object),
                    "TERMINAL_ID": pd.Series([10, 20], dtype=object),
                    "TX_AMOUNT": [0.0, 250.0],
                    "TX_TIME_SECONDS": pd.Series(seconds, dtype=object),
                    "TX_TIME_DAYS": pd.Series(seconds // 86400, dtype=object),
                    "TX_FRAUD": [0, 1],
                    "TX_FRAUD_SCENARIO": [0, 1],
                }
            )
            if change:
                change(frame, number)
            # Match the column Index representation retained by the original pickles.
            frame.columns = pd.Index(frame.columns, dtype=object)
            frame.index = [number * 2 + 50, number * 2 + 51]
            buffer = BytesIO()
            frame.to_pickle(buffer)
            payload = buffer.getvalue()
            filename = f"{day}.pkl"
            payloads[filename] = payload
            files.append(
                {
                    "date": day,
                    "filename": filename,
                    "source_path": f"data/{filename}",
                    "size_bytes": len(payload),
                    "git_blob_sha1": hashlib.sha1(
                        f"blob {len(payload)}".encode() + bytes([0]) + payload
                    ).hexdigest(),
                }
            )
        inventory = tmp_path / "inventory.json"
        inventory.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "source": {"repository": bronze.SOURCE_REPOSITORY, "commit": "a" * 40},
                    "files": files,
                }
            )
        )
        monkeypatch.setattr(
            bronze,
            "urlopen",
            lambda request, timeout: BytesIO(payloads[request.full_url.rsplit("/", 1)[-1]]),
        )
        root = tmp_path / "bronze"
        bronze.extract_bronze(root, inventory_path=inventory)
        return root, tmp_path / "interim", inventory

    return prepare


def test_build_reconciles_every_row_preserves_zeros_and_exposes_sql(source):
    root, output, inventory = source()
    result = silver.build_silver(root, output, inventory_path=inventory)
    assert result["status"] == "success"
    assert result["rows"] == result["distinct_transaction_ids"] == 4
    assert result["fraud_count"] == result["genuine_count"] == result["zero_amounts"] == 2
    directory = output / ("a" * 40) / "silver_v1"
    manifest = json.loads((directory / "manifest.json").read_text())
    for record in manifest["files"]:
        original = pd.read_pickle(root / ("a" * 40) / record["input_filename"])
        untouched = deepcopy(original)
        normalized = silver.normalize_dataframe(original)
        restored = pq.ParquetFile(directory / record["path"]).read().to_pandas()
        pd.testing.assert_frame_equal(normalized, restored, check_exact=True)
        pd.testing.assert_frame_equal(original, untouched)
        assert {name: str(dtype) for name, dtype in restored.dtypes.items()} == silver.DTYPES
        assert "__index_level_0__" not in restored.columns
    with silver.connect_silver(output, inventory_path=inventory) as connection:
        assert connection.execute(
            "SELECT tx_date, count(*) FROM transactions GROUP BY tx_date ORDER BY tx_date"
        ).fetchall() == [(date(2018, 4, 1), 2), (date(2018, 4, 2), 2)]
        assert connection.execute("SELECT sum(TX_AMOUNT) FROM transactions").fetchone() == (500.0,)
    audit = json.loads(Path(result["audit_path"]).read_text())
    assert audit["acceptance_summary"]["checks"]["zero_amounts"] == 2
    assert audit["status"] == "success"


def test_repeated_build_verifies_and_reuses_without_rewriting(source):
    root, output, inventory = source()
    first = silver.build_silver(root, output, inventory_path=inventory)
    directory = output / ("a" * 40) / "silver_v1"
    before = {
        str(path): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in directory.rglob("*")
        if path.is_file()
    }
    second = silver.build_silver(root, output, inventory_path=inventory)
    assert second["status"] == "reused"
    assert second["audit_path"] != first["audit_path"]
    assert before == {
        str(path): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in directory.rglob("*")
        if path.is_file()
    }


@pytest.mark.parametrize(
    "column,value",
    [
        ("TX_AMOUNT", -1.0),
        ("TX_AMOUNT", float("inf")),
        ("TX_DATETIME", pd.NaT),
        ("CUSTOMER_ID", 0.5),
        ("TX_FRAUD", 2),
        ("TX_FRAUD_SCENARIO", 0),
        ("TX_TIME_DAYS", 4),
    ],
)
def test_invalid_inputs_fail_without_publishing_silver(source, column, value):
    root, output, inventory = source(
        lambda frame, number: frame.__setitem__(column, [value, value])
    )
    with pytest.raises(silver.SilverError, match="acceptance"):
        silver.build_silver(root, output, inventory_path=inventory)
    parent = output / ("a" * 40)
    assert not (parent / "silver_v1").exists()
    assert not (parent / ".silver_v1.lock").exists()
    assert json.loads(next((parent / "runs").glob("*.json")).read_text())["status"] == "failed"


def test_duplicate_ids_across_days_fail_before_conversion(source):
    root, output, inventory = source(
        lambda frame, number: frame.__setitem__("TRANSACTION_ID", [1, 2])
    )
    with pytest.raises(silver.SilverError, match="duplicate_tx_ids_across_previous_partitions"):
        silver.build_silver(root, output, inventory_path=inventory)


def test_second_partition_write_failure_cleans_staging_and_records_failure(source, monkeypatch):
    root, output, inventory = source()
    original = pd.DataFrame.to_parquet

    def fail_second(frame, path, **kwargs):
        if "2018-04-02" in str(path):
            raise OSError("simulated disk failure")
        return original(frame, path, **kwargs)

    monkeypatch.setattr(pd.DataFrame, "to_parquet", fail_second)
    with pytest.raises(OSError, match="simulated disk failure"):
        silver.build_silver(root, output, inventory_path=inventory)
    parent = output / ("a" * 40)
    assert not (parent / "silver_v1").exists()
    assert not list(parent.glob(".silver-staging-*"))
    audit = json.loads(next((parent / "runs").glob("*.json")).read_text())
    assert audit["status"] == "failed" and audit["partitions_written"] == 1


def test_source_change_between_profile_and_conversion_is_detected(source, monkeypatch):
    root, output, inventory = source()
    original = silver.profile_bronze

    def change_after_profile(*args):
        profile = original(*args)
        path = root / ("a" * 40) / "2018-04-01.pkl"
        path.write_bytes(path.read_bytes() + b"changed")
        return profile

    monkeypatch.setattr(silver, "profile_bronze", change_after_profile)
    with pytest.raises(silver.SilverError, match="changed after verification"):
        silver.build_silver(root, output, inventory_path=inventory)
    assert not (output / ("a" * 40) / "silver_v1").exists()


@pytest.mark.parametrize("damage", ["corrupt", "missing", "extra", "counts", "schema"])
def test_verification_rejects_damaged_or_inconsistent_outputs(source, damage):
    root, output, inventory = source()
    silver.build_silver(root, output, inventory_path=inventory)
    directory = output / ("a" * 40) / "silver_v1"
    path = directory / "transactions/tx_date=2018-04-01/part-00000.parquet"
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if damage == "corrupt":
        path.write_bytes(path.read_bytes() + b"corrupt")
    elif damage == "missing":
        path.unlink()
    elif damage == "extra":
        (directory / "extra.parquet").write_bytes(path.read_bytes())
    elif damage == "counts":
        manifest["counts"]["rows"] += 1
        manifest_path.write_text(json.dumps(manifest))
    else:
        frame = pq.ParquetFile(path).read().to_pandas().drop(columns="CUSTOMER_ID")
        frame.to_parquet(path, index=False)
        manifest["files"][0]["size_bytes"] = path.stat().st_size
        manifest["files"][0]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(silver.SilverError):
        silver.verify_silver(output, inventory_path=inventory)
    with pytest.raises(silver.SilverError):
        silver.build_silver(root, output, inventory_path=inventory)


def test_contract_fingerprint_prevents_reusing_a_different_contract(source, tmp_path):
    root, output, inventory = source()
    silver.build_silver(root, output, inventory_path=inventory)
    contract = tmp_path / "contract.json"
    contract.write_bytes(silver.DEFAULT_CONTRACT.read_bytes() + b"\n")
    with pytest.raises(silver.SilverError, match="contract or inventory mismatch"):
        silver.verify_silver(output, inventory_path=inventory, contract_path=contract)


def test_external_lock_is_preserved(source):
    root, output, inventory = source()
    parent = output / ("a" * 40)
    parent.mkdir(parents=True)
    lock = parent / ".silver_v1.lock"
    lock.write_text("another process")
    with pytest.raises(silver.SilverError, match="lock exists"):
        silver.build_silver(root, output, inventory_path=inventory)
    assert lock.read_text() == "another process"
    assert not (parent / "runs").exists()


def test_cli_build_and_verify_use_requested_roots(source):
    root, output, inventory = source()
    options = ["--output-root", str(output), "--inventory", str(inventory)]
    runner = CliRunner()
    built = runner.invoke(silver.app, ["build", "--bronze-root", str(root), *options])
    assert built.exit_code == 0, built.output
    verified = runner.invoke(silver.app, ["verify", *options])
    assert verified.exit_code == 0, verified.output
    assert json.loads(verified.output)["verified_partitions"] == 2
