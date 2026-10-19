"""Explicit HTTP routing, proxy-only artifacts and restoration of local behavior."""

import json
import os
import sys
from types import SimpleNamespace

from examples import model_contribution as example
import mlflow
import pytest

from fraud_detection_mlops.integrations import mlflow_tracking as tracking
from fraud_detection_mlops.storage import managed_paths


@pytest.mark.parametrize(
    "uri",
    [
        "",
        "sqlite:///db",
        "file:///tmp/models",
        "s3://bucket",
        "http://user:secret@localhost:15001",
        "http://localhost/path",
        "http://localhost/?x=1",
        "http://localhost/#tag",
        "http://localhost:bad",
        "http://localhost:0",
        "http://[bad",
        " http://localhost",
    ],
)
def test_invalid_http_destination_rejected_before_training(tmp_path, monkeypatch, uri):
    monkeypatch.setattr(
        example.candidate_training, "fit_candidate", lambda *a, **k: pytest.fail("fit")
    )
    with pytest.raises(tracking.TrackingError):
        example.run(tmp_path / "output", http_uri=uri)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("uri", ["http://127.0.0.1:15001/", "https://localhost:15001"])
def test_server_root_normalized(uri):
    assert tracking.validate_http_uri(uri) == uri.rstrip("/")


@pytest.mark.parametrize("raises", [False, True])
@pytest.mark.parametrize("had_limits", [False, True])
def test_http_context_restores_uri_request_limits_and_proxy_policy(
    monkeypatch, raises, had_limits
):
    settings = {
        "MLFLOW_HTTP_REQUEST_TIMEOUT": "10",
        "MLFLOW_HTTP_REQUEST_MAX_RETRIES": "0",
        "MLFLOW_ENABLE_PROXY_MULTIPART_DOWNLOAD": "false",
        "MLFLOW_ENABLE_PROXY_MULTIPART_UPLOAD": "false",
    }
    limits = list(settings)
    for name in limits:
        if had_limits:
            monkeypatch.setenv(name, "true" if "MULTIPART" in name else "21")
        else:
            monkeypatch.delenv(name, raising=False)
    before = {name: os.environ.get(name) for name in limits}
    previous = mlflow.get_tracking_uri()
    try:
        with tracking.http_tracking("http://127.0.0.1:15001"):
            assert mlflow.get_tracking_uri() == "http://127.0.0.1:15001"
            assert {name: os.environ[name] for name in limits} == settings
            if raises:
                raise RuntimeError("injected failure")
    except RuntimeError:
        assert raises
    assert mlflow.get_tracking_uri() == previous
    assert {name: os.environ.get(name) for name in limits} == before


@pytest.mark.parametrize(
    "uri",
    [
        "file:///tmp/a",
        "s3://bucket/model",
        "mlflow-artifacts://host/a",
        "mlflow-artifacts:/../a",
        "mlflow-artifacts:/a?x=1",
    ],
)
def test_nonproxy_or_ambiguous_artifact_location_rejected(uri):
    with pytest.raises(tracking.TrackingError, match="server-proxied"):
        tracking.require_proxy_artifacts(uri)


def test_wrong_experiment_artifacts_rejected_before_creating_run(toy_pipeline, monkeypatch):
    model, features = toy_pipeline
    client = SimpleNamespace(
        get_experiment_by_name=lambda _: SimpleNamespace(
            experiment_id="1", artifact_location="file:///wrong"
        )
    )
    monkeypatch.setattr(tracking, "MlflowClient", lambda: client)
    monkeypatch.setattr(mlflow, "start_run", lambda **k: pytest.fail("Run created"))
    previous = mlflow.get_tracking_uri()
    with pytest.raises(tracking.TrackingError, match="server-proxied"):
        tracking.log_candidate(
            model,
            features,
            model.predict_proba(features)[:, 1],
            name="test",
            parameters={},
            metrics={},
            tags={"experiment_run_id": "test"},
            http_uri="http://127.0.0.1:15001",
        )
    assert mlflow.get_tracking_uri() == previous


def test_dual_destination_rejected_without_output_or_fit(tmp_path, monkeypatch):
    monkeypatch.setattr(
        example.candidate_training, "fit_candidate", lambda *a, **k: pytest.fail("fit")
    )
    with pytest.raises(tracking.TrackingError, match="not both"):
        example.run(tmp_path / "output", tmp_path / "store", http_uri="http://127.0.0.1:15001")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "arguments",
    [
        ["--tracking-root", "unused", "--tracking-uri", "http://localhost"],
        ["--tracking-uri", "file:///unmanaged"],
    ],
)
def test_cli_invalid_route_rejected_before_run(tmp_path, monkeypatch, arguments):
    monkeypatch.setattr(managed_paths, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(
        sys, "argv", ["example", "--output", str(tmp_path / "data/examples/new"), *arguments]
    )
    monkeypatch.setattr(example, "run", lambda *a, **k: pytest.fail("run"))
    with pytest.raises(SystemExit) as error:
        example.main()
    assert error.value.code == 2
    assert list(tmp_path.iterdir()) == []


def test_cli_has_json_only_stdout_and_explicit_http_argument(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(managed_paths, "DATA_DIR", tmp_path / "data")
    output = tmp_path / "data/examples/new"
    monkeypatch.setattr(
        sys,
        "argv",
        ["example", "--output", str(output), "--tracking-uri", "http://localhost:15001/"],
    )
    calls = []

    def run(*args, **kwargs):
        calls.append((args, kwargs))
        print("SDK notice")
        return {"status": "success"}

    monkeypatch.setattr(example, "run", run)
    example.main()
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"status": "success"}
    assert "SDK notice" in captured.err
    assert calls == [((output, None), {"http_uri": "http://localhost:15001"})]
    assert not output.exists()


def test_remote_identifiers_do_not_break_historical_local_reference_inventory(
    tmp_path, monkeypatch
):
    from scripts.storage import mlflow_inventory as inventory

    recorded = {
        "run_id": "a" * 32,
        "model_uri": "models:/m-" + "b" * 32,
        "tracking_uri": "http://localhost:15001",
    }
    monkeypatch.setattr(tracking, "log_candidate", lambda *a, **k: recorded)
    root = tmp_path / "checkout"
    output = root / "data/examples/http-model"
    result = example.run(output, http_uri="http://localhost:15001")
    assert result["tracking"] == recorded
    receipt = root / "data/storage/tracking-lab/receipts/model.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text(json.dumps({"tracking": recorded}))
    references, sources = inventory.project_references(root, root / "data/tracking", [], [])
    assert any(item["kind"] == "unclassified_run_id" for item in references)
    assert not any(item.get("resolved") is False for item in references)
    assert set(sources) == {"data/examples/http-model/metrics.json"}
