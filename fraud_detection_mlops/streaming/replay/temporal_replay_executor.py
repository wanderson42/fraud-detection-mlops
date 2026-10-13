"""Commit each event, feature decision and checkpoint in one local SQLite transaction."""

import argparse
from contextlib import closing
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import platform
import re
import sqlite3
from urllib.parse import quote

import pandas as pd

from fraud_detection_mlops.artifacts import source_fingerprint
from fraud_detection_mlops.features.feature_schema import FEATURE_COLUMNS
from fraud_detection_mlops.serving.model_release import ScoringRuntime
from fraud_detection_mlops.streaming.contracts.transaction_events import (
    EVENT_CONTRACT_VERSION,
    LABEL_DELAY_NS,
    FeedbackEvent,
    ReplayEvent,
    TransactionEvent,
    canonical_event,
    event_digest,
)
from fraud_detection_mlops.streaming.features.causal_feature_state import feature_request

VERSION = "causal_replay_v1"
SCHEMA_VERSION = 1
SOURCE_PATHS = (
    "streaming/contracts/transaction_events.py",
    "streaming/features/causal_feature_state.py",
    "streaming/replay/temporal_replay_executor.py",
    "features/feature_schema.py",
    "features/feature_validation.py",
    "features/causal_history.py",
    "serving/request_schema.py",
    "serving/model_release.py",
    "modeling/contracts/model_interface.py",
)
SCHEMA = (
    "CREATE TABLE identity (singleton INTEGER PRIMARY KEY CHECK (singleton = 1), payload TEXT NOT NULL)",
    "CREATE TABLE checkpoint (singleton INTEGER PRIMARY KEY CHECK (singleton = 1), clock_ns INTEGER)",
    (
        "CREATE TABLE transactions (transaction_id INTEGER PRIMARY KEY, tx_ns INTEGER NOT NULL, "
        "customer_id INTEGER NOT NULL, terminal_id INTEGER NOT NULL, amount REAL NOT NULL)"
    ),
    "CREATE INDEX customer_history ON transactions (customer_id, tx_ns)",
    "CREATE INDEX terminal_history ON transactions (terminal_id, tx_ns)",
    (
        "CREATE TABLE feedback (transaction_id INTEGER PRIMARY KEY REFERENCES transactions, "
        "available_ns INTEGER NOT NULL, fraud_label INTEGER NOT NULL CHECK (fraud_label IN (0, 1)))"
    ),
    (
        "CREATE TABLE events (event_id TEXT PRIMARY KEY, digest TEXT NOT NULL, payload TEXT NOT NULL, "
        "result TEXT NOT NULL)"
    ),
    (
        "CREATE TABLE decisions (transaction_id INTEGER PRIMARY KEY REFERENCES transactions, "
        "request TEXT NOT NULL, response TEXT)"
    ),
)


class ReplayContractError(ValueError):
    """A permanent ordering, identity or event-contract violation."""


def json_text(value: dict) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def source_identity() -> str:
    root = Path(__file__).resolve().parents[2]
    return source_fingerprint(root, (root / path for path in SOURCE_PATHS))


def feature_environment() -> dict[str, str]:
    return {name: version(name) for name in ("numpy", "pandas", "pyarrow", "pydantic")}


class TemporalReplayExecutor:
    """One local writer per call; repeat delivery returns the committed original decision.

    A scorer is the existing in-process release runtime. No network side effect is
    included in the transaction; external publication needs a separate delivery contract.
    """

    def __init__(
        self,
        state_path: Path,
        *,
        run_id: str,
        input_sha256: str,
        runtime: ScoringRuntime | None = None,
    ):
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", run_id):
            raise ReplayContractError("Use an explicit lowercase replay run identity")
        if not re.fullmatch(r"[0-9a-f]{64}", input_sha256):
            raise ReplayContractError("Pin the complete synthetic input sequence SHA-256")
        self.runtime = runtime
        self.identity = {
            "version": VERSION,
            "schema_version": SCHEMA_VERSION,
            "run_id": run_id,
            "input_sha256": input_sha256,
            "data_scope": "synthetic_fixture",
            "event_contract_version": EVENT_CONTRACT_VERSION,
            "feature_contract_version": "gold_v1",
            "feature_columns": FEATURE_COLUMNS,
            "source_sha256": source_identity(),
            "python": platform.python_version(),
            "sqlite": sqlite3.sqlite_version,
            "feature_environment": feature_environment(),
            "scoring": runtime.information() if runtime is not None else None,
        }
        state_path = Path(state_path)
        if state_path.is_symlink():
            raise ReplayContractError("The checkpoint cannot be a symbolic link")
        state_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(state_path, autocommit=True, timeout=5)
        try:
            self.connection.execute("PRAGMA foreign_keys = ON")
            self.connection.execute("PRAGMA synchronous = FULL")
            self.connection.execute("BEGIN IMMEDIATE")
            tables = self.connection.execute(
                "SELECT name FROM sqlite_schema WHERE type = 'table'"
            ).fetchall()
            if not tables:
                for statement in SCHEMA:
                    self.connection.execute(statement)
                self.connection.execute("PRAGMA user_version = 1")
                self.connection.execute(
                    "INSERT INTO identity VALUES (1, ?)", (json_text(self.identity),)
                )
                self.connection.execute("INSERT INTO checkpoint VALUES (1, NULL)")
            else:
                if self.connection.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
                    raise ReplayContractError("Checkpoint schema is incompatible; preserve it")
                saved = self.connection.execute(
                    "SELECT payload FROM identity WHERE singleton = 1"
                ).fetchone()
                if saved is None or json.loads(saved[0]) != self.identity:
                    raise ReplayContractError("Replay inputs, code, environment or scorer changed")
                if self.connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise ReplayContractError("Checkpoint integrity check failed")
            self.connection.execute("COMMIT")
        except BaseException:
            if self.connection.in_transaction:
                self.connection.execute("ROLLBACK")
            self.connection.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        self.connection.close()

    def handle(self, event: ReplayEvent) -> dict:
        if type(event) not in (TransactionEvent, FeedbackEvent):
            raise ReplayContractError("Validate the event contract before processing")
        event = type(event).model_validate(event.model_dump())
        connection = self.connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            previous = connection.execute(
                "SELECT digest, result FROM events WHERE event_id = ?", (event.event_id,)
            ).fetchone()
            if previous is not None:
                if previous[0] != event_digest(event):
                    raise ReplayContractError("Conflicting duplicate event; preserve the original")
                result = json.loads(previous[1])
                connection.execute("COMMIT")
                return {**result, "reused": True}
            now = event.event_clock_ns
            last = connection.execute(
                "SELECT clock_ns FROM checkpoint WHERE singleton = 1"
            ).fetchone()[0]
            if last is not None and now < last:
                raise ReplayContractError(
                    "Unseen late event rejected; prior decisions remain unchanged"
                )
            result = {"event_id": event.event_id, "event_type": event.event_type, "reused": False}
            if isinstance(event, TransactionEvent):
                connection.execute(
                    "INSERT INTO transactions VALUES (?, ?, ?, ?, ?)",
                    (
                        event.transaction_id,
                        now,
                        event.customer_id,
                        event.terminal_id,
                        event.amount,
                    ),
                )
                request = feature_request(connection, event)
                response = self.runtime.score(request) if self.runtime is not None else None
                if response is not None and (
                    response.transaction_id != event.transaction_id
                    or response.model_sha256 != self.runtime.manifest.model_sha256
                    or response.serving_release_id != self.runtime.manifest.serving_release_id
                ):
                    raise ReplayContractError("Scorer returned a different transaction or release")
                connection.execute(
                    "INSERT INTO decisions VALUES (?, ?, ?)",
                    (
                        event.transaction_id,
                        request.model_dump_json(),
                        response.model_dump_json() if response else None,
                    ),
                )
                result.update(
                    request=request.model_dump(),
                    response=response.model_dump() if response else None,
                )
            else:
                transaction = connection.execute(
                    "SELECT tx_ns FROM transactions WHERE transaction_id = ?",
                    (event.transaction_id,),
                ).fetchone()
                if transaction is None:
                    raise ReplayContractError("Feedback has no previously observed transaction")
                if now != transaction[0] + LABEL_DELAY_NS:
                    raise ReplayContractError(
                        "Gold v1 feedback must arrive exactly seven event-clock days later"
                    )
                connection.execute(
                    "INSERT INTO feedback VALUES (?, ?, ?)",
                    (event.transaction_id, now, event.fraud_label),
                )
            connection.execute(
                "INSERT INTO events VALUES (?, ?, ?, ?)",
                (event.event_id, event_digest(event), canonical_event(event), json_text(result)),
            )
            connection.execute("UPDATE checkpoint SET clock_ns = ? WHERE singleton = 1", (now,))
            connection.execute("COMMIT")
            return result
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    def summary(self) -> dict:
        self.connection.execute("BEGIN")
        try:
            result = checkpoint_summary(self.connection)
            self.connection.execute("COMMIT")
            return result
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise


def checkpoint_summary(connection: sqlite3.Connection) -> dict:
    identity = json.loads(
        connection.execute("SELECT payload FROM identity WHERE singleton = 1").fetchone()[0]
    )
    counts = {
        name: connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
        for name in ("events", "transactions", "feedback", "decisions")
    }
    clock = connection.execute("SELECT clock_ns FROM checkpoint WHERE singleton = 1").fetchone()[0]
    return {
        **identity,
        "status": "success",
        "counts": counts,
        "last_event_clock_ns": clock,
        "last_event_clock": pd.Timestamp(clock).isoformat() if clock is not None else None,
        "native_replay_evaluated": False,
        "production_promotion": False,
    }


def inspect_checkpoint(path: Path) -> dict:
    """Read an existing checkpoint without opening or creating an operational writer."""
    uri = "file:" + quote(str(path.resolve()), safe="/") + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, autocommit=True)) as connection:
        connection.execute("BEGIN")
        if connection.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
            raise ReplayContractError("Checkpoint schema is incompatible")
        if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ReplayContractError("Checkpoint integrity check failed")
        result = checkpoint_summary(connection)
        connection.execute("COMMIT")
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    demo = commands.add_parser(
        "demo", help="Generated fixtures only; no Handbook access or fitting"
    )
    demo.add_argument("--state", required=True, type=Path)
    demo.add_argument("--stop-after", type=int)
    inspect = commands.add_parser("inspect")
    inspect.add_argument("state", type=Path)
    args = parser.parse_args()
    if args.command == "inspect":
        report = inspect_checkpoint(args.state)
    else:
        from fraud_detection_mlops.streaming.replay.synthetic_event_stream import synthetic_events

        events = synthetic_events()
        if args.stop_after is not None and args.stop_after <= 0:
            parser.error("--stop-after must be positive")
        checksum = hashlib.sha256(
            "\n".join(canonical_event(e) for e in events).encode()
        ).hexdigest()
        accepted = reused = 0
        with TemporalReplayExecutor(
            args.state, run_id="synthetic-replay-v1", input_sha256=checksum
        ) as executor:
            for event in events[: args.stop_after]:
                result = executor.handle(event)
                reused += int(result["reused"])
                accepted += int(not result["reused"])
            report = executor.summary()
        report.update(
            accepted_this_invocation=accepted,
            reused_this_invocation=reused,
            input_events=len(events),
            complete=report["counts"]["events"] == len({e.event_id for e in events}),
            feature_parity_evaluated=False,
            score_parity_evaluated=False,
        )
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
