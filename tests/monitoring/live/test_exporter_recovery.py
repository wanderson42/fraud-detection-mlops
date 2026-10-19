"""Explicit infrastructure check; skipped unless the dedicated tox environment enables it."""

import os
from pathlib import Path

import pytest
from scripts.validation.exporter_recovery import run_check

pytestmark = pytest.mark.skipif(
    os.environ.get("FRAUD_EXPORTER_RECOVERY_LIVE") != "1",
    reason="Run explicitly with tox -e exporter-recovery; this check stops the exporter.",
)


def test_live_exporter_alert_and_recovery():
    report = run_check(Path(os.environ["FRAUD_PROJECT_ROOT"]))
    assert report["status"] == "success"
    assert report["state_files_unchanged"] is True
