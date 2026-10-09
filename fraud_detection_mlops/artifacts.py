"""Small file operations shared by ingestion and modeling."""

from collections.abc import Iterable
import hashlib
import json
from pathlib import Path
import tempfile


def sha256(path: Path) -> str:
    """Hash files in bounded memory, including large Parquet partitions."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_fingerprint(project_root: Path, paths: Iterable[Path]) -> str:
    """Bind all selected source files to relative paths and bytes, independent of checkout."""
    root = project_root.resolve()
    records = {str(Path(path).resolve().relative_to(root)): sha256(Path(path)) for path in paths}
    if not records:
        raise ValueError("Source fingerprint requires at least one file")
    payload = json.dumps(records, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def write_json(path: Path, value: dict, *, overwrite: bool = True) -> None:
    """Publish JSON atomically; optionally refuse to replace an existing document."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False
        ) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
        if overwrite:
            temporary.replace(path)
        else:
            path.hardlink_to(temporary)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
