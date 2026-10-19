"""Explicit native build/export qualification; no historical data/services."""

import os
from pathlib import Path
import subprocess

import pytest


@pytest.mark.skipif(
    os.environ.get("FRAUD_SERVING_DELIVERY_LIVE") != "1",
    reason="Opt-in image build and synthetic delivery gate",
)
def test_native_build_qualifies_and_exports_the_tested_image():
    from scripts.serving.delivery_gate import ROOT, qualify, verify

    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    result = qualify(commit)
    print("Delivery: " + result["directory"])
    print("Descriptor SHA-256: " + result["descriptor_sha256"])
    assert (
        verify(Path(result["directory"]), commit, result["descriptor_sha256"])["status"]
        == "verified_bytes_and_records"
    )
