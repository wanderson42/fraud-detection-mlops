"""Load the initial temporal evaluation protocol without reading transaction labels."""

from datetime import date, timedelta
import json
from pathlib import Path

from fraud_detection_mlops.profiling import PROJECT_ROOT

DEFAULT_PROTOCOL = PROJECT_ROOT / "references/temporal_protocol_v1.json"


class ProtocolError(ValueError):
    """An evaluation protocol cannot be used with the selected source."""


def load_protocol(path: Path, inventory: dict) -> dict:
    """Validate source, chronology, feedback delay and complete date coverage."""
    protocol = json.loads(path.read_text(encoding="utf-8"))
    expected_keys = {
        "schema_version",
        "version",
        "source_commit",
        "label_delay_days",
        "windows",
        "eda_window",
        "refit_before_test",
        "evaluation_population",
        "selection_metric",
        "secondary_metrics",
        "excluded_predictors",
        "random_seed",
    }
    if (
        set(protocol) != expected_keys
        or protocol["schema_version"] != 1
        or protocol["version"] != "temporal_v1"
        or protocol["source_commit"] != inventory["source"]["commit"]
        or type(protocol["label_delay_days"]) is not int
        or protocol["label_delay_days"] != 7
        or protocol["eda_window"] != "train"
        or protocol["refit_before_test"] is not False
        or protocol["evaluation_population"] != "all_transactions_without_customer_blocking"
        or protocol["selection_metric"] != "average_precision"
        or protocol["secondary_metrics"] != ["roc_auc", "daily_customer_precision_at_100"]
        or protocol["excluded_predictors"]
        != ["TRANSACTION_ID", "CUSTOMER_ID", "TERMINAL_ID", "TX_FRAUD", "TX_FRAUD_SCENARIO"]
        or protocol["random_seed"] != 42
        or not isinstance(protocol["windows"], dict)
        or set(protocol["windows"]) != {"train", "validation", "test"}
    ):
        raise ProtocolError("Unsupported temporal protocol or source mismatch")
    available = {date.fromisoformat(item["date"]) for item in inventory["files"]}
    previous_end = None
    for name in ("train", "validation", "test"):
        window = protocol["windows"][name]
        if not isinstance(window, dict) or set(window) != {"start", "end_exclusive"}:
            raise ProtocolError(f"Invalid window: {name}")
        start, end = (date.fromisoformat(window[key]) for key in ("start", "end_exclusive"))
        if start.isoformat() != window["start"] or end.isoformat() != window["end_exclusive"]:
            raise ProtocolError(f"Use YYYY-MM-DD dates: {name}")
        if start >= end or (
            previous_end is not None
            and start < previous_end + timedelta(days=protocol["label_delay_days"])
        ):
            raise ProtocolError(f"Invalid chronology or feedback delay: {name}")
        required = {start + timedelta(days=i) for i in range((end - start).days)}
        if not required.issubset(available):
            raise ProtocolError(f"Source does not cover the complete {name} window")
        previous_end = end
    return protocol
