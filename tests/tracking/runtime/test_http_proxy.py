"""Pinned operational dependencies and real HTTP proxy; SQLite/file fixtures only."""

from importlib.metadata import version
import json
import os
import signal
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("FRAUD_TRACKING_RUNTIME_TEST") != "1",
    reason="Run with the isolated tracking-runtime tox environment",
)


def test_real_http_probe_survives_server_restart(tmp_path):
    assert version("mlflow") == "3.17.0"
    assert version("psycopg") == "3.3.6"
    assert version("boto3") == "1.43.114"
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    uri = f"http://127.0.0.1:{port}"
    home = tmp_path / "home"
    home.mkdir()
    temporary = tmp_path / "tmp"
    temporary.mkdir()
    environment = {k: v for k, v in os.environ.items() if not k.startswith(("MLFLOW_", "AWS_"))}
    environment.update(
        HOME=str(home),
        TMPDIR=str(temporary),
        MLFLOW_DISABLE_TELEMETRY="true",
        MLFLOW_DISABLE_AGENT_HINT="1",
        MLFLOW_SERVER_ENABLE_JOB_EXECUTION="false",
        MLFLOW_SQL_TRACE_ROLLUPS_ENABLED="false",
        PYTHONDONTWRITEBYTECODE="1",
    )
    args = [
        sys.executable,
        "-m",
        "mlflow",
        "server",
        "--backend-store-uri",
        "sqlite:///" + str(tmp_path / "mlflow.db"),
        "--artifacts-destination",
        str(tmp_path / "artifacts"),
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--workers",
        "1",
        "--allowed-hosts",
        f"127.0.0.1:{port}",
    ]
    worker_code = (
        "import sys; from scripts.tracking import probe; "
        "probe.TRACKING_URI=sys.argv.pop(1); probe.main()"
    )
    record_path = tmp_path / "probe-record.json"
    environment["MLFLOW_SUPPRESS_PRINTING_URL_TO_STDOUT"] = "false"
    record = None
    pids = []
    for iteration in range(2):
        with (tmp_path / f"server-{iteration}.log").open("w+") as log:
            process = subprocess.Popen(
                args,
                cwd=tmp_path,
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            pids.append(process.pid)
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
                        pytest.fail(
                            "Local fixture server did not become ready: " + log.read()[-2500:]
                        )
                    time.sleep(0.2)
                action = ["write"] if iteration == 0 else ["read", "--record", str(record_path)]
                completed = subprocess.run(
                    [sys.executable, "-c", worker_code, uri, *action],
                    cwd=tmp_path,
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=60,
                    check=True,
                )
                result = json.loads(completed.stdout)
                if iteration == 0:
                    record = result
                    record_path.write_text(json.dumps(record))
                    assert "View run" in completed.stderr
                    assert "View experiment" in completed.stderr
                else:
                    assert result == record
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=5)
    assert pids[0] != pids[1] and record["artifact_verified"]
    assert (tmp_path / "mlflow.db").is_file()
    assert not (tmp_path / "mlruns").exists()
