"""Validate explicit durable destinations before operational work starts.

This is a path check, not an OS sandbox or protection against concurrent link swaps.
Internal APIs retain their explicit paths for tests and controlled integrations.
"""

from pathlib import Path

from fraud_detection_mlops.config import DATA_DIR

AREAS = {"examples", "serving", "tracking", "replay", "reports", "storage"}


def managed_destination(path: Path, area: str) -> Path:
    """Require a child of data/<area>, without creating directories or following links."""
    if area not in AREAS:
        raise ValueError("Unknown persistence area")
    destination = Path(path).absolute()
    base = DATA_DIR.absolute() / area
    if ".." in destination.parts or destination == base or not destination.is_relative_to(base):
        raise ValueError(f"Choose a destination inside {base}; received {destination}")
    for item in [destination, *destination.parents]:
        if item.is_symlink():
            raise ValueError(f"Symbolic links are not accepted in output paths: {item}")
        if item.exists() and not item.is_dir() and (item != destination or not item.is_file()):
            raise ValueError(f"Invalid output path component: {item}")
    return destination
