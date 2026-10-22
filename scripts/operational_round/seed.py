"""Install or verify the same pinned release; resume atomic copies after interruption."""

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat

FILES = {"manifest.json", "pipeline.skops", "smoke.json"}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def regular(path):
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= 16 * 1024 * 1024:
        raise ValueError("Invalid release file")


@contextmanager
def destination_lock(destination):
    # Lock the directory inode: no marker is added to the model's three-file set.
    fd = os.open(destination, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield fd
    finally:
        os.close(fd)


def seed(source, destination, expected):
    source, destination = Path(source).absolute(), Path(destination).absolute()
    if (
        source.resolve() != source
        or destination.resolve() != destination
        or not source.is_dir()
        or {p.name for p in source.iterdir()} != FILES
    ):
        raise ValueError("Exactly three regular release files required")
    for name in FILES:
        regular(source / name)
    hashes = {name: digest(source / name) for name in FILES}
    manifest = json.loads((source / "manifest.json").read_bytes())
    if (
        hashes["manifest.json"] != expected
        or manifest["evidence_kind"] != "verified_reference"
        or manifest["model_sha256"] != hashes["pipeline.skops"]
        or manifest["smoke_sha256"] != hashes["smoke.json"]
        or manifest["source"]["registered_model"] != "fraud-detector"
        or not manifest["source"].get("round_id")
    ):
        raise ValueError("Canonical model release identity differs")
    destination.mkdir(exist_ok=True)
    if not destination.is_dir():
        raise ValueError("Release destination must be a directory")
    pending = {name: f".seed-{expected}-{name}.tmp" for name in FILES}
    with destination_lock(destination) as directory_fd:
        entries = {p.name for p in destination.iterdir()}
        if entries - FILES - set(pending.values()):
            raise ValueError("Unexpected release destination contents; preserve them")
        # Validate ALL existing final files before deleting temporaries or copying anything.
        for name in entries & FILES:
            regular(destination / name)
            if digest(destination / name) != hashes[name]:
                raise ValueError("Existing release bytes differ; do not replace them")
        for name in entries & set(pending.values()):
            path = destination / name
            if path.is_symlink() or not stat.S_ISREG(path.lstat().st_mode):
                raise ValueError("Invalid interrupted seed temporary; preserve it")
        written = 0
        for name in sorted(FILES):
            target = destination / name
            temporary = destination / pending[name]
            if temporary.exists():
                temporary.unlink()  # Only this manifest's validated, reserved temporary name.
            if target.exists():
                continue
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as out, (source / name).open("rb") as inp:
                shutil.copyfileobj(inp, out)
                out.flush()
                os.fchmod(out.fileno(), 0o444)
                os.fsync(out.fileno())
            if digest(temporary) != hashes[name]:
                raise ValueError("Interrupted copy differs from the pinned source")
            # Create-if-absent, unlike replace(): a divergent final file is never overwritten.
            os.link(temporary, target, follow_symlinks=False)
            temporary.unlink()
            os.fsync(directory_fd)
            written += 1
        if {p.name for p in destination.iterdir()} != FILES or any(
            digest(destination / n) != hashes[n] or digest(source / n) != hashes[n] for n in FILES
        ):
            raise ValueError("Seeded bytes differ")
        os.fsync(directory_fd)
    return {
        "status": "canonical_release_seeded" if written else "canonical_release_reused",
        "model_sha256": manifest["model_sha256"],
        "files_written": written,
        "files_reused": len(FILES) - written,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("slot", choices=["blue", "green"])
    parser.add_argument("--manifest-sha256", required=True)
    args = parser.parse_args()
    print(json.dumps(seed(Path("/seed") / args.slot, Path("/model"), args.manifest_sha256)))


if __name__ == "__main__":
    main()
