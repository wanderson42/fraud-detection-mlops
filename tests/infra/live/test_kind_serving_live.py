"""Opt-in blue-green Service routing and native Helm rollback using the prepared qualified runtime."""

import os

import pytest


@pytest.mark.skipif(
    os.environ.get("FRAUD_KIND_SERVING_LIVE") != "1", reason="Opt-in owned KinD serving rehearsal"
)
def test_qualified_serving_Service_switch_and_native_Helm_rollback():
    import json

    from scripts.platform import kind_foundation as f
    from scripts.platform.kind_serving import EVENTS, rehearse

    with f.exclusive():
        result = rehearse()
    assert result["status"] == "success" and result["events"] == EVENTS
    assert result["rollback_verified"] and result["native_Helm_rollback_validated"]
    assert result["candidate_deployment_removed"] and result["baseline_and_gateway_pods_unchanged"]
    assert result["foundation_before"] == result["foundation_after"]
    assert not result["runtime_rebuilt"] and not result["fit_executed"]
    print(json.dumps(result, indent=2))
