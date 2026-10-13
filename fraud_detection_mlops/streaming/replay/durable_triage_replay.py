"""Commit causal features, scores, customer alerts and progress atomically in SQLite."""

import argparse
from contextlib import closing
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import platform
import re
import sqlite3
from urllib.parse import quote

import pandas as pd
from threadpoolctl import threadpool_limits

from fraud_detection_mlops.artifacts import source_fingerprint
from fraud_detection_mlops.features.feature_schema import FEATURE_COLUMNS
from fraud_detection_mlops.serving.model_release import (
    ReleaseManifest,
    ScoringRuntime,
    checked_bytes,
    load_release,
)
from fraud_detection_mlops.serving.request_schema import ScoreResponse
from fraud_detection_mlops.streaming.contracts.customer_day_alerts import CustomerDayAlertConfig
from fraud_detection_mlops.streaming.contracts.transaction_events import (
    EVENT_CONTRACT_VERSION,
    LABEL_DELAY_NS,
    FeedbackEvent,
    ReplayEvent,
    TransactionEvent,
    canonical_event,
    clock_ns,
    event_digest,
)
from fraud_detection_mlops.streaming.decisions.customer_day_alert_policy import (
    decide_customer_day_alert,
)
from fraud_detection_mlops.streaming.features.causal_feature_state import feature_request
from fraud_detection_mlops.streaming.replay.temporal_replay_executor import (
    SCHEMA as HISTORY_SCHEMA,
)
from fraud_detection_mlops.streaming.replay.temporal_replay_executor import (
    SOURCE_PATHS as HISTORY_SOURCES,
)
from fraud_detection_mlops.streaming.replay.temporal_replay_executor import (
    ReplayContractError,
    feature_environment,
    json_text,
)

VERSION = "durable_triage_replay_v1"
SCHEMA_VERSION = 2
SOURCE_PATHS = (
    *HISTORY_SOURCES,
    "streaming/replay/durable_triage_replay.py",
    "streaming/decisions/customer_day_alert_policy.py",
    "streaming/contracts/customer_day_alerts.py",
)
SCHEMA = tuple(
    "CREATE TABLE checkpoint (singleton INTEGER PRIMARY KEY CHECK (singleton = 1), "
    "clock_ns INTEGER, event_phase INTEGER, transaction_id INTEGER)"
    if statement.startswith("CREATE TABLE checkpoint")
    else statement
    for statement in HISTORY_SCHEMA
) + (
    (
        "CREATE TABLE alert_decisions (transaction_id INTEGER PRIMARY KEY REFERENCES decisions, "
        "payload TEXT NOT NULL)"
    ),
    (
        "CREATE TABLE alerts (alert_id TEXT PRIMARY KEY, day TEXT NOT NULL, customer_id INTEGER NOT NULL, "
        "transaction_id INTEGER UNIQUE NOT NULL REFERENCES decisions, payload TEXT NOT NULL, "
        "UNIQUE(day, customer_id))"
    ),
)


def source_identity() -> str:
    root = Path(__file__).resolve().parents[2]
    return source_fingerprint(root, (root / path for path in SOURCE_PATHS))


def event_order(event: ReplayEvent) -> tuple[int, int, int]:
    return event.event_clock_ns, int(isinstance(event, TransactionEvent)), event.transaction_id


class DurableTriageReplay:
    """Local score/alert ledger; no external effects are included in the transaction.

    Use a new checkpoint path. Policy and runtime are required and bound to the
    identity. The input digest is the caller's complete, prevalidated event stream.
    No data loader, fit or broker publication is performed by this component.
    """

    def __init__(
        self,
        state_path: Path,
        *,
        run_id: str,
        input_sha256: str,
        runtime: ScoringRuntime,
        policy: CustomerDayAlertConfig,
        scoring_window: tuple[str, str] | None = None,
        data_scope: str = "caller_supplied_events",
    ):
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", run_id):
            raise ReplayContractError("Use an explicit lowercase replay identity")
        if not re.fullmatch(r"[0-9a-f]{64}", input_sha256):
            raise ReplayContractError("Pin the complete input stream SHA-256")
        if type(runtime) is not ScoringRuntime or type(policy) is not CustomerDayAlertConfig:
            raise ReplayContractError("Supply an identified runtime and validated alert policy")
        self._policy = CustomerDayAlertConfig.model_validate(policy.model_dump())
        if (
            self._policy.model_sha256 != runtime.manifest.model_sha256
            or self._policy.serving_release_id != runtime.manifest.serving_release_id
        ):
            raise ReplayContractError("Policy and runtime identify different releases")
        if data_scope not in ("caller_supplied_events", "synthetic_fixture"):
            raise ReplayContractError("Declare the input scope without authorizing dataset access")
        if scoring_window is not None:
            if type(scoring_window) is not tuple or len(scoring_window) != 2:
                raise ReplayContractError(
                    "Declare a half-open scoring window with two source clocks"
                )
            scoring_window = tuple(clock_ns(value) for value in scoring_window)
            if scoring_window[0] >= scoring_window[1]:
                raise ReplayContractError("Scoring window must be nonempty")
        self._scoring_window = scoring_window
        self.runtime = runtime
        self.identity = {
            "version": VERSION,
            "schema_version": SCHEMA_VERSION,
            "run_id": run_id,
            "input_sha256": input_sha256,
            "data_scope": data_scope,
            "scoring_window_ns": list(scoring_window) if scoring_window else None,
            "event_contract_version": EVENT_CONTRACT_VERSION,
            "feature_contract_version": "gold_v1",
            "feature_columns": FEATURE_COLUMNS,
            "policy": self._policy.model_dump(),
            "policy_sha256": self._policy.policy_sha256,
            "scoring": runtime.information(),
            "source_sha256": source_identity(),
            "python": platform.python_version(),
            "sqlite": sqlite3.sqlite_version,
            "feature_environment": feature_environment(),
        }
        state_path = Path(state_path).absolute()
        if state_path.resolve() != state_path:
            raise ReplayContractError("Checkpoint path cannot be redirected")
        state_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(state_path, autocommit=True, timeout=5)
        try:
            self.connection.execute("PRAGMA foreign_keys = ON")
            self.connection.execute("PRAGMA synchronous = FULL")
            self.connection.execute("BEGIN IMMEDIATE")
            tables = self.connection.execute(
                "SELECT name FROM sqlite_schema WHERE type='table'"
            ).fetchall()
            if not tables:
                if self.connection.execute("PRAGMA user_version").fetchone()[0] != 0:
                    raise ReplayContractError("Foreign checkpoint metadata; preserve it")
                for statement in SCHEMA:
                    self.connection.execute(statement)
                self.connection.execute("PRAGMA user_version = 2")
                self.connection.execute(
                    "INSERT INTO identity VALUES (1, ?)", (json_text(self.identity),)
                )
                self.connection.execute("INSERT INTO checkpoint VALUES (1, NULL, NULL, NULL)")
            else:
                if self.connection.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
                    raise ReplayContractError(
                        "Use a new durable triage checkpoint; preserve earlier state"
                    )
                saved = self.connection.execute(
                    "SELECT payload FROM identity WHERE singleton=1"
                ).fetchone()
                if saved is None or json.loads(saved[0]) != self.identity:
                    raise ReplayContractError(
                        "Inputs, policy, release, code or environment changed"
                    )
                if (
                    self.connection.execute("PRAGMA quick_check").fetchone()[0] != "ok"
                    or self.connection.execute("PRAGMA foreign_key_check").fetchone() is not None
                ):
                    raise ReplayContractError("Durable checkpoint integrity check failed")
            self.connection.execute("COMMIT")
        except BaseException:
            if self.connection.in_transaction:
                self.connection.execute("ROLLBACK")
            self.connection.close()
            raise

    @property
    def policy(self) -> CustomerDayAlertConfig:
        return self._policy

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.connection.close()

    def handle(self, event: ReplayEvent) -> dict:
        if type(event) not in (TransactionEvent, FeedbackEvent):
            raise ReplayContractError("Validate the event before processing")
        event = type(event).model_validate(event.model_dump())
        connection = self.connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            previous = connection.execute(
                "SELECT digest,result FROM events WHERE event_id=?", (event.event_id,)
            ).fetchone()
            if previous is not None:
                if previous[0] != event_digest(event):
                    raise ReplayContractError("Conflicting duplicate; preserve committed decision")
                result = json.loads(previous[1])
                connection.execute("COMMIT")
                return {**result, "reused": True}
            order = event_order(event)
            last = connection.execute(
                "SELECT clock_ns,event_phase,transaction_id FROM checkpoint WHERE singleton=1"
            ).fetchone()
            if last[0] is not None and order <= last:
                raise ReplayContractError(
                    "New events must follow clock, feedback-first and transaction-ID order"
                )
            result = {"event_id": event.event_id, "event_type": event.event_type, "reused": False}
            if isinstance(event, TransactionEvent):
                if self._scoring_window is not None and order[0] >= self._scoring_window[1]:
                    raise ReplayContractError(
                        "New transactions at or beyond the scoring end are rejected"
                    )
                context_only = (
                    self._scoring_window is not None and order[0] < self._scoring_window[0]
                )
                result["context_only"] = context_only
                connection.execute(
                    "INSERT INTO transactions VALUES (?,?,?,?,?)",
                    (
                        event.transaction_id,
                        order[0],
                        event.customer_id,
                        event.terminal_id,
                        event.amount,
                    ),
                )
                if not context_only:
                    request = feature_request(connection, event)
                    response = self.runtime.score(request)
                    if type(response) is not ScoreResponse:
                        raise ReplayContractError(
                            "Scorer must return the validated score contract"
                        )
                    response = ScoreResponse.model_validate(response.model_dump())
                    if response.model_id != self.runtime.manifest.model_id:
                        raise ReplayContractError("Scorer returned a different model identity")
                    day = pd.Timestamp(order[0]).strftime("%Y-%m-%d")
                    used = connection.execute(
                        "SELECT count(*) FROM alerts WHERE day=?", (day,)
                    ).fetchone()[0]
                    alerted = (
                        connection.execute(
                            "SELECT 1 FROM alerts WHERE day=? AND customer_id=?",
                            (day, event.customer_id),
                        ).fetchone()
                        is not None
                    )
                    decision = decide_customer_day_alert(
                        self._policy,
                        event,
                        response,
                        already_alerted=alerted,
                        daily_budget_used=used,
                    )
                    payload = asdict(decision)
                    connection.execute(
                        "INSERT INTO decisions VALUES (?,?,?)",
                        (
                            event.transaction_id,
                            request.model_dump_json(),
                            response.model_dump_json(),
                        ),
                    )
                    connection.execute(
                        "INSERT INTO alert_decisions VALUES (?,?)",
                        (event.transaction_id, json_text(payload)),
                    )
                    if decision.status == "alert":
                        connection.execute(
                            "INSERT INTO alerts VALUES (?,?,?,?,?)",
                            (
                                decision.alert_id,
                                day,
                                event.customer_id,
                                event.transaction_id,
                                json_text(payload),
                            ),
                        )
                    result.update(
                        request=request.model_dump(), response=response.model_dump(), alert=payload
                    )
            else:
                transaction = connection.execute(
                    "SELECT tx_ns FROM transactions WHERE transaction_id=?",
                    (event.transaction_id,),
                ).fetchone()
                if transaction is None or order[0] != transaction[0] + LABEL_DELAY_NS:
                    raise ReplayContractError(
                        "Feedback requires an observed transaction and exactly seven days"
                    )
                connection.execute(
                    "INSERT INTO feedback VALUES (?,?,?)",
                    (event.transaction_id, order[0], event.fraud_label),
                )
            connection.execute(
                "INSERT INTO events VALUES (?,?,?,?)",
                (event.event_id, event_digest(event), canonical_event(event), json_text(result)),
            )
            connection.execute(
                "UPDATE checkpoint SET clock_ns=?,event_phase=?,transaction_id=? WHERE singleton=1",
                order,
            )
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
        connection.execute("SELECT payload FROM identity WHERE singleton=1").fetchone()[0]
    )
    counts = {
        name: connection.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
        for name in (
            "events",
            "transactions",
            "feedback",
            "decisions",
            "alert_decisions",
            "alerts",
        )
    }
    clock, phase, transaction_id = connection.execute(
        "SELECT clock_ns,event_phase,transaction_id FROM checkpoint WHERE singleton=1"
    ).fetchone()
    return {
        **identity,
        "status": "success",
        "counts": counts,
        "last_event_order": [clock, phase, transaction_id],
        "last_event_clock": pd.Timestamp(clock).isoformat() if clock is not None else None,
        "external_alerts_published": False,
        "native_replay_evaluated": False,
        "production_promotion": False,
    }


def inspect_checkpoint(path: Path) -> dict:
    if path.absolute().resolve() != path.absolute():
        raise ReplayContractError("Checkpoint path cannot be redirected")
    uri = "file:" + quote(str(path.absolute()), safe="/") + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, autocommit=True)) as connection:
        connection.execute("BEGIN")
        if (
            connection.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION
            or connection.execute("PRAGMA quick_check").fetchone()[0] != "ok"
        ):
            raise ReplayContractError("Durable checkpoint schema or integrity differs")
        result = checkpoint_summary(connection)
        connection.execute("COMMIT")
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo", help="Generated stream and synthetic release only")
    demo.add_argument("--state", type=Path, required=True)
    demo.add_argument("--release", type=Path, required=True)
    demo.add_argument("--manifest-sha256", required=True)
    demo.add_argument("--stop-after", type=int)
    inspection = commands.add_parser("inspect")
    inspection.add_argument("state", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "inspect":
            report = inspect_checkpoint(args.state)
        else:
            from fraud_detection_mlops.streaming.replay.synthetic_event_stream import (
                synthetic_events,
            )

            if args.stop_after is not None and args.stop_after <= 0:
                parser.error("--stop-after must be positive")
            with threadpool_limits(limits=4):
                manifest = ReleaseManifest.model_validate_json(
                    checked_bytes(args.release / "manifest.json", args.manifest_sha256)
                )
                if manifest.evidence_kind != "synthetic_smoke":
                    raise ReplayContractError("Demo accepts only an explicitly synthetic release")
                runtime = load_release(args.release, args.manifest_sha256)
                policy = CustomerDayAlertConfig(
                    schema_version=1,
                    version="first_crossing_customer_day_v1",
                    model_sha256=runtime.manifest.model_sha256,
                    serving_release_id=runtime.manifest.serving_release_id,
                    score_threshold=0.7,
                    daily_customer_budget=100,
                )
                events = synthetic_events()
                checksum = hashlib.sha256(
                    "\n".join(canonical_event(e) for e in events).encode()
                ).hexdigest()
                with DurableTriageReplay(
                    args.state,
                    run_id="synthetic-durable-v1",
                    input_sha256=checksum,
                    runtime=runtime,
                    policy=policy,
                    data_scope="synthetic_fixture",
                ) as replay:
                    accepted = reused = 0
                    for event in events[: args.stop_after]:
                        result = replay.handle(event)
                        reused += int(result["reused"])
                        accepted += int(not result["reused"])
                    report = replay.summary()
            report.update(
                accepted_this_invocation=accepted,
                reused_this_invocation=reused,
                input_events=len(events),
                complete=report["counts"]["events"] == len({e.event_id for e in events}),
            )
    except (ValueError, OSError, sqlite3.DatabaseError) as exc:
        parser.exit(1, f"Durable replay failed: {exc}\n")
    print(json.dumps(report, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
