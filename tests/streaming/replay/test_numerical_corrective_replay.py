"""Corrective replay isolation, resumability and strict independent parity on generated data."""

from contextlib import closing
import json
import shutil
import sqlite3

import pytest

from fraud_detection_mlops.artifacts import sha256
from fraud_detection_mlops.streaming.replay import native_causal_replay as native
from fraud_detection_mlops.streaming.replay import numerical_corrective_replay as correction


@pytest.fixture
def correction_source(source, monkeypatch):
    receipt = native.prepare(
        source["silver"], source["release"], source["threshold"], source["input"], source["root"]
    )

    def fail_parity(*_):
        raise ValueError("generated final parity failure")

    monkeypatch.setattr(native, "assessment_report", fail_parity)
    with pytest.raises(ValueError, match="generated final parity failure"):
        native.run(
            source["input"],
            receipt["manifest_sha256"],
            source["release"],
            source["threshold"],
            source["output"],
            source["root"],
        )
    state = source["output"] / "checkpoint.sqlite"
    manifest = json.loads((source["input"] / "manifest.json").read_text())
    rule = correction.correction_protocol(source["root"])
    rule.update(
        original_input_manifest_sha256=receipt["manifest_sha256"],
        original_checkpoint_sha256=sha256(state),
        original_git_revision=manifest["provenance"]["git_revision"],
        original_provenance=manifest["provenance"]["files"],
    )
    monkeypatch.setattr(correction, "correction_protocol", lambda *_: rule)
    monkeypatch.setattr(
        correction, "identified_runtime", lambda *_: (source["runtime"], source["config"])
    )
    return {
        **source,
        "receipt": receipt,
        "state": state,
        "original_hash": sha256(state),
        "corrective_input": source["input"].with_name("corrective-input"),
        "corrective_output": source["output"].with_name("corrective-job"),
    }


def prepare(source):
    return correction.prepare(
        source["input"],
        source["receipt"]["manifest_sha256"],
        source["state"],
        source["release"],
        source["threshold"],
        source["corrective_input"],
        source["root"],
    )


def run(source, receipt, **kwargs):
    return correction.run(
        source["corrective_input"],
        receipt["manifest_sha256"],
        source["release"],
        source["threshold"],
        source["corrective_output"],
        source["root"],
        **kwargs,
    )


def test_corrective_workflow_removes_silver_resumes_and_verifies_read_only(
    correction_source, monkeypatch
):
    s = correction_source
    shutil.rmtree(s["source_root"])
    receipt = prepare(s)
    stopped = run(s, receipt, stop_after=110)
    assert not stopped["complete"]
    result = run(s, receipt)
    assert result["complete"] and result["feature_parity_exact"]
    assert result["score_parity_atol"] == 1e-12 and result["score_parity_rtol"] == 0.0
    assert result["counts"]["decisions"] == 45 and result["counts"]["alerts"] == 15
    assert result["comparison_to_original"]["threshold_eligibility_changes"] == 0
    assert result["comparison_to_original"]["alert_status_changes"] == 0
    assert sha256(s["state"]) == s["original_hash"]
    new_state = s["corrective_output"] / "checkpoint.sqlite"
    state_hash = sha256(new_state)
    monkeypatch.setattr(s["runtime"], "score", lambda *_: pytest.fail("completed replay rescored"))
    assert run(s, receipt)["reused"]
    verified = correction.verify(
        s["corrective_input"],
        receipt["manifest_sha256"],
        s["corrective_output"],
        s["root"],
        result["receipt_sha256"],
    )
    assert verified["quality"] == result["quality"]
    assert sha256(new_state) == state_hash and sha256(s["state"]) == s["original_hash"]


@pytest.mark.parametrize(
    "changed", ["original_checkpoint", "original_input", "digest", "output", "overlap"]
)
def test_original_guards_reject_before_preparing_corrective_input(correction_source, changed):
    s = correction_source
    if changed == "original_checkpoint":
        with closing(sqlite3.connect(s["state"])) as connection, connection:
            connection.execute(
                "UPDATE events SET payload='{}' WHERE event_id=(SELECT event_id FROM events LIMIT 1)"
            )
    elif changed == "original_input":
        (s["input"] / "oracle.parquet").write_bytes(b"changed")
    elif changed == "digest":
        s["receipt"]["manifest_sha256"] = "0" * 64
    elif changed == "output":
        s["corrective_input"].mkdir()
    else:
        s["corrective_input"] = s["input"] / "correction"
    with pytest.raises(ValueError):
        prepare(s)
    assert not (s["corrective_input"] / "manifest.json").exists()


@pytest.mark.parametrize("name", ["oracle.parquet", "manifest.json"])
def test_corrective_input_tampering_cannot_resume(correction_source, name):
    s = correction_source
    receipt = prepare(s)
    run(s, receipt, stop_after=110)
    state = s["corrective_output"] / "checkpoint.sqlite"
    before = sha256(state)
    (s["corrective_input"] / name).write_bytes(b"changed")
    with pytest.raises(ValueError):
        run(s, receipt)
    assert sha256(state) == before and sha256(s["state"]) == s["original_hash"]


def test_atomic_assessment_failure_keeps_committed_corrective_ledger(
    correction_source, monkeypatch
):
    s = correction_source
    receipt = prepare(s)
    write_json = correction.write_json

    def fail_receipt(path, *args, **kwargs):
        if path.name == "receipt.json":
            raise OSError("generated publication interruption")
        return write_json(path, *args, **kwargs)

    monkeypatch.setattr(correction, "write_json", fail_receipt)
    with pytest.raises(OSError, match="publication interruption"):
        run(s, receipt)
    new_state = s["corrective_output"] / "checkpoint.sqlite"
    before = sha256(new_state)
    assert not (s["corrective_output"] / "assessment").exists()
    monkeypatch.setattr(correction, "write_json", write_json)
    monkeypatch.setattr(
        s["runtime"], "score", lambda *_: pytest.fail("committed corrective scores replaced")
    )
    assert run(s, receipt)["complete"]
    assert sha256(new_state) == before and sha256(s["state"]) == s["original_hash"]


@pytest.mark.parametrize("changed", ["feature", "score", "decision", "event", "alert"])
def test_corrective_semantic_parity_is_enforced(correction_source, changed):
    s = correction_source
    receipt = prepare(s)
    run(s, receipt)
    state = s["corrective_output"] / "checkpoint.sqlite"
    with closing(sqlite3.connect(state)) as connection, connection:
        tx, request, response = connection.execute("SELECT * FROM decisions LIMIT 1").fetchone()
        if changed == "feature":
            value = json.loads(request)
            value["features"]["CUSTOMER_AVG_AMOUNT_1D"] += 1e-11
            connection.execute(
                "UPDATE decisions SET request=? WHERE transaction_id=?", (json.dumps(value), tx)
            )
        elif changed == "score":
            value = json.loads(response)
            value["score"] = 0.123
            connection.execute(
                "UPDATE decisions SET response=? WHERE transaction_id=?", (json.dumps(value), tx)
            )
        elif changed == "decision":
            value = json.loads(
                connection.execute(
                    "SELECT payload FROM alert_decisions WHERE transaction_id=?", (tx,)
                ).fetchone()[0]
            )
            value["status"] = "below_threshold"
            connection.execute(
                "UPDATE alert_decisions SET payload=? WHERE transaction_id=?",
                (json.dumps(value), tx),
            )
        elif changed == "event":
            connection.execute(
                "UPDATE events SET payload='{}' WHERE event_id=?", (f"transaction:{tx}",)
            )
        else:
            connection.execute("DELETE FROM alerts WHERE transaction_id=?", (tx,))
    manifest = json.loads((s["corrective_input"] / "manifest.json").read_text())
    with pytest.raises(ValueError):
        correction.assessment_report(s["corrective_input"], manifest, state)
    assert sha256(s["state"]) == s["original_hash"]


def test_external_result_digest_and_original_preservation_are_checked(correction_source):
    s = correction_source
    receipt = prepare(s)
    result = run(s, receipt)
    with pytest.raises(ValueError, match="external receipt"):
        correction.verify(
            s["corrective_input"],
            receipt["manifest_sha256"],
            s["corrective_output"],
            s["root"],
            "0" * 64,
        )
    (s["corrective_output"] / "assessment/result.json").write_text("{}")
    with pytest.raises(ValueError, match="artifact changed"):
        correction.verify(
            s["corrective_input"],
            receipt["manifest_sha256"],
            s["corrective_output"],
            s["root"],
            result["receipt_sha256"],
        )
    assert sha256(s["state"]) == s["original_hash"]
