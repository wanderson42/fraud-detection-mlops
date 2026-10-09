"""Pinned Handbook source identities and authorized date selection."""

from datetime import date, timedelta
from itertools import pairwise
import json
from pathlib import Path
import re

from fraud_detection_mlops.config import PROJECT_ROOT
from fraud_detection_mlops.data.contracts.dataset_errors import BronzeError

SOURCE_REPOSITORY = "Fraud-Detection-Handbook/simulated-data-raw"
DEFAULT_INVENTORY = PROJECT_ROOT / "references/handbook_source.json"


def read_source_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BronzeError(f"Cannot read JSON document {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise BronzeError(f"Expected a JSON object in {path}")
    return value


def load_inventory(path: Path = DEFAULT_INVENTORY) -> dict:
    """Validate the version-controlled inventory before accessing network or output paths."""
    inventory = read_source_json(path)
    try:
        source = inventory["source"]
        if inventory["schema_version"] != 1 or source["repository"] != SOURCE_REPOSITORY:
            raise ValueError("unsupported inventory schema or repository")
        if not re.fullmatch(r"[0-9a-f]{40}", source["commit"]):
            raise ValueError("source commit must be a full hexadecimal SHA")
        files = inventory["files"]
        if not isinstance(files, list) or not files:
            raise ValueError("empty source inventory")
        dates = []
        for item in files:
            day = date.fromisoformat(item["date"])
            filename = f"{day.isoformat()}.pkl"
            if item["filename"] != filename or item["source_path"] != f"data/{filename}":
                raise ValueError("file paths must match the transaction date")
            if type(item["size_bytes"]) is not int or item["size_bytes"] <= 0:
                raise ValueError("file size must be a positive integer")
            if not re.fullmatch(r"[0-9a-f]{40}", item["git_blob_sha1"]):
                raise ValueError("invalid Git blob SHA")
            dates.append(day)
        if dates != sorted(set(dates)):
            raise ValueError("dates must be unique and ordered")
        if any(right - left != timedelta(days=1) for left, right in pairwise(dates)):
            raise ValueError("source inventory has missing daily partitions")
    except (KeyError, TypeError, ValueError) as exc:
        raise BronzeError(f"Invalid source inventory {path}: {exc}") from exc
    return inventory


def select_files(inventory: dict, start_date: str | None, end_date: str | None) -> list[dict]:
    files = inventory["files"]
    first, last = date.fromisoformat(files[0]["date"]), date.fromisoformat(files[-1]["date"])
    try:
        start = date.fromisoformat(start_date) if start_date is not None else first
        end = date.fromisoformat(end_date) if end_date is not None else last
    except ValueError as exc:
        raise BronzeError("Dates must use ISO format YYYY-MM-DD") from exc
    if start > end or start < first or end > last:
        raise BronzeError(f"Date range must be ordered and contained in {first} through {last}")
    return [item for item in files if start.isoformat() <= item["date"] <= end.isoformat()]
