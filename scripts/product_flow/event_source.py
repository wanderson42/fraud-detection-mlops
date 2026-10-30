"""Prepare a bounded, model-free session event source from verified Silver partitions."""

import argparse
from contextlib import closing
from datetime import date, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
from tempfile import TemporaryDirectory

from fraud_detection_mlops.config import PROJECT_ROOT
from fraud_detection_mlops.data.datasets.native_replay_dataset import (
    assessment_projection,
    readonly_connection,
    replay_snapshot,
    stream_events,
    validate_event_stream,
    write_event_stream,
)
from fraud_detection_mlops.data.datasets.reference_assessment_dataset import (
    build_assessment_features,
    check_silver_bytes,
)
from fraud_detection_mlops.storage.managed_paths import managed_destination
from fraud_detection_mlops.streaming.contracts.transaction_events import (
    TransactionEvent,
    canonical_event,
    event_digest,
)
from fraud_detection_mlops.streaming.replay.native_causal_replay import job_lock

SCHEMA = "fraud_session_event_source_v1"
USER_VERSION = 3  # Deliberately distinct from durable replay checkpoints (version 2).
CODE = "scripts/product_flow/event_source.py"


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def protocol(root, start, end):
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if not 1 <= (last - first).days <= 7:
        raise ValueError("Declare a scoring window of one to seven complete days")
    inventory = json.loads((root / "references/handbook_source.json").read_bytes())
    return {
        "source_commit": inventory["source"]["commit"],
        "windows": {
            "context": {"start": str(first - timedelta(days=14)), "end_exclusive": start},
            "scored_replay": {"start": start, "end_exclusive": end},
            "feedback_drain_end_exclusive": str(last + timedelta(days=7)) + "T00:00:00",
        },
    }


def write_projection(stream, target, identity):
    """Persist only events and their selection indexes, never decisions or scores."""
    with closing(sqlite3.connect(target)) as conn, conn:
        conn.executescript(
            "PRAGMA user_version=3;"
            "CREATE TABLE identity(payload TEXT NOT NULL);"
            "CREATE TABLE events(event_id TEXT PRIMARY KEY,digest TEXT NOT NULL,payload TEXT NOT NULL);"
            "CREATE TABLE transactions(transaction_id INTEGER PRIMARY KEY,tx_ns INTEGER NOT NULL);"
            "CREATE TABLE feedback(transaction_id INTEGER PRIMARY KEY,available_ns INTEGER NOT NULL);"
        )
        conn.execute("INSERT INTO identity VALUES(?)", (json.dumps(identity, sort_keys=True),))
        for event in stream_events(stream):
            conn.execute(
                "INSERT INTO events VALUES(?,?,?)",
                (event.event_id, event_digest(event), canonical_event(event)),
            )
            table, stamp = (
                ("transactions", event.event_clock_ns)
                if isinstance(event, TransactionEvent)
                else ("feedback", event.event_clock_ns)
            )
            conn.execute(f"INSERT INTO {table} VALUES(?,?)", (event.transaction_id, stamp))


def plan(silver_root, start, end, root=PROJECT_ROOT):
    snapshot = replay_snapshot(Path(silver_root), protocol(Path(root), start, end), Path(root))
    return {
        "status": "session_event_source_metadata_planned",
        "silver_directory": snapshot["source_root"],
        "partitions": len(snapshot["files"]),
        "transactions_including_context": sum(r["rows"] for r in snapshot["files"]),
        "silver_manifest_sha256": snapshot["manifest_sha256"],
        "partition_payloads_verified": False,
        "scoring_executed": False,
        "fit_executed": False,
    }


def verify(directory, expected_manifest=None):
    directory = managed_destination(directory, "replay")
    manifest_path = managed_destination(directory / "source.json", "replay")
    checkpoint = managed_destination(directory / "checkpoint.sqlite", "replay")
    if any(
        Path(str(checkpoint) + s).exists() or Path(str(checkpoint) + s).is_symlink()
        for s in ("-wal", "-shm", "-journal")
    ):
        raise ValueError("Event source must be closed without SQLite sidecars")
    if expected_manifest is not None and sha(manifest_path) != expected_manifest:
        raise ValueError("Event source manifest differs")
    value = json.loads(manifest_path.read_bytes())
    if (
        value["schema"] != SCHEMA
        or sha(checkpoint) != value["checkpoint_sha256"]
        or value["scoring_executed"] is not False
        or value["fit_executed"] is not False
    ):
        raise ValueError("Event source identity or bytes differ")
    with closing(readonly_connection(checkpoint)) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
        if (
            tables != {"identity", "events", "transactions", "feedback"}
            or conn.execute("PRAGMA user_version").fetchone()[0] != USER_VERSION
            or conn.execute("PRAGMA quick_check").fetchone()[0] != "ok"
            or conn.execute("SELECT count(*) FROM identity").fetchone()[0] != 1
            or json.loads(conn.execute("SELECT payload FROM identity").fetchone()[0])
            != value["identity"]
            or value["identity"]["schema"] != SCHEMA
        ):
            raise ValueError("Event source schema differs")
        counts = {
            k: conn.execute(f"SELECT count(*) FROM {k}").fetchone()[0]
            for k in ("events", "transactions", "feedback")
        }
        if (
            counts != value["counts"]
            or counts["events"] != 2 * counts["transactions"]
            or counts["feedback"] != counts["transactions"]
        ):
            raise ValueError("Event source counts differ")
    return value


def prepare(silver_root, source_id, start, end, root=PROJECT_ROOT):
    root = Path(root).absolute()
    parent = managed_destination(root / "data/replay/session-sources", "replay")
    with job_lock(parent):
        return _prepare(silver_root, source_id, start, end, root)


def _prepare(silver_root, source_id, start, end, root):
    root = Path(root).absolute()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", source_id):
        raise ValueError("Invalid source ID")
    output = managed_destination(root / "data/replay/session-sources" / source_id, "replay")
    if output.exists():
        raise ValueError("Source destination already exists; verify it instead of overwriting")
    policy = protocol(root, start, end)
    names = [
        CODE,
        "poetry.lock",
        "references/handbook_source.json",
        "references/silver_contract_v1.json",
        "references/gold_contract_v1.json",
    ]
    names += [
        p.relative_to(root).as_posix() for p in (root / "fraud_detection_mlops").rglob("*.py")
    ]
    pins = {name: sha(root / name) for name in names}
    for name, digest in pins.items():
        result = subprocess.run(
            ["git", "show", "HEAD:" + name], cwd=root, capture_output=True, check=False
        )
        if result.returncode or hashlib.sha256(result.stdout).hexdigest() != digest:
            raise ValueError("Commit reviewed event source inputs before prepare: " + name)
    snapshot = replay_snapshot(Path(silver_root), policy, root)
    if len(snapshot["files"]) > 21 or sum(r["rows"] for r in snapshot["files"]) > 500_000:
        raise ValueError("Event source exceeds the 21 day / 500000 transaction budget")
    output.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(output.parent).free < 1024**3:
        raise ValueError("At least 1 GiB free disk is required for bounded source preparation")
    with TemporaryDirectory(prefix=".source-", dir=output.parent) as temporary:
        stage = Path(temporary) / "published"
        stage.mkdir(mode=0o700)
        # Reuse independent Silver schema/domain/duplicate and causal Gold checks;
        # transient features are not scored or published as a model evaluation.
        build_assessment_features(
            snapshot,
            assessment_projection(policy),
            Path(temporary) / "features",
            {"threads": 2, "duckdb_memory_limit": "1GB"},
        )
        stream = Path(temporary) / "events.sqlite"
        counts = write_event_stream(snapshot, policy, stream)
        validate_event_stream(stream, policy, counts)
        identity = {
            "schema": SCHEMA,
            "source_id": source_id,
            "protocol": policy,
            "silver_snapshot": snapshot,
            "code_pins": pins,
            "implementation_commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=root, text=True
            ).strip(),
        }
        checkpoint = stage / "checkpoint.sqlite"
        write_projection(stream, checkpoint, identity)
        os.chmod(checkpoint, 0o600)
        value = {
            "schema": SCHEMA,
            "identity": identity,
            "counts": counts,
            "checkpoint_sha256": sha(checkpoint),
            "scoring_executed": False,
            "fit_executed": False,
            "source_modified": False,
        }
        manifest = stage / "source.json"
        manifest.write_text(json.dumps(value, indent=2) + "\n")
        os.chmod(manifest, 0o600)
        verify(stage)
        check_silver_bytes(snapshot)
        if pins != {name: sha(root / name) for name in pins}:
            raise ValueError("Event source inputs changed during preparation")
        # Destination must still be absent; directory is never adopted or replaced.
        if output.exists():
            raise ValueError("Source destination appeared during preparation")
        stage.rename(output)
    return {
        "status": "session_event_source_prepared",
        "directory": str(output),
        "manifest_sha256": sha(output / "source.json"),
        "counts": counts,
        "scoring_executed": False,
        "fit_executed": False,
        "source_modified": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["plan", "prepare", "verify"])
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--silver-root", type=Path, default=PROJECT_ROOT / "data/interim/handbook")
    parser.add_argument("--start", default="2018-09-16")
    parser.add_argument("--end", default="2018-09-17")
    parser.add_argument("--expected-manifest-sha256")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", args.source_id):
        parser.error("Invalid source ID")
    try:
        if args.action == "plan":
            result = plan(args.silver_root, args.start, args.end)
        elif args.action == "prepare":
            result = prepare(args.silver_root, args.source_id, args.start, args.end)
        else:
            if not args.expected_manifest_sha256:
                raise ValueError("Verify requires --expected-manifest-sha256 from preparation")
            value = verify(
                PROJECT_ROOT / "data/replay/session-sources" / args.source_id,
                args.expected_manifest_sha256,
            )
            result = {
                "status": "session_event_source_verified",
                "counts": value["counts"],
                "scoring_executed": False,
                "fit_executed": False,
            }
        print(json.dumps(result, indent=2))
    except (ValueError, OSError, KeyError, sqlite3.Error) as exc:
        parser.exit(
            1,
            f"Event source stopped ({type(exc).__name__}): {exc}\n",
        )


if __name__ == "__main__":
    main()
