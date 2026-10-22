"""Read-only snapshot of previously observed events, with feedback availability preserved."""

from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

import pandas as pd

from fraud_detection_mlops.data.datasets.native_replay_dataset import readonly_connection
from fraud_detection_mlops.streaming.contracts.transaction_events import (
    LABEL_DELAY_NS,
    canonical_event,
    clock_ns,
    event_digest,
    parse_event,
)
from fraud_detection_mlops.streaming.replay.durable_triage_replay import event_order


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_files(path):
    return {
        p.name: sha(p)
        for p in [path, Path(str(path) + "-wal")]
        if p.is_file() and not p.is_symlink()
    }


def capture(source, destination, policy):
    """Select by time and ID only; never by source scores, decisions or fraud labels."""
    source, destination = Path(source).absolute(), Path(destination).absolute()
    if source.resolve() != source or not source.is_file() or destination.exists():
        raise ValueError("Use an existing regular source and a new input snapshot")
    for suffix in ("-wal", "-shm"):
        if Path(str(source) + suffix).is_symlink():
            raise ValueError("Source SQLite sidecar cannot be redirected")
    before = source_files(source)
    start = clock_ns(policy["scoring_start"])
    context = clock_ns(policy["context_start"])
    if start - context < 2 * LABEL_DELAY_NS:
        raise ValueError("Fourteen days of declared context are required")
    with closing(readonly_connection(source)) as src:
        src.execute("BEGIN")
        if (
            src.execute("PRAGMA user_version").fetchone()[0] != 2
            or src.execute("PRAGMA quick_check").fetchone()[0] != "ok"
        ):
            raise ValueError("Source must be an intact durable replay checkpoint")
        identity = json.loads(src.execute("SELECT payload FROM identity").fetchone()[0])
        # Source identity is provenance only; no old model, score or policy is reused.
        total = src.execute("SELECT count(*) FROM transactions").fetchone()[0]
        feedback = src.execute("SELECT count(*) FROM feedback").fetchone()[0]
        if total == 0 or feedback != total:
            raise ValueError("Capture a completed source with feedback for every transaction")
        earliest = src.execute("SELECT min(tx_ns) FROM transactions").fetchone()[0]
        if (
            earliest > context
            and pd.Timestamp(earliest).strftime("%Y-%m-%d") != policy["context_start"][:10]
        ):
            raise ValueError("Source does not cover the declared context start")
        selected = src.execute(
            "SELECT tx_ns,transaction_id FROM transactions WHERE tx_ns>=? "
            "ORDER BY tx_ns,transaction_id LIMIT ?",
            (start, policy["scored_transactions"]),
        ).fetchall()
        if len(selected) != policy["scored_transactions"]:
            raise ValueError("Not enough transactions in the scoring prefix")
        end = selected[-1][0] + 1
        scored = src.execute(
            "SELECT count(*) FROM transactions WHERE tx_ns>=? AND tx_ns<?", (start, end)
        ).fetchone()[0]
        if scored > policy["maximum_scored_with_timestamp_peers"]:
            raise ValueError("Timestamp peers exceed the predeclared HTTP request budget")
        rows = src.execute(
            "SELECT t.transaction_id,t.tx_ns,f.available_ns FROM transactions t "
            "JOIN feedback f ON f.transaction_id=t.transaction_id "
            "WHERE t.tx_ns>=? AND t.tx_ns<? ORDER BY t.tx_ns,t.transaction_id",
            (context, end),
        )
        with closing(sqlite3.connect(destination)) as dest, dest:
            dest.execute(
                "CREATE TABLE stream (clock_ns INTEGER,phase INTEGER,transaction_id INTEGER,"
                "event_id TEXT UNIQUE,payload TEXT NOT NULL,PRIMARY KEY(clock_ns,phase,transaction_id))"
            )
            count = 0
            for tx, tx_ns, available in rows:
                if available != tx_ns + LABEL_DELAY_NS:
                    raise ValueError("Source feedback has an invalid availability clock")
                for key in (f"transaction:{tx}", f"feedback:{tx}"):
                    row = src.execute(
                        "SELECT digest,payload FROM events WHERE event_id=?", (key,)
                    ).fetchone()
                    if row is None:
                        raise ValueError("Source event is missing")
                    event = parse_event(json.loads(row[1]))
                    if event.event_id != key or event_digest(event) != row[0]:
                        raise ValueError("Source event conflicts with its recorded digest")
                    expected_clock = tx_ns if event.event_type == "transaction" else available
                    if event.event_clock_ns != expected_clock:
                        raise ValueError("Source event and ledger clocks differ")
                    dest.execute(
                        "INSERT INTO stream VALUES (?,?,?,?,?)",
                        (*event_order(event), key, canonical_event(event)),
                    )
                    count += 1
        src.execute("COMMIT")
    if before != source_files(source):
        raise ValueError("Source changed during capture; preserve it and stop other writers")
    return {
        "source_files_sha256": before,
        "source_identity_sha256": hashlib.sha256(
            json.dumps(identity, sort_keys=True).encode()
        ).hexdigest(),
        "input_sha256": sha(destination),
        "counts": {
            "events": count,
            "transactions": count // 2,
            "feedback": count // 2,
            "decisions": scored,
        },
        "scoring_window_ns": [start, end],
        "source_scores_reused": False,
        "features_recomputed_causally": True,
    }
