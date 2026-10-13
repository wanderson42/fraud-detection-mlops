"""Atomic alerts, real process interruption and legacy checkpoint compatibility."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import hashlib
import json
import sqlite3
import subprocess
import sys

import pytest

from fraud_detection_mlops.serving.request_schema import ScoreResponse
from fraud_detection_mlops.streaming.contracts.customer_day_alerts import CustomerDayAlertConfig
from fraud_detection_mlops.streaming.contracts.transaction_events import (
    TransactionEvent,
    canonical_event,
)
from fraud_detection_mlops.streaming.decisions.customer_day_alert_policy import (
    CustomerDayAlertPolicy,
)
from fraud_detection_mlops.streaming.replay.durable_triage_replay import (
    DurableTriageReplay,
    ReplayContractError,
    inspect_checkpoint,
)
from fraud_detection_mlops.streaming.replay.temporal_replay_executor import (
    TemporalReplayExecutor,
)
from fraud_detection_mlops.streaming.replay.temporal_replay_executor import (
    inspect_checkpoint as inspect_legacy,
)


def policy(runtime, threshold=0.7, budget=100):
    return CustomerDayAlertConfig(
        schema_version=1,
        version="first_crossing_customer_day_v1",
        model_sha256=runtime.manifest.model_sha256,
        serving_release_id=runtime.manifest.serving_release_id,
        score_threshold=threshold,
        daily_customer_budget=budget,
    )


def checksum(events):
    return hashlib.sha256("\n".join(canonical_event(e) for e in events).encode()).hexdigest()


def replay(path, runtime, config, digest):
    return DurableTriageReplay(
        path,
        run_id="durable-fixture-v1",
        input_sha256=digest,
        runtime=runtime,
        policy=config,
        data_scope="synthetic_fixture",
    )


def transaction(identifier, customer, clock="2018-04-01T10:00:00"):
    return TransactionEvent(
        schema_version=1,
        event_type="transaction",
        transaction_id=identifier,
        customer_id=customer,
        terminal_id=1,
        amount=10.0,
        tx_datetime=clock,
    )


def controlled_scores(runtime, monkeypatch, score=0.8):
    def predict(request):
        return ScoreResponse(
            schema_version=1,
            transaction_id=request.transaction_id,
            score=score,
            score_semantics="uncalibrated_ranking",
            model_id=runtime.manifest.model_id,
            model_sha256=runtime.manifest.model_sha256,
            feature_contract_version="gold_v1",
            serving_release_id=runtime.manifest.serving_release_id,
        )

    monkeypatch.setattr(runtime, "score", predict)


def test_real_scores_features_and_alerts_match_legacy_and_memory_policy(
    tmp_path, replay_events, replay_checksum, replay_runtime
):
    config = policy(replay_runtime)
    memory = CustomerDayAlertPolicy(config)
    with (
        replay(tmp_path / "durable.sqlite", replay_runtime, config, replay_checksum) as durable,
        TemporalReplayExecutor(
            tmp_path / "legacy.sqlite",
            run_id="legacy-fixture-v1",
            input_sha256=replay_checksum,
            runtime=replay_runtime,
        ) as legacy,
    ):
        for event in replay_events:
            current = durable.handle(event)
            old = legacy.handle(event)
            if isinstance(event, TransactionEvent):
                assert (
                    current["request"] == old["request"] and current["response"] == old["response"]
                )
                response = ScoreResponse.model_validate(current["response"])
                assert current["alert"] == asdict(memory.handle(event, response))
        assert durable.summary()["counts"] == {
            "events": 20,
            "transactions": 10,
            "feedback": 10,
            "decisions": 10,
            "alert_decisions": 10,
            "alerts": 1,
        }


def test_restart_repeated_delivery_never_rescores_or_changes_budget(
    tmp_path, replay_events, replay_checksum, replay_runtime, monkeypatch
):
    path = tmp_path / "durable.sqlite"
    config = policy(replay_runtime)
    first = replay_events[0]
    with replay(path, replay_runtime, config, replay_checksum) as durable:
        original = durable.handle(first)
    with replay(path, replay_runtime, config, replay_checksum) as durable:
        with monkeypatch.context() as patch:
            patch.setattr(replay_runtime, "score", lambda _: pytest.fail("Duplicate rescored"))
            assert durable.handle(first) == {**original, "reused": True}
        for event in replay_events[1:]:
            durable.handle(event)
        before = durable.summary()
        with monkeypatch.context() as patch:
            patch.setattr(replay_runtime, "score", lambda _: pytest.fail("Old event rescored"))
            assert all(durable.handle(event)["reused"] for event in replay_events)
        assert durable.summary() == before
    raw = path.read_bytes()
    assert inspect_checkpoint(path)["counts"] == before["counts"]
    assert path.read_bytes() == raw


def test_budget_and_customer_reset_survive_restart_without_replacement(
    tmp_path, replay_runtime, monkeypatch
):
    controlled_scores(replay_runtime, monkeypatch)
    events = [
        transaction(1, 1),
        transaction(2, 2),
        transaction(3, 3),
        transaction(4, 1),
        transaction(5, 1, "2018-04-02T00:00:00"),
    ]
    config = policy(replay_runtime, budget=2)
    digest = checksum(events)
    path = tmp_path / "budget.sqlite"
    with replay(path, replay_runtime, config, digest) as durable:
        saved = [durable.handle(e)["alert"] for e in events[:2]]
    with replay(path, replay_runtime, config, digest) as durable:
        denied = durable.handle(events[2])["alert"]
        repeated_customer = durable.handle(events[3])["alert"]
        tomorrow = durable.handle(events[4])["alert"]
        assert denied["status"] == "budget_exhausted" and denied["daily_budget_used"] == 2
        assert (
            repeated_customer["status"] == "already_alerted"
            and repeated_customer["daily_budget_used"] == 2
        )
        assert tomorrow["status"] == "alert" and tomorrow["daily_budget_used"] == 1
        assert tomorrow["alert_id"] != saved[0]["alert_id"]
        assert durable.summary()["counts"]["alerts"] == 3
        assert [
            json.loads(r[0])
            for r in durable.connection.execute(
                "SELECT payload FROM alerts ORDER BY transaction_id"
            )
        ] == [*saved, tomorrow]


@pytest.mark.parametrize("failure", ["scorer", "after_alert", "before_checkpoint"])
def test_failure_rolls_back_all_event_score_alert_and_progress_rows(
    tmp_path, replay_runtime, monkeypatch, failure
):
    event = transaction(1, 1)
    config = policy(replay_runtime, threshold=0.0)
    path = tmp_path / "rollback.sqlite"
    with replay(path, replay_runtime, config, checksum([event])) as durable:
        before = durable.summary()
        with monkeypatch.context() as patch:
            if failure == "scorer":
                patch.setattr(
                    replay_runtime,
                    "score",
                    lambda _: (_ for _ in ()).throw(RuntimeError("scorer failed")),
                )
            else:
                trigger = (
                    "AFTER INSERT ON alerts"
                    if failure == "after_alert"
                    else "BEFORE UPDATE ON checkpoint"
                )
                durable.connection.execute(
                    f"CREATE TEMP TRIGGER inject_failure {trigger} BEGIN SELECT RAISE(ABORT,'injected failure'); END"
                )
            with pytest.raises((RuntimeError, sqlite3.DatabaseError)):
                durable.handle(event)
        assert durable.summary() == before
        if failure != "scorer":
            durable.connection.execute("DROP TRIGGER inject_failure")
        assert durable.handle(event)["alert"]["status"] == "alert"
        assert durable.summary()["counts"]["alerts"] == 1


@pytest.mark.parametrize("exit_moment", ["before_commit", "after_commit"])
def test_real_process_exit_at_commit_boundary_recovers_atomically(
    tmp_path, replay_runtime, replay_release, monkeypatch, exit_moment
):
    event = transaction(1, 1)
    config = policy(replay_runtime, threshold=0.0)
    digest = checksum([event])
    path = tmp_path / "crash.sqlite"
    with replay(path, replay_runtime, config, digest) as durable:
        before = durable.summary()
    script = """
import json, os, sys
from pathlib import Path
from threadpoolctl import threadpool_limits
from fraud_detection_mlops.serving.model_release import load_release
from fraud_detection_mlops.streaming.contracts.customer_day_alerts import CustomerDayAlertConfig
from fraud_detection_mlops.streaming.contracts.transaction_events import parse_event
from fraud_detection_mlops.streaming.replay.durable_triage_replay import DurableTriageReplay
with threadpool_limits(limits=4):
    runtime=load_release(Path(sys.argv[2]),sys.argv[3])
    config=CustomerDayAlertConfig.model_validate_json(sys.argv[5])
    with DurableTriageReplay(Path(sys.argv[1]),run_id='durable-fixture-v1',input_sha256=sys.argv[4],runtime=runtime,policy=config,data_scope='synthetic_fixture') as replay:
        if sys.argv[7]=='before_commit':
            replay.connection.create_function('exit_now',0,lambda:os._exit(23))
            replay.connection.execute('CREATE TEMP TRIGGER crash BEFORE UPDATE ON checkpoint BEGIN SELECT exit_now(); END')
        replay.handle(parse_event(json.loads(sys.argv[6])))
        os._exit(23)
"""
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(path),
            str(replay_release[0]),
            replay_release[1],
            digest,
            config.model_dump_json(),
            event.model_dump_json(),
            exit_moment,
        ],
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert child.returncode == 23, child.stderr.decode()
    with replay(path, replay_runtime, config, digest) as durable:
        if exit_moment == "before_commit":
            assert durable.summary() == before
        with monkeypatch.context() as patch:
            if exit_moment == "after_commit":
                patch.setattr(
                    replay_runtime, "score", lambda _: pytest.fail("Lost acknowledgement rescored")
                )
            recovered = durable.handle(event)
        assert recovered["alert"]["status"] == "alert"
        assert recovered["reused"] == (exit_moment == "after_commit")
        assert durable.summary()["counts"]["alerts"] == 1
    with replay(path, replay_runtime, config, digest) as durable:
        assert durable.handle(event) == {**recovered, "reused": True}


@pytest.mark.parametrize(
    "failure",
    [
        "conflict",
        "late",
        "peer_reverse",
        "feedback_after_peer",
        "orphan_feedback",
        "early_feedback",
        "late_feedback",
    ],
)
def test_rejected_event_does_not_change_durable_alert_state(tmp_path, replay_runtime, failure):
    from fraud_detection_mlops.streaming.contracts.transaction_events import FeedbackEvent

    first = transaction(2, 1)
    with replay(
        tmp_path / "invalid.sqlite",
        replay_runtime,
        policy(replay_runtime, threshold=0.0),
        checksum([first]),
    ) as durable:
        original = durable.handle(first)
        if failure == "feedback_after_peer":
            durable.handle(transaction(3, 2, "2018-04-08T10:00:00"))
        before = durable.summary()
        if failure == "conflict":
            invalid = first.model_copy(update={"amount": 20.0})
        elif failure == "late":
            invalid = transaction(3, 2, "2018-04-01T09:59:59")
        elif failure == "peer_reverse":
            invalid = transaction(1, 2)
        else:
            invalid = FeedbackEvent(
                schema_version=1,
                event_type="feedback",
                transaction_id=999 if failure == "orphan_feedback" else 2,
                available_at="2018-04-08T10:00:00"
                if failure in ("orphan_feedback", "feedback_after_peer")
                else "2018-04-09T10:00:00"
                if failure == "late_feedback"
                else "2018-04-07T10:00:00",
                fraud_label=1,
            )
        with pytest.raises(ReplayContractError):
            durable.handle(invalid)
        assert durable.summary() == before
        assert durable.handle(first) == {**original, "reused": True}


@pytest.mark.parametrize("change", ["threshold", "budget", "input", "code", "release"])
def test_changed_identity_is_rejected_without_rewriting_state(
    tmp_path, replay_runtime, monkeypatch, change
):
    config = policy(replay_runtime)
    path = tmp_path / "identity.sqlite"
    with replay(path, replay_runtime, config, "a" * 64):
        pass
    raw = path.read_bytes()
    if change in ("threshold", "budget"):
        config = config.model_copy(
            update={"score_threshold": 0.8}
            if change == "threshold"
            else {"daily_customer_budget": 2}
        )
    elif change == "code":
        monkeypatch.setattr(
            "fraud_detection_mlops.streaming.replay.durable_triage_replay.source_identity",
            lambda: "f" * 64,
        )
    elif change == "release":
        config = config.model_copy(update={"serving_release_id": "c" * 32})
    with (
        pytest.raises(ReplayContractError),
        replay(path, replay_runtime, config, "b" * 64 if change == "input" else "a" * 64),
    ):
        pass
    assert path.read_bytes() == raw


def test_two_writers_accept_one_score_and_one_alert(tmp_path, replay_runtime, monkeypatch):
    event = transaction(1, 1)
    digest = checksum([event])
    config = policy(replay_runtime, threshold=0.0)
    path = tmp_path / "writers.sqlite"
    with replay(path, replay_runtime, config, digest):
        pass

    def accept(_):
        with replay(path, replay_runtime, config, digest) as durable:
            return durable.handle(event)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(accept, range(2)))
    assert sorted(r["reused"] for r in results) == [False, True]
    assert results[0]["alert"]["alert_id"] == results[1]["alert"]["alert_id"]
    assert inspect_checkpoint(path)["counts"]["alerts"] == 1


def test_legacy_checkpoint_remains_resumable_and_durable_state_has_distinct_schema(
    tmp_path, replay_events, replay_checksum, replay_runtime
):
    old = tmp_path / "legacy.sqlite"
    with TemporalReplayExecutor(
        old, run_id="legacy-fixture-v1", input_sha256=replay_checksum
    ) as legacy:
        legacy.handle(replay_events[0])
    raw = old.read_bytes()
    with pytest.raises(ReplayContractError, match="preserve earlier state"):
        replay(old, replay_runtime, policy(replay_runtime), replay_checksum)
    assert old.read_bytes() == raw
    with TemporalReplayExecutor(
        old, run_id="legacy-fixture-v1", input_sha256=replay_checksum
    ) as legacy:
        assert legacy.handle(replay_events[0])["reused"]
        legacy.handle(replay_events[1])
    assert inspect_legacy(old)["counts"]["events"] == 2
    new = tmp_path / "durable.sqlite"
    with replay(new, replay_runtime, policy(replay_runtime), replay_checksum):
        pass
    with pytest.raises(ReplayContractError):
        inspect_legacy(new)


def test_readonly_inspection_does_not_create_missing_database(tmp_path):
    missing = tmp_path / "missing.sqlite"
    with pytest.raises(sqlite3.DatabaseError):
        inspect_checkpoint(missing)
    assert not missing.exists()


@pytest.mark.parametrize(
    "field,value", [("transaction_id", 999), ("model_sha256", "f" * 64), ("score", float("nan"))]
)
def test_invalid_score_rolls_back_the_pending_transaction(
    tmp_path, replay_runtime, monkeypatch, field, value
):
    event = transaction(1, 1)
    actual = replay_runtime.score
    monkeypatch.setattr(
        replay_runtime, "score", lambda request: actual(request).model_copy(update={field: value})
    )
    with replay(
        tmp_path / "bad-score.sqlite",
        replay_runtime,
        policy(replay_runtime, threshold=0.0),
        checksum([event]),
    ) as durable:
        before = durable.summary()
        with pytest.raises(ValueError):
            durable.handle(event)
        assert durable.summary() == before


def test_foreign_empty_database_metadata_is_preserved(tmp_path, replay_runtime):
    path = tmp_path / "foreign.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version = 7")
    raw = path.read_bytes()
    with pytest.raises(ReplayContractError, match="Foreign checkpoint"):
        replay(path, replay_runtime, policy(replay_runtime), "a" * 64)
    assert path.read_bytes() == raw


def test_context_warms_history_without_scoring_alerts_and_feedback_can_drain(
    tmp_path, replay_events, replay_runtime, monkeypatch
):
    from fraud_detection_mlops.streaming.contracts.transaction_events import FeedbackEvent

    config = policy(replay_runtime, threshold=0.0)
    selected = [
        e
        for e in replay_events
        if (isinstance(e, TransactionEvent) and e.transaction_id <= 4)
        or (isinstance(e, FeedbackEvent) and e.transaction_id <= 4)
    ]
    digest = checksum(selected)
    window = ("2018-04-02T00:00:00", "2018-04-03T00:00:00")
    actual = replay_runtime.score
    scored = []

    def observe(request):
        scored.append(request.transaction_id)
        return actual(request)

    monkeypatch.setattr(replay_runtime, "score", observe)
    with DurableTriageReplay(
        tmp_path / "context.sqlite",
        run_id="warmup-fixture",
        input_sha256=digest,
        runtime=replay_runtime,
        policy=config,
        scoring_window=window,
        data_scope="synthetic_fixture",
    ) as durable:
        results = [durable.handle(e) for e in selected]
        assert scored == [3, 4]
        assert all(r.get("context_only") for r in results[:3])
        assert all("response" not in r and "alert" not in r for r in results[:3])
        assert results[3]["request"]["features"]["CUSTOMER_TX_COUNT_1D"] == 3
        assert durable.summary()["counts"] == {
            "events": 10,
            "transactions": 5,
            "feedback": 5,
            "decisions": 2,
            "alert_decisions": 2,
            "alerts": 2,
        }
        assert all(durable.handle(e)["reused"] for e in selected)
        before = durable.summary()
        with pytest.raises(ReplayContractError, match="scoring end"):
            durable.handle(transaction(99, 3, "2018-04-10T00:00:00"))
        assert durable.summary() == before


def test_scoring_window_is_bound_to_checkpoint_identity(tmp_path, replay_runtime):
    path = tmp_path / "window.sqlite"
    config = policy(replay_runtime)
    with DurableTriageReplay(
        path,
        run_id="window-fixture",
        input_sha256="a" * 64,
        runtime=replay_runtime,
        policy=config,
        scoring_window=("2018-04-02T00:00:00", "2018-04-03T00:00:00"),
    ):
        pass
    raw = path.read_bytes()
    with (
        pytest.raises(ReplayContractError),
        DurableTriageReplay(
            path,
            run_id="window-fixture",
            input_sha256="a" * 64,
            runtime=replay_runtime,
            policy=config,
            scoring_window=("2018-04-02T00:00:00", "2018-04-04T00:00:00"),
        ),
    ):
        pass
    assert path.read_bytes() == raw


def test_demo_rejects_native_release_before_loading_model_or_creating_state(
    tmp_path, replay_release, monkeypatch
):
    from fraud_detection_mlops.artifacts import sha256, write_json
    from fraud_detection_mlops.streaming.replay import durable_triage_replay as module

    directory = tmp_path / "native-tagged-release"
    directory.mkdir()
    manifest = json.loads((replay_release[0] / "manifest.json").read_text())
    manifest["evidence_kind"] = "verified_reference"
    write_json(directory / "manifest.json", manifest)
    state = tmp_path / "uncreated.sqlite"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "durable-demo",
            "demo",
            "--state",
            str(state),
            "--release",
            str(directory),
            "--manifest-sha256",
            sha256(directory / "manifest.json"),
        ],
    )
    monkeypatch.setattr(
        module, "load_release", lambda *a: pytest.fail("Native model loaded by demo")
    )
    with pytest.raises(SystemExit) as error:
        module.main()
    assert error.value.code == 1
    assert not state.exists()
