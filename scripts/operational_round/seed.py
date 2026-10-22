"""Seed only a hash-pinned release qualified by the canonical round."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil

FILES = {"manifest.json", "pipeline.skops", "smoke.json"}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def seed(source, destination, expected):
    if (
        source.is_symlink()
        or destination.is_symlink()
        or {p.name for p in source.iterdir()} != FILES
    ):
        raise ValueError("Exactly three regular release files required")
    for name in FILES:
        path = source / name
        if (
            path.is_symlink()
            or not path.is_file()
            or not 0 < path.stat().st_size <= 16 * 1024 * 1024
        ):
            raise ValueError("Invalid release file")
    manifest = json.loads((source / "manifest.json").read_bytes())
    if (
        digest(source / "manifest.json") != expected
        or manifest["evidence_kind"] != "verified_reference"
        or manifest["model_sha256"] != digest(source / "pipeline.skops")
        or manifest["smoke_sha256"] != digest(source / "smoke.json")
        or manifest["source"]["registered_model"] != "fraud-detector"
        or not manifest["source"].get("round_id")
    ):
        raise ValueError("Canonical model release identity differs")
    destination.mkdir(exist_ok=True)
    if any(destination.iterdir()):
        raise ValueError("Do not replace an existing release")
    for name in sorted(FILES):
        shutil.copyfile(source / name, destination / name)
        (destination / name).chmod(0o444)
    if {n: digest(source / n) for n in FILES} != {n: digest(destination / n) for n in FILES}:
        raise ValueError("Seeded bytes differ")
    return {"status": "canonical_release_seeded", "model_sha256": manifest["model_sha256"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("slot", choices=["blue", "green"])
    parser.add_argument("--manifest-sha256", required=True)
    args = parser.parse_args()
    print(json.dumps(seed(Path("/seed") / args.slot, Path("/model"), args.manifest_sha256)))


if __name__ == "__main__":
    main()
