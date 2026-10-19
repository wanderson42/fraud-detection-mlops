"""Native opt-in consumption of the author's pinned, already qualified archive."""

import json
import os

import pytest


@pytest.mark.skipif(
    os.environ.get("FRAUD_SERVING_CONSUMER_LIVE") != "1",
    reason="Opt-in image archive load and isolated HTTP target",
)
def test_native_approved_archive_loaded_and_served_without_rebuild():
    from scripts.serving.delivery_consumer import ROOT, consume

    evidence = json.loads(
        (
            ROOT / "references/evidence/serving_delivery_author_validation_2026-10-19.json"
        ).read_text()
    )
    summary = evidence["descriptor_summary"]
    result = consume(
        ROOT / evidence["delivery_directory_relative_to_repo"],
        summary["source_commit"],
        summary["descriptor_sha256"],
    )
    print(json.dumps(result, indent=2, allow_nan=False))
    assert result["status"] == "success" and result["archive_reload_validated"]
    assert result["owned_import_tag_removed"] and result["original_delivery_bytes_unchanged"]
