"""Materialize only the declared Silver dates as ordered, label-separated replay events."""

from contextlib import closing
import json
from pathlib import Path
import sqlite3
from urllib.parse import quote

import pandas as pd

from fraud_detection_mlops.data.datasets.reference_assessment_dataset import (
    check_silver_bytes,
    silver_snapshot,
    window_days,
)
from fraud_detection_mlops.streaming.contracts.transaction_events import (
    LABEL_DELAY_NS,
    FeedbackEvent,
    TransactionEvent,
    canonical_event,
    clock_ns,
    parse_event,
)
from fraud_detection_mlops.streaming.replay.durable_triage_replay import event_order


def assessment_projection(protocol: dict) -> dict:
    """Reuse the existing source/schema/Gold checks with the replay's exact windows."""
    return {
        "source_commit": protocol["source_commit"],
        "windows": {
            "assessment_context": protocol["windows"]["context"],
            "assessment": protocol["windows"]["scored_replay"],
        },
        "time_policy": {
            "assessment_labels_complete_before": protocol["windows"][
                "feedback_drain_end_exclusive"
            ]
        },
    }


def replay_snapshot(silver_root: Path, protocol: dict, root: Path) -> dict:
    """Metadata only: no partition open, hash or listing of native transaction bytes."""
    return silver_snapshot(silver_root, assessment_projection(protocol), root)


def readonly_connection(path: Path):
    path = path.absolute()
    if path.resolve() != path:
        raise ValueError("Replay input/state path cannot be redirected")
    return sqlite3.connect("file:" + quote(str(path), safe="/") + "?mode=ro", uri=True)


def write_event_stream(snapshot: dict, protocol: dict, output: Path) -> dict:
    """The caller first validates Silver schema/domains with the independent Gold builder."""
    check_silver_bytes(snapshot)
    if output.exists() or output.is_symlink():
        raise ValueError("Replay event stream already exists; no overwrite")
    context = clock_ns(protocol["windows"]["context"]["start"] + "T00:00:00")
    end = clock_ns(protocol["windows"]["scored_replay"]["end_exclusive"] + "T00:00:00")
    drain = clock_ns(protocol["windows"]["feedback_drain_end_exclusive"])
    output.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(output)) as connection, connection:
        connection.execute(
            "CREATE TABLE stream (clock_ns INTEGER, phase INTEGER, transaction_id INTEGER, "
            "event_id TEXT UNIQUE, payload TEXT NOT NULL, PRIMARY KEY(clock_ns,phase,transaction_id))"
        )
        for record in snapshot["files"]:
            frame = pd.read_parquet(Path(snapshot["source_root"]) / record["path"])
            for row in frame.itertuples(index=False):
                event = TransactionEvent(
                    schema_version=1,
                    event_type="transaction",
                    transaction_id=int(row.TRANSACTION_ID),
                    customer_id=int(row.CUSTOMER_ID),
                    terminal_id=int(row.TERMINAL_ID),
                    amount=float(row.TX_AMOUNT),
                    tx_datetime=pd.Timestamp(row.TX_DATETIME).isoformat(),
                )
                if (
                    not context <= event.event_clock_ns < end
                    or event.event_clock_ns + LABEL_DELAY_NS >= drain
                ):
                    raise ValueError("Transaction or label outside the restricted native window")
                feedback = FeedbackEvent(
                    schema_version=1,
                    event_type="feedback",
                    transaction_id=event.transaction_id,
                    available_at=pd.Timestamp(event.event_clock_ns + LABEL_DELAY_NS).isoformat(),
                    fraud_label=int(row.TX_FRAUD),
                )
                for item in (event, feedback):
                    connection.execute(
                        "INSERT INTO stream VALUES (?,?,?,?,?)",
                        (*event_order(item), item.event_id, canonical_event(item)),
                    )
        rows = connection.execute("SELECT count(*) FROM stream").fetchone()[0]
    check_silver_bytes(snapshot)
    return {"events": rows, "transactions": rows // 2, "feedback": rows // 2}


def stream_events(path: Path, *, after: tuple | None = None):
    """Bounded cursor over immutable input; restore only the still-uncommitted suffix."""
    with closing(readonly_connection(path)) as connection:
        query = "SELECT payload FROM stream"
        if after is not None:
            query += " WHERE (clock_ns,phase,transaction_id) > (?,?,?)"
        query += " ORDER BY clock_ns,phase,transaction_id"
        for (payload,) in connection.execute(query, after or ()):
            yield parse_event(json.loads(payload))


def validate_event_stream(path: Path, protocol: dict, expected_counts: dict) -> None:
    """No target in transactions, exact delay, complete days and deterministic strict order."""
    projection = assessment_projection(protocol)
    days = window_days(
        {
            "start": projection["windows"]["assessment_context"]["start"],
            "end_exclusive": projection["windows"]["assessment"]["end_exclusive"],
        }
    )
    seen, labelled, observed_days = {}, set(), set()
    last = None
    for event in stream_events(path):
        order = event_order(event)
        if last is not None and order <= last:
            raise ValueError("Replay event stream is not in strict total order")
        last = order
        if isinstance(event, TransactionEvent):
            day = pd.Timestamp(event.event_clock_ns).strftime("%Y-%m-%d")
            if day not in days or event.transaction_id in seen:
                raise ValueError("Replay transaction coverage/identity differs")
            seen[event.transaction_id] = event.event_clock_ns
            observed_days.add(day)
        else:
            if (
                event.transaction_id not in seen
                or event.transaction_id in labelled
                or event.event_clock_ns != seen[event.transaction_id] + LABEL_DELAY_NS
                or event.event_clock_ns
                >= clock_ns(protocol["windows"]["feedback_drain_end_exclusive"])
            ):
                raise ValueError("Replay feedback coverage or availability differs")
            labelled.add(event.transaction_id)
    if (
        observed_days != set(days)
        or labelled != set(seen)
        or expected_counts
        != {"events": 2 * len(seen), "transactions": len(seen), "feedback": len(seen)}
    ):
        raise ValueError("Replay input omits declared days, transactions or mature feedback")
