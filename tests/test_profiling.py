from copy import deepcopy
import hashlib
from io import BytesIO
import json

import pandas as pd
import pytest
from typer.testing import CliRunner

from fraud_detection_mlops import bronze, profiling


def frame_for_day(day="2018-04-01", ids=(1, 2)):
    dates = pd.to_datetime([f"{day} 01:00:00", f"{day} 02:00:00"])
    seconds = ((dates - pd.Timestamp("2018-04-01")).total_seconds()).astype(int)
    return pd.DataFrame(
        {
            "TRANSACTION_ID": ids,
            "TX_DATETIME": dates,
            "CUSTOMER_ID": pd.Series([0, 1], dtype=object),
            "TERMINAL_ID": pd.Series([10, 20], dtype=object),
            "TX_AMOUNT": [10.0, 250.0],
            "TX_TIME_SECONDS": pd.Series(seconds, dtype=object),
            "TX_TIME_DAYS": pd.Series(seconds // 86400, dtype=object),
            "TX_FRAUD": [0, 1],
            "TX_FRAUD_SCENARIO": [0, 1],
        }
    )


def test_cross_partition_duplicates_are_detected_after_local_uniqueness():
    seen = set()
    first = profiling.profile_dataframe(frame_for_day(), "2018-04-01", "2018-04-01", seen)
    second = profiling.profile_dataframe(
        frame_for_day("2018-04-02", (2, 3)), "2018-04-02", "2018-04-01", seen
    )
    assert not any(first["checks"].values())
    assert second["checks"]["duplicate_tx_ids_within_partition"] == 0
    assert second["checks"]["duplicate_tx_ids_across_previous_partitions"] == 1
    assert seen == {1, 2, 3}


def test_diagnostics_detect_invalid_values_without_changing_input():
    frame = frame_for_day()
    frame.loc[0, "CUSTOMER_ID"] = "invalid"
    frame.loc[0, "TX_AMOUNT"] = float("inf")
    frame.loc[0, "TX_TIME_DAYS"] = 1
    frame.loc[1, "TX_FRAUD_SCENARIO"] = 0
    original = deepcopy(frame)
    report = profiling.profile_dataframe(frame, "2018-04-01", "2018-04-01", set())
    assert report["invalid_int64_by_column"]["CUSTOMER_ID"] == 1
    assert report["checks"]["invalid_or_nonfinite_amounts"] == 1
    assert report["checks"]["days_datetime_mismatch"] == 1
    assert report["checks"]["fraud_scenario_mismatch"] == 1
    pd.testing.assert_frame_equal(frame, original)


def test_duplicate_rows_missing_values_and_wrong_partition_are_observations():
    frame = frame_for_day(ids=(1, 1))
    frame.loc[1] = frame.loc[0]
    report = profiling.profile_dataframe(frame, "2018-04-02", "2018-04-01", set())
    assert report["checks"]["duplicate_rows"] == 1
    assert report["checks"]["duplicate_tx_ids_within_partition"] == 1
    assert report["checks"]["timestamps_outside_partition"] == 2
    frame.loc[0, "TX_DATETIME"] = pd.NaT
    report = profiling.profile_dataframe(frame, "2018-04-01", "2018-04-01", set())
    assert report["checks"]["invalid_timestamps"] == 1
    assert report["checks"]["null_values"] == 1


@pytest.mark.parametrize("value", [1.5, float("inf"), float("nan"), 2**63, -(2**63) - 1])
def test_invalid_int64_values_are_detected_before_silver_conversion(value):
    assert not profiling._is_int64(value)


def test_unexpected_schema_requires_review_instead_of_partial_report():
    with pytest.raises(ValueError, match="Unexpected columns"):
        profiling.profile_dataframe(
            frame_for_day().drop(columns="TX_FRAUD"), "2018-04-01", "2018-04-01", set()
        )


@pytest.fixture
def snapshot(tmp_path, monkeypatch):
    payloads, files = {}, []
    for day, ids in [("2018-04-01", (1, 2)), ("2018-04-02", (2, 3))]:
        buffer = BytesIO()
        frame_for_day(day, ids).to_pickle(buffer)
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
    return tmp_path / "bronze", inventory


def test_complete_profile_reconciles_records_and_cli_writes_diagnostic(snapshot, tmp_path):
    root, inventory = snapshot
    bronze.extract_bronze(root, inventory_path=inventory)
    report = profiling.profile_bronze(root, inventory)
    assert report["summary"]["profiled_files"] == 2
    assert report["summary"]["rows"] == 4
    assert report["summary"]["distinct_valid_transaction_ids"] == 3
    assert report["summary"]["checks"]["duplicate_tx_ids_across_previous_partitions"] == 1
    destination = tmp_path / "profile.json"
    result = CliRunner().invoke(
        profiling.app,
        [
            "--bronze-root",
            str(root),
            "--inventory",
            str(inventory),
            "--report-path",
            str(destination),
        ],
    )
    assert result.exit_code == 0, result.output
    assert (
        json.loads(destination.read_text())["kind"] == "diagnostic_profile_not_silver_acceptance"
    )


def test_incomplete_bronze_cannot_be_profiled_as_complete(snapshot):
    root, inventory = snapshot
    bronze.extract_bronze(root, inventory_path=inventory, end_date="2018-04-01")
    with pytest.raises(bronze.BronzeError, match="incomplete"):
        profiling.profile_bronze(root, inventory)
