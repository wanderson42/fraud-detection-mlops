"""Copy exactly one pinned synthetic release from an immutable carrier image to emptyDir."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil

FILES = {"manifest.json", "pipeline.skops", "smoke.json"}
MAX_FILE_BYTES = 16 * 1024 * 1024


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def seed(source, destination, expected):
    if not re.fullmatch(r"[a-f0-9]{64}", expected):
        raise ValueError("Pin the release manifest SHA-256")
    if source.resolve() != source.absolute() or destination.resolve() != destination.absolute():
        raise ValueError("Release paths cannot be redirected")
    if {p.name for p in source.iterdir()} != FILES:
        raise ValueError("Exactly three release files required")
    for name in FILES:
        path = source / name
        if (
            path.is_symlink()
            or not path.is_file()
            or not 0 < path.stat().st_size <= MAX_FILE_BYTES
        ):
            raise ValueError("Release contains a link, missing file or excessive size")
    if sha(source / "manifest.json") != expected:
        raise ValueError("Manifest pin differs")
    manifest = json.loads((source / "manifest.json").read_text())
    if (
        manifest.get("evidence_kind") != "synthetic_smoke"
        or manifest.get("model_file") != "pipeline.skops"
        or manifest.get("smoke_file") != "smoke.json"
        or manifest.get("model_sha256") != sha(source / "pipeline.skops")
        or manifest.get("smoke_sha256") != sha(source / "smoke.json")
    ):
        raise ValueError("Synthetic release identity differs")
    destination.mkdir(exist_ok=True)
    if any(destination.iterdir()):
        raise ValueError("Do not replace existing model files")
    for name in sorted(FILES):
        shutil.copyfile(source / name, destination / name)
        (destination / name).chmod(0o444)
    if {name: sha(source / name) for name in FILES} != {
        name: sha(destination / name) for name in FILES
    }:
        raise ValueError("Copied bytes differ")
    return {
        "status": "release_seeded",
        "manifest_sha256": expected,
        "serving_release_id": manifest["serving_release_id"],
        "model_sha256": manifest["model_sha256"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("slot", choices=["blue", "green"])
    parser.add_argument("--manifest-sha256", required=True)
    args = parser.parse_args()
    print(json.dumps(seed(Path("/seed") / args.slot, Path("/model"), args.manifest_sha256)))


if __name__ == "__main__":
    main()
