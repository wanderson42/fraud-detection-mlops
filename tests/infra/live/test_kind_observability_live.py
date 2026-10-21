"""Opt-in verification and exporter recreation in the existing owned cluster."""

import os

import pytest


@pytest.mark.skipif(
    os.environ.get("FRAUD_KIND_OBSERVABILITY_LIVE") != "1",
    reason="Opt-in existing KinD observability gate",
)
def test_readonly_completed_replay_scrape_grafana_and_exporter_recreation():
    from scripts.platform import kind_foundation as foundation
    from scripts.platform.kind_observability import rehearse

    with foundation.exclusive():
        result = rehearse()
    assert result["status"] == "success"
    assert result["exporter_pod_recreation_validated"]
    assert result["verification"]["healthy_alert_rules"] == 8
