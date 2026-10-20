"""Opt-in full dataset transfer to the existing owned KinD object store."""

import json
import os

import pytest


@pytest.mark.skipif(
    os.environ.get("FRAUD_KIND_DATASETS_LIVE") != "1",
    reason="Opt-in publication and independent restoration of a prepared dataset bundle",
)
def test_four_prepared_dataset_bundles_roundtrip_on_kind():
    from scripts.platform import kind_datasets as k
    from scripts.platform import kind_foundation as f

    with f.exclusive():
        result = k.rehearse()
    assert result["status"] == "success"
    assert set(result["datasets"]) == set(k.LAYERS)
    for row in result["datasets"].values():
        assert row["independent_restored_bundle_verified"]
        assert row["foundation_identities_preserved"]
        assert row["manifest_sha256"] == row["bundle_id"]
        assert row["reused_objects"] == row["verified_objects"]
    assert not result["primary_storage_migrated"]
    print(json.dumps(result, indent=2))
