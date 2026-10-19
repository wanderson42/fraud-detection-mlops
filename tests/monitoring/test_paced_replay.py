"""Recovery, source pacing and independently reconstructed decisions."""

from contextlib import closing
import sqlite3
import time

import numpy as np
import pandas as pd
import pytest
from scripts.monitoring.collector import snapshot
from scripts.monitoring.telemetry import Journal
from scripts.replay.paced_triage_replay import execute

from fraud_detection_mlops.artifacts import sha256
from fraud_detection_mlops.data.contracts.transaction_schema import DTYPES
from fraud_detection_mlops.features.deterministic_causal_history import deterministic_features
from fraud_detection_mlops.features.feature_schema import FEATURE_COLUMNS
from fraud_detection_mlops.streaming.evaluation.native_triage_assessment import ledger_frame
from fraud_detection_mlops.streaming.replay.numerical_corrective_replay import saved_decisions


def run(s, **kwargs):
    return execute(s["factory"], s["output"], s["metadata"], s["runtime"], s["policy"], **kwargs)


def test_resume_exactly_once_and_independent_nineteen_feature_parity(setup_job, monkeypatch):
    s = setup_job
    assert not run(s, max_paced_events=8)["complete"]
    result = run(s)
    assert result["complete"] and result["counts"]["events"] == 20
    before = sha256(s["output"] / "checkpoint.sqlite")
    monkeypatch.setattr(s["runtime"], "score", lambda _: pytest.fail("completed event rescored"))
    assert run(s)["new_paced_events"] == 0
    assert sha256(s["output"] / "checkpoint.sqlite") == before
    labels = {e.transaction_id: e.fraud_label for e in s["events"] if e.event_type == "feedback"}
    records = []
    for e in s["events"]:
        if e.event_type != "transaction":
            continue
        stamp = pd.Timestamp(e.tx_datetime)
        seconds = int((stamp - pd.Timestamp("2018-04-01")).total_seconds())
        records.append(
            {
                "TRANSACTION_ID": e.transaction_id,
                "TX_DATETIME": stamp,
                "CUSTOMER_ID": e.customer_id,
                "TERMINAL_ID": e.terminal_id,
                "TX_AMOUNT": e.amount,
                "TX_TIME_SECONDS": seconds,
                "TX_TIME_DAYS": seconds // 86400,
                "TX_FRAUD": labels[e.transaction_id],
                "TX_FRAUD_SCENARIO": labels[e.transaction_id],
            }
        )
    oracle = deterministic_features(pd.DataFrame(records).astype(DTYPES))
    # Scoring against independently reconstructed rows uses the original loaded pipeline.
    oracle["offline_score"] = s["runtime"].pipeline.predict_proba(oracle[FEATURE_COLUMNS])[:, 1]
    protocol = {
        "parity": {
            "float_features_rtol": 0.0,
            "float_features_atol": 0.0,
            "scores_rtol": 0.0,
            "scores_atol": 1e-12,
        }
    }
    frame = ledger_frame(s["output"] / "checkpoint.sqlite", oracle, s["policy"], protocol)
    actual = saved_decisions(s["output"] / "checkpoint.sqlite")
    ordered = oracle.sort_values("TRANSACTION_ID")
    assert all(
        np.array_equal([r[1][name] for r in actual], ordered[name]) for name in FEATURE_COLUMNS
    )
    assert len(frame) == 10
    observed = snapshot(s["output"])
    assert observed["quality"]["mature_transactions"] == 10
    assert observed["unmeasured_committed_events"] == 0
    assert len(observed["durations"]["processing"]) == 10


def test_commit_before_timing_failure_is_recovered_without_invented_time(setup_job, monkeypatch):
    s = setup_job
    original = Journal.committed

    def fail(*_):
        raise OSError("generated timing interruption after atomic decision commit")

    monkeypatch.setattr(Journal, "committed", fail)
    with pytest.raises(OSError, match="timing interruption"):
        run(s)
    monkeypatch.setattr(Journal, "committed", original)
    assert run(s)["complete"]
    data = snapshot(s["output"])
    assert data["counts"]["events"] == 20 and data["unmeasured_committed_events"] == 1
    assert len(data["durations"]["processing"]) == 9
    assert data["pending_uncommitted_events"] == 0


def test_producer_failure_propagates_and_preserves_commits(setup_job):
    s = setup_job

    def broken(after):
        yield s["events"][0]
        raise OSError("generated source read failure")

    s["factory"] = broken
    with pytest.raises(OSError, match="source read"):
        run(s)
    assert snapshot(s["output"])["counts"]["events"] == 1


def test_changed_identity_cannot_mutate_checkpoint(setup_job):
    s = setup_job
    run(s, max_paced_events=4)
    before = sha256(s["output"] / "checkpoint.sqlite")
    s["metadata"]["speed"] = 100
    with pytest.raises(ValueError, match="identity changed"):
        run(s)
    assert sha256(s["output"] / "checkpoint.sqlite") == before


def test_context_is_excluded_from_timing(setup_job):
    s = setup_job
    s["metadata"]["scoring_window"] = ["2018-04-08T00:00:00", "2018-04-16T00:00:00"]
    result = run(s)
    data = snapshot(s["output"])
    assert result["new_warmup_events"] == 5 and data["context_events"] == 5
    assert data["counts"]["decisions"] == 5 and len(data["durations"]["processing"]) == 5
    assert data["unmeasured_committed_events"] == 0


def test_one_x_uses_relative_source_intervals_instead_of_historical_epoch(setup_job):
    s = setup_job
    first = s["events"][0]
    events = [
        first.model_copy(
            update={"transaction_id": i, "tx_datetime": f"2018-04-01T00:00:00.{i * 30:03d}"}
        )
        for i in range(3)
    ]
    s["factory"] = lambda after: iter(events)
    s["metadata"].update(
        speed=1.0, expected_counts={"events": 3, "transactions": 3, "feedback": 0}
    )
    started = time.perf_counter()
    assert run(s)["complete"]
    assert 0.055 <= time.perf_counter() - started < 5
    with closing(sqlite3.connect(s["output"] / "telemetry.sqlite")) as con:
        stamps = [r[0] for r in con.execute("SELECT scheduled FROM timings ORDER BY source_ns")]
    assert stamps[-1] - stamps[0] == pytest.approx(0.06, abs=1e-6)


@pytest.mark.parametrize("speed,capacity", [(0, 1), (float("nan"), 1), (1, 0), (1, 10001)])
def test_invalid_load_parameters_fail_early(setup_job, speed, capacity):
    from scripts.replay.paced_triage_replay import make_metadata

    s = setup_job
    with pytest.raises(ValueError):
        make_metadata(
            input_sha256="a" * 64,
            runtime=s["runtime"],
            policy=s["policy"],
            speed=speed,
            capacity=capacity,
            window=None,
            expected_counts={},
            baseline=[1] * 10,
            source_scope="generated_fixture",
            code={},
        )


def test_failure_before_commit_retry_does_not_become_a_false_timing_gap(setup_job, monkeypatch):
    from scripts.replay.paced_triage_replay import DeterministicTriageReplay

    s = setup_job
    original = DeterministicTriageReplay.handle

    def fail(*_):
        raise OSError("generated failure before commit")

    monkeypatch.setattr(DeterministicTriageReplay, "handle", fail)
    with pytest.raises(OSError, match="before commit"):
        run(s)
    monkeypatch.setattr(DeterministicTriageReplay, "handle", original)
    assert run(s)["complete"]
    assert snapshot(s["output"])["unmeasured_committed_events"] == 0
    assert run(s)["status"] == "reused"
    assert snapshot(s["output"])["unmeasured_committed_events"] == 0
