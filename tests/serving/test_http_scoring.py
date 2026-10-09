"""HTTP validation, canonical order, parity, readiness and no fitting."""

import json

from fastapi.testclient import TestClient
import numpy as np
import pytest
from sklearn.ensemble import HistGradientBoostingClassifier

from fraud_detection_mlops.modeling.contracts.model_interface import predict_scores
from fraud_detection_mlops.serving.http_service import create_app
from fraud_detection_mlops.serving.request_schema import ScoreRequest


def test_http_scores_match_native_model_without_refit(serving_release, score_payload, monkeypatch):
    monkeypatch.setattr(
        HistGradientBoostingClassifier,
        "fit",
        lambda *a, **k: pytest.fail("Serving fitted a model"),
    )
    directory, digest = serving_release
    app = create_app(directory, digest)
    scores = []
    with TestClient(app) as client:
        runtime = app.state.runtime
        assert client.get("/health").json() == {"status": "alive"}
        assert (
            client.get("/ready").json()["serving_release_id"]
            == runtime.manifest.serving_release_id
        )
        assert client.get("/info").json()["usage_scope"] == "laboratory_only"
        assert client.get("/info").json()["evidence_kind"] == "synthetic_smoke"
        for amount in (0.0, 80.0, 159.0):
            score_payload["features"]["TX_AMOUNT"] = amount
            score_payload["features"] = dict(reversed(list(score_payload["features"].items())))
            score_payload["transaction_id"] = 9223372036854775807
            request = ScoreRequest.model_validate(score_payload)
            assert request.tx_datetime.endswith("000000001")
            response = client.post("/score", json=score_payload)
            assert response.status_code == 200, response.text
            data = response.json()
            assert data == {
                "schema_version": 1,
                "transaction_id": score_payload["transaction_id"],
                "score": pytest.approx(
                    float(predict_scores(runtime.pipeline, request.feature_frame())[0]), abs=1e-12
                ),
                "score_semantics": "uncalibrated_ranking",
                "model_id": "hist_gradient_boosting",
                "model_sha256": runtime.manifest.model_sha256,
                "feature_contract_version": "gold_v1",
                "serving_release_id": runtime.manifest.serving_release_id,
            }
            scores.append(data["score"])
        assert max(scores) - min(scores) > 0.5
        assert app.state.runtime is runtime
    assert app.state.runtime is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("transaction_id", True),
        ("customer_id", -1),
        ("terminal_id", "1"),
        ("transaction_id", 2**63),
        ("schema_version", True),
        ("schema_version", 2),
        ("feature_contract_version", "gold_v2"),
        ("TX_FRAUD", 0),
        ("features.TX_AMOUNT", "10"),
        ("features.TX_AMOUNT", None),
        ("features.TX_AMOUNT", True),
        ("features.TX_AMOUNT", -1.0),
        ("features.TX_AMOUNT", float("nan")),
        ("features.TX_AMOUNT", float("inf")),
        ("features.CUSTOMER_TX_COUNT_1D", 1.5),
        ("features.CUSTOMER_TX_COUNT_1D", 2**63),
        ("features.TX_HOUR", 24),
        ("features.TX_WEEKDAY", 0),
        ("features.TX_HOUR", 1),
        ("features.TX_WEEKDAY", 3),
        ("features.TX_FRAUD_SCENARIO", 1),
        ("features.LABEL_AVAILABLE_AT", "2018-04-10"),
        ("features.CUSTOMER_AMOUNT_RATIO_VALID_1D", 1),
        ("features.CUSTOMER_AMOUNT_RATIO_7D", 2.0),
        ("features.TERMINAL_KNOWN_FRAUD_RATE_7D", 0.5),
        ("features.TERMINAL_KNOWN_FRAUD_COUNT_1D", 1),
        ("tx_datetime", "2018-04-03T00:00:00Z"),
        ("feature_as_of", "2018-04-03T00:00:00+00:00"),
        ("feature_as_of", "2018-04-03T00:00:00.000000002"),
        ("tx_datetime", "2018-04-03T00:00:00.0000000001"),
        ("tx_datetime", "2018-04-03 00:00:00"),
        ("tx_datetime", "2018-02-30T00:00:00"),
    ],
)
def test_invalid_inputs_never_reach_prediction(
    serving_release, score_payload, monkeypatch, field, value
):
    directory, digest = serving_release
    with TestClient(create_app(directory, digest)) as client:
        monkeypatch.setattr(
            HistGradientBoostingClassifier,
            "predict_proba",
            lambda *a, **k: pytest.fail("Invalid input reached prediction"),
        )
        target = score_payload["features"] if field.startswith("features.") else score_payload
        target[field.split(".")[-1]] = value
        response = client.post(
            "/score",
            content=json.dumps(score_payload),
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 422, response.text
        assert response.json()["error"] == "invalid_request"


def test_missing_features_and_malformed_json_are_rejected(serving_release, score_payload):
    directory, digest = serving_release
    with TestClient(create_app(directory, digest)) as client:
        del score_payload["features"]["TX_AMOUNT"]
        assert client.post("/score", json=score_payload).status_code == 422
        assert (
            client.post(
                "/score", content=b"{", headers={"Content-Type": "application/json"}
            ).status_code
            == 422
        )


def test_model_readiness_is_separate_from_health(serving_release, score_payload):
    directory, digest = serving_release
    client = TestClient(create_app(directory, digest))  # No lifespan: model not loaded.
    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code == 503
    assert client.get("/info").status_code == 503
    assert client.post("/score", json=score_payload).status_code == 503


def test_invalid_model_probabilities_fail_without_emitting_score(
    serving_release, score_payload, monkeypatch
):
    directory, digest = serving_release
    with TestClient(create_app(directory, digest)) as client:
        monkeypatch.setattr(
            HistGradientBoostingClassifier,
            "predict_proba",
            lambda *a, **k: np.array([[0.5, float("nan")]]),
        )
        response = client.post("/score", json=score_payload)
        assert response.status_code == 503
        assert response.json() == {"detail": "model_contract_failure"}
