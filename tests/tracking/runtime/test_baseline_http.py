"""Three baseline models through a real disposable HTTP tracking service."""

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("FRAUD_BASELINE_HTTP_TEST") != "1", reason="Run baseline-http tox environment"
)
ROOT = Path(__file__).resolve().parents[3]


def run(source, uri):
    from fraud_detection_mlops.modeling.experiments import baseline_experiment as baseline

    return baseline.run_baseline(
        source / "gold",
        source / "experiments",
        inventory_path=source / "inventory.json",
        protocol_path=source / "protocol.json",
        tracking_root=None,
        http_uri=uri,
    )


def test_three_models_cli_reload_and_scoped_http_manifest(baseline_source, model_server):
    import mlflow
    from mlflow import MlflowClient
    import numpy as np
    import pandas as pd

    from fraud_detection_mlops.integrations import mlflow_tracking as tracking
    from fraud_detection_mlops.modeling.experiments import baseline_experiment as baseline

    source = baseline_source
    uri, _, environment = model_server
    base = ROOT / "data/storage/tracking-lab"
    base.mkdir(parents=True, exist_ok=True)
    previous = mlflow.get_tracking_uri()
    with TemporaryDirectory(prefix=".baseline-http-", dir=base) as temporary:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "fraud_detection_mlops.modeling.experiments.baseline_experiment",
                "run",
                "--gold-root",
                str(source / "gold"),
                "--inventory",
                str(source / "inventory.json"),
                "--protocol",
                str(source / "protocol.json"),
                "--output-root",
                str(Path(temporary) / "experiments"),
                "--tracking-uri",
                uri,
            ],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr[-3500:]
        result = json.loads(completed.stdout)
        assert "Fitting" in completed.stderr
        assert set(result["tracking"]) == set(baseline.CLASSIFIERS)
        assert not result["test_evaluated"]
        directory = Path(result["baseline_path"])
        manifest = json.loads((directory / "manifest.json").read_text())
        audit = json.loads(Path(result["audit_path"]).read_text())
        assert audit["status"] == "success" and audit["tracking"] == result["tracking"]
        assert manifest["tracking_target"] == {
            "mode": "http",
            "uri": uri,
            "experiment": baseline.HTTP_EXPERIMENT,
        }
        assert manifest["tracking_target"] == audit["tracking_target"]
        gold_dir = source / "gold" / ("b" * 40) / "gold_v1"
        gold_manifest = json.loads((gold_dir / "manifest.json").read_text())
        features, _, _ = baseline.load_split(gold_dir, gold_manifest, "validation")
        expected = {
            name: pd.read_parquet(
                directory / "models" / name / "validation_predictions.parquet"
            ).SCORE.to_numpy()
            for name in baseline.CLASSIFIERS
        }
        hidden = directory.with_name(directory.name + "-hidden")
        directory.rename(hidden)
        try:
            client = MlflowClient(tracking_uri=uri)
            for name, record in result["tracking"].items():
                remote = client.get_run(record["run_id"])
                assert remote.info.status == "FINISHED"
                assert remote.data.tags["model_id"] == name
                assert remote.data.tags["baseline_run_id"] == result["run_id"]
                assert remote.data.tags["promotion_status"] == "not_promoted"
                assert (
                    remote.data.metrics["validation_average_precision"]
                    == result["comparison"][name]["average_precision"]
                )
                assert (
                    client.get_experiment(remote.info.experiment_id).name
                    == baseline.HTTP_EXPERIMENT
                )
                with tracking.http_tracking(uri), TemporaryDirectory(dir=temporary) as download:
                    restored = tracking.load_tracked_model(record["model_uri"], Path(download))
                    np.testing.assert_allclose(
                        restored.predict(features)[:, 1], expected[name], rtol=1e-12, atol=1e-12
                    )
            runs = client.search_runs(
                [remote.info.experiment_id],
                filter_string=f"tags.baseline_run_id = '{result['run_id']}'",
            )
            assert len(runs) == 3 and not any("mlflow.parentRunId" in r.data.tags for r in runs)
        finally:
            hidden.rename(directory)
        assert not list(Path(temporary).rglob("*.db"))
    assert mlflow.get_tracking_uri() == previous


def test_second_reload_failure_retains_first_run_and_no_completed_baseline(
    baseline_source, model_server, monkeypatch
):
    from mlflow import MlflowClient

    from fraud_detection_mlops.integrations import mlflow_tracking as tracking
    from fraud_detection_mlops.integrations.skops_persistence import PersistenceError
    from fraud_detection_mlops.modeling.experiments import baseline_experiment as baseline

    uri, _, _ = model_server
    original = tracking.check_scores
    calls = []

    def fail_second(*args):
        calls.append(1)
        if len(calls) == 2:
            raise PersistenceError("Injected second-model parity failure")
        return original(*args)

    monkeypatch.setattr(tracking, "check_scores", fail_second)
    with pytest.raises(PersistenceError, match="second-model"):
        run(baseline_source, uri)
    parent = baseline_source / "experiments" / ("b" * 40)
    audit = json.loads(next((parent / "runs").glob("*.json")).read_text())
    assert audit["status"] == "failed" and set(audit["tracking"]) == {"dummy_prior"}
    assert not (parent / "baseline_v2").exists()
    assert not list(parent.glob(".baseline-staging-*"))
    client = MlflowClient(tracking_uri=uri)
    experiment = client.get_experiment_by_name(baseline.HTTP_EXPERIMENT)
    runs = client.search_runs(
        [experiment.experiment_id], filter_string=f"tags.baseline_run_id = '{audit['run_id']}'"
    )
    assert {r.data.tags["model_id"]: r.info.status for r in runs} == {
        "dummy_prior": "FINISHED",
        "logistic_regression": "FAILED",
    }


def test_unavailable_http_fails_without_local_fallback_and_explicit_local_still_works(
    baseline_source,
):
    from mlflow.exceptions import MlflowException

    from fraud_detection_mlops.modeling.experiments import baseline_experiment as baseline

    with socket.socket() as unavailable:
        unavailable.bind(("127.0.0.1", 0))
        uri = f"http://127.0.0.1:{unavailable.getsockname()[1]}"
        with pytest.raises(MlflowException):
            run(baseline_source, uri)
    parent = baseline_source / "experiments" / ("b" * 40)
    assert not (parent / "baseline_v2").exists()
    assert not list(baseline_source.rglob("*.db"))
    result = baseline.run_baseline(
        baseline_source / "gold",
        baseline_source / "local-experiments",
        inventory_path=baseline_source / "inventory.json",
        protocol_path=baseline_source / "protocol.json",
        tracking_root=baseline_source / "local-tracking",
    )
    assert (
        len(result["tracking"]) == 3 and (baseline_source / "local-tracking/mlflow.db").is_file()
    )
    assert all(set(record) == {"run_id", "model_uri"} for record in result["tracking"].values())
    assert "tracking_target" not in json.loads(
        (Path(result["baseline_path"]) / "manifest.json").read_text()
    )


def test_native_harness_receipt_with_disposable_http_fixture(
    baseline_source, model_server, monkeypatch
):
    import importlib.util
    import shutil
    from types import SimpleNamespace
    from uuid import uuid4

    from scripts.tracking import local_stack as stack

    source = Path(__file__).parents[1] / "live/test_baseline_http.py"
    spec = importlib.util.spec_from_file_location("baseline_native_fixture", source)
    live = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(live)
    monkeypatch.setattr(live, "URI", model_server[0])
    monkeypatch.setattr(stack, "inspect_service", lambda root, name: {"fixture_service": name})
    identity = uuid4().hex
    monkeypatch.setattr(live, "uuid4", lambda: SimpleNamespace(hex=identity))
    workspace = stack.workspace(ROOT)
    output = workspace / ("baseline-" + identity)
    receipt = workspace / "receipts" / ("baseline-" + identity + ".json")
    assert not output.exists() and not receipt.exists()
    try:
        live.test_three_synthetic_baseline_models_reload_without_local_outputs(baseline_source)
        result = json.loads(receipt.read_text())
        assert result["status"] == "success" and result["independent_main_runs"] == 3
        assert len(result["models"]) == 3 and result["training_rows"] == 160
        assert all(r["max_absolute_score_error"] <= 1e-12 for r in result["models"].values())
    finally:
        if output.exists():
            shutil.rmtree(output)
        receipt.unlink(missing_ok=True)
