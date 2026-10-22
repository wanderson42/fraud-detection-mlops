"""Copy a completed monitoring state without fitting, scoring or exposing raw records."""

from contextlib import closing
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys
from urllib.parse import quote

FILES = ("metadata.json", "checkpoint.sqlite", "telemetry.sqlite")
LIMIT = 1536 * 1024 * 1024


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def hashes(directory):
    require(directory.resolve() == directory.absolute(), "State path redirected")
    result = {}
    for name in FILES:
        path = directory / name
        require(path.is_file() and not path.is_symlink(), "State file missing or redirected")
        require(0 < path.stat().st_size <= LIMIT, "State file exceeds copy budget")
        result[name] = {"sha256": sha(path), "bytes": path.stat().st_size}
    require(sum(item["bytes"] for item in result.values()) <= LIMIT, "State exceeds copy budget")
    require(
        not any(
            (directory / (name + suffix)).exists()
            for name in FILES[1:]
            for suffix in ("-wal", "-journal", "-shm")
        ),
        "Live SQLite sidecars present; finish the writer before preparing",
    )
    return result


def aggregate(value):
    return {k: v for k, v in value.items() if k not in {"snapshot_timestamp", "durations"}}


def capture(source, target, no_other_writers):
    from scripts.monitoring.collector import snapshot

    require(no_other_writers, "Declare no other writers for this completed source")
    before = hashes(source)
    observed = snapshot(source)
    require(
        observed["complete"]
        and not observed["session_running"]
        and observed["pending_uncommitted_events"] == 0,
        "Only a completed, stopped monitoring state is supported",
    )
    target.mkdir(mode=0o700)
    shutil.copyfile(source / "metadata.json", target / "metadata.json")
    for name in FILES[1:]:
        with (
            closing(
                sqlite3.connect(
                    "file:" + quote(str((source / name).absolute()), safe="/") + "?mode=ro",
                    uri=True,
                )
            ) as src,
            closing(sqlite3.connect(target / name)) as dst,
        ):
            src.backup(dst)
            require(
                dst.execute("PRAGMA integrity_check").fetchall() == [("ok",)],
                "SQLite backup integrity failed",
            )
    copied = snapshot(target)
    require(aggregate(copied) == aggregate(observed), "Copied monitoring aggregates differ")
    require(hashes(source) == before, "Source changed while preparing; preserve failed copy")
    for name in FILES:
        (target / name).chmod(0o400)
    return {
        "files": hashes(target),
        "source_files": before,
        "expected_summary": aggregate(copied),
        "source_scope": "completed_native_replay_copy_not_live_ingestion_or_new_model_quality",
        "other_writers_absent_operator_declared": True,
    }


def receive(root, expected_manifest, stream):
    require(root.is_dir() and not root.is_symlink(), "State root missing or redirected")
    destination = root / "snapshot"
    require(
        not destination.exists() and not destination.is_symlink(),
        "Snapshot already exists; refuse overwrite",
    )
    staging = root / "incoming"
    require(
        not staging.exists() and not staging.is_symlink(),
        "Incomplete import preserved; inspect it",
    )
    line = stream.readline(32769)
    require(len(line) <= 32768 and line.endswith(b"\n"), "Invalid transfer manifest")
    require(hashlib.sha256(line).hexdigest() == expected_manifest, "Manifest identity differs")
    manifest = json.loads(line)
    require(set(manifest) == set(FILES), "Unexpected state files")
    require(
        all(type(v["bytes"]) is int and 0 < v["bytes"] <= LIMIT for v in manifest.values()),
        "Invalid transfer sizes",
    )
    require(sum(v["bytes"] for v in manifest.values()) <= LIMIT, "Transfer exceeds budget")
    staging.mkdir(mode=0o700)
    for name in FILES:
        remaining = manifest[name]["bytes"]
        digest = hashlib.sha256()
        with (staging / name).open("xb") as output:
            while remaining:
                chunk = stream.read(min(1024 * 1024, remaining))
                require(bool(chunk), "Truncated transfer")
                output.write(chunk)
                digest.update(chunk)
                remaining -= len(chunk)
        require(digest.hexdigest() == manifest[name]["sha256"], "Transferred file hash differs")
        (staging / name).chmod(0o400)
    require(stream.read(1) == b"", "Unexpected trailing transfer bytes")
    require(hashes(staging) == manifest, "Transferred state differs")
    staging.rename(destination)
    return {"status": "state_imported", "files": manifest}


def main():
    action, directory = sys.argv[1:3]
    root = Path(directory)
    if action == "receive":
        result = receive(root, sys.argv[3], sys.stdin.buffer)
    elif action == "verify":
        result = {"status": "state_verified", "files": hashes(root)}
    else:
        raise ValueError("Unknown action")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
