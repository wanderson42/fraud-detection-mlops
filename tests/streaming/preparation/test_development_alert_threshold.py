"""Real generated release and projected Gold; no native data or MLflow runs."""

import hashlib
import json
from pathlib import Path
import shutil

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pytest

from fraud_detection_mlops.artifacts import sha256, write_json
from fraud_detection_mlops.data.datasets import development_scoring_dataset as dataset
from fraud_detection_mlops.features.feature_schema import (
    DTYPES,
    FEATURE_COLUMNS,
)
from fraud_detection_mlops.modeling.experiments import temporal_development_data as development
from fraud_detection_mlops.streaming.decisions.development_threshold_selection import (
    selection_days,
)
from fraud_detection_mlops.streaming.preparation import development_alert_threshold as executor


@pytest.fixture
def threshold_environment(tmp_path, replay_release, replay_runtime, monkeypatch):
    root = tmp_path / "project"
    (root / "references").mkdir(parents=True)
    shutil.copy(development.DEFAULT_POLICY, root / "references/hgb_optuna_protocol_v1.json")
    source_protocol = (
        Path(executor.__file__).resolve().parents[3] / "references/causal_replay_protocol_v1.json"
    )
    policy = json.loads(source_protocol.read_text())
    directory, digest = replay_release
    policy["reference"].update(
        model_sha256=replay_runtime.manifest.model_sha256,
        serving_release_id=replay_runtime.manifest.serving_release_id,
        manifest_sha256=digest,
    )
    write_json(root / "references/causal_replay_protocol_v1.json", policy)
    monkeypatch.setattr(
        executor, "PROTOCOL_SHA256", sha256(root / "references/causal_replay_protocol_v1.json")
    )
    monkeypatch.setattr(
        executor,
        "committed_selection_inputs",
        lambda p: {"git_revision": "synthetic-fixture", "files": {"fixture": "a" * 64}},
    )
    source = tmp_path / "development"
    (source / "transactions").mkdir(parents=True)
    records = []
    # Non-selected partitions intentionally do not exist: any expanded read must fail.
    for index, day in enumerate(development.selected_days(development.load_policy())):
        path = f"transactions/{day}.parquet"
        record = {"path": path, "date": day, "rows": 100, "sha256": "c" * 64, "size_bytes": 1}
        if day in selection_days():
            frame = pd.DataFrame({name: np.zeros(100) for name in DTYPES})
            frame.TRANSACTION_ID = np.arange(100) + index * 100
            frame.CUSTOMER_ID = np.arange(100)
            frame.TERMINAL_ID = 1
            frame.TX_DATETIME = pd.Timestamp(day) + pd.Timedelta(hours=10)
            frame.LABEL_AVAILABLE_AT = frame.TX_DATETIME + pd.Timedelta(days=7)
            frame.TX_FRAUD = np.arange(100) % 2
            frame.TX_AMOUNT = np.arange(100, dtype="float64")
            frame.TX_HOUR = 10
            frame.TX_WEEKDAY = pd.Timestamp(day).isoweekday()
            frame = frame.astype(DTYPES)
            frame.to_parquet(source / path, index=False, version="2.6")
            record.update(sha256=sha256(source / path), size_bytes=(source / path).stat().st_size)
        records.append(record)
    identity = {
        "policy_sha256": sha256(root / "references/hgb_optuna_protocol_v1.json"),
        "silver_manifest_sha256": "b" * 64,
        "implementation": {"fixture": "a" * 64},
    }
    run_id = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    monkeypatch.setattr(dataset, "DEVELOPMENT_RUN_ID", run_id)
    monkeypatch.setattr(executor, "DEVELOPMENT_RUN_ID", run_id)
    write_json(
        source / "manifest.json",
        {
            "version": "development_gold_v2",
            "run_id": run_id,
            "source_commit": policy["source_commit"],
            **identity,
            "files": records,
            "feature_columns": FEATURE_COLUMNS,
            "confirmation_evaluated": False,
            "original_test_reused": False,
        },
    )
    # Only the evidence tag is bypassed in this synthetic fixture, never the CLI.
    replay_runtime.manifest = replay_runtime.manifest.model_copy(
        update={"evidence_kind": "verified_reference"}
    )
    monkeypatch.setattr(executor, "load_release", lambda p, h: replay_runtime)
    return root, source, directory, tmp_path / "policies/result"


def run(environment):
    root, development_path, release, output = environment
    return executor.prepare(development_path, release, output, root)


def test_end_to_end_projects_no_targets_only_21_days_and_verifies_without_scoring(
    threshold_environment, monkeypatch
):
    read_columns = []
    read = pq.ParquetFile.read

    def observed(self, *args, **kwargs):
        read_columns.append(kwargs.get("columns"))
        return read(self, *args, **kwargs)

    monkeypatch.setattr(pq.ParquetFile, "read", observed)
    result = run(threshold_environment)
    assert result["days"] == 21 and result["rows"] == 2100
    assert result["refit"] is False and result["native_replay_evaluated"] is False
    assert len(read_columns) == 21
    assert all(columns == dataset.METADATA_COLUMNS + FEATURE_COLUMNS for columns in read_columns)
    assert all(
        "TX_FRAUD" not in columns and "LABEL_AVAILABLE_AT" not in columns
        for columns in read_columns
    )
    root, _, _, output = threshold_environment
    monkeypatch.setattr(
        executor, "load_release", lambda *a: pytest.fail("Verification loaded a model")
    )
    monkeypatch.setattr(
        executor, "predict_scores", lambda *a: pytest.fail("Verification rescored")
    )
    assert executor.verify(output, result["manifest_sha256"], root) == result
    with pytest.raises(ValueError, match="already exists"):
        run(threshold_environment)


@pytest.mark.parametrize(
    "filename", ["scores.parquet", "policy.json", "report.json", "manifest.json"]
)
def test_corrupt_result_is_rejected(threshold_environment, filename):
    result = run(threshold_environment)
    root, _, _, output = threshold_environment
    with (output / filename).open("ab") as stream:
        stream.write(b"changed")
    with pytest.raises(ValueError, match="integrity"):
        executor.verify(output, result["manifest_sha256"], root)


def test_modified_protocol_is_rejected_before_input_or_model_access(
    threshold_environment, monkeypatch
):
    root = threshold_environment[0]
    path = root / "references/causal_replay_protocol_v1.json"
    policy = json.loads(path.read_text())
    policy["threshold_selection"]["windows"][0]["start"] = "2018-09-16"
    write_json(path, policy)
    monkeypatch.setattr(
        executor, "development_snapshot", lambda *a: pytest.fail("Unauthorized dataset read")
    )
    with pytest.raises(ValueError, match="separate protocol"):
        run(threshold_environment)


def test_corrupt_selected_partition_blocks_scoring_and_publication(
    threshold_environment, monkeypatch
):
    source = threshold_environment[1]
    (source / f"transactions/{selection_days()[0]}.parquet").write_bytes(b"corrupted")
    monkeypatch.setattr(
        executor, "load_release", lambda *a: pytest.fail("Model loaded before integrity check")
    )
    with pytest.raises(ValueError, match="integrity"):
        run(threshold_environment)
    assert not threshold_environment[3].exists()


def test_redirected_partition_or_reserved_path_is_rejected(threshold_environment):
    root, source, _, _ = threshold_environment
    day = selection_days()[0]
    path = source / f"transactions/{day}.parquet"
    replacement = source / "renamed.parquet"
    path.rename(replacement)
    path.symlink_to(replacement)
    with pytest.raises(ValueError, match="redirected"):
        run(threshold_environment)
    path.unlink()
    replacement.rename(path)
    manifest = json.loads((source / "manifest.json").read_text())
    next(r for r in manifest["files"] if r["date"] == day)["path"] = (
        "transactions/2018-09-16.parquet"
    )
    write_json(source / "manifest.json", manifest)
    with pytest.raises(ValueError, match="identity"):
        dataset.development_snapshot(source, root, manifest["source_commit"])


def test_failure_during_scoring_leaves_no_published_result(threshold_environment, monkeypatch):
    def fail(*args):
        raise ValueError("simulated scorer failure")

    monkeypatch.setattr(executor, "predict_scores", fail)
    with pytest.raises(ValueError, match="scorer failure"):
        run(threshold_environment)
    assert not threshold_environment[3].exists()
    assert not list(threshold_environment[3].parent.glob(".threshold-preparing-*"))


def test_input_change_during_scoring_is_detected(threshold_environment, monkeypatch):
    original = executor.predict_scores
    source = threshold_environment[1]
    calls = 0

    def score(*args):
        nonlocal calls
        values = original(*args)
        calls += 1
        if calls == 21:
            with (source / "manifest.json").open("a") as stream:
                stream.write("\n")
        return values

    monkeypatch.setattr(executor, "predict_scores", score)
    with pytest.raises(ValueError, match="manifest changed"):
        run(threshold_environment)
    assert not threshold_environment[3].exists()


def test_concurrent_writer_is_rejected(threshold_environment):
    output = threshold_environment[3]
    with executor.publication_lock(output), pytest.raises(ValueError, match="Another threshold"):
        run(threshold_environment)


def test_gold_schema_is_checked_without_decoding_targets(threshold_environment):
    root, source, _, _ = threshold_environment
    snap = dataset.development_snapshot(
        source, root, json.loads((source / "manifest.json").read_text())["source_commit"]
    )
    record = snap["files"][0]
    frame = pd.read_parquet(source / record["path"])
    frame = frame.drop(columns="TX_FRAUD")
    frame.to_parquet(source / record["path"], index=False)
    with pytest.raises(ValueError, match="schema"):
        dataset.load_scoring_partition(snap, record)


def test_another_model_or_fold_release_is_rejected_before_partition_decode(
    threshold_environment, monkeypatch
):
    runtime = executor.load_release(threshold_environment[2], "unused_in_fixture")
    runtime.manifest = runtime.manifest.model_copy(update={"model_sha256": "f" * 64})
    monkeypatch.setattr(
        executor, "load_scoring_partition", lambda *a: pytest.fail("Wrong model read data")
    )
    with pytest.raises(ValueError, match="frozen April reference"):
        run(threshold_environment)


def test_missing_day_in_manifest_is_rejected_before_any_partition_access(threshold_environment):
    source = threshold_environment[1]
    manifest = json.loads((source / "manifest.json").read_text())
    manifest["files"] = [r for r in manifest["files"] if r["date"] != selection_days()[0]]
    write_json(source / "manifest.json", manifest)
    with pytest.raises(ValueError, match="identity"):
        run(threshold_environment)


@pytest.mark.parametrize("case", ["date", "ratio", "negative_id"])
def test_invalid_clock_feature_or_identity_is_rejected(threshold_environment, case):
    root, source, _, _ = threshold_environment
    source_commit = json.loads((source / "manifest.json").read_text())["source_commit"]
    snapshot = dataset.development_snapshot(source, root, source_commit)
    record = snapshot["files"][0]
    frame = pd.read_parquet(source / record["path"])
    if case == "date":
        frame.loc[0, "TX_DATETIME"] = pd.Timestamp("2018-09-16")
    elif case == "ratio":
        frame.loc[0, "CUSTOMER_AMOUNT_RATIO_1D"] = 1.0
    else:
        frame.loc[0, "CUSTOMER_ID"] = -1
    frame.to_parquet(source / record["path"], index=False, version="2.6")
    with pytest.raises(ValueError):
        dataset.load_scoring_partition(snapshot, record)


def test_changed_code_before_publication_leaves_no_result(threshold_environment, monkeypatch):
    calls = 0

    def changed(root):
        nonlocal calls
        calls += 1
        return {
            "git_revision": "fixture",
            "files": {"fixture": "a" * 64 if calls == 1 else "b" * 64},
        }

    monkeypatch.setattr(executor, "committed_selection_inputs", changed)
    with pytest.raises(ValueError, match="Code, development or release changed"):
        run(threshold_environment)
    assert not threshold_environment[3].exists()


def test_wrong_saved_threshold_is_rejected_even_with_updated_artifact_hash(threshold_environment):
    run(threshold_environment)
    root, _, _, output = threshold_environment
    config = json.loads((output / "policy.json").read_text())
    config["score_threshold"] = 1.0
    write_json(output / "policy.json", config)
    manifest = json.loads((output / "manifest.json").read_text())
    next(r for r in manifest["files"] if r["path"] == "policy.json")["sha256"] = sha256(
        output / "policy.json"
    )
    write_json(output / "manifest.json", manifest)
    with pytest.raises(ValueError, match="does not reproduce"):
        executor.verify(output, sha256(output / "manifest.json"), root)
