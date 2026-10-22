"""Opt-in execution against the already prepared owned KinD champion."""

import os

import pytest


@pytest.mark.skipif(
    os.environ.get("FRAUD_PRODUCT_FLOW_LIVE") != "1",
    reason="Opt-in causal flow through the owned KinD HTTP API",
)
def test_causal_events_HTTP_champion_resume_and_full_reconciliation():
    from scripts.platform import kind_foundation as f
    from scripts.product_flow import controller

    assert os.environ.get("FRAUD_PRODUCT_FLOW_NO_OTHER_WRITERS") == "1", (
        "Declare no other writers explicitly"
    )
    key = os.environ.get("FRAUD_PRODUCT_FLOW_ID", "champion-http-v1")
    with f.exclusive():
        result = controller.execute(key, "rehearse", True)
    assert result["status"] == "product_rehearsal_passed"
    assert result["HTTP_receipts"] == result["counts"]["decisions"]
    assert result["pause_resume_verified"]
    assert result["completed_reinvocation_without_new_score_calls"]
    assert result["champion_modified"] is False
