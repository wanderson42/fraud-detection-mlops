from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
import subprocess
import sys

import duckdb
import pandas as pd
import pytest

from fraud_detection_mlops.features.causal_history import compute_features
from fraud_detection_mlops.features.feature_schema import DTYPES, FEATURE_COLUMNS
from fraud_detection_mlops.modeling.contracts.model_interface import predict_scores
from fraud_detection_mlops.streaming.contracts.transaction_events import (
    FeedbackEvent,
    TransactionEvent,
)
from fraud_detection_mlops.streaming.replay.temporal_replay_executor import (
    ReplayContractError,
    TemporalReplayExecutor,
    inspect_checkpoint,
)


def executor(path, checksum, runtime=None):
    return TemporalReplayExecutor(
        path, run_id="synthetic-test-v1", input_sha256=checksum, runtime=runtime
    )


def offline_gold(events):
    """Existing batch implementation is an independent oracle for incremental SQL."""
    labels = {
        event.transaction_id: event.fraud_label
        for event in events
        if isinstance(event, FeedbackEvent)
    }
    transactions = {
        event.transaction_id: event for event in events if isinstance(event, TransactionEvent)
    }
    frame = pd.DataFrame(
        [
            {
                "TRANSACTION_ID": e.transaction_id,
                "TX_DATETIME": pd.Timestamp(e.tx_datetime),
                "CUSTOMER_ID": e.customer_id,
                "TERMINAL_ID": e.terminal_id,
                "TX_AMOUNT": e.amount,
                "TX_FRAUD": labels[e.transaction_id],
            }
            for e in transactions.values()
        ]
    )
    with duckdb.connect() as connection:
        connection.register("transactions", frame)
        compute_features(connection)
        return (
            connection.execute("SELECT * FROM gold_features ORDER BY TRANSACTION_ID")
            .fetchdf()
            .astype(DTYPES)
        )


def test_incremental_features_and_scores_match_offline_for_peers_and_ns_boundaries(
    tmp_path, replay_events, replay_checksum, replay_runtime
):
    gold = offline_gold(replay_events)
    decisions = {}
    with executor(tmp_path / "checkpoint.sqlite", replay_checksum, replay_runtime) as replay:
        for event in replay_events:
            result = replay.handle(event)
            if result.get("request"):
                decisions[event.transaction_id] = result
        assert replay.summary()["counts"] == {
            "events": 20,
            "transactions": 10,
            "feedback": 10,
            "decisions": 10,
        }
    online = pd.DataFrame(
        [decisions[i]["request"]["features"] for i in gold.TRANSACTION_ID], columns=FEATURE_COLUMNS
    )
    pd.testing.assert_frame_equal(
        online, gold[FEATURE_COLUMNS], check_dtype=False, rtol=1e-12, atol=1e-12
    )
    expected = predict_scores(replay_runtime.pipeline, gold[FEATURE_COLUMNS])
    assert [decisions[i]["response"]["score"] for i in gold.TRANSACTION_ID] == pytest.approx(
        expected, abs=1e-12, rel=0
    )
    assert decisions[0]["request"]["features"]["CUSTOMER_TX_COUNT_1D"] == 0
    assert decisions[1]["request"]["features"]["CUSTOMER_TX_COUNT_1D"] == 0
    assert decisions[2]["request"]["features"]["CUSTOMER_TX_COUNT_1D"] == 2
    assert decisions[5]["request"]["features"]["TERMINAL_KNOWN_LABEL_COUNT_7D"] == 0
    assert decisions[6]["request"]["features"]["TERMINAL_KNOWN_LABEL_COUNT_7D"] == 2


def test_restart_reuses_immutable_decision_without_rescoring(
    tmp_path, replay_events, replay_checksum, replay_runtime, monkeypatch
):
    path = tmp_path / "checkpoint.sqlite"
    event = replay_events[0]
    with executor(path, replay_checksum, replay_runtime) as replay:
        original = replay.handle(event)
    with executor(path, replay_checksum, replay_runtime) as replay:
        with monkeypatch.context() as patch:
            patch.setattr(
                replay_runtime, "score", lambda _: pytest.fail("Repeated event was rescored")
            )
            repeated = replay.handle(event)
        assert repeated == {**original, "reused": True}
        for incoming in replay_events[1:]:
            replay.handle(incoming)
        assert replay.summary()["counts"]["decisions"] == 10
    before = path.read_bytes()
    assert inspect_checkpoint(path)["counts"]["events"] == 20
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "failure", ["changed_duplicate", "late", "orphan", "early_label", "late_label"]
)
def test_invalid_event_does_not_advance_checkpoint_or_rewrite_decisions(
    tmp_path, replay_events, replay_checksum, failure
):
    first = replay_events[0]
    later = next(
        e for e in replay_events if isinstance(e, TransactionEvent) and e.transaction_id == 3
    )
    with executor(tmp_path / "checkpoint.sqlite", replay_checksum) as replay:
        original = replay.handle(first)
        replay.handle(later)
        before = replay.summary()
        if failure == "changed_duplicate":
            invalid = first.model_copy(update={"amount": 1.0})
        elif failure == "late":
            invalid = first.model_copy(update={"transaction_id": 100})
        else:
            invalid = FeedbackEvent(
                schema_version=1,
                event_type="feedback",
                transaction_id=100 if failure == "orphan" else first.transaction_id,
                available_at="2018-04-07T00:00:00"
                if failure == "early_label"
                else "2018-04-09T00:00:00"
                if failure == "late_label"
                else "2018-04-08T00:00:00",
                fraud_label=1,
            )
        with pytest.raises(ReplayContractError):
            replay.handle(invalid)
        assert replay.summary() == before
        assert replay.handle(first) == {**original, "reused": True}


def test_missing_feedback_never_leaks_offline_targets(tmp_path, replay_events, replay_checksum):
    with executor(tmp_path / "checkpoint.sqlite", replay_checksum) as replay:
        decisions = [replay.handle(e) for e in replay_events if isinstance(e, TransactionEvent)]
    assert all(
        result["request"]["features"]["TERMINAL_KNOWN_LABEL_COUNT_7D"] == 0 for result in decisions
    )


@pytest.mark.parametrize("changed", ["run_id", "input_sha256", "runtime", "source", "environment"])
def test_restart_rejects_changed_execution_identity(
    tmp_path, replay_checksum, replay_runtime, changed, monkeypatch
):
    path = tmp_path / "checkpoint.sqlite"
    with executor(path, replay_checksum):
        pass
    before = path.read_bytes()
    options = {"run_id": "synthetic-test-v1", "input_sha256": replay_checksum}
    if changed == "environment":
        monkeypatch.setattr(
            "fraud_detection_mlops.streaming.replay.temporal_replay_executor.feature_environment",
            lambda: {"pandas": "changed"},
        )
    elif changed == "runtime":
        options["runtime"] = replay_runtime
    elif changed == "source":
        monkeypatch.setattr(
            "fraud_detection_mlops.streaming.replay.temporal_replay_executor.source_identity",
            lambda: "f" * 64,
        )
    else:
        options[changed] = "another-run" if changed == "run_id" else "0" * 64
    with pytest.raises(ReplayContractError):
        TemporalReplayExecutor(path, **options)
    assert path.read_bytes() == before


def test_scoring_failure_rolls_back_transaction_features_and_checkpoint(
    tmp_path, replay_events, replay_checksum, replay_runtime, monkeypatch
):
    with executor(tmp_path / "checkpoint.sqlite", replay_checksum, replay_runtime) as replay:
        before = replay.summary()
        with monkeypatch.context() as patch:

            def fail(_):
                raise RuntimeError("Scoring failed")

            patch.setattr(replay_runtime, "score", fail)
            with pytest.raises(RuntimeError, match="Scoring failed"):
                replay.handle(replay_events[0])
        assert replay.summary() == before
        assert not replay.handle(replay_events[0])["reused"]


def test_process_exit_during_scoring_leaves_no_partial_event(
    tmp_path, replay_events, replay_checksum, replay_release, replay_runtime
):
    path = tmp_path / "checkpoint.sqlite"
    with executor(path, replay_checksum, replay_runtime):
        pass
    script = """
import json, os, sys
from pathlib import Path
from fraud_detection_mlops.serving.model_release import load_release
from fraud_detection_mlops.streaming.contracts.transaction_events import parse_event
from fraud_detection_mlops.streaming.replay.temporal_replay_executor import TemporalReplayExecutor
runtime=load_release(Path(sys.argv[2]),sys.argv[3])
runtime.score=lambda request: os._exit(23)
with TemporalReplayExecutor(Path(sys.argv[1]),run_id="synthetic-test-v1",input_sha256=sys.argv[4],runtime=runtime) as replay:
    replay.handle(parse_event(json.loads(sys.argv[5])))
"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(path),
            str(replay_release[0]),
            replay_release[1],
            replay_checksum,
            replay_events[0].model_dump_json(),
        ],
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 23, result.stderr.decode()
    with executor(path, replay_checksum, replay_runtime) as replay:
        assert replay.summary()["counts"] == {
            "events": 0,
            "transactions": 0,
            "feedback": 0,
            "decisions": 0,
        }
        assert not replay.handle(replay_events[0])["reused"]
        assert replay.summary()["counts"]["decisions"] == 1


def test_two_writers_publish_one_committed_decision(tmp_path, replay_events, replay_checksum):
    path = tmp_path / "checkpoint.sqlite"
    with executor(path, replay_checksum):
        pass

    def accept():
        with executor(path, replay_checksum) as replay:
            return replay.handle(replay_events[0])

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: accept(), range(2)))
    assert sorted(r["reused"] for r in results) == [False, True]
    assert inspect_checkpoint(path)["counts"]["decisions"] == 1


def test_foreign_database_is_preserved_and_inspection_never_creates_a_missing_file(
    tmp_path, replay_checksum
):
    path = tmp_path / "foreign.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE user_data (value TEXT)")
        connection.execute("INSERT INTO user_data VALUES ('preserve')")
    before = path.read_bytes()
    with pytest.raises(ReplayContractError):
        executor(path, replay_checksum)
    assert path.read_bytes() == before
    missing = tmp_path / "missing.sqlite"
    with pytest.raises(sqlite3.OperationalError):
        inspect_checkpoint(missing)
    assert not missing.exists()


def test_cli_partial_run_resume_and_complete_repetition(tmp_path):
    path = tmp_path / "demo.sqlite"
    command = [
        sys.executable,
        "-m",
        "fraud_detection_mlops.streaming.replay.temporal_replay_executor",
        "demo",
        "--state",
        str(path),
    ]
    partial = json.loads(subprocess.check_output([*command, "--stop-after", "6"], timeout=60))
    complete = json.loads(subprocess.check_output(command, timeout=60))
    repeated = json.loads(subprocess.check_output(command, timeout=60))
    assert not partial["complete"] and partial["counts"]["events"] == 6
    assert complete["complete"] and complete["counts"]["events"] == 20
    assert repeated["accepted_this_invocation"] == 0 and repeated["reused_this_invocation"] == 22
    assert complete["counts"] == repeated["counts"]
    assert repeated["data_scope"] == "synthetic_fixture"
    assert repeated["native_replay_evaluated"] is False


def test_peer_order_is_irrelevant_and_feedback_at_exact_boundary_is_not_usable(
    tmp_path, replay_events, replay_checksum
):
    for ordering in (False, True):
        events = list(replay_events[:20])
        if ordering:
            groups = {}
            for event in events:
                groups.setdefault(event.event_clock_ns, []).append(event)
            events = [event for group in groups.values() for event in reversed(group)]
        outcomes = {}
        with executor(tmp_path / f"order-{ordering}.sqlite", replay_checksum) as replay:
            for event in events:
                result = replay.handle(event)
                if result.get("request"):
                    outcomes[event.transaction_id] = result["request"]
        if not ordering:
            baseline = outcomes
        else:
            assert outcomes == baseline
