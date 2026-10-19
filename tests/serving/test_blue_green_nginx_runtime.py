"""Optional real NGINX HTTP reload/outage test, without Docker or native data.

FRAUD_NGINX_BIN points to a local nginx binary. Only generated releases and
loopback ports are used. The Docker gate separately proves image/network/mounts.
"""

import os
import socket
import subprocess
import time

import pytest
from scripts.serving import blue_green_rehearsal as rollout
from scripts.serving import check_docker_runtime as checks
from tests.serving.test_docker_runtime_checks import http_runtime


def test_real_nginx_switch_corrupt_gate_and_failure_rollback(serving_release, tmp_path):
    binary = os.environ.get("FRAUD_NGINX_BIN")
    if not binary:
        pytest.skip("Optional: set FRAUD_NGINX_BIN for real local proxy reload test")
    source, pin = serving_release
    before = checks.hashes(source)
    blue, green, invalid = rollout.stage_releases(source, tmp_path)
    manifest, smoke, _ = checks.read_release(blue, pin)
    green_manifest, green_smoke, _ = checks.read_release(
        green, checks.hashes(green)["manifest.json"]
    )
    with socket.socket() as port_socket:
        port_socket.bind(("127.0.0.1", 0))
        port = port_socket.getsockname()[1]
    gateway_url = f"http://127.0.0.1:{port}"
    name = "fraud-serving-check-" + "a" * 32 + "-blue"
    config = tmp_path / "nginx.conf"
    process = None
    try:
        with (
            http_runtime(blue, pin) as blue_url,
            http_runtime(green, checks.hashes(green)["manifest.json"]) as green_url,
        ):

            def select(url):
                body = rollout.configuration(name).replace(
                    "listen 8080;", f"listen 127.0.0.1:{port};"
                )
                body = body.replace(f"http://{name}:8000", url).replace(
                    "/tmp/", str(tmp_path) + "/"
                )
                # Isolated assistant namespace maps only UID 0. This fixture-only
                # directive is never present in the Docker runtime configuration.
                if os.geteuid() == 0:
                    body = "user root;\n" + body
                config.write_text(body)
                subprocess.run(
                    [binary, "-t", "-c", str(config), "-e", "stderr"],
                    check=True,
                    capture_output=True,
                    timeout=10,
                )
                if process is not None:
                    subprocess.run(
                        [binary, "-s", "reload", "-c", str(config)],
                        check=True,
                        capture_output=True,
                        timeout=10,
                    )

            select(blue_url)
            process = subprocess.Popen(
                [binary, "-c", str(config), "-g", "daemon off;", "-e", "stderr"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            rollout.await_gate(gateway_url, manifest, smoke)
            with pytest.raises(checks.CheckFailed, match="Hash do modelo diverge"):
                checks.read_release(invalid, pin)
            rollout.gate(gateway_url, manifest, smoke)
            select(green_url)
            rollout.await_gate(gateway_url, green_manifest, green_smoke)
            assert (
                checks.request_json(
                    gateway_url, "/score", {**smoke["request"], "transaction_id": True}
                )[0]
                == 422
            )
            # A closed loopback port is an explicit unavailable upstream; no
            # implicit alternate backend is configured in the proxy.
            with socket.socket() as dead:
                dead.bind(("127.0.0.1", 0))
                dead_port = dead.getsockname()[1]
            select(f"http://127.0.0.1:{dead_port}")
            for _ in range(50):
                if checks.request_json(gateway_url, "/score", smoke["request"])[0] == 502:
                    break
                time.sleep(0.05)
            else:
                pytest.fail("NGINX did not expose the injected outage")
            gateway = type(
                "Gateway",
                (),
                {"url": gateway_url, "select": lambda self, backend: select(blue_url)},
            )()
            baseline = ("blue", blue_url, manifest, smoke)
            candidate = ("green", green_url, green_manifest, green_smoke)
            assert rollout.recover_if_unhealthy(gateway, candidate, baseline)
            assert (
                rollout.gate(gateway_url, manifest, smoke)["serving_release_id"]
                == manifest["serving_release_id"]
            )
    finally:
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    assert checks.hashes(source) == before
