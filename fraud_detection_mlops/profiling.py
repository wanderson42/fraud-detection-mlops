"""Describe verified Bronze partitions before defining the Silver contract."""

from datetime import UTC, datetime
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Annotated

import pandas as pd
import typer

from fraud_detection_mlops.bronze import (
    DEFAULT_INVENTORY,
    BronzeError,
    load_inventory,
    verify_bronze,
)
from fraud_detection_mlops.config import PROJECT_ROOT

DEFAULT_BRONZE = PROJECT_ROOT / "data/raw/handbook"
INTEGER_COLUMNS = (
    "TRANSACTION_ID",
    "CUSTOMER_ID",
    "TERMINAL_ID",
    "TX_TIME_SECONDS",
    "TX_TIME_DAYS",
    "TX_FRAUD",
    "TX_FRAUD_SCENARIO",
)
EXPECTED_COLUMNS = {*INTEGER_COLUMNS, "TX_DATETIME", "TX_AMOUNT"}
app = typer.Typer()


def is_int64(value) -> bool:
    return (
        pd.notna(value)
        and math.isfinite(value)
        and value == int(value)
        and -(2**63) <= int(value) < 2**63
    )


def profile_dataframe(frame: pd.DataFrame, day: str, origin: str, seen_ids: set) -> dict:
    """Report observations without modifying, filtering or imputing the input."""
    if not isinstance(frame, pd.DataFrame) or set(frame.columns) != EXPECTED_COLUMNS:
        raise ValueError(f"Unexpected columns or object type in partition {day}")
    if frame.columns.duplicated().any():
        raise ValueError(f"Duplicate column names in partition {day}")
    numbers = {column: pd.to_numeric(frame[column], errors="coerce") for column in INTEGER_COLUMNS}
    integer_valid = {column: values.map(is_int64) for column, values in numbers.items()}
    dates = pd.to_datetime(frame["TX_DATETIME"], errors="coerce")
    if dates.dt.tz is not None:
        raise ValueError(f"Timezone-aware timestamps require contract review: {day}")
    seconds = (dates - pd.Timestamp(origin)).dt.total_seconds()
    amount = pd.to_numeric(frame["TX_AMOUNT"], errors="coerce")
    amount_valid = amount.map(lambda value: pd.notna(value) and math.isfinite(value))
    ids = numbers["TRANSACTION_ID"][integer_valid["TRANSACTION_ID"]].map(int)
    fraud = numbers["TX_FRAUD"]
    scenario = numbers["TX_FRAUD_SCENARIO"]
    valid_labels = fraud.isin([0, 1]) & scenario.isin([0, 1, 2, 3])
    checks = {
        "null_values": int(frame.isna().sum().sum()),
        "duplicate_rows": int(frame.duplicated().sum()),
        "duplicate_tx_ids_within_partition": int(ids.duplicated().sum()),
        "duplicate_tx_ids_across_previous_partitions": int(ids.isin(seen_ids).sum()),
        "negative_identifier_values": sum(
            int(numbers[column].lt(0).sum())
            for column in ["TRANSACTION_ID", "CUSTOMER_ID", "TERMINAL_ID"]
        ),
        "invalid_timestamps": int(dates.isna().sum()),
        "timestamps_outside_partition": int(
            (dates.notna() & dates.dt.normalize().ne(pd.Timestamp(day))).sum()
        ),
        "timestamp_order_decreases": int(dates.diff().dt.total_seconds().lt(0).sum()),
        "seconds_datetime_mismatch": int(
            (dates.notna() & numbers["TX_TIME_SECONDS"].ne(seconds)).sum()
        ),
        "days_datetime_mismatch": int(
            (dates.notna() & numbers["TX_TIME_DAYS"].ne(seconds // 86400)).sum()
        ),
        "invalid_fraud_labels": int((~fraud.isin([0, 1])).sum()),
        "invalid_scenario_labels": int((~scenario.isin([0, 1, 2, 3])).sum()),
        "fraud_scenario_mismatch": int(
            (valid_labels & fraud.ne(scenario.gt(0).astype(int))).sum()
        ),
        "invalid_or_nonfinite_amounts": int((~amount_valid).sum()),
        "negative_amounts": int((amount_valid & amount.lt(0)).sum()),
        "zero_amounts": int((amount_valid & amount.eq(0)).sum()),
    }
    seen_ids.update(ids.tolist())
    valid_dates = dates.dropna()
    return {
        "date": day,
        "rows": len(frame),
        "raw_dtypes": {column: str(dtype) for column, dtype in frame.dtypes.items()},
        "nulls_by_column": {column: int(count) for column, count in frame.isna().sum().items()},
        "invalid_int64_by_column": {
            column: int((~valid).sum()) for column, valid in integer_valid.items()
        },
        "scalar_types_by_integer_column": {
            column: {
                str(name): int(count)
                for name, count in frame[column]
                .map(lambda value: type(value).__name__)
                .value_counts()
                .items()
            }
            for column in INTEGER_COLUMNS
        },
        "fraud_count": int(fraud.eq(1).sum()),
        "genuine_count": int(fraud.eq(0).sum()),
        "datetime_min": valid_dates.min().isoformat() if len(valid_dates) else None,
        "datetime_max": valid_dates.max().isoformat() if len(valid_dates) else None,
        "checks": checks,
    }


def profile_bronze(bronze_root: Path, inventory_path: Path = DEFAULT_INVENTORY) -> dict:
    inventory = load_inventory(inventory_path)
    verified = verify_bronze(bronze_root, inventory_path=inventory_path, require_complete=True)
    snapshot = Path(verified["snapshot_path"])
    seen_ids, partitions = set(), []
    origin = inventory["files"][0]["date"]
    for item in inventory["files"]:
        frame = pd.read_pickle(snapshot / item["filename"])
        partitions.append(profile_dataframe(frame, item["date"], origin, seen_ids))
    rows = sum(partition["rows"] for partition in partitions)
    checks = {
        name: sum(partition["checks"][name] for partition in partitions)
        for name in partitions[0]["checks"]
    }
    return {
        "schema_version": 1,
        "kind": "diagnostic_profile_not_silver_acceptance",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "source": inventory["source"],
        "inventory_sha256": hashlib.sha256(inventory_path.read_bytes()).hexdigest(),
        "profiler_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "environment": {"python": sys.version.split()[0], "pandas": pd.__version__},
        "summary": {
            "verified_files": verified["verified_file_count"],
            "profiled_files": len(partitions),
            "empty_partitions": sum(partition["rows"] == 0 for partition in partitions),
            "rows": rows,
            "distinct_valid_transaction_ids": len(seen_ids),
            "fraud_count": sum(partition["fraud_count"] for partition in partitions),
            "genuine_count": sum(partition["genuine_count"] for partition in partitions),
            "checks": checks,
            "invalid_int64_by_column": {
                column: sum(
                    partition["invalid_int64_by_column"][column] for partition in partitions
                )
                for column in INTEGER_COLUMNS
            },
            "raw_dtype_variants": {
                column: sorted({partition["raw_dtypes"][column] for partition in partitions})
                for column in sorted(EXPECTED_COLUMNS)
            },
            "integer_scalar_type_variants": {
                column: sorted(
                    {
                        name
                        for partition in partitions
                        for name in partition["scalar_types_by_integer_column"][column]
                    }
                )
                for column in INTEGER_COLUMNS
            },
        },
        "partitions": partitions,
    }


@app.command()
def main(
    bronze_root: Annotated[
        Path, typer.Option(help="Parent of the verified Bronze snapshot.")
    ] = DEFAULT_BRONZE,
    inventory: Annotated[Path, typer.Option(help="Pinned source inventory.")] = DEFAULT_INVENTORY,
    report_path: Annotated[
        Path | None,
        typer.Option(help="Output JSON; defaults to data/interim/handbook/<commit>/profile.json."),
    ] = None,
):
    """Inspect every partition offline and save a diagnostic report."""
    try:
        report = profile_bronze(bronze_root, inventory)
        destination = (
            report_path
            or PROJECT_ROOT / "data/interim/handbook" / report["source"]["commit"] / "profile.json"
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    except (BronzeError, OSError, ValueError, TypeError) as exc:
        typer.echo(f"Profiling failed: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    typer.echo(f"Report: {destination}")
    typer.echo("Diagnostic only: interpret findings before defining Silver acceptance rules.")


if __name__ == "__main__":
    app()
