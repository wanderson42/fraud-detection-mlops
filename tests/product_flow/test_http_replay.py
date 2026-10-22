"""Real HTTP, causal feature reconstruction, rollback and completed reuse."""

from contextlib import closing
import json
import sqlite3
from urllib.error import HTTPError

import pytest
from scripts.product_flow.controller import replay, run_events, verify_ledger
from scripts.product_flow.inputs import sha
from scripts.product_flow.ledger import HTTPReplay
from tests.product_flow.conftest import scorer

from fraud_detection_mlops.data.datasets.native_replay_dataset import stream_events
from fraud_detection_mlops.streaming.replay.durable_triage_replay import DurableTriageReplay


def counts(folder):
    with closing(sqlite3.connect(folder / "checkpoint.sqlite")) as connection:
        return {
            name: connection.execute("SELECT count(*) FROM " + name).fetchone()[0]
            for name in ["events", "transactions", "decisions", "http_exchanges"]
        }


def test_real_HTTP_pause_resume_reuse_and_full_reconstruction(prepared, server):
    folder, value, source = prepared
    url, faults = server
    original = sha(source)
    runtime = scorer(url, value)
    runtime.ready()
    first = run_events(folder, value, runtime, stop_after_scored=1)
    assert first["status"] == "paused" and first["new_decisions"] == 1
    second = run_events(folder, value, runtime)
    assert second["status"] == "complete" and second["new_decisions"] == 1
    assert faults["scores"] == 2
    final = run_events(folder, value, runtime)
    assert final["new_decisions"] == final["scoring_HTTP_attempts_this_invocation"] == 0
    verified = verify_ledger(folder, value)
    assert verified["counts"] == {
        "events": 20,
        "transactions": 10,
        "feedback": 10,
        "decisions": 2,
        "alert_decisions": 2,
        "alerts": 1,
    }
    assert verified["causal_requests_and_triage_rebuilt"]
    assert verified["alerts_with_positive_transaction_feedback"] == 1
    assert sha(source) == original
    assert HTTPReplay.handle is DurableTriageReplay.handle
    # An already-committed duplicate must not issue another HTTP call.
    with replay(folder / "checkpoint.sqlite", value, runtime) as engine:
        duplicate = next(stream_events(folder / "events.sqlite"))
        assert engine.handle(duplicate)["reused"]
    assert faults["scores"] == 2


@pytest.mark.parametrize(
    "fault,error,attempts",
    [("failures", HTTPError, 3), ("invalid", HTTPError, 1), ("wrong_identity", ValueError, 1)],
)
def test_failed_HTTP_never_advances_the_scored_transaction(
    prepared, server, fault, error, attempts
):
    folder, value, _ = prepared
    url, faults = server
    faults[fault] = 10 if fault == "failures" else True
    runtime = scorer(url, value)
    with pytest.raises(error):
        run_events(folder, value, runtime)
    assert faults["scores"] == attempts
    saved = counts(folder)
    assert saved["decisions"] == saved["http_exchanges"] == 0
    assert (
        saved["transactions"] == 8
    )  # Eight context transactions, scored transaction rolled back.
    faults[fault] = 0 if fault == "failures" else False
    assert run_events(folder, value, runtime)["status"] == "complete"
    assert verify_ledger(folder, value)["HTTP_receipts"] == 2


def test_transient_503_has_bounded_retry_and_one_atomic_receipt(prepared, server):
    folder, value, _ = prepared
    url, faults = server
    faults["failures"] = 2
    assert run_events(folder, value, scorer(url, value))["status"] == "complete"
    assert faults["scores"] == 4
    with closing(sqlite3.connect(folder / "checkpoint.sqlite")) as conn:
        assert conn.execute(
            "SELECT attempts FROM http_exchanges ORDER BY transaction_id"
        ).fetchall() == [(3,), (1,)]
    verify_ledger(folder, value)


def test_failure_after_HTTP_before_SQL_commit_retries_pure_score_without_duplicate(
    prepared, server
):
    folder, value, _ = prepared
    url, faults = server
    runtime = scorer(url, value)
    events = list(stream_events(folder / "events.sqlite"))
    with replay(folder / "checkpoint.sqlite", value, runtime) as engine:
        for event in events:
            if (
                event.event_type == "transaction"
                and event.event_clock_ns >= value["capture"]["scoring_window_ns"][0]
            ):
                break
            engine.handle(event)
        record = runtime.record

        def fail(*args):
            record(*args)
            raise RuntimeError("process interruption after HTTP receipt before decision")

        runtime.record = fail
        with pytest.raises(RuntimeError):
            engine.handle(event)
        assert engine.summary()["counts"]["decisions"] == 0
        assert engine.connection.execute("SELECT count(*) FROM http_exchanges").fetchone()[0] == 0
    run_events(folder, value, runtime)
    assert faults["scores"] == 3  # HTTP is at least once; ledger decisions remain exactly once.
    assert verify_ledger(folder, value)["HTTP_receipts"] == 2


@pytest.mark.parametrize(
    "table,column",
    [
        ("decisions", "request"),
        ("decisions", "response"),
        ("alert_decisions", "payload"),
        ("http_exchanges", "response_sha256"),
    ],
)
def test_verifier_detects_committed_tampering(prepared, server, table, column):
    folder, value, _ = prepared
    run_events(folder, value, scorer(server[0], value))
    with closing(sqlite3.connect(folder / "checkpoint.sqlite")) as conn, conn:
        row = conn.execute(
            "SELECT transaction_id," + column + " FROM " + table + " LIMIT 1"
        ).fetchone()
        if column == "response_sha256":
            changed = "f" * 64
        else:
            data = json.loads(row[1])
            if column == "request":
                data["features"]["TX_AMOUNT"] += 1
            elif column == "response":
                data["score"] = 0.999
            else:
                data["status"] = "forged"
            changed = json.dumps(data)
        conn.execute(
            "UPDATE " + table + " SET " + column + "=? WHERE transaction_id=?", (changed, row[0])
        )
    with pytest.raises(ValueError):
        verify_ledger(folder, value)


def test_resume_refuses_another_model_or_binding(prepared, server):
    folder, value, _ = prepared
    runtime = scorer(server[0], value)
    run_events(folder, value, runtime, stop_after_scored=1)
    altered = {**value, "flow_id": "foreign"}
    with pytest.raises(ValueError, match="another input"):
        run_events(folder, altered, runtime)
