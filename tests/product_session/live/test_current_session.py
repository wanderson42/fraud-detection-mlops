"""Opt-in native champion session; no training, Registry writes or route changes."""

import json
import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("FRAUD_PRODUCT_SESSION_LIVE") != "1", reason="Opt-in native session"
)


def test_native_session_arrivals_pause_resume_feedback_and_reconciliation():
    from scripts.platform import kind_foundation as f
    from scripts.product_flow import controller as flow
    from scripts.product_session import controller as session
    from scripts.product_session import reader

    assert os.environ.get("FRAUD_SESSION_NO_OTHER_WRITERS") == "1"
    key = os.environ.get("FRAUD_SESSION_ID", "arrival-v1")
    directory, value = session.load(key)
    assert not (directory / "checkpoint.sqlite").exists(), (
        "Use a fresh prepared session; preserve existing checkpoints"
    )
    pin = reader.sha(directory / "session.json")
    first = session.execute(key, True, 0.05, False, 1)
    paused = reader.read(directory, pin)
    assert first["status"] == "paused" and len(paused["cases"]) == 1
    assert paused["cases"][0]["transaction_feedback"] is None
    second = session.execute(key, True, 0.05, False, None)
    arrived = reader.read(directory, pin)
    assert second["status"] == "awaiting_feedback"
    assert arrived["counts"]["decisions"] == value["capture"]["counts"]["decisions"]
    assert all(c["transaction_feedback"] is None for c in arrived["cases"])
    final = session.execute(key, True, 0.05, True, None)
    assert final["status"] == "complete"
    verified = flow.verify_ledger(directory, value)
    with flow.target(value) as scorer:
        reused = flow.run_events(directory, value, scorer)
    assert reused["new_decisions"] == reused["scoring_HTTP_attempts_this_invocation"] == 0
    result = {
        "schema": "fraud_product_session_native_rehearsal_v1",
        "status": "success",
        "session_sha256": pin,
        "first": first,
        "arrivals": second,
        "feedback": final,
        "verification": verified,
        "completed_reuse": reused,
        "champion_modified": False,
        "visual_review_validated": False,
        "same_session_Grafana_validated": False,
    }
    f.private_json(directory / "native-rehearsal.json", result)
    print(json.dumps(result, indent=2))
