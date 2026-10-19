"""Opt-in recovery in temporary volumes; the original lab is never stopped."""

import json
import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("FRAUD_TRACKING_RESTORE_LIVE") != "1",
    reason="Explicit isolated restore opt-in required",
)


def test_isolated_postgres_s3_full_history_and_pinned_model_reload():
    from scripts.tracking.restore import run_restore

    result = run_restore()
    assert result["status"] == "success"
    assert result["postgres_backup_restore_validated"]
    assert result["temporary_resources_removed"] and result["temporary_credentials_removed"]
    assert result["source_before"] == result["source_after"]
    assert result["capture_unchanged"] and result["client"]["model_reload_validated"]
    print(json.dumps(result, indent=2, allow_nan=False))
