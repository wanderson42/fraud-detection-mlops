"""Real HTTP proxy regression using disposable SQLite/files, not PostgreSQL/S3 proof."""

from copy import deepcopy
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen

import pytest
from scripts.platform import kind_tracking as k

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def http_server(tmp_path_factory):
    directory = tmp_path_factory.mktemp("kind-tracking-http")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    uri = "http://127.0.0.1:" + str(port)
    env = {
        key: value for key, value in os.environ.items() if not key.startswith(("MLFLOW_", "AWS_"))
    }
    env.update(HOME=str(directory), MLFLOW_DISABLE_TELEMETRY="true", MLFLOW_DISABLE_AGENT_HINT="1")
    with (directory / "server.log").open("w") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "mlflow",
                "server",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--workers",
                "1",
                "--backend-store-uri",
                "sqlite:///" + str(directory / "test.db"),
                "--serve-artifacts",
                "--artifacts-destination",
                str(directory / "artifacts"),
                "--allowed-hosts",
                "127.0.0.1:*",
            ],
            cwd=directory,
            env=env,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
        try:
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                assert process.poll() is None, (directory / "server.log").read_text()[-2000:]
                try:
                    with urlopen(uri + "/health", timeout=2) as response:
                        if response.status == 200:
                            break
                except URLError, TimeoutError, ConnectionError:
                    pass
                time.sleep(0.2)
            else:
                pytest.fail("Disposable HTTP server did not become ready")
            yield uri
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)


def test_real_proxy_model_reload_independent_process_and_hash_rejection(
    http_server, tmp_path, monkeypatch
):
    monkeypatch.setattr(k, "work", lambda: tmp_path)
    monkeypatch.setattr(k.f, "ROOT", ROOT)
    catalogue = ROOT / "references/persistence_catalogue_v1.json"
    original_catalogue = catalogue.read_bytes()
    record = k.client("write", http_server)
    assert catalogue.read_bytes() == original_catalogue
    assert record["fit_executed"]
    assert record["max_absolute_score_error"] <= 1e-12
    read = k.client("read", http_server, record)
    assert not read["fit_executed"]
    assert read["metric_history_rows"] == 2
    assert read["model_sha256"] == record["model_sha256"]
    wrong = deepcopy(record)
    wrong["model_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="Downloaded model hash differs"):
        k.client("read", http_server, wrong)
    wrong = deepcopy(record)
    wrong["expected_scores"][0] += 0.1
    with pytest.raises(ValueError, match="Prediction parity differs"):
        k.client("read", http_server, wrong)
    assert not list(tmp_path.iterdir())
