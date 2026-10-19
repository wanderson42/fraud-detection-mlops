"""Generated native MLflow schema shared by read-only inventory and snapshot tests."""

import json
from pathlib import Path

from mlflow import MlflowClient
import pytest


@pytest.fixture
def store(tmp_path):
    root = tmp_path / "project"
    tracking = root / "data/tracking"
    artifacts = tracking / "artifacts"
    artifacts.mkdir(parents=True)
    client = MlflowClient(tracking_uri="sqlite:///" + str(tracking / "mlflow.db"))
    experiment = client.create_experiment(
        "inventory-fixture", artifact_location=artifacts.as_uri()
    )
    run = client.create_run(experiment)
    client.log_param(run.info.run_id, "private", "do-not-emit-parameter-value")
    client.log_metric(run.info.run_id, "ap", 0.5, timestamp=10, step=0)
    client.log_metric(run.info.run_id, "ap", 0.6, timestamp=20, step=1)
    model = client.create_logged_model(experiment, name="fixture", source_run_id=run.info.run_id)
    model_path = Path(model.artifact_location.removeprefix("file://"))
    model_path.mkdir(parents=True)
    (model_path / "pipeline.skops").write_bytes(b"not-a-deserializable-model")
    client.finalize_logged_model(model.model_id, "READY")
    run_path = Path(run.info.artifact_uri.removeprefix("file://"))
    run_path.mkdir(parents=True, exist_ok=True)
    (run_path / "metrics.json").write_text('{"ap":0.6}\n')
    client.set_terminated(run.info.run_id)
    removed = client.create_run(experiment)
    client.set_terminated(removed.info.run_id, "FAILED")
    client.delete_run(removed.info.run_id)
    report = root / "data/processed/fixture/manifest.json"
    report.parent.mkdir(parents=True)
    report.write_text(
        json.dumps({"tracking": {"run_id": run.info.run_id, "model_uri": model.model_uri}})
    )
    return root, tracking, client, run, model
