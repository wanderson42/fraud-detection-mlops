"""Versioned Silver acceptance policy; loading performs no conversion."""

import json
from pathlib import Path

from fraud_detection_mlops.config import PROJECT_ROOT
from fraud_detection_mlops.data.contracts.dataset_errors import SilverError
from fraud_detection_mlops.data.contracts.transaction_schema import DTYPES, INFORMATIONAL

DEFAULT_CONTRACT = PROJECT_ROOT / "references/silver_contract_v1.json"


def load_silver_contract(path: Path) -> dict:
    contract = json.loads(path.read_text(encoding="utf-8"))
    expected = {
        "schema_version": 1,
        "version": "silver_v1",
        "temporal_origin": "2018-04-01",
        "timestamp_timezone": None,
        "columns": DTYPES,
        "amount_policy": "finite_nonnegative_preserve_zero_and_extremes",
        "row_policy": "preserve_all_rows_and_order_fail_on_invalid_input",
        "informational_checks": sorted(INFORMATIONAL),
        "excluded_predictors": ["TX_FRAUD", "TX_FRAUD_SCENARIO"],
    }
    if contract != expected:
        raise SilverError(
            "Unsupported contract; changing policy requires a new implementation/version"
        )
    return contract


def accept_bronze_profile(profile: dict) -> dict:
    summary = profile["summary"]
    failures = {
        name: count
        for name, count in summary["checks"].items()
        if name not in INFORMATIONAL and count
    }
    failures.update(
        {name: count for name, count in summary["invalid_int64_by_column"].items() if count}
    )
    if summary["empty_partitions"] or failures:
        raise SilverError(
            f"Bronze fails Silver acceptance: {failures}; "
            f"empty_partitions={summary['empty_partitions']}"
        )
    if (
        summary["verified_files"] != summary["profiled_files"]
        or summary["distinct_valid_transaction_ids"] != summary["rows"]
    ):
        raise SilverError("Incomplete profile or nonunique transaction IDs")
    return {
        "rows": summary["rows"],
        "distinct_transaction_ids": summary["distinct_valid_transaction_ids"],
        "fraud_count": summary["fraud_count"],
        "genuine_count": summary["genuine_count"],
        "zero_amounts": summary["checks"]["zero_amounts"],
    }
