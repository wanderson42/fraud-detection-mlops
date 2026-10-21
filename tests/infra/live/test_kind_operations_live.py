"""Disruptive, explicit local stop/start gate; never reboots the host."""

import json
import os
from pathlib import Path

import pytest


@pytest.mark.skipif(
    os.environ.get("FRAUD_KIND_OPERATIONS_LIVE") != "1",
    reason="Opt-in local KinD service interruption",
)
def test_owned_stack_controlled_stop_start_and_existing_readback():
    from scripts.platform import kind_foundation as f
    from scripts.platform.kind_operations import rehearse

    f.require(
        os.environ.get("FRAUD_KIND_NO_OTHER_WRITERS") == "1", "Declare no other writers explicitly"
    )
    path = os.environ.get("FRAUD_KIND_TRACKING_REPORT")
    f.require(bool(path), "Explicit FRAUD_KIND_TRACKING_REPORT required")
    with f.exclusive():
        result = rehearse(Path(path), True)
    assert result["status"] == "success"
    assert result["verification"]["controlled_stop_start_validated"]
    assert not result["host_reboot_validated"]
    print(json.dumps(result, indent=2))
