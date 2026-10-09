"""Build only the declared context/assessment features from pinned Silver partitions."""

from datetime import date, timedelta
import json
from pathlib import Path

import duckdb
import pandas as pd
import pyarrow.parquet as pq

from fraud_detection_mlops.artifacts import sha256
from fraud_detection_mlops.data.contracts.gold_contract import load_gold_contract
from fraud_detection_mlops.data.contracts.silver_contract import load_silver_contract
from fraud_detection_mlops.data.contracts.transaction_schema import ARROW_SCHEMA as SILVER_SCHEMA
from fraud_detection_mlops.data.ingestion.handbook_inventory import load_inventory
from fraud_detection_mlops.features.causal_history import compute_features
from fraud_detection_mlops.features.feature_schema import DTYPES, FEATURE_ARROW_SCHEMA
from fraud_detection_mlops.features.feature_validation import check_feature_values


def window_days(window: dict) -> list[str]:
    start, end = (date.fromisoformat(window[k]) for k in ("start", "end_exclusive"))
    return [str(start + timedelta(days=i)) for i in range((end - start).days)]


def silver_snapshot(silver_root: Path, policy: dict, project_root: Path) -> dict:
    """Read metadata only; selected partition bytes require an external access record."""
    inventory_path = project_root / "references/handbook_source.json"
    contract_path = project_root / "references/silver_contract_v1.json"
    inventory = load_inventory(inventory_path)
    load_silver_contract(contract_path)
    load_gold_contract(project_root / "references/gold_contract_v1.json")
    source = (silver_root / policy["source_commit"] / "silver_v1").resolve()
    manifest_path = source / "manifest.json"
    if manifest_path.resolve() != manifest_path or not manifest_path.is_file():
        raise ValueError("Assessment Silver manifest must be a regular file in the dataset")
    manifest = json.loads(manifest_path.read_text())
    if (
        inventory["source"]["commit"] != policy["source_commit"]
        or manifest.get("source") != inventory["source"]
        or manifest.get("layer") != "silver"
        or manifest.get("schema_version") != 1
        or manifest.get("contract_version") != "silver_v1"
        or manifest.get("contract_sha256") != sha256(contract_path)
        or manifest.get("inventory_sha256") != sha256(inventory_path)
    ):
        raise ValueError("Assessment Silver source, inventory or contract differs")
    context, assessment = (policy["windows"][k] for k in ("assessment_context", "assessment"))
    days = window_days({"start": context["start"], "end_exclusive": assessment["end_exclusive"]})
    allowed = set(days)
    selected = [r for r in manifest["files"] if r["input_filename"][:10] in allowed]
    by_day = {r["date"]: r for r in inventory["files"]}
    if [r["input_filename"][:10] for r in selected] != days:
        raise ValueError("Assessment requires complete ordered Silver context and daily coverage")
    for day, record in zip(days, selected, strict=True):
        item = by_day[day]
        if (
            record["path"] != f"transactions/tx_date={day}/part-00000.parquet"
            or record["input_filename"] != item["filename"]
            or record["input_size_bytes"] != item["size_bytes"]
            or record["input_git_blob_sha1"] != item["git_blob_sha1"]
            or type(record["rows"]) is not int
            or record["rows"] <= 0
        ):
            raise ValueError("Assessment Silver partition metadata does not reconcile")
    return {
        "source_root": str(source),
        "source_commit": policy["source_commit"],
        "manifest_sha256": sha256(manifest_path),
        "inventory_sha256": sha256(inventory_path),
        "contract_sha256": sha256(contract_path),
        "files": selected,
    }


def check_silver_bytes(snapshot: dict) -> None:
    """Hash only selected bytes; refuse redirected/symlinked daily partitions."""
    source = Path(snapshot["source_root"])
    for record in snapshot["files"]:
        path = source / record["path"]
        if (
            path.resolve() != path
            or not path.is_file()
            or path.stat().st_size != record["size_bytes"]
            or sha256(path) != record["sha256"]
        ):
            raise ValueError("Assessment Silver partition integrity mismatch")
    if sha256(source / "manifest.json") != snapshot["manifest_sha256"]:
        raise ValueError("Assessment Silver manifest changed during access")


def build_assessment_features(snapshot: dict, policy: dict, output: Path, resources: dict) -> list:
    """Compute the existing causal features and persist only the assessment's daily rows."""
    check_silver_bytes(snapshot)
    source = Path(snapshot["source_root"])
    with duckdb.connect() as connection:
        connection.execute(f"SET threads={resources['threads']}")
        connection.execute(f"SET memory_limit='{resources['duckdb_memory_limit']}'")
        paths = []
        for record in snapshot["files"]:
            path = source / record["path"]
            parquet = pq.ParquetFile(path)
            if (
                not parquet.schema_arrow.equals(SILVER_SCHEMA, check_metadata=False)
                or parquet.metadata.num_rows != record["rows"]
            ):
                raise ValueError("Assessment Silver schema or row count mismatch")
            connection.read_parquet(str(path), hive_partitioning=False).create_view(
                "part", replace=True
            )
            wrong = connection.execute(
                "SELECT count(*) FROM part WHERE TX_DATETIME IS NULL OR "
                "CAST(TX_DATETIME AS DATE) != ?::DATE",
                [record["input_filename"][:10]],
            ).fetchone()[0]
            if wrong:
                raise ValueError("Assessment Silver timestamp outside its declared day")
            invalid = connection.execute(
                "SELECT count(*) FROM part WHERE "
                + " OR ".join(f"{name} IS NULL" for name in SILVER_SCHEMA.names)
                + " OR TX_FRAUD NOT IN (0,1) OR TX_FRAUD_SCENARIO NOT BETWEEN 0 AND 3"
                " OR TX_FRAUD != CAST(TX_FRAUD_SCENARIO > 0 AS TINYINT)"
                " OR NOT isfinite(TX_AMOUNT) OR TX_AMOUNT < 0"
                " OR TRANSACTION_ID < 0 OR CUSTOMER_ID < 0 OR TERMINAL_ID < 0"
                " OR epoch_ns(TX_DATETIME) != epoch_ns('2018-04-01'::TIMESTAMP) + TX_TIME_SECONDS * 1000000000"
                " OR TX_TIME_DAYS != floor(TX_TIME_SECONDS / 86400.0)"
            ).fetchone()[0]
            if invalid:
                raise ValueError(
                    "Assessment Silver values violate the existing acceptance contract"
                )
            paths.append(str(path))
        connection.read_parquet(paths, hive_partitioning=False).create_view("transactions")
        if connection.execute(
            "SELECT count(*) - count(DISTINCT TRANSACTION_ID) FROM transactions"
        ).fetchone()[0]:
            raise ValueError("Duplicate transaction identity in assessment context")
        compute_features(connection)
        if check_feature_values(connection, "gold_features"):
            raise ValueError("Assessment features failed the existing Gold contract")
        output.mkdir(parents=True)
        records = []
        for day in window_days(policy["windows"]["assessment"]):
            frame = (
                connection.execute(
                    "SELECT " + ", ".join(DTYPES) + " FROM gold_features "
                    "WHERE CAST(TX_DATETIME AS DATE) = ?::DATE ORDER BY TX_DATETIME, TRANSACTION_ID",
                    [day],
                )
                .fetchdf()
                .astype(DTYPES)
            )
            path = output / f"{day}.parquet"
            frame.to_parquet(path, index=False, compression="zstd", version="2.6")
            records.append({"path": f"features/{path.name}", "date": day, "rows": len(frame)})
    check_silver_bytes(snapshot)
    return records


def load_assessment_features(directory: Path, records: list, policy: dict) -> pd.DataFrame:
    """Verify saved Gold domains, exact daily coverage and unique/mature transaction labels."""
    days = window_days(policy["windows"]["assessment"])
    if [r["date"] for r in records] != days or [r["path"] for r in records] != [
        f"features/{day}.parquet" for day in days
    ]:
        raise ValueError("Saved assessment coverage differs from the declared window")
    frames = []
    with duckdb.connect() as connection:
        for record in records:
            path = directory / record["path"]
            parquet = pq.ParquetFile(path)
            if (
                not parquet.schema_arrow.equals(FEATURE_ARROW_SCHEMA, check_metadata=False)
                or parquet.metadata.num_rows != record["rows"]
                or record["rows"] <= 0
            ):
                raise ValueError("Saved assessment Gold schema or row count mismatch")
            connection.read_parquet(str(path), hive_partitioning=False).create_view(
                "part", replace=True
            )
            if check_feature_values(connection, "part"):
                raise ValueError("Saved assessment features violate Gold semantics")
            frame = (
                connection.execute("SELECT * FROM part ORDER BY TX_DATETIME, TRANSACTION_ID")
                .fetchdf()
                .astype(DTYPES)
            )
            if not (frame.TX_DATETIME.dt.strftime("%Y-%m-%d") == record["date"]).all():
                raise ValueError("Saved assessment timestamps outside their daily partition")
            frames.append(frame)
    result = pd.concat(frames, ignore_index=True)
    if (
        result.TRANSACTION_ID.duplicated().any()
        or not (
            result.LABEL_AVAILABLE_AT
            < pd.Timestamp(policy["time_policy"]["assessment_labels_complete_before"])
        ).all()
    ):
        raise ValueError("Assessment transaction identity or label maturity differs")
    return result
