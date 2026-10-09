"""Versioned Gold feature contract; loading does not build a dataset."""

import json
from pathlib import Path

from fraud_detection_mlops.config import PROJECT_ROOT
from fraud_detection_mlops.data.contracts.dataset_errors import GoldError
from fraud_detection_mlops.features.feature_schema import (
    FEATURE_DTYPES,
    LABEL_DELAY_DAYS,
    METADATA_DTYPES,
    WINDOW_DAYS,
)

DEFAULT_GOLD_CONTRACT = PROJECT_ROOT / "references/gold_contract_v1.json"


def load_gold_contract(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    expected = {
        "schema_version": 1,
        "version": "gold_v1",
        "source_layer": "silver_v1",
        "protocol_version": "temporal_v1",
        "window_days": list(WINDOW_DAYS),
        "label_delay_days": LABEL_DELAY_DAYS,
        "event_history": "[t-window,t)",
        "known_label_history": "[t-delay-window,t-delay)",
        "timestamp_ties": "exclude_all_events_at_current_timestamp",
        "cold_start": "zero_with_support_counts_and_amount_ratio_valid_flag",
        "row_policy": "preserve_every_transaction_in_protocol_windows",
        "metadata_columns": METADATA_DTYPES,
        "feature_columns": FEATURE_DTYPES,
        "excluded_column": "TX_FRAUD_SCENARIO",
        "fitted_transformations": [],
    }
    if value != expected:
        raise GoldError("Unsupported Gold contract; policy changes require a new version")
    return value
