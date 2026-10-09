"""Temporal authorization and feature causality on small synthetic daily partitions."""

import json
import shutil

import pandas as pd
import pytest

from fraud_detection_mlops.artifacts import write_json
from fraud_detection_mlops.features import FEATURE_COLUMNS
from fraud_detection_mlops.modeling import development


def test_preparation_reads_only_authorized_dates_and_reuses_data(
    prepared_data, silver_fixture, tmp_path, monkeypatch
):
    directory, policy_path = prepared_data
    manifest = json.loads((directory / "manifest.json").read_text())
    assert len(manifest["source_files"]) == 84
    assert len(manifest["files"]) == 63
    assert manifest["files"][0]["date"] == "2018-06-10"
    assert manifest["files"][-1]["date"] == "2018-08-18"
    assert "TX_FRAUD_SCENARIO" not in manifest["feature_columns"]
    repeated = development.prepare_data(
        silver_fixture / "silver",
        tmp_path / "output",
        policy_path=policy_path,
        inventory_path=silver_fixture / "inventory.json",
    )
    assert repeated["development_path"] == str(directory)
    policy = development.load_policy(policy_path)
    training, validation = development.load_fold(directory, manifest, policy["folds"][0])
    assert len(training[0]) == 84 and len(validation[0]) == 21
    assert list(training[0].columns) == FEATURE_COLUMNS
    assert training[2].LABEL_AVAILABLE_AT.max() < validation[2].TX_DATETIME.min()
    # Label history excludes exactly the boundary t-7d; three transactions per day.
    first = pd.read_parquet(directory / "transactions/2018-06-10.parquet").iloc[0]
    assert first.TERMINAL_KNOWN_LABEL_COUNT_7D == 21
    assert first.TERMINAL_KNOWN_FRAUD_COUNT_7D == 7
    assert first.TERMINAL_KNOWN_FRAUD_RATE_7D == pytest.approx(1 / 3)


@pytest.mark.parametrize(
    "fault",
    ["gap", "consumed", "overlap", "reserved", "budget", "iteration", "features", "reference"],
)
def test_policy_rejects_unauthorized_windows_and_scope(tmp_path, fault):
    policy = development.load_policy()
    if fault == "gap":
        policy["folds"][0]["validation"] = {"start": "2018-07-14", "end_exclusive": "2018-07-21"}
    elif fault == "consumed":
        policy["context_start"] = "2018-05-20"
    elif fault == "overlap":
        policy["folds"][1]["validation"] = policy["folds"][0]["validation"]
    elif fault == "reserved":
        policy["development_end_exclusive"] = "2018-09-03"
    elif fault == "budget":
        policy["budget"]["max_trials"] = 21
    elif fault == "iteration":
        policy["search_space"]["classifier__max_iter"]["choices"].append(500)
    elif fault == "features":
        policy["feature_selection"] = True
    else:
        policy["reference_parameters"]["early_stopping"] = True
    path = tmp_path / "policy.json"
    write_json(path, policy)
    with pytest.raises(ValueError):
        development.load_policy(path)


def test_tampered_development_partition_is_rejected(prepared_data):
    directory, policy_path = prepared_data
    (directory / "transactions/2018-06-10.parquet").write_bytes(b"changed")
    with pytest.raises(ValueError, match="integrity"):
        development.verify_data(directory, policy_path=policy_path)


def test_missing_or_corrupt_authorized_silver_is_rejected(
    silver_fixture, tmp_path, monkeypatch, policy_path
):
    root = tmp_path / "silver"
    shutil.copytree(silver_fixture / "silver", root)
    monkeypatch.setattr(
        development, "committed_inputs", lambda p: {"git_revision": "test", "files": {}}
    )
    source = root / development.load_policy()["source_commit"] / "silver_v1"
    (source / "transactions/tx_date=2018-06-01/part-00000.parquet").write_bytes(b"bad")
    with pytest.raises(ValueError, match="integrity"):
        development.prepare_data(
            root,
            tmp_path / "out",
            policy_path=policy_path,
            inventory_path=silver_fixture / "inventory.json",
        )


def test_manifest_cannot_redirect_authorized_day_to_reserved_bytes(
    silver_fixture, tmp_path, monkeypatch, policy_path
):
    root = tmp_path / "silver"
    shutil.copytree(silver_fixture / "silver", root)
    monkeypatch.setattr(
        development, "committed_inputs", lambda p: {"git_revision": "test", "files": {}}
    )
    source = root / development.load_policy()["source_commit"] / "silver_v1"
    manifest_path = source / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"][0]["path"] = "transactions/tx_date=2018-09-02/part-00000.parquet"
    write_json(manifest_path, manifest)
    monkeypatch.setattr(
        development, "check_records", lambda *a: pytest.fail("Unauthorized partition opened")
    )
    with pytest.raises(development.DevelopmentError, match="authorized dates"):
        development.prepare_data(
            root,
            tmp_path / "out",
            policy_path=policy_path,
            inventory_path=silver_fixture / "inventory.json",
        )
