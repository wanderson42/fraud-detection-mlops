"""Opt-in: create a lab run and recreate only dedicated PostgreSQL/MLflow containers."""

import json
import os

import pytest
from scripts.tracking.local_stack import rehearse

pytestmark = pytest.mark.skipif(
    os.environ.get("FRAUD_TRACKING_LAB_LIVE") != "1",
    reason="Explicit tracking-lab opt-in required",
)


def test_native_postgres_s3_and_proxy_persist_across_container_recreation():
    result = rehearse()
    print(json.dumps(result, indent=2))
    assert result["status"] == "success"
