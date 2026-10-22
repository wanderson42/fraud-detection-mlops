"""Small explicitly synthetic snapshots for UI tests; no project model dependencies."""

import copy
import math

import pytest
from scripts.fraud_portal import data


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    model, release = "a" * 64, "b" * 32
    cases = []
    for i, (score, status, feedback) in enumerate(
        [(0.92, "alert", 1), (0.61, "alert", 0), (0.1, "below_threshold", None)]
    ):
        features = {name: 0 for name in data.FEATURE_LABELS}
        features.update(
            TX_AMOUNT=float(42 * (i + 1)),
            CUSTOMER_AVG_AMOUNT_7D=50.0,
            CUSTOMER_TX_COUNT_7D=3,
            CUSTOMER_AMOUNT_RATIO_7D=0.84,
            CUSTOMER_AMOUNT_RATIO_VALID_7D=1,
        )
        request = {
            "transaction_id": 101 + i,
            "customer_id": 11 + i,
            "terminal_id": 21,
            "tx_datetime": f"2018-09-16T0{i + 1}:00:00",
            "feature_as_of": f"2018-09-16T0{i + 1}:00:00",
            "features": features,
        }
        explanation = None
        if i == 0:
            margin = math.log(score / (1 - score))
            contributions = {n: 0.0 for n in features}
            contributions["TX_AMOUNT"] = margin + 1
            explanation = {
                "model_sha256": model,
                "serving_release_id": release,
                "base_log_odds": -1.0,
                "raw_log_odds": margin,
                "reconstructed_score": score,
                "contributions": contributions,
            }
        cases.append(
            {
                "request": request,
                "response": {
                    "transaction_id": 101 + i,
                    "score": score,
                    "model_sha256": model,
                    "serving_release_id": release,
                    "model_id": "hist_gradient_boosting",
                    "feature_contract_version": "gold_v1",
                },
                "triage": {"status": status, "daily_budget_used": i + 1},
                "transaction_feedback": feedback,
                "feedback_available_ns": 1537660800000000000 if feedback is not None else None,
                "SHAP_explanation": explanation,
            }
        )
    value = {
        "schema": "fraud_investigation_snapshot_v1",
        "model_sha256": model,
        "serving_release_id": release,
        "cases": cases,
        "alert_policy": {"score_threshold": 0.5, "daily_customer_budget": 100},
    }
    directory = tmp_path / "report"
    directory.mkdir()
    (directory / "snapshot.json").write_bytes(data.encoded(value))
    manifest = {
        "schema": "fraud_investigation_portal_v1",
        "snapshot_sha256": data.sha(directory / "snapshot.json"),
        "model_sha256": model,
        "serving_release_id": release,
        "case_count": 3,
        "created_at": "2026-10-22T15:00:00+00:00",
    }
    (directory / "manifest.json").write_bytes(data.encoded(manifest))
    pin = data.sha(directory / "manifest.json")
    monkeypatch.setenv("FRAUD_PORTAL_BUNDLE", str(directory))
    monkeypatch.setenv("FRAUD_PORTAL_MANIFEST_SHA256", pin)
    return directory, pin, copy.deepcopy(value)
