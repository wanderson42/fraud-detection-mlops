import json
from pathlib import Path

from mlflow import MlflowClient
import mlflow.pyfunc
import numpy as np
import pytest
from test_baseline import prepared_baseline  # noqa: F401 -- shared pytest source fixture
from typer.testing import CliRunner

from fraud_detection_mlops.modeling import tracking, train


@pytest.fixture(scope="module")
def baseline(request):
    source = request.getfixturevalue("prepared_baseline")
    result = train.run_baseline(
        source / "gold",
        source / "tracking-baseline",
        inventory_path=source / "inventory.json",
        protocol_path=source / "protocol.json",
    )
    return source, Path(result["baseline_path"])


def publish(baseline, root, **kwargs):
    source, directory = baseline
    return tracking.publish_baseline(
        directory,
        tracking_root=root,
        gold_root=source / "gold",
        inventory=source / "inventory.json",
        protocol=source / "protocol.json",
        **kwargs,
    )


def test_three_main_runs_reuse_preserve_source_and_verify_without_joblib(
    baseline, tmp_path, monkeypatch
):
    source, directory = baseline
    before = {str(p): p.read_bytes() for p in directory.rglob("*") if p.is_file()}
    observed = []
    original = train._read_split

    def loader(directory, manifest, split):
        observed.append(split)
        return original(directory, manifest, split)

    monkeypatch.setattr(train, "_read_split", loader)
    monkeypatch.setattr(train.Pipeline, "fit", lambda *a, **k: pytest.fail("tracking retrained"))
    result = publish(baseline, tmp_path, trust_local_models=True)
    assert len(result["models"]) == 3
    assert result["test_evaluated"] is False and result["refit"] is False
    client = MlflowClient(tracking_uri=result["tracking_uri"])
    runs = client.search_runs([result["experiment_id"]])
    assert len(runs) == 3
    assert {r.data.tags["model_id"] for r in runs} == set(train.CLASSIFIERS)
    for run in runs:
        assert run.info.status == "FINISHED"
        assert "mlflow.parentRunId" not in run.data.tags
        assert run.data.tags["promotion_status"] == "not_promoted"
        assert run.data.params["feature_count"] == "19"
        assert run.data.params["fit_rows"] == "160"
        files = client.list_artifacts(run.info.run_id, "model")
        assert sum(f.path.endswith(".skops") for f in files) == 1
        assert not any(f.path.endswith((".pkl", ".joblib")) for f in files)
        assert not any("input_example" in f.path for f in files)
    manifest = json.loads((source / "gold" / ("b" * 40) / "gold_v1/manifest.json").read_text())
    X, _, _ = original(source / "gold" / ("b" * 40) / "gold_v1", manifest, "validation")
    logistic = next(r for r in runs if r.data.tags["model_id"] == "logistic_regression")
    # Explicit client URI: no reliance on fluent MLflow global state.
    path = client.download_artifacts(logistic.info.run_id, "model", str(tmp_path))
    probabilities = mlflow.pyfunc.load_model(path).predict(X)
    assert np.asarray(probabilities).shape == (160, 2)
    monkeypatch.setattr(tracking.joblib, "load", lambda *a, **k: pytest.fail("joblib reloaded"))
    repeated = publish(baseline, tmp_path, trust_local_models=True)
    assert all(m["reused"] for m in repeated["models"])
    assert [m["mlflow_run_id"] for m in repeated["models"]] == [
        m["mlflow_run_id"] for m in result["models"]
    ]
    assert publish(baseline, tmp_path, verify_only=True)["status"] == "success"
    assert len(client.search_runs([result["experiment_id"]])) == 3
    assert set(observed) == {"validation"}
    assert before == {str(p): p.read_bytes() for p in directory.rglob("*") if p.is_file()}
    client.log_metric(runs[0].info.run_id, "validation_average_precision", 0.99)
    with pytest.raises(tracking.TrackingError, match="metadata or metrics mismatch"):
        publish(baseline, tmp_path, verify_only=True)


def test_legacy_migration_requires_explicit_local_trust(baseline, tmp_path, monkeypatch):
    monkeypatch.setattr(tracking.joblib, "load", lambda *a, **k: pytest.fail("joblib loaded"))
    with pytest.raises(tracking.TrackingError, match="trust-local-models"):
        publish(baseline, tmp_path)
    assert not (tmp_path / "mlflow.db").exists()
    result = CliRunner().invoke(tracking.app, ["publish", str(baseline[1])])
    assert result.exit_code == 1 and "trust-local-models" in result.output


def test_environment_mismatch_prevents_loading_models(baseline, tmp_path, monkeypatch):
    original = tracking._context

    def context(*args):
        result, manifest, X, scores = original(*args)
        manifest["environment"]["sklearn"] = "0.0.0"
        return result, manifest, X, scores

    monkeypatch.setattr(tracking, "_context", context)
    monkeypatch.setattr(tracking.joblib, "load", lambda *a, **k: pytest.fail("joblib loaded"))
    with pytest.raises(tracking.TrackingError, match="exact Python"):
        publish(baseline, tmp_path, trust_local_models=True)
    assert not (tmp_path / "mlflow.db").exists()


def test_validation_integrity_failure_prevents_deserialization(baseline, tmp_path, monkeypatch):
    original = train._sha256

    def digest(path):
        return (
            "0" * 64
            if path.suffix == ".parquet" and "fraud-validation-" in str(path)
            else original(path)
        )

    monkeypatch.setattr(train, "_sha256", digest)
    monkeypatch.setattr(tracking.joblib, "load", lambda *a, **k: pytest.fail("joblib loaded"))
    with pytest.raises(tracking.TrackingError, match="partition integrity"):
        publish(baseline, tmp_path, trust_local_models=True)


def test_score_change_prevents_a_completed_run(baseline, tmp_path, monkeypatch):
    original = tracking.joblib.load

    def model(path):
        value = original(path)
        value.predict_proba = lambda X: np.tile([0.1, 0.9], (len(X), 1))
        return value

    monkeypatch.setattr(tracking.joblib, "load", model)
    with pytest.raises(tracking.TrackingError, match="changed validation scores"):
        publish(baseline, tmp_path, trust_local_models=True)
    client = MlflowClient(tracking_uri=tracking.tracking_uri(tmp_path))
    experiment = client.get_experiment_by_name(tracking.EXPERIMENT)
    assert not client.search_runs([experiment.experiment_id])


def test_unknown_skops_type_is_rejected_before_loading(baseline, tmp_path, monkeypatch):
    source, directory = baseline
    _, manifest, X, scores = tracking._context(
        directory,
        source / "gold",
        source / "inventory.json",
        train.DEFAULT_CONTRACT,
        source / "protocol.json",
        train.DEFAULT_GOLD_CONTRACT,
        train.DEFAULT_CONFIG,
    )
    model = tracking.joblib.load(directory / "models/dummy_prior/model.joblib")
    path = tmp_path / "model"
    mlflow.sklearn.save_model(
        model,
        str(path),
        serialization_format="skops",
        skops_trusted_types=sorted(tracking.TRUSTED_TYPES),
        pyfunc_predict_fn="predict_proba",
        pip_requirements=[],
    )
    monkeypatch.setattr(tracking.sio, "get_untrusted_types", lambda **k: ["unreviewed.CustomCode"])
    monkeypatch.setattr(
        tracking.sio, "load", lambda *a, **k: pytest.fail("unreviewed model loaded")
    )
    with pytest.raises(tracking.TrackingError, match="Unreviewed skops types"):
        tracking._check_export(
            path,
            "dummy_prior",
            manifest["config"]["models"]["dummy_prior"],
            X,
            scores["dummy_prior"],
        )


def test_partial_failure_retry_reuses_finished_models(baseline, tmp_path, monkeypatch):
    original = MlflowClient.log_artifacts
    calls = 0

    def fail_second(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated artifact failure")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(MlflowClient, "log_artifacts", fail_second)
    with pytest.raises(OSError, match="artifact failure"):
        publish(baseline, tmp_path, trust_local_models=True)
    assert not (tmp_path / "exports").exists()
    client = MlflowClient(tracking_uri=tracking.tracking_uri(tmp_path))
    experiment = client.get_experiment_by_name(tracking.EXPERIMENT)
    assert sorted(r.info.status for r in client.search_runs([experiment.experiment_id])) == [
        "FAILED",
        "FINISHED",
    ]
    monkeypatch.setattr(MlflowClient, "log_artifacts", original)
    result = publish(baseline, tmp_path, trust_local_models=True)
    assert [m["reused"] for m in result["models"]] == [True, False, False]
    runs = client.search_runs([experiment.experiment_id])
    assert sum(r.info.status == "FINISHED" for r in runs) == 3
    assert sum(r.info.status == "FAILED" for r in runs) == 1
    assert publish(baseline, tmp_path, verify_only=True)["status"] == "success"


def test_concurrent_publication_is_rejected_before_loading(baseline, tmp_path, monkeypatch):
    from filelock import FileLock, Timeout

    monkeypatch.setattr(tracking.joblib, "load", lambda *a, **k: pytest.fail("joblib loaded"))
    with FileLock(str(tmp_path / "publish.lock")), pytest.raises(Timeout):
        publish(baseline, tmp_path, trust_local_models=True)
    assert not (tmp_path / "mlflow.db").exists()
