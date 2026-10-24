"""Opt-in native Prometheus/Grafana session readback and collection recovery."""

import json
import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("FRAUD_SESSION_MONITORING_LIVE") != "1",
    reason="explicit native session monitoring opt-in",
)


def test_completed_session_metrics_staleness_and_recovery():
    from scripts.platform import kind_foundation as f
    from scripts.session_monitoring import controller

    with f.exclusive():
        result = controller.rehearse(os.environ.get("FRAUD_SESSION_ID", "arrival-2k-v1"))
    print(json.dumps(result, indent=2))
    assert result["status"] == "success"
    assert result["same_session_Grafana_validated"]
    assert not result["champion_modified"]
