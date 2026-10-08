"""Acquire original Handbook bytes and audit provenance without deserializing pickle."""

from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
import hashlib
from http.client import IncompleteRead
from itertools import pairwise
import json
from pathlib import Path
import re
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from fraud_detection_mlops.artifacts import write_json
from fraud_detection_mlops.config import PROJECT_ROOT

SOURCE_REPOSITORY = "Fraud-Detection-Handbook/simulated-data-raw"
DEFAULT_INVENTORY = PROJECT_ROOT / "references/handbook_source.json"
CHUNK_SIZE = 64 * 1024
RETRYABLE_HTTP_STATUSES = {429, 500, 502, 503, 504}


class BronzeError(Exception):
    """An acquisition, integrity or provenance check failed."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BronzeError(f"Cannot read JSON document {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise BronzeError(f"Expected a JSON object in {path}")
    return value


def load_inventory(path: Path = DEFAULT_INVENTORY) -> dict:
    """Validate the version-controlled inventory before accessing network or output paths."""
    inventory = _read_json(path)
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


def _file_checksums(path: Path, item: dict) -> dict:
    """Validate original bytes against the pinned Git blob and compute SHA-256."""
    sha256 = hashlib.sha256()
    git_hash = hashlib.sha1(f"blob {item['size_bytes']}\0".encode("ascii"))
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK_SIZE), b""):
            size += len(chunk)
            sha256.update(chunk)
            git_hash.update(chunk)
    if size != item["size_bytes"] or git_hash.hexdigest() != item["git_blob_sha1"]:
        raise BronzeError(f"Integrity check failed for {item['filename']}; file was not accepted")
    return {
        "size_bytes": size,
        "sha256": sha256.hexdigest(),
        "git_blob_sha1": git_hash.hexdigest(),
    }


def _source_url(commit: str, item: dict) -> str:
    return f"https://raw.githubusercontent.com/{SOURCE_REPOSITORY}/{commit}/{item['source_path']}"


def _download(url: str, destination: Path, item: dict, timeout: float, attempts: int) -> dict:
    for attempt in range(attempts):
        temporary = None
        try:
            request = Request(url, headers={"User-Agent": "fraud-detection-mlops/0.0.1"})
            with (
                urlopen(request, timeout=timeout) as response,
                tempfile.NamedTemporaryFile(
                    dir=destination.parent,
                    prefix=f".{destination.name}.",
                    suffix=".part",
                    delete=False,
                ) as stream,
            ):
                temporary = Path(stream.name)
                size = 0
                for chunk in iter(lambda: response.read(CHUNK_SIZE), b""):
                    size += len(chunk)
                    if size > item["size_bytes"]:
                        raise BronzeError(f"Response exceeds expected size for {item['filename']}")
                    stream.write(chunk)
            checksums = _file_checksums(temporary, item)
            temporary.replace(destination)
            return checksums
        except (HTTPError, URLError, TimeoutError, ConnectionError, IncompleteRead) as exc:
            retryable = not isinstance(exc, HTTPError) or exc.code in RETRYABLE_HTTP_STATUSES
            if not retryable or attempt + 1 == attempts:
                raise BronzeError(f"Download failed for {item['filename']}: {exc}") from exc
            time.sleep(2**attempt)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    raise BronzeError("Download attempts exhausted")


@contextmanager
def _snapshot_lock(snapshot: Path):
    lock = snapshot / ".extract.lock"
    try:
        with lock.open("x", encoding="utf-8") as stream:
            stream.write(_now() + "\n")
    except FileExistsError as exc:
        raise BronzeError(
            f"Snapshot is locked: {lock}; check for another running extraction"
        ) from exc
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


def _load_manifest(snapshot: Path, inventory: dict) -> dict:
    manifest_path = snapshot / "manifest.json"
    source = {"repository": SOURCE_REPOSITORY, "commit": inventory["source"]["commit"]}
    if not manifest_path.exists():
        return {"schema_version": 1, "source": source, "files": []}
    manifest = _read_json(manifest_path)
    if manifest.get("schema_version") != 1 or manifest.get("source") != source:
        raise BronzeError("Manifest source or schema does not match the pinned inventory")
    files = manifest.get("files")
    if not isinstance(files, list):
        raise BronzeError("Manifest files must be a list")
    expected = {item["filename"]: item for item in inventory["files"]}
    names = set()
    for record in files:
        if not isinstance(record, dict):
            raise BronzeError("Invalid manifest file record")
        name = record.get("filename")
        if not isinstance(name, str) or name not in expected or name in names:
            raise BronzeError("Manifest contains an unknown or duplicate file")
        item = expected[name]
        if any(record.get(key) != item[key] for key in item):
            raise BronzeError(f"Manifest metadata differs from source inventory for {name}")
        if not re.fullmatch(r"[0-9a-f]{64}", str(record.get("sha256", ""))):
            raise BronzeError(f"Invalid SHA-256 in manifest for {name}")
        if record.get("source_url") != _source_url(source["commit"], item):
            raise BronzeError(f"Manifest source URL differs for {name}")
        names.add(name)
    return manifest


def _checkpoint(snapshot: Path, manifest: dict, records: dict, expected_count: int) -> None:
    manifest["files"] = [records[name] for name in sorted(records)]
    manifest["expected_file_count"] = expected_count
    manifest["stored_file_count"] = len(records)
    manifest["complete"] = len(records) == expected_count
    write_json(snapshot / "manifest.json", manifest)


def extract_bronze(
    output_root: Path,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    inventory_path: Path = DEFAULT_INVENTORY,
    timeout: float = 30,
    attempts: int = 3,
    progress: Callable[[str, str], None] | None = None,
) -> dict:
    """Store verified originals, checkpoint each success and audit every extraction run."""
    inventory = load_inventory(inventory_path)
    selected = select_files(inventory, start_date, end_date)
    if not 0 < timeout <= 60 or type(attempts) is not int or not 1 <= attempts <= 5:
        raise BronzeError("Timeout must be in (0, 60] seconds and attempts between 1 and 5")
    commit = inventory["source"]["commit"]
    snapshot = output_root / commit
    snapshot.mkdir(parents=True, exist_ok=True)
    with _snapshot_lock(snapshot):
        audit_path = snapshot / "runs" / f"{uuid4().hex}.json"
        audit = {
            "schema_version": 1,
            "run_id": audit_path.stem,
            "source": {"repository": SOURCE_REPOSITORY, "commit": commit},
            "inventory_sha256": hashlib.sha256(inventory_path.read_bytes()).hexdigest(),
            "started_at": _now(),
            "status": "running",
            "requested_start_date": selected[0]["date"],
            "requested_end_date": selected[-1]["date"],
            "parameters": {"timeout_seconds": timeout, "max_attempts": attempts},
            "files": [],
        }
        write_json(audit_path, audit)
        try:
            manifest = _load_manifest(snapshot, inventory)
            records = {record["filename"]: record for record in manifest["files"]}
            # Recheck prior provenance, including dates outside this extraction interval.
            selected_names = {item["filename"] for item in selected}
            observed_names = {path.name for path in snapshot.glob("*.pkl")}
            if observed_names - (set(records) | selected_names):
                raise BronzeError(
                    "Untracked pickle files found; include their dates or inspect snapshot"
                )
            missing = []
            for name, record in records.items():
                path = snapshot / name
                if not path.exists() and name in selected_names:
                    missing.append(name)
                    continue  # A selected missing file can be downloaded again from the same commit.
                if _file_checksums(path, record)["sha256"] != record["sha256"]:
                    raise BronzeError(f"Manifest SHA-256 differs for {name}")
            for name in missing:
                del records[name]
            if missing:
                _checkpoint(snapshot, manifest, records, len(inventory["files"]))
            for item in selected:
                filename = item["filename"]
                destination = snapshot / filename
                url = _source_url(commit, item)
                status = "skipped" if destination.exists() else "downloaded"
                checksums = (
                    _file_checksums(destination, item)
                    if destination.exists()
                    else _download(url, destination, item, timeout, attempts)
                )
                records[filename] = {
                    **item,
                    **checksums,
                    "source_url": url,
                    "recorded_at": records.get(filename, {}).get("recorded_at", _now()),
                }
                _checkpoint(snapshot, manifest, records, len(inventory["files"]))
                audit["files"].append({"filename": filename, "status": status, **checksums})
                write_json(audit_path, audit)
                if progress is not None:
                    progress(filename, status)
            audit["status"] = "success"
        except (BronzeError, OSError) as exc:
            audit["status"] = "failed"
            audit["error"] = str(exc)
            raise BronzeError(str(exc)) from exc
        except KeyboardInterrupt:
            audit["status"] = "interrupted"
            raise
        finally:
            audit["finished_at"] = _now()
            audit["downloaded_count"] = sum(f["status"] == "downloaded" for f in audit["files"])
            audit["skipped_count"] = sum(f["status"] == "skipped" for f in audit["files"])
            write_json(audit_path, audit)
    return {**audit, "snapshot_path": str(snapshot), "audit_path": str(audit_path)}


def verify_bronze(
    output_root: Path,
    *,
    inventory_path: Path = DEFAULT_INVENTORY,
    require_complete: bool = False,
) -> dict:
    """Check every manifested file offline against source identity and saved SHA-256."""
    inventory = load_inventory(inventory_path)
    snapshot = output_root / inventory["source"]["commit"]
    if not (snapshot / "manifest.json").is_file():
        raise BronzeError(f"No manifest found in {snapshot}")
    with _snapshot_lock(snapshot):
        manifest = _load_manifest(snapshot, inventory)
        if not manifest["files"]:
            raise BronzeError("Manifest has no verified files")
        names = {record["filename"] for record in manifest["files"]}
        if {path.name for path in snapshot.glob("*.pkl")} != names:
            raise BronzeError("Snapshot contains missing or untracked pickle files")
        try:
            for record in manifest["files"]:
                checksums = _file_checksums(snapshot / record["filename"], record)
                if checksums["sha256"] != record["sha256"]:
                    raise BronzeError(f"Manifest SHA-256 differs for {record['filename']}")
        except OSError as exc:
            raise BronzeError(f"Cannot verify stored file: {exc}") from exc
        complete = len(names) == len(inventory["files"])
        if (
            manifest.get("stored_file_count") != len(names)
            or manifest.get("expected_file_count") != len(inventory["files"])
            or manifest.get("complete") is not complete
        ):
            raise BronzeError("Manifest coverage metadata is inconsistent")
        if require_complete and not complete:
            raise BronzeError(
                "Snapshot is incomplete; extract all dates before requiring completeness"
            )
    return {
        "snapshot_path": str(snapshot),
        "verified_file_count": len(names),
        "expected_file_count": len(inventory["files"]),
        "complete": complete,
    }
