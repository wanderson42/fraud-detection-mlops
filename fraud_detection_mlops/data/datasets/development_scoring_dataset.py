"""Project only the 21 authorized inference partitions from existing development Gold."""

import hashlib
import json
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

from fraud_detection_mlops.artifacts import sha256
from fraud_detection_mlops.features.feature_schema import (
    FEATURE_ARROW_SCHEMA,
    FEATURE_COLUMNS,
    FEATURE_DTYPES,
)
from fraud_detection_mlops.features.feature_validation import validate_feature_record
from fraud_detection_mlops.modeling.experiments.temporal_development_data import (
    load_policy,
    selected_days,
)
from fraud_detection_mlops.streaming.decisions.development_threshold_selection import (
    selection_days,
)

DEVELOPMENT_RUN_ID = "9f86edf1e8f2408098c5377456e8968e3a7ca5925a20222b8ab8547ef94cbaf9"
METADATA_COLUMNS = ["TRANSACTION_ID", "CUSTOMER_ID", "TERMINAL_ID", "TX_DATETIME"]


def regular_file(path: Path) -> Path:
    path = path.absolute()
    if path.resolve() != path or not path.is_file():
        raise ValueError("Expected an existing regular file without redirected paths")
    return path


def development_snapshot(directory: Path, project_root: Path, source_commit: str) -> dict:
    """Validate the manifest without opening any Parquet partition."""
    directory = directory.absolute()
    path = regular_file(directory / "manifest.json")
    policy_path = regular_file(project_root / "references/hgb_optuna_protocol_v1.json")
    policy = load_policy(policy_path)
    manifest = json.loads(path.read_text())
    records = manifest["files"]
    expected_days = selected_days(policy)
    identity = {
        key: manifest[key] for key in ("policy_sha256", "silver_manifest_sha256", "implementation")
    }
    if (
        manifest.get("version") != "development_gold_v2"
        or manifest.get("source_commit") != source_commit
        or manifest.get("policy_sha256") != sha256(policy_path)
        or manifest.get("feature_columns") != FEATURE_COLUMNS
        or manifest.get("confirmation_evaluated") is not False
        or manifest.get("original_test_reused") is not False
        or manifest.get("run_id") != DEVELOPMENT_RUN_ID
        or manifest.get("run_id")
        != hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        or [r["date"] for r in records] != expected_days
        or [r["path"] for r in records] != [f"transactions/{day}.parquet" for day in expected_days]
    ):
        raise ValueError("Development Gold identity or authorized partition metadata differs")
    chosen = [record for record in records if record["date"] in selection_days()]
    if [r["date"] for r in chosen] != selection_days() or any(
        type(r["rows"]) is not int or r["rows"] <= 0 for r in chosen
    ):
        raise ValueError("Incomplete positive-row development coverage")
    return {
        "development_path": str(directory),
        "run_id": manifest["run_id"],
        "manifest_sha256": sha256(path),
        "files": chosen,
    }


def check_selected_bytes(snapshot: dict) -> None:
    directory = Path(snapshot["development_path"])
    if sha256(regular_file(directory / "manifest.json")) != snapshot["manifest_sha256"]:
        raise ValueError("Development manifest changed during threshold selection")
    if [r["date"] for r in snapshot["files"]] != selection_days():
        raise ValueError("Unexpected development selection dates")
    for record in snapshot["files"]:
        if record["path"] != f"transactions/{record['date']}.parquet":
            raise ValueError("Development path is outside its authorized day")
        path = regular_file(directory / record["path"])
        if path.stat().st_size != record["size_bytes"] or sha256(path) != record["sha256"]:
            raise ValueError("Development partition integrity mismatch")


def load_scoring_partition(snapshot: dict, record: dict) -> pd.DataFrame:
    if record not in snapshot["files"] or record["date"] not in selection_days():
        raise ValueError("Partition is outside development threshold authorization")
    path = regular_file(Path(snapshot["development_path"]) / record["path"])
    parquet = pq.ParquetFile(path)
    if (
        not parquet.schema_arrow.equals(FEATURE_ARROW_SCHEMA, check_metadata=False)
        or parquet.metadata.num_rows != record["rows"]
    ):
        raise ValueError("Development feature schema or row count differs")
    # Current targets and their availability metadata are deliberately not projected.
    frame = parquet.read(columns=METADATA_COLUMNS + FEATURE_COLUMNS).to_pandas()
    if (
        frame.isna().any().any()
        or not (frame.TX_DATETIME.dt.strftime("%Y-%m-%d") == record["date"]).all()
    ):
        raise ValueError("Development partition has nulls or timestamps outside its day")
    if (
        any((frame[name] < 0).any() for name in METADATA_COLUMNS[:3])
        or not (frame.TX_HOUR == frame.TX_DATETIME.dt.hour).all()
        or not (frame.TX_WEEKDAY == frame.TX_DATETIME.dt.isocalendar().day).all()
    ):
        raise ValueError("Development identity or clock features differ")
    features = frame[FEATURE_COLUMNS].astype(FEATURE_DTYPES)
    for values in features.to_dict(orient="records"):
        validate_feature_record(values)
    return frame.sort_values(["TX_DATETIME", "TRANSACTION_ID"]).reset_index(drop=True)
