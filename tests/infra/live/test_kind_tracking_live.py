"""Explicit native acceptance; requires the initialized and ready dedicated cluster."""

import os

import pytest


@pytest.mark.skipif(
    os.environ.get("FRAUD_KIND_TRACKING_LIVE") != "1",
    reason="Opt-in KinD MLflow publication and pod recreation",
)
def test_kind_mlflow_proxy_model_survives_own_pod_recreation():
    from scripts.platform import kind_foundation as f
    from scripts.platform import kind_tracking as k

    with f.exclusive():
        result = k.rehearse()
    assert result["status"] == "success"
    assert result["independent_http_reload_validated"]
    assert result["model_reload_after_pod_recreation_validated"]
    assert result["foundation_identities_preserved"]
    assert result["max_absolute_score_error"] <= 1e-12
    assert result["s3"]["status"] == "s3_model_verified"
