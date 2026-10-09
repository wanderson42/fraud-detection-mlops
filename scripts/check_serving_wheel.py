"""Install locked inference dependencies in a disposable venv and score outside Git."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory

# Executed with -I by the wheel's Python, in an empty directory outside the checkout.
ISOLATED_CHECK = r"""
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import Request, urlopen

import fraud_detection_mlops.serving.http_service as service

assert Path(service.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
excluded = ["mlflow", "shap", "duckdb", "matplotlib", "optuna", "typer"]
assert all(importlib.util.find_spec(name) is None for name in excluded)
release = Path(os.environ["FRAUD_SERVING_RELEASE"])
smoke = json.loads((release / "smoke.json").read_text())
with socket.socket() as reservation:
    reservation.bind(("127.0.0.1", 0))
    port = reservation.getsockname()[1]
base = f"http://127.0.0.1:{port}"
with open("uvicorn.log", "w+") as log:
    process = subprocess.Popen([
        sys.executable, "-I", "-m", "uvicorn",
        "fraud_detection_mlops.serving.http_service:create_app", "--factory",
        "--host", "127.0.0.1", "--port", str(port), "--workers", "1",
    ], stdout=log, stderr=subprocess.STDOUT)
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                with urlopen(base + "/ready", timeout=1) as response:
                    ready = json.load(response)
                break
            except URLError:
                if process.poll() is not None or time.monotonic() >= deadline:
                    log.seek(0)
                    raise RuntimeError("Wheel service did not become ready: " + log.read())
                time.sleep(0.1)
        request = Request(base + "/score", data=json.dumps(smoke["request"]).encode(), headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(request, timeout=5) as response:
            score = json.load(response)
        assert abs(score["score"] - smoke["expected_score"]) <= 1e-12
        assert score["serving_release_id"] == ready["serving_release_id"]
        invalid = smoke["request"] | {"transaction_id": True}
        try:
            with urlopen(Request(base + "/score", data=json.dumps(invalid).encode(), headers={"Content-Type": "application/json"}), timeout=5):
                raise AssertionError("Invalid identifier was accepted")
        except URLError as exc:
            assert getattr(exc, "code", None) == 422
        print(json.dumps({"status": "success", "installed_module": service.__file__, "outside_checkout": True, "excluded_packages": excluded, "http_score": score, "parity_tolerance": 1e-12, "evidence_kind": "synthetic_smoke" if json.loads((release / "manifest.json").read_text())["evidence_kind"] == "synthetic_smoke" else "verified_reference"}))
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
"""


def run(command, *, cwd, env):
    return subprocess.run(
        command, cwd=cwd, env=env, check=True, text=True, capture_output=True, timeout=240
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    parser.add_argument("--release", required=True, type=Path)
    parser.add_argument("--manifest-sha256", required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    wheel, release = args.wheel.resolve(strict=True), args.release.resolve(strict=True)
    poetry = shutil.which("poetry")
    if not poetry:
        parser.error("Poetry must be on PATH")
    with TemporaryDirectory(prefix="fraud-wheel-check-") as temporary:
        base = Path(temporary)
        if base.is_relative_to(root):
            raise RuntimeError("Choose TMPDIR outside the checkout")
        venv = base / "runtime"
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
        python = venv / "bin/python"
        env = os.environ | {
            "VIRTUAL_ENV": str(venv),
            "POETRY_VIRTUALENVS_CREATE": "false",
            "POETRY_NO_INTERACTION": "1",
        }
        env.pop("PYTHONPATH", None)
        target = run([poetry, "env", "info", "--executable"], cwd=root, env=env).stdout.strip()
        if Path(target).absolute() != python.absolute():
            raise RuntimeError("Poetry did not select the disposable environment")
        run([poetry, "check", "--lock"], cwd=root, env=env)
        run([poetry, "sync", "--only", "main", "--no-root"], cwd=root, env=env)
        run([str(python), "-m", "pip", "install", "--no-deps", str(wheel)], cwd=base, env=env)
        outside = base / "outside"
        outside.mkdir()
        env.update(
            FRAUD_SERVING_RELEASE=str(release), FRAUD_SERVING_MANIFEST_SHA256=args.manifest_sha256
        )
        result = run([str(python), "-I", "-c", ISOLATED_CHECK], cwd=outside, env=env)
        print(json.dumps(json.loads(result.stdout), indent=2))


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        print(exc.stdout or "", file=sys.stderr)
        print(exc.stderr or "", file=sys.stderr)
        raise SystemExit(exc.returncode) from exc
