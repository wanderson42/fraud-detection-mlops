"""Opt-in bounded SHAP of accepted native HTTP cases; no training or replay."""

import json
import os

import pytest


@pytest.mark.skipif(
    os.environ.get("FRAUD_CASE_EXPLANATIONS_LIVE") != "1",
    reason="Opt-in current KinD champion identity gates",
)
def test_native_current_champion_cases_reconstruct_scores_without_changing_ledger():
    from scripts.case_explanations import controller as c

    p = os.environ.get("FRAUD_PRODUCT_FLOW_ID", "champion-http-v1")
    key = os.environ.get("FRAUD_CASE_EXPLANATION_ID", "champion-shap-v1")
    with c.f.exclusive():
        result = c.run(
            p, key, 5, os.environ.get("FRAUD_CASE_EXPLANATIONS_NO_OTHER_WRITERS") == "1"
        )
        checked = c.verify(key, result["manifest_sha256"])
    assert checked["case_count"] == 5
    assert checked["model_sha256"] == result["model_sha256"]
    assert checked["champion_modified"] is False
    print(json.dumps({"generation": result, "verification": checked}, indent=2))
