"""Final holdout access, frozen identities, fixed gates and retry semantics."""

from importlib.metadata import version
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import mlflow
import numpy as np
import pandas as pd
import pytest

from fraud_detection_mlops.artifacts import sha256, write_json
from fraud_detection_mlops.features.feature_schema import DTYPES, FEATURE_COLUMNS
from fraud_detection_mlops.integrations import mlflow_tracking as tracking
from fraud_detection_mlops.modeling.experiments import (
    baseline_experiment as train,
)
from fraud_detection_mlops.modeling.experiments import (
    baseline_policy as baseline,
)
from fraud_detection_mlops.modeling.experiments import (
    candidate_freeze as freeze,
)
from fraud_detection_mlops.modeling.experiments import candidate_training
from fraud_detection_mlops.modeling.experiments import (
    final_holdout_evaluation as evaluation,
)
from fraud_detection_mlops.modeling.experiments import (
    terminal_feature_ablation as experiments,
)


@pytest.fixture
def evaluation_source(tmp_path, monkeypatch):
    root = tmp_path
    for module in (evaluation, freeze):
        monkeypatch.setattr(module, "PROJECT_ROOT", root)
    gold = root / "gold_v1"
    gold.mkdir()
    records = []
    for number, day in enumerate(pd.date_range("2018-05-20", periods=2)):
        stamps = day + pd.to_timedelta([1, 2, 3, 4], unit="h")
        frame = pd.DataFrame({name: np.zeros(4) for name in DTYPES})
        frame["TRANSACTION_ID"] = np.arange(4) + number * 4
        frame["CUSTOMER_ID"] = [1, 1, 2, 3]
        frame["TERMINAL_ID"] = 1
        frame["TX_DATETIME"] = stamps
        frame["LABEL_AVAILABLE_AT"] = stamps + pd.Timedelta(days=7)
        frame["TX_AMOUNT"] = [10, 200, 200, 10]
        frame["TX_FRAUD"] = [0, 1, 1, 0]
        frame["TX_HOUR"] = stamps.hour
        frame["TX_WEEKDAY"] = stamps.dayofweek + 1
        path = gold / f"{day.date()}.parquet"
        frame.astype(DTYPES).to_parquet(path, index=False)
        records.append(
            {
                "path": path.name,
                "date": str(day.date()),
                "split": "test",
                "rows": 4,
                "size_bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
        )
    write_json(gold / "manifest.json", {"files": records})
    policy = json.loads(freeze.DEFAULT_POLICY.read_text())
    policy["holdout"].update(start="2018-05-20", end_exclusive="2018-05-22", expected_rows=8)
    policy["reference"]["gold_manifest_sha256"] = sha256(gold / "manifest.json")
    write_json(root / "policy.json", policy)
    receipt = {
        "schema_version": 1,
        "version": "freeze_v1",
        "feature_columns": FEATURE_COLUMNS,
        "policy": policy,
        "policy_path": "policy.json",
        "gold_path": gold.name,
        "tracking_root": "tracking",
        "model_uri": policy["reference"]["model_uri"],
        "environment": {
            p: version(p) for p in ("mlflow", "scikit-learn", "skops", "numpy", "pandas")
        },
        "model_parameters": baseline.load_configuration(baseline.DEFAULT_CONFIG)["models"][
            evaluation.MODEL_ID
        ],
        "test_evaluated": False,
        "refit": False,
        "bound_files": {"policy.json": sha256(root / "policy.json")},
    }
    frozen = root / "frozen.json"
    write_json(frozen, receipt)
    provenance = lambda path: {"git_revision": "a" * 40, "files": {"frozen.json": sha256(frozen)}}
    monkeypatch.setattr(freeze, "committed_inputs", provenance)
    monkeypatch.setattr(evaluation, "committed_inputs", provenance)
    calls = []

    class Model:
        def predict_proba(self, features):
            assert list(features.columns) == FEATURE_COLUMNS
            calls.append(len(features))
            positive = np.where(features.TX_AMOUNT > 100, 0.9, 0.1)
            return np.column_stack([1 - positive, positive])

        def fit(self, *args):
            pytest.fail("Final evaluation fitted a model")

    monkeypatch.setattr(evaluation, "load_frozen_model", lambda *args: Model())
    monkeypatch.setattr(evaluation, "publish_evaluation", lambda *args: {"run_id": "evaluation"})
    directory = root / evaluation.EVALUATION_VERSION / sha256(frozen)
    return SimpleNamespace(
        root=root,
        gold=gold,
        frozen=frozen,
        directory=directory,
        calls=calls,
        policy=policy,
        records=records,
        receipt=receipt,
    )


def test_fixed_candidate_scores_once_and_offline_verification_needs_no_raw_data(evaluation_source):
    source = evaluation_source
    first = evaluation.run_evaluation(source.frozen)
    original = (source.directory / "report.json").read_bytes()
    second = evaluation.run_evaluation(source.frozen)
    assert first["reused"] is False and second["reused"] is True
    assert first["gate"]["passed"] is True
    assert first["gate"]["production_promotion"] is False
    assert source.calls == [8]
    assert original == (source.directory / "report.json").read_bytes()
    metrics = first["comparison"][evaluation.MODEL_ID]
    assert metrics["average_precision"] == 1.0
    assert metrics["daily_customer_precision_at_100"] == pytest.approx(2 / 3)
    assert first["comparison"]["constant_score_control"]["average_precision"] == 0.5
    state = (source.directory / "state.json").read_bytes()
    (source.directory / "state.json").unlink()
    with pytest.raises(evaluation.EvaluationError, match="original access record"):
        evaluation.run_evaluation(source.frozen)
    (source.directory / "state.json").write_bytes(state)
    shutil.rmtree(source.gold)
    assert evaluation.verify_evaluation(source.directory)["status"] == "success"


@pytest.mark.parametrize(
    "ap,precision,passed", [(0.5, 0.45, True), (0.6, 0.44, False), (0.49, 0.6, False)]
)
def test_gate_requires_both_predefined_targets(evaluation_source, ap, precision, passed):
    decision = evaluation.acceptance(
        {"average_precision": ap, "daily_customer_precision_at_100": precision},
        evaluation_source.policy,
    )
    assert decision["passed"] is passed
    assert decision["production_promotion"] is False


def test_commit_guard_blocks_before_creating_access_record(evaluation_source, monkeypatch):
    def reject(path):
        raise freeze.FreezeError("Commit first")

    monkeypatch.setattr(freeze, "committed_inputs", reject)
    with pytest.raises(freeze.FreezeError, match="Commit"):
        evaluation.run_evaluation(evaluation_source.frozen)
    assert not evaluation_source.directory.exists()
    assert evaluation_source.calls == []


def test_model_failure_is_recorded_before_test_consumption(evaluation_source, monkeypatch):
    def reject(*args):
        raise evaluation.EvaluationError("Frozen model bytes changed")

    monkeypatch.setattr(evaluation, "load_frozen_model", reject)
    with pytest.raises(evaluation.EvaluationError, match="model bytes"):
        evaluation.run_evaluation(evaluation_source.frozen)
    state = json.loads((evaluation_source.directory / "state.json").read_text())
    assert state["status"] == "failed" and state["test_access_started"] is False
    assert evaluation_source.calls == []


def test_corrupt_test_cannot_be_scored_and_access_attempt_remains_recorded(evaluation_source):
    source = evaluation_source
    (source.gold / source.records[0]["path"]).write_bytes(b"corrupt")
    with pytest.raises(experiments.ExperimentError, match="integrity"):
        evaluation.run_evaluation(source.frozen)
    state = json.loads((source.directory / "state.json").read_text())
    assert state["status"] == "failed" and state["test_access_started"] is True
    assert source.calls == []


def test_identical_failed_attempt_can_retry_but_implementation_change_cannot(
    evaluation_source, monkeypatch
):
    source = evaluation_source
    original = evaluation.load_test
    monkeypatch.setattr(
        evaluation, "load_test", lambda *a: (_ for _ in ()).throw(OSError("interrupted"))
    )
    with pytest.raises(OSError, match="interrupted"):
        evaluation.run_evaluation(source.frozen)
    state = json.loads((source.directory / "state.json").read_text())
    assert state["test_access_started"] is True and state["attempts"] == 1
    monkeypatch.setattr(evaluation, "load_test", original)
    with monkeypatch.context() as patch:
        patch.setattr(
            evaluation,
            "committed_inputs",
            lambda path: {
                "git_revision": "b" * 40,
                "files": {"frozen.json": sha256(source.frozen), "evaluation.py": "changed"},
            },
        )
        with pytest.raises(evaluation.EvaluationError, match="Retry inputs"):
            evaluation.run_evaluation(source.frozen)
    assert evaluation.run_evaluation(source.frozen)["status"] == "success"
    assert json.loads((source.directory / "state.json").read_text())["attempts"] == 2


def test_tracking_retry_does_not_score_again(evaluation_source, monkeypatch):
    source = evaluation_source
    publish = evaluation.publish_evaluation
    monkeypatch.setattr(
        evaluation,
        "publish_evaluation",
        lambda *a: (_ for _ in ()).throw(OSError("tracking offline")),
    )
    with pytest.raises(OSError, match="tracking offline"):
        evaluation.run_evaluation(source.frozen)
    assert (source.directory / "manifest.json").exists()
    monkeypatch.setattr(evaluation, "publish_evaluation", publish)
    assert evaluation.run_evaluation(source.frozen)["reused"] is True
    assert source.calls == [8]


def test_rehashed_report_cannot_change_the_fixed_gate(evaluation_source):
    source = evaluation_source
    evaluation.run_evaluation(source.frozen)
    report = json.loads((source.directory / "report.json").read_text())
    report["gate"]["criteria"]["average_precision"]["minimum"] = 0.01
    write_json(source.directory / "report.json", report)
    manifest = json.loads((source.directory / "manifest.json").read_text())
    manifest["files"] = experiments.file_records(source.directory, evaluation.OUTPUTS)
    write_json(source.directory / "manifest.json", manifest)
    with pytest.raises(evaluation.EvaluationError, match="decision"):
        evaluation.verify_evaluation(source.directory)


def test_concurrent_evaluation_is_rejected(evaluation_source):
    source = evaluation_source
    source.directory.mkdir(parents=True)
    with (source.directory / ".lock").open("a") as lock:
        evaluation.fcntl.flock(lock, evaluation.fcntl.LOCK_EX | evaluation.fcntl.LOCK_NB)
        with pytest.raises(evaluation.EvaluationError, match="already running"):
            evaluation.run_evaluation(source.frozen)
    assert source.calls == []


@pytest.mark.parametrize("defect", ["label", "day", "duplicate", "nan"])
def test_test_loader_reuses_gold_semantics_and_unique_identity(evaluation_source, defect):
    source = evaluation_source
    path = source.gold / source.records[0]["path"]
    frame = pd.read_parquet(path)
    if defect == "label":
        frame.loc[0, "TX_FRAUD"] = 2
    elif defect == "day":
        frame.loc[0, "TX_DATETIME"] += pd.Timedelta(days=1)
    elif defect == "duplicate":
        frame.loc[0, "TRANSACTION_ID"] = frame.loc[1, "TRANSACTION_ID"]
    else:
        frame.loc[0, "CUSTOMER_AVG_AMOUNT_1D"] = np.nan
    frame.to_parquet(path, index=False)
    source.records[0].update(sha256=sha256(path), size_bytes=path.stat().st_size)
    with pytest.raises(evaluation.EvaluationError):
        evaluation.load_test(source.gold, source.records, source.policy["holdout"])


def test_real_native_model_and_evaluation_run_reuse_only_test_without_fit(
    baseline_source, monkeypatch
):
    root = baseline_source
    gold = root / "gold" / ("b" * 40) / "gold_v1"
    manifest = json.loads((gold / "manifest.json").read_text())
    features, labels, _ = train.load_split(gold, manifest, "train")
    model = candidate_training.make_model(evaluation.MODEL_ID).fit(features, labels)
    logged = tracking.log_candidate(
        model,
        features,
        model.predict_proba(features)[:, 1],
        name=evaluation.MODEL_ID,
        parameters={},
        metrics={},
        tags={"baseline_run_id": "synthetic-reference"},
        root=root / "tracking",
    )
    with tracking.local_tracking(root / "tracking"):
        model_path = Path(
            mlflow.artifacts.download_artifacts(
                artifact_uri=logged["model_uri"], dst_path=str(root / "snapshot")
            )
        )
        metadata = mlflow.models.Model.load(model_path / "MLmodel")
    policy = json.loads(freeze.DEFAULT_POLICY.read_text())
    policy["holdout"].update(**manifest["protocol"]["windows"]["test"], expected_rows=160)
    policy["reference"].update(
        gold_manifest_sha256=sha256(gold / "manifest.json"), model_uri=logged["model_uri"]
    )
    write_json(root / "policy.json", policy)
    receipt = {
        "schema_version": 1,
        "version": "freeze_v1",
        "feature_columns": FEATURE_COLUMNS,
        "policy": policy,
        "policy_path": "policy.json",
        "gold_path": str(gold.relative_to(root)),
        "tracking_root": "tracking",
        "model_uri": logged["model_uri"],
        "model": {
            "mlflow_run_id": logged["run_id"],
            "serialization_format": "skops",
            "mlmodel_sha256": sha256(model_path / "MLmodel"),
            "model_sha256": sha256(model_path / metadata.flavors["sklearn"]["pickled_model"]),
        },
        "model_parameters": baseline.load_configuration(baseline.DEFAULT_CONFIG)["models"][
            evaluation.MODEL_ID
        ],
        "environment": {
            p: version(p) for p in ("mlflow", "scikit-learn", "skops", "numpy", "pandas")
        },
        "test_evaluated": False,
        "refit": False,
        "bound_files": {"policy.json": sha256(root / "policy.json")},
    }
    frozen = root / "frozen.json"
    write_json(frozen, receipt)
    for module in (evaluation, freeze):
        monkeypatch.setattr(module, "PROJECT_ROOT", root)
    provenance = lambda path: {"git_revision": "a" * 40, "files": {"frozen.json": sha256(frozen)}}
    monkeypatch.setattr(freeze, "committed_inputs", provenance)
    monkeypatch.setattr(evaluation, "committed_inputs", provenance)
    for split in ("train", "validation"):
        shutil.rmtree(gold / "transactions" / f"split={split}")
    monkeypatch.setattr(
        type(model[-1]),
        "fit",
        lambda *a, **k: pytest.fail("Unexpected fit"),
    )
    with monkeypatch.context() as patch:
        patch.setattr(
            mlflow, "log_artifact", lambda *a: (_ for _ in ()).throw(OSError("upload failed"))
        )
        with pytest.raises(OSError, match="upload failed"):
            evaluation.run_evaluation(frozen)
    with tracking.local_tracking(root / "tracking"):
        client = mlflow.MlflowClient()
        exp = client.get_experiment_by_name("fraud-final-evaluation-v1")
        failed = client.search_runs([exp.experiment_id])
        assert len(failed) == 1 and failed[0].info.status == "FAILED"
        failed_run_id = failed[0].info.run_id
    first = evaluation.run_evaluation(frozen)
    second = evaluation.run_evaluation(frozen)
    assert first["reused"] is True and second["reused"] is True
    assert first["tracking"]["run_id"] == failed_run_id
    assert first["tracking"]["run_id"] == second["tracking"]["run_id"]
    assert first["comparison"][evaluation.MODEL_ID]["rows"] == 160
    with tracking.local_tracking(root / "tracking"):
        client = mlflow.MlflowClient()
        assert client.get_run(logged["run_id"]).data.tags["test_evaluated"] == "false"
        exp = client.get_experiment_by_name("fraud-final-evaluation-v1")
        assert len(client.search_runs([exp.experiment_id])) == 1
    # Frozen hash mismatch must be caught before skops deserialization.
    receipt["model"]["model_sha256"] = "0" * 64
    monkeypatch.setattr(
        tracking, "download_pipeline", lambda *a: pytest.fail("Loaded wrong frozen bytes")
    )
    with pytest.raises(evaluation.EvaluationError, match="model bytes"):
        evaluation.load_frozen_model(receipt, root / "mismatch")
