"""Opt-in expanded native session: preserves the already accepted 200-decision session."""

import json
import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("FRAUD_PRODUCT_SESSION_SCALE_LIVE") != "1",
    reason="Opt-in expanded native session",
)


def test_expanded_native_HTTP_pages_timings_and_independent_reconciliation():
    from scripts.platform import kind_foundation as f
    from scripts.product_flow import controller as flow
    from scripts.product_session import controller as session
    from scripts.product_session import reader

    assert os.environ.get("FRAUD_SESSION_NO_OTHER_WRITERS") == "1"
    old, _ = session.load("arrival-v1")
    names = ["session.json", "input.json", "checkpoint.sqlite", "native-rehearsal.json"]
    before = {name: reader.sha(old / name) for name in names}
    key = os.environ.get("FRAUD_SESSION_ID", "arrival-2k-v1")
    directory, value = session.load(key)
    assert value["expanded_window"]["target_decisions"] == 2000
    assert not (directory / "checkpoint.sqlite").exists(), (
        "Use a fresh expanded preparation; preserve existing checkpoints"
    )
    pin = reader.sha(directory / "session.json")
    first = session.execute(key, True, 0.05, False, 5)
    assert first["status"] == "paused"
    assert reader.read(directory, pin)["counts"]["decisions"] == 5
    arrivals = session.execute(key, True, 0.05, False, None)
    assert arrivals["status"] == "awaiting_feedback"
    head = reader.read(directory, pin, page=1, page_size=25)
    next_page = reader.read(directory, pin, page=2, page_size=25)
    assert len(head["cases"]) == len(next_page["cases"]) == 25
    assert not {c["request"]["transaction_id"] for c in head["cases"]} & {
        c["request"]["transaction_id"] for c in next_page["cases"]
    }
    assert head["totals"]["decisions"] == value["capture"]["counts"]["decisions"] >= 2000
    assert sum(head["score_histogram"]) == head["totals"]["decisions"]
    feedback = session.execute(key, True, 0.05, True, None)
    assert feedback["status"] == "complete"
    verified = session.verify(directory, value)
    with flow.target(value) as scorer:
        repeated = session.pump(directory, value, scorer, feedback=True, interval=0.05)
    assert repeated["scoring_HTTP_attempts"] == 0
    assert {name: reader.sha(old / name) for name in names} == before
    report = {
        "schema": "fraud_product_session_scale_native_rehearsal_v1",
        "status": "success",
        "session_sha256": pin,
        "window": value["expanded_window"],
        "first": first,
        "arrivals": arrivals,
        "feedback": feedback,
        "verification": verified,
        "completed_reuse": repeated,
        "accepted_200_session_bytes_unchanged": before,
        "champion_modified": False,
        "same_session_Grafana_validated": False,
        "visual_review_validated": False,
    }
    f.private_json(directory / "native-scale-rehearsal.json", report)
    print(json.dumps(report, indent=2))
