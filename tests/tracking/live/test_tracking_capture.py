"""Explicitly pauses only the dedicated MLflow server to capture PostgreSQL/S3."""

import json
import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("FRAUD_TRACKING_CAPTURE_LIVE") != "1",
    reason="Opt-in capture pauses MLflow and requires no other PG/S3 writers",
)


def test_coordinated_capture_and_source_health():
    from pathlib import Path

    from scripts.tracking.capture import capture, verify

    result = capture(no_other_writers=True)
    assert result["status"] == "captured_verified"
    assert result["source_recovered"]
    assert result["containers_before"] == result["containers_after"]
    assert not result["postgres_backup_restore_validated"]
    assert verify(Path(result["directory"]), result["manifest_sha256"])["objects"] > 0
    print(json.dumps(result, indent=2, allow_nan=False))
