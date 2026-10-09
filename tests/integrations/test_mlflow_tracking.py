import json
from pathlib import Path

import mlflow
from mlflow import MlflowClient
import mlflow.sklearn
import pytest
from typer.testing import CliRunner

from fraud_detection_mlops.integrations import mlflow_tracking as tracking
from fraud_detection_mlops.integrations import skops_persistence as persistence
from fraud_detection_mlops.modeling.experiments import (
    baseline_experiment as train,
)


def test_training_creates_three_main_runs_and_checks_native_reload(baseline_source, monkeypatch):
    calls = []
    original = train.load_split

    def observed(directory, manifest, split):
        calls.append(split)
        return original(directory, manifest, split)

    monkeypatch.setattr(train, "load_split", observed)
    root = baseline_source / "tracking"
    previous_uri = mlflow.get_tracking_uri()
    result = train.run_baseline(
        baseline_source / "gold",
        baseline_source / "experiments",
        inventory_path=baseline_source / "inventory.json",
        protocol_path=baseline_source / "protocol.json",
        tracking_root=root,
    )
    assert calls == ["train", "validation"]
    assert result["test_evaluated"] is False
    assert mlflow.get_tracking_uri() == previous_uri
    client = MlflowClient(tracking_uri=tracking.tracking_uri(root))
    experiment = client.get_experiment_by_name(tracking.EXPERIMENT)
    runs = client.search_runs([experiment.experiment_id])
    assert len(runs) == 3
    assert {run.data.tags["model_id"] for run in runs} == set(train.CLASSIFIERS)
    for run in runs:
        assert run.info.status == "FINISHED"
        assert "mlflow.parentRunId" not in run.data.tags
        assert run.data.tags["baseline_run_id"] == result["run_id"]
        assert run.data.tags["test_evaluated"] == "false"
        assert run.data.tags["promotion_status"] == "not_promoted"
        model_id = run.data.tags["model_id"]
        assert (
            run.data.metrics["validation_average_precision"]
            == result["comparison"][model_id]["average_precision"]
        )
        record = result["tracking"][model_id]
        assert record["run_id"] == run.info.run_id
        assert tracking.verify_model(record["model_uri"], root)["feature_count"] == 19
    runner = CliRunner()
    record = result["tracking"]["logistic_regression"]
    response = runner.invoke(
        tracking.app, ["verify", record["model_uri"], "--tracking-root", str(root)]
    )
    assert response.exit_code == 0, response.output
    assert json.loads(response.stdout)["status"] == "success"
    manifest = json.loads((Path(result["baseline_path"]) / "manifest.json").read_text())
    assert manifest["tracking"] == result["tracking"]
    assert manifest["version"] == "baseline_v2"
    assert not list(Path(result["baseline_path"]).rglob("*.joblib"))


def test_reload_mismatch_marks_run_failed_and_does_not_publish_baseline(
    baseline_source, monkeypatch
):
    def mismatch(*args):
        raise persistence.PersistenceError("changed validation scores")

    monkeypatch.setattr(tracking, "check_scores", mismatch)
    root = baseline_source / "tracking"
    with pytest.raises(persistence.PersistenceError, match="changed validation"):
        train.run_baseline(
            baseline_source / "gold",
            baseline_source / "experiments",
            inventory_path=baseline_source / "inventory.json",
            protocol_path=baseline_source / "protocol.json",
            tracking_root=root,
        )
    client = MlflowClient(tracking_uri=tracking.tracking_uri(root))
    experiment = client.get_experiment_by_name(tracking.EXPERIMENT)
    assert [run.info.status for run in client.search_runs([experiment.experiment_id])] == [
        "FAILED"
    ]
    parent = baseline_source / "experiments" / ("b" * 40)
    assert not (parent / "baseline_v2").exists()
    assert json.loads(next((parent / "runs").glob("*.json")).read_text())["status"] == "failed"
    assert not list(parent.glob(".baseline-staging-*"))


def test_logging_failure_retains_candidate_receipt_but_no_completed_baseline(
    baseline_source, monkeypatch
):
    original = tracking.log_candidate

    def fail_second(*args, name, **kwargs):
        if name == "logistic_regression":
            raise OSError("tracking unavailable")
        return original(*args, name=name, **kwargs)

    monkeypatch.setattr(tracking, "log_candidate", fail_second)
    with pytest.raises(OSError, match="tracking unavailable"):
        train.run_baseline(
            baseline_source / "gold",
            baseline_source / "experiments",
            inventory_path=baseline_source / "inventory.json",
            protocol_path=baseline_source / "protocol.json",
            tracking_root=baseline_source / "tracking",
        )
    parent = baseline_source / "experiments" / ("b" * 40)
    audit = json.loads(next((parent / "runs").glob("*.json")).read_text())
    assert audit["status"] == "failed"
    assert set(audit["tracking"]) == {"dummy_prior"}
    assert not (parent / "baseline_v2").exists()


def test_active_run_is_rejected_instead_of_creating_a_child(toy_pipeline, tmp_path):
    model, features = toy_pipeline
    root = tmp_path / "tracking"
    root.mkdir()
    with (
        tracking.local_tracking(root),
        mlflow.start_run(),
        pytest.raises(tracking.TrackingError, match="active MLflow run"),
    ):
        tracking.log_candidate(
            model,
            features,
            model.predict_proba(features)[:, 1],
            name="dummy_prior",
            parameters={},
            metrics={},
            tags={"baseline_run_id": "a" * 32},
            root=root,
        )


def test_pickle_format_is_rejected_before_model_loading(toy_pipeline, tmp_path, monkeypatch):
    model, _ = toy_pipeline
    path = tmp_path / "pickle"
    mlflow.sklearn.save_model(
        model, str(path), serialization_format="cloudpickle", pip_requirements=[]
    )
    monkeypatch.setattr(
        tracking, "load_pipeline", lambda *args: pytest.fail("pickle model loaded")
    )
    with pytest.raises(tracking.TrackingError, match="Unexpected MLflow model"):
        tracking.load_tracked_model(str(path), tmp_path / "download")
