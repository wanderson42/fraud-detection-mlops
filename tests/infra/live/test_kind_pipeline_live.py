"""Native Helm Job execution; opt-in, real frozen source cohort, no model training."""

import json
import os

import pytest


@pytest.mark.skipif(
    os.environ.get("FRAUD_KIND_PIPELINE_LIVE") != "1",
    reason="Opt-in full Bronze to Silver to Gold producer reexecution in KinD Jobs",
)
def test_helm_jobs_publish_and_independently_restore_new_silver_and_gold():
    from scripts.platform import kind_foundation as f
    from scripts.platform import kind_pipeline as pipeline

    with f.exclusive():
        result = pipeline.rehearse()
    assert result["status"] == "success" and result["temporary_jobs_removed"]
    assert result["foundation_identities_preserved"]
    assert not result["fit_executed"] and not result["primary_storage_migrated"]
    assert result["features_recomputed"] and not result["new_model_quality_evidence"]
    silver = result["stages"]["silver"]
    gold = result["stages"]["gold"]
    assert gold["result"]["input_bundle_id"] == silver["result"]["output_bundle_id"]
    s = silver["independent_restore"]["semantic_verification"]
    g = gold["independent_restore"]["semantic_verification"]
    assert s["rows"] == s["distinct_transaction_ids"] == 1754155
    assert s["verified_partitions"] == 183 and s["fraud_count"] == 14681
    assert g["rows"] == 402877 and g["feature_count"] == 19
    assert g["split_rows"] == {"train": 268668, "validation": 67255, "test": 66954}
    assert g["verified_partitions"] == 42
    print(json.dumps(result, indent=2))
