"""Freeze orchestration: no holdout access, no fit, immutable receipt and drift guards."""

import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from fraud_detection_mlops.artifacts import sha256, write_json
from fraud_detection_mlops.features import FEATURE_COLUMNS, METADATA_DTYPES
from fraud_detection_mlops.modeling import freeze


@pytest.fixture
def freeze_source(tmp_path, monkeypatch):
    from fraud_detection_mlops.modeling import experiments, train

    root = tmp_path
    monkeypatch.setattr(freeze, "PROJECT_ROOT", root)
    source = "b" * 40
    gold = root / "gold" / source / "gold_v1"
    gold.mkdir(parents=True)
    metadata = pd.DataFrame(
        {
            "TRANSACTION_ID": [1, 2],
            "CUSTOMER_ID": [1, 2],
            "TERMINAL_ID": [1, 1],
            "TX_DATETIME": pd.to_datetime(["2018-05-06 01:00", "2018-05-06 02:00"]),
            "LABEL_AVAILABLE_AT": pd.to_datetime(["2018-05-13 01:00", "2018-05-13 02:00"]),
            "TX_FRAUD": [0, 1],
        }
    ).astype(METADATA_DTYPES)[list(METADATA_DTYPES)]
    validation = gold / "validation.parquet"
    metadata.to_parquet(validation, index=False)
    partitions = [
        {
            "path": validation.name,
            "split": "validation",
            "sha256": sha256(validation),
            "size_bytes": validation.stat().st_size,
        }
    ]
    protocol = {"windows": {"test": {"start": "2018-05-20", "end_exclusive": "2018-05-27"}}}
    write_json(
        gold / "manifest.json",
        {
            "protocol": protocol,
            "feature_columns": FEATURE_COLUMNS,
            "split_rows": {"test": 66954},
            "files": partitions,
        },
    )
    baseline_dir = root / "baseline"
    model_dir = baseline_dir / "models/hist_gradient_boosting"
    model_dir.mkdir(parents=True)
    predictions = model_dir / "validation_predictions.parquet"
    metadata.assign(SCORE=[0.1, 0.9]).to_parquet(predictions, index=False)
    write_json(
        baseline_dir / "manifest.json",
        {
            "run_id": "reference",
            "gold_manifest_sha256": sha256(gold / "manifest.json"),
            "source": {"commit": source},
            "protocol": protocol,
            "input_partitions": partitions,
            "environment": {"sklearn": "fixture", "numpy": "fixture", "pandas": "fixture"},
            "config": {"models": {"hist_gradient_boosting": {"classifier": "fixture"}}},
            "files": [
                {
                    "path": "models/hist_gradient_boosting/validation_predictions.parquet",
                    "sha256": sha256(predictions),
                }
            ],
        },
    )
    experiment = root / "experiment"
    experiment.mkdir()
    write_json(experiment / "manifest.json", {"run_id": "ablation"})
    write_json(
        experiment / "report.json",
        {
            "identity": {
                "baseline_manifest_sha256": sha256(baseline_dir / "manifest.json"),
                "gold_manifest_sha256": sha256(gold / "manifest.json"),
            }
        },
    )
    policy = json.loads(freeze.DEFAULT_POLICY.read_text())
    policy["reference"].update(
        baseline_run_id="reference",
        experiment_run_id="ablation",
        baseline_manifest_sha256=sha256(baseline_dir / "manifest.json"),
        gold_manifest_sha256=sha256(gold / "manifest.json"),
    )
    policy_path = root / "policy.json"
    write_json(policy_path, policy)
    monkeypatch.setattr(freeze, "version", lambda package: "fixture")
    monkeypatch.setattr(
        freeze,
        "committed_inputs",
        lambda path: {
            "git_revision": "a" * 40,
            "files": {"policy.json": sha256(policy_path)},
        },
    )
    monkeypatch.setattr(
        freeze.baseline,
        "verify_baseline",
        lambda *a, **kw: {
            "selected_model": "hist_gradient_boosting",
            "comparison": {"hist_gradient_boosting": {"average_precision": 0.62}},
        },
    )
    monkeypatch.setattr(
        experiments,
        "verify_experiments",
        lambda path: {
            "run_id": "ablation",
            "eligible_ranking": [],
            "fallback": "retain_reference",
        },
    )
    loaded = []

    def load(directory, manifest, split):
        assert split == "validation", "Freeze accessed train or test"
        loaded.append(split)
        return (
            pd.DataFrame(np.zeros((2, 19)), columns=FEATURE_COLUMNS),
            metadata.TX_FRAUD,
            metadata,
        )

    monkeypatch.setattr(train, "load_split", load)
    monkeypatch.setattr(
        train, "fit_candidate", lambda *a, **k: pytest.fail("Freeze fitted a model")
    )
    monkeypatch.setattr(
        freeze,
        "reference_identity",
        lambda *a: {
            "model_sha256": "c" * 64,
            "mlmodel_sha256": "d" * 64,
            "mlflow_run_id": "reference",
            "serialization_format": "skops",
        },
    )
    return SimpleNamespace(
        root=root,
        baseline=baseline_dir,
        experiment=experiment,
        policy=policy_path,
        gold_root=root / "gold",
        validation=validation,
        loaded=loaded,
        frozen=root / "frozen.json",
    )


def build(source):
    return freeze.freeze_reference(
        source.baseline,
        source.experiment,
        policy_path=source.policy,
        freeze_path=source.frozen,
        gold_root=source.gold_root,
        tracking_root=source.root / "tracking",
    )


def test_freeze_reads_only_validation_and_reuse_preserves_receipt(freeze_source):
    source = freeze_source
    assert build(source)["test_evaluated"] is False
    original = source.frozen.read_bytes()
    assert build(source)["reused"] is True
    assert source.frozen.read_bytes() == original
    assert source.loaded == ["validation"]
    assert freeze.verify_freeze(source.frozen, require_committed=True)["status"] == "success"


def test_changed_validation_bytes_block_freeze(freeze_source):
    source = freeze_source
    source.validation.write_bytes(source.validation.read_bytes() + b"corruption")
    with pytest.raises(ValueError, match="integrity"):
        build(source)
    assert not source.frozen.exists()
    assert source.loaded == []


def test_post_freeze_policy_change_is_rejected(freeze_source):
    source = freeze_source
    build(source)
    policy = json.loads(source.policy.read_text())
    policy["acceptance"]["minimum_average_precision"] = 0.1
    write_json(source.policy, policy)
    with pytest.raises(freeze.FreezeError, match="changed"):
        freeze.verify_freeze(source.frozen)
    with pytest.raises(freeze.FreezeError, match="different policy"):
        build(source)


def test_changed_input_during_reference_check_does_not_publish(freeze_source, monkeypatch):
    source = freeze_source

    def change(*args):
        source.validation.write_bytes(source.validation.read_bytes() + b"mutation")
        return {}

    monkeypatch.setattr(freeze, "reference_identity", change)
    with pytest.raises(freeze.FreezeError, match="changed during"):
        build(source)
    assert not source.frozen.exists()


@pytest.mark.parametrize("value", [float("nan"), True, 0, 1.1])
def test_invalid_acceptance_targets_are_rejected(tmp_path, value):
    policy = json.loads(freeze.DEFAULT_POLICY.read_text())
    policy["acceptance"]["minimum_average_precision"] = value
    path = tmp_path / "policy.json"
    write_json(path, policy)
    with pytest.raises(freeze.FreezeError, match="finite acceptance"):
        freeze.load_policy(path)


def test_json_create_only_never_replaces_existing_receipt(tmp_path):
    path = tmp_path / "receipt.json"
    write_json(path, {"original": True}, overwrite=False)
    original = path.read_bytes()
    with pytest.raises(FileExistsError):
        write_json(path, {"replacement": True}, overwrite=False)
    assert path.read_bytes() == original
    assert sorted(p.name for p in tmp_path.iterdir()) == ["receipt.json"]


def test_eligible_ablation_cannot_be_ignored(freeze_source, monkeypatch):
    from fraud_detection_mlops.modeling import experiments

    monkeypatch.setattr(
        experiments,
        "verify_experiments",
        lambda path: {
            "run_id": "ablation",
            "eligible_ranking": ["candidate"],
            "fallback": "retain_reference",
        },
    )
    with pytest.raises(freeze.FreezeError, match="Ablation evidence"):
        build(freeze_source)
    assert freeze_source.loaded == []
    assert not freeze_source.frozen.exists()


def test_commit_guard_blocks_freeze_before_data_access(freeze_source, monkeypatch):
    def reject(path):
        raise freeze.FreezeError("Commit first")

    monkeypatch.setattr(freeze, "committed_inputs", reject)
    with pytest.raises(freeze.FreezeError, match="Commit"):
        build(freeze_source)
    assert freeze_source.loaded == []
    assert not freeze_source.frozen.exists()


def test_real_mlflow_reference_is_frozen_without_train_or_test_files(baseline_source, monkeypatch):
    from fraud_detection_mlops.modeling import baseline, experiments, train

    # The small linear fixture normally selects LR. This integration exercises
    # HGB artifact identity and parity; model ranking has its own tests.
    monkeypatch.setattr(
        baseline, "rank_models", lambda *args: ["hist_gradient_boosting", "logistic_regression"]
    )
    root = baseline_source
    options = {"inventory_path": root / "inventory.json", "protocol_path": root / "protocol.json"}
    result = train.run_baseline(
        root / "gold", root / "baseline", tracking_root=root / "tracking", **options
    )
    directory = Path(result["baseline_path"])
    gold = root / "gold" / ("b" * 40) / "gold_v1"
    manifest = json.loads((gold / "manifest.json").read_text())
    ablation = json.loads(experiments.DEFAULT_POLICY.read_text())
    ablation["data_policy"].update(train_rows=160, validation_rows=160, validation_days=2)
    ablation["reference"].update(
        baseline_run_id=result["run_id"],
        baseline_manifest_sha256=sha256(directory / "manifest.json"),
        model_uri=result["tracking"]["hist_gradient_boosting"]["model_uri"],
        **{
            k: result["comparison"]["hist_gradient_boosting"][k]
            for k in ("average_precision", "daily_customer_precision_at_100")
        },
    )
    ablation_path = root / "ablation.json"
    write_json(ablation_path, ablation)
    monkeypatch.setattr(
        experiments,
        "committed_inputs",
        lambda path: {
            "git_revision": "a" * 40,
            "files": {"poetry.lock": sha256(experiments.PROJECT_ROOT / "poetry.lock")},
        },
    )
    experiment = experiments.run_experiments(
        directory,
        policy_path=ablation_path,
        gold_root=root / "gold",
        tracking_root=root / "tracking",
        output_root=root / "experiments",
        **options,
    )
    policy = json.loads(freeze.DEFAULT_POLICY.read_text())
    policy["reference"].update(
        baseline_run_id=result["run_id"],
        baseline_manifest_sha256=sha256(directory / "manifest.json"),
        gold_manifest_sha256=sha256(gold / "manifest.json"),
        experiment_run_id=experiment["run_id"],
        model_uri=result["tracking"]["hist_gradient_boosting"]["model_uri"],
    )
    policy["holdout"].update(**manifest["protocol"]["windows"]["test"], expected_rows=160)
    policy_path = root / "final_policy.json"
    write_json(policy_path, policy)
    monkeypatch.setattr(freeze, "PROJECT_ROOT", root)
    monkeypatch.setattr(
        freeze,
        "committed_inputs",
        lambda path: {
            "git_revision": "a" * 40,
            "files": {policy_path.name: sha256(policy_path)},
        },
    )
    for split in ("train", "test"):
        shutil.rmtree(gold / "transactions" / f"split={split}")
    monkeypatch.setattr(train, "fit_candidate", lambda *a, **kw: pytest.fail("Unexpected fit"))
    frozen = root / "frozen_candidate.json"
    freeze.freeze_reference(
        directory,
        Path(experiment["experiment_path"]),
        policy_path=policy_path,
        freeze_path=frozen,
        gold_root=root / "gold",
        tracking_root=root / "tracking",
        **options,
    )
    receipt = json.loads(frozen.read_text())
    assert receipt["model"]["serialization_format"] == "skops"
    assert len(receipt["model"]["model_sha256"]) == 64
    assert receipt["model_uri"] == policy["reference"]["model_uri"]
    assert receipt["test_evaluated"] is False
    assert freeze.verify_freeze(frozen)["status"] == "success"
