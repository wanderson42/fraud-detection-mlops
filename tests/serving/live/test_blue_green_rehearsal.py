"""Explicit local-engine drill; never runs as part of ordinary CI."""

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("FRAUD_SERVING_ROLLOUT_LIVE") != "1",
    reason="Run tox -e serving-rollout explicitly",
)


def test_native_nginx_release_switch_and_rollback():
    import json

    from scripts.serving.blue_green_rehearsal import EXPECTED_EVENTS, rehearse

    result = rehearse()
    print(json.dumps(result, indent=2, allow_nan=False))
    assert result["status"] == "success"
    assert result["events"] == EXPECTED_EVENTS
    assert result["baseline_release_id"] != result["candidate_release_id"]
    assert result["rollback_verified"] and result["temporary_resources_removed"]
    assert result["observed_failure_http_status"] == 502
    from scripts.serving.check_docker_runtime import PARITY_TOLERANCE

    assert abs(result["candidate_score"] - result["rollback_score"]) <= 2 * PARITY_TOLERANCE
    assert result["release_bytes_unchanged"]
    assert not result["production_promotion"] and not result["model_weights_changed"]
