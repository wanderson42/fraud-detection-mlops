"""Opt-in real HTTP model transport with disposable SQLite/file server fixtures."""

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
from urllib.request import urlopen

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("FRAUD_CONTRIBUTION_HTTP_TEST") != "1",
    reason="Run the contribution-http tox environment",
)
ROOT = Path(__file__).resolve().parents[3]


def test_real_cli_logs_model_through_proxy_and_reloads_without_saved_local_model(model_server):
    from examples import model_contribution as example
    import mlflow
    from mlflow import MlflowClient
    import numpy as np

    from fraud_detection_mlops.integrations import mlflow_tracking as tracking

    uri, directory, environment = model_server
    base = ROOT / "data/examples"
    base.mkdir(parents=True, exist_ok=True)
    previous = mlflow.get_tracking_uri()
    with TemporaryDirectory(prefix=".http-contract-", dir=base) as temporary:
        output = Path(temporary) / "candidate"
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "examples.model_contribution",
                "--output",
                str(output),
                "--tracking-uri",
                uri,
            ],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=90,
            check=True,
        )
        result = json.loads(completed.stdout)
        recorded = result["tracking"]
        assert "View run" in completed.stderr
        assert result["status"] == "success" and result["promotion_status"] == "not_promoted"
        assert recorded["model_reload_validated"] and recorded["max_absolute_score_error"] <= 1e-12
        assert {p.name for p in output.iterdir()} == {
            "model.skops",
            "metrics.json",
            "validation_predictions.parquet",
        }
        assert recorded["tracking_uri"] == uri
        assert recorded["model_artifact_uri"].startswith("mlflow-artifacts:/")
        client = MlflowClient(tracking_uri=uri)
        run = client.get_run(recorded["run_id"])
        assert (
            run.info.status == "FINISHED"
            and run.data.tags["scope"] == "synthetic_integration_smoke"
        )
        assert client.get_experiment(run.info.experiment_id).name == example.HTTP_EXPERIMENT
        assert len(client.search_runs([run.info.experiment_id])) == 1
        output.rename(Path(temporary) / "source-unavailable")
        features = example.synthetic_batches()[1][0]
        expected = (
            example.gaussian_nb()
            .set_params(classifier__var_smoothing=1e-8)
            .fit(*example.synthetic_batches()[0][:2])
            .predict_proba(features)
        )
        with tracking.http_tracking(uri), TemporaryDirectory(dir=temporary) as download:
            restored = tracking.load_tracked_model(recorded["model_uri"], Path(download))
            np.testing.assert_allclose(
                restored.predict(features), expected, rtol=1e-12, atol=1e-12
            )
        assert mlflow.get_tracking_uri() == previous
        assert not list(Path(temporary).rglob("*.db"))
    assert not (directory / "mlruns").exists()
    assert not any(k.startswith("AWS_") for k in environment)


def test_unavailable_http_fails_without_fallback_then_explicit_local_route_works(tmp_path):
    from examples import model_contribution as example
    import mlflow
    from mlflow.exceptions import MlflowException

    from fraud_detection_mlops.integrations import mlflow_tracking as tracking

    previous = mlflow.get_tracking_uri()
    with socket.socket() as unavailable:
        unavailable.bind(("127.0.0.1", 0))
        uri = f"http://127.0.0.1:{unavailable.getsockname()[1]}"
        with pytest.raises(MlflowException):
            example.run(tmp_path / "failed", http_uri=uri)
    assert mlflow.get_tracking_uri() == previous
    assert not list(tmp_path.rglob("*.db"))
    result = example.run(tmp_path / "local", tmp_path / "explicit-local-store")
    assert result["status"] == "success" and result["tracking"]
    assert (tmp_path / "explicit-local-store/mlflow.db").is_file()
    assert mlflow.get_tracking_uri() == previous
    assert not (tmp_path / "mlruns").exists()
    assert tracking.tracking_uri(tmp_path / "explicit-local-store").startswith("sqlite:///")


def test_reload_failure_marks_remote_run_failed_and_restores_context(
    model_server, tmp_path, monkeypatch
):
    from examples import model_contribution as example
    import mlflow
    from mlflow import MlflowClient

    from fraud_detection_mlops.integrations import mlflow_tracking as tracking
    from fraud_detection_mlops.integrations.skops_persistence import PersistenceError

    uri, _, _ = model_server
    previous = mlflow.get_tracking_uri()

    def mismatch(*args):
        raise PersistenceError("Injected score mismatch")

    monkeypatch.setattr(tracking, "check_scores", mismatch)
    with pytest.raises(PersistenceError, match="Injected"):
        example.run(tmp_path / "failed", http_uri=uri)
    assert mlflow.get_tracking_uri() == previous and mlflow.active_run() is None
    client = MlflowClient(tracking_uri=uri)
    experiment = client.get_experiment_by_name(example.HTTP_EXPERIMENT)
    runs = client.search_runs([experiment.experiment_id], order_by=["start_time DESC"])
    assert runs[0].info.status == "FAILED"


@pytest.fixture
def advertised_object_store(model_server):
    """Real SDK/server, with a gateway advertising unreachable direct object downloads."""
    from http.client import HTTPConnection
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    from urllib.parse import urlsplit

    backend, directory, environment = model_server
    target = urlsplit(backend)
    calls = []

    class Gateway(BaseHTTPRequestHandler):
        def handle_request(self):
            calls.append((self.command, self.path.split("?")[0]))
            if self.path.startswith("/api/2.0/mlflow-artifacts/presigned/"):
                data = json.dumps(
                    {
                        "url": f"http://127.0.0.1:{self.server.server_port}/unreachable-object",
                        "headers": {},
                        "file_size": 1,
                    }
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
            elif self.path.startswith("/unreachable-object"):
                data = b"Object endpoint is inaccessible to this client"
                self.send_response(403)
                self.send_header("Content-Type", "text/plain")
            else:
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                headers = {
                    k: v
                    for k, v in self.headers.items()
                    if k.lower() not in {"host", "connection", "transfer-encoding"}
                }
                headers.update(Host=target.netloc, **{"Accept-Encoding": "identity"})
                connection = HTTPConnection(target.hostname, target.port, timeout=15)
                try:
                    connection.request(self.command, self.path, body=body, headers=headers)
                    response = connection.getresponse()
                    data = response.read()
                    if self.path.split("?")[0] == "/api/3.0/mlflow/server-info":
                        info = json.loads(data)
                        info.update(
                            multipart_downloads_enabled=True, multipart_uploads_enabled=True
                        )
                        data = json.dumps(info).encode()
                    self.send_response(response.status)
                    for name, value in response.getheaders():
                        if name.lower() not in {
                            "connection",
                            "transfer-encoding",
                            "content-length",
                        }:
                            self.send_header(name, value)
                finally:
                    connection.close()
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = handle_request

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Gateway)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", directory, environment, calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_real_cli_keeps_model_download_on_proxy_when_object_store_advertises_multipart(
    advertised_object_store,
):
    gateway, _, environment, calls = advertised_object_store
    with urlopen(gateway + "/api/3.0/mlflow/server-info", timeout=5) as response:
        assert json.load(response)["multipart_downloads_enabled"] is True
    base = ROOT / "data/examples"
    base.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".proxy-routing-", dir=base) as temporary:
        output = Path(temporary) / "candidate"
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "examples.model_contribution",
                "--output",
                str(output),
                "--tracking-uri",
                gateway,
            ],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr[-3000:]
        result = json.loads(completed.stdout)
        assert result["tracking"]["model_reload_validated"]
        assert result["tracking"]["max_absolute_score_error"] <= 1e-12
        assert any(
            method == "GET"
            and "/mlflow-artifacts/artifacts/" in path
            and path.endswith("/model.skops")
            for method, path in calls
        )
        assert not any("/presigned/" in path or path == "/unreachable-object" for _, path in calls)
