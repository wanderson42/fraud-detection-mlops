"""Disposable real MLflow HTTP server shared by opt-in transport tests."""

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

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def model_server(tmp_path_factory):
    directory = tmp_path_factory.mktemp("contribution-http-server")
    for name in ["home", "tmp"]:
        (directory / name).mkdir()
    environment = {k: v for k, v in os.environ.items() if not k.startswith(("MLFLOW_", "AWS_"))}
    environment.update(
        HOME=str(directory / "home"),
        TMPDIR=str(directory / "tmp"),
        PYTHONPATH=str(ROOT),
        MLFLOW_DISABLE_TELEMETRY="true",
        MLFLOW_DISABLE_AGENT_HINT="1",
        MLFLOW_SERVER_ENABLE_JOB_EXECUTION="false",
        MLFLOW_SQL_TRACE_ROLLUPS_ENABLED="false",
        MLFLOW_SUPPRESS_PRINTING_URL_TO_STDOUT="false",
        PYTHONDONTWRITEBYTECODE="1",
    )
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    uri = f"http://127.0.0.1:{port}"
    with (directory / "server.log").open("w+") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "mlflow",
                "server",
                "--backend-store-uri",
                "sqlite:///" + str(directory / "server.db"),
                "--artifacts-destination",
                str(directory / "artifacts"),
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--workers",
                "1",
                "--allowed-hosts",
                f"127.0.0.1:{port}",
            ],
            cwd=directory,
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            deadline = time.monotonic() + 45
            while True:
                try:
                    with urlopen(uri + "/health", timeout=1) as response:
                        if response.status == 200:
                            break
                except URLError, TimeoutError, ConnectionError:
                    pass
                if process.poll() is not None or time.monotonic() >= deadline:
                    log.seek(0)
                    pytest.fail("Fixture server not ready: " + log.read()[-2500:])
                time.sleep(0.2)
            yield uri, directory, environment
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
