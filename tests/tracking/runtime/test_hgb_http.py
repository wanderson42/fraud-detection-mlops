"""Real HTTP/skops transport for one HGB fold, independent of study scheduling."""

import json
import os
from pathlib import Path
import socket
from tempfile import TemporaryDirectory

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("FRAUD_HGB_HTTP_TEST") != "1", reason="Run the hgb-http tox environment"
)


def fold_inputs(prepared_data, uri):
    from fraud_detection_mlops.artifacts import sha256, write_json
    from fraud_detection_mlops.modeling.experiments import hgb_optimization as hgb
    from fraud_detection_mlops.modeling.experiments import temporal_development_data as development

    data, policy_path = prepared_data
    policy = development.load_policy(policy_path)
    manifest = json.loads((data / "manifest.json").read_text())
    directory = data / "study"
    write_json(directory / "protocol.json", policy)
    identity = {
        "policy_sha256": sha256(policy_path),
        "data_sha256": sha256(data / "manifest.json"),
        "max_fit_attempts": 1,
        **hgb.hgb_tracking_target(None, uri),
    }
    return data, policy, manifest, directory, identity


def fit(inputs, uri):
    from fraud_detection_mlops.modeling.experiments import hgb_optimization as hgb

    data, policy, manifest, directory, identity = inputs
    return hgb.fit_hgb_fold(
        directory,
        "reference",
        policy["folds"][0],
        {},
        data,
        manifest,
        identity,
        None,
        http_uri=uri,
    )


def test_http_fold_reload_without_local_model_and_reuse_without_fit_or_publication(
    prepared_data, model_server, monkeypatch, tmp_path
):
    import mlflow
    from mlflow import MlflowClient
    import numpy as np
    import pandas as pd

    from fraud_detection_mlops.integrations import mlflow_tracking as tracking
    from fraud_detection_mlops.modeling.experiments import candidate_training
    from fraud_detection_mlops.modeling.experiments import hgb_optimization as hgb
    from fraud_detection_mlops.modeling.experiments import temporal_development_data as development

    uri, _, _ = model_server
    inputs = fold_inputs(prepared_data, uri)
    previous = mlflow.get_tracking_uri()
    result = fit(inputs, uri)
    record = result["tracking"]
    client = MlflowClient(tracking_uri=uri)
    run = client.get_run(record["run_id"])
    assert run.info.status == "FINISHED" and record["model_reload_validated"]
    assert run.data.tags["fold_id"] == "fold_1"
    assert run.data.tags["test_evaluated"] == "false"
    assert client.get_experiment(run.info.experiment_id).name == hgb.HTTP_EXPERIMENT
    assert run.data.metrics["average_precision"] == result["metrics"]["average_precision"]
    data, policy, manifest, directory, _ = inputs
    _, (features, _, _) = development.load_fold(data, manifest, policy["folds"][0])
    part = directory / result["path"]
    scores = pd.read_parquet(part / "validation_predictions.parquet").SCORE.to_numpy()
    hidden = part.with_name("unavailable")
    part.rename(hidden)
    try:
        with tracking.http_tracking(uri), TemporaryDirectory(dir=tmp_path) as temporary:
            model = tracking.load_tracked_model(record["model_uri"], Path(temporary))
            np.testing.assert_allclose(
                model.predict(features)[:, 1], scores, rtol=1e-12, atol=1e-12
            )
    finally:
        hidden.rename(part)
    monkeypatch.setattr(candidate_training, "fit_candidate", lambda *a, **k: pytest.fail("refit"))
    monkeypatch.setattr(tracking, "log_candidate", lambda *a, **k: pytest.fail("republished"))
    assert fit(inputs, uri) == result
    assert mlflow.get_tracking_uri() == previous
    assert not list(data.rglob("*.db"))
    assert len(list((directory / "attempts").rglob("*.json"))) == 1


def test_unavailable_http_preserves_attempt_and_blocks_automatic_refit(prepared_data, monkeypatch):
    from mlflow.exceptions import MlflowException

    from fraud_detection_mlops.modeling.experiments import candidate_training
    from fraud_detection_mlops.modeling.experiments import hgb_optimization as hgb

    with socket.socket() as unavailable:
        unavailable.bind(("127.0.0.1", 0))
        uri = f"http://127.0.0.1:{unavailable.getsockname()[1]}"
        inputs = fold_inputs(prepared_data, uri)
        with pytest.raises(MlflowException):
            fit(inputs, uri)
    data, _, _, directory, _ = inputs
    assert not list(data.rglob("*.db"))
    assert not list((directory / "models").rglob("result.json"))
    assert len(list((directory / "attempts").rglob("*.json"))) == 1
    monkeypatch.setattr(candidate_training, "fit_candidate", lambda *a, **k: pytest.fail("refit"))
    with pytest.raises(hgb.HGBOptimizationError, match="budget exhausted|Incomplete fit"):
        fit(inputs, uri)


def test_reload_mismatch_marks_remote_failed_without_completed_fit_receipt(
    prepared_data, model_server, monkeypatch
):
    import mlflow
    from mlflow import MlflowClient

    from fraud_detection_mlops.integrations import mlflow_tracking as tracking
    from fraud_detection_mlops.integrations.skops_persistence import PersistenceError
    from fraud_detection_mlops.modeling.experiments import hgb_optimization as hgb

    uri, _, _ = model_server
    inputs = fold_inputs(prepared_data, uri)
    monkeypatch.setattr(
        tracking,
        "check_scores",
        lambda *a: (_ for _ in ()).throw(PersistenceError("parity mismatch")),
    )
    with pytest.raises(PersistenceError, match="parity mismatch"):
        fit(inputs, uri)
    client = MlflowClient(tracking_uri=uri)
    experiment = client.get_experiment_by_name(hgb.HTTP_EXPERIMENT)
    runs = client.search_runs([experiment.experiment_id], order_by=["start_time DESC"])
    assert runs[0].info.status == "FAILED"
    assert not list(inputs[3].rglob("result.json"))
    assert mlflow.active_run() is None


def test_live_harness_receipt_with_disposable_http_fixture(
    prepared_data, model_server, monkeypatch, tmp_path
):
    import importlib.util

    from scripts.tracking import local_stack as stack

    source = Path(__file__).parents[1] / "live/test_hgb_fold_http.py"
    spec = importlib.util.spec_from_file_location("hgb_live_fixture_validation", source)
    live = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(live)
    monkeypatch.setattr(live, "ROOT", tmp_path / "managed-fixture-root")
    monkeypatch.setattr(live, "URI", model_server[0])
    # Only Docker inspection is replaced; fit, HTTP model/metrics and receipt are real.
    monkeypatch.setattr(stack, "inspect_service", lambda root, name: {"fixture_service": name})
    live.test_synthetic_hgb_fold_proxy_reload_and_no_duplicate_fit(prepared_data, monkeypatch)
    receipts = list((live.ROOT / "data/storage/tracking-lab/receipts").glob("hgb-fold-*.json"))
    assert len(receipts) == 1
    result = json.loads(receipts[0].read_text())
    assert result["status"] == "success" and result["fit_attempts"] == 1
    assert result["independent_http_reload_validated"]
    assert result["completed_fold_reused_without_fit_or_publication"]
    assert not result["full_optuna_search_validated"]
