"""Bounded laboratory checks using a local Docker engine and an existing release.

No fitting, export, access to Handbook partitions, or changes to release files.
Only containers created by this invocation are removed. Requires no Docker SDK.
"""

import argparse
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import uuid

RELEASE_FILES = ("manifest.json", "pipeline.skops", "smoke.json")
PARITY_TOLERANCE = 1e-12
MEMORY_BYTES = 2 * 1024**3


class CheckFailed(RuntimeError):
    """An operational check failed; do not publish a successful receipt."""


def require(condition, message):
    if not condition:
        raise CheckFailed(message)


def hashes(directory):
    return {
        filename: hashlib.sha256((directory / filename).read_bytes()).hexdigest()
        for filename in RELEASE_FILES
    }


def read_release(directory, manifest_sha256):
    require(directory.is_dir(), f"Release não encontrada: {directory}")
    require(
        len(manifest_sha256) == 64 and all(c in "0123456789abcdef" for c in manifest_sha256),
        "MANIFEST_SHA256 deve conter 64 caracteres hexadecimais minúsculos.",
    )
    require(
        all(
            (directory / name).is_file() and not (directory / name).is_symlink()
            for name in RELEASE_FILES
        ),
        "A release deve conter três arquivos regulares, sem links simbólicos.",
    )
    original = hashes(directory)
    require(original["manifest.json"] == manifest_sha256, "SHA-256 do manifesto diverge.")
    manifest = json.loads((directory / "manifest.json").read_text())
    require(manifest["model_file"] == "pipeline.skops", "Nome de modelo incompatível.")
    require(manifest["smoke_file"] == "smoke.json", "Nome de controle incompatível.")
    require(original["pipeline.skops"] == manifest["model_sha256"], "Hash do modelo diverge.")
    require(original["smoke.json"] == manifest["smoke_sha256"], "Hash do controle diverge.")
    smoke = json.loads((directory / "smoke.json").read_text())
    require(math.isfinite(smoke["expected_score"]), "Score de controle não finito.")
    return manifest, smoke, original


def request_json(base_url, route, payload=None):
    data = None if payload is None else json.dumps(payload, allow_nan=False).encode()
    request = Request(base_url + route, data=data, headers={"Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=5) as response:
            return response.status, json.load(response)
    except HTTPError as error:
        with error:
            return error.code, json.load(error)


def check_score(base_url, smoke, manifest):
    status, score = request_json(base_url, "/score", smoke["request"])
    require(status == 200, f"/score retornou HTTP {status}.")
    expected = {
        "schema_version": 1,
        "transaction_id": smoke["request"]["transaction_id"],
        "model_id": manifest["model_id"],
        "model_sha256": manifest["model_sha256"],
        "feature_contract_version": manifest["feature_contract_version"],
        "serving_release_id": manifest["serving_release_id"],
        "score_semantics": "uncalibrated_ranking",
    }
    require(
        all(score.get(key) == value for key, value in expected.items()),
        "Identidade ou contrato da resposta diverge da release.",
    )
    value = score.get("score")
    require(type(value) in (int, float) and math.isfinite(value), "Score inválido.")
    require(
        abs(value - smoke["expected_score"]) <= PARITY_TOLERANCE,
        "Paridade HTTP diverge do score de controle.",
    )
    return score


def latency_summary(samples):
    ordered = sorted(samples)
    require(bool(ordered), "Não há medições de latência.")
    return {
        "unit": "milliseconds",
        "requests": len(ordered),
        "mean": statistics.fmean(ordered),
        **{
            name: ordered[math.ceil(fraction * len(ordered)) - 1]
            for name, fraction in (("p50", 0.50), ("p95", 0.95), ("p99", 0.99))
        },
        "scope": "sequential_single_client_repeated_control_request_including_http",
        "sla_validated": False,
    }


class Docker:
    def __init__(self):
        self.containers = []

    def run(self, *args, check=True):
        try:
            result = subprocess.run(
                ["docker", *args], capture_output=True, text=True, timeout=30, check=False
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise CheckFailed(f"Docker indisponível ou comando excedeu 30s: {args[0]}") from error
        if check and result.returncode:
            raise CheckFailed(f"docker {args[0]} falhou: {result.stderr.strip()[-3000:]}")
        return result.stdout.strip()

    def inspect(self, container):
        return json.loads(self.run("inspect", container))[0]

    def start(self, image_id, release, manifest_sha256, scenario):
        name = f"fraud-serving-check-{uuid.uuid4().hex}-{scenario}"
        # Track the unique name before create so a client timeout still permits cleanup.
        self.containers.append(name)
        require("," not in str(release), "Docker --mount não aceita vírgulas neste path.")
        self.run(
            "create",
            "--name",
            name,
            "--pull",
            "never",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges=true",
            "--memory",
            str(MEMORY_BYTES),
            "--memory-swap",
            str(MEMORY_BYTES),
            "--cpus",
            "4",
            "--pids-limit",
            "128",
            "--restart",
            "no",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=67108864",
            "--publish",
            "127.0.0.1::8000",
            "--mount",
            f"type=bind,source={release},target=/model,readonly",
            "--env",
            f"FRAUD_SERVING_MANIFEST_SHA256={manifest_sha256}",
            image_id,
        )
        self.run("start", name)
        return name

    def remove(self, name):
        self.run("rm", "--force", name)
        self.containers.remove(name)

    def cleanup(self):
        failures = []
        for name in list(self.containers):
            try:
                # A failed create may never have produced a container.
                if self.run("ps", "--all", "--quiet", "--filter", f"name=^/{name}$"):
                    self.remove(name)
                else:
                    self.containers.remove(name)
            except CheckFailed as error:
                failures.append(str(error))
        require(not failures, "Cleanup incompleto: " + "; ".join(failures))


def wait_ready(docker, container, release_id, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        info = docker.inspect(container)
        state = info["State"]
        if not state["Running"]:
            raise CheckFailed(
                "Serviço encerrou antes de ficar pronto: "
                + docker.run("logs", container, check=False)[-2000:]
            )
        ports = info["NetworkSettings"]["Ports"].get("8000/tcp")
        if ports:
            require(ports[0]["HostIp"] == "127.0.0.1", "Porta exposta fora do loopback.")
            base_url = "http://127.0.0.1:" + ports[0]["HostPort"]
            try:
                status, ready = request_json(base_url, "/ready")
                if status == 200:
                    require(
                        ready.get("serving_release_id") == release_id,
                        "Readiness corresponde a outra release.",
                    )
                    if state.get("Health", {}).get("Status") == "healthy":
                        return base_url
            except URLError, TimeoutError, ConnectionError:
                pass
        time.sleep(0.2)
    raise CheckFailed("Readiness/HEALTHCHECK não aprovados em 60s.")


def check_failed_start(docker, container, expected_message, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = docker.inspect(container)["State"]
        if not state["Running"]:
            logs = docker.run("logs", container, check=False)
            require(
                state["ExitCode"] != 0 and not state.get("OOMKilled", False),
                "Falha esperada deve ser do contrato, sem OOM ou saída zero.",
            )
            require(expected_message in logs, "Inicialização falhou por motivo inesperado.")
            docker.remove(container)
            return {
                "startup_rejected": True,
                "exit_code": state["ExitCode"],
                "reason_matched": expected_message,
            }
        time.sleep(0.2)
    raise CheckFailed("Release inválida não abortou a inicialização em 60s.")


RUNTIME_PROBE = """
import importlib.util, json, os
from pathlib import Path
import fraud_detection_mlops.serving.http_service as service
excluded = ('mlflow', 'shap', 'duckdb', 'matplotlib', 'optuna', 'typer')
assert all(importlib.util.find_spec(name) is None for name in excluded)
assert os.getuid() == 10001 and os.getgid() == 10001
assert str(Path(service.__file__).resolve()).startswith('/opt/runtime/')
assert not any(Path('/app').iterdir())
for path in ('/app/.write-probe', '/model/.write-probe'):
    try:
        Path(path).write_text('probe')
    except OSError:
        pass
    else:
        Path(path).unlink()
        raise AssertionError('unexpected writable path: ' + path)
print(json.dumps({'uid': os.getuid(), 'gid': os.getgid(),
                  'installed_module': service.__file__,
                  'excluded_packages': excluded, 'readonly_write_probes': 'passed'}))
"""


def verify_container_settings(info, image_id):
    host = info["HostConfig"]
    model_mounts = [mount for mount in info["Mounts"] if mount["Destination"] == "/model"]
    require(info["Image"] == image_id, "Container não utiliza a imagem fixada.")
    require(info["Config"]["User"] == "10001:10001", "Usuário da imagem incompatível.")
    require(host["ReadonlyRootfs"] and "ALL" in host["CapDrop"], "Proteções ausentes.")
    require(
        any(value.startswith("no-new-privileges") for value in host["SecurityOpt"]),
        "no-new-privileges ausente.",
    )
    require(len(model_mounts) == 1 and not model_mounts[0]["RW"], "Modelo não está read-only.")
    require(
        host["Memory"] == MEMORY_BYTES
        and host["MemorySwap"] == MEMORY_BYTES
        and host["NanoCpus"] == 4_000_000_000
        and host["PidsLimit"] == 128,
        "Limites de recursos divergentes.",
    )


@contextmanager
def failure_copy(release):
    with tempfile.TemporaryDirectory(prefix="fraud-serving-failure-") as temporary:
        copy = Path(temporary) / "release"
        # Normalized permissions allow UID 10001 to read disposable fixtures.
        shutil.copytree(release, copy, copy_function=shutil.copyfile)
        copy.chmod(0o755)
        for name in RELEASE_FILES:
            (copy / name).chmod(0o644)
        yield copy


def assess(image, release, manifest_sha256, requests=100):
    manifest, smoke, original = read_release(release, manifest_sha256)
    docker = Docker()
    report = {}
    try:
        require(
            shutil.which("docker") is not None, "Instale/inicie Docker no host de laboratório."
        )
        context = json.loads(docker.run("context", "inspect"))[0]
        endpoint = os.environ.get("DOCKER_HOST") or context["Endpoints"]["docker"]["Host"]
        require(endpoint.startswith("unix://"), "Use um engine Docker Linux local (socket Unix).")
        engine = json.loads(docker.run("info", "--format", "{{json .}}"))
        require(engine["OSType"] == "linux", "Este check requer containers Linux.")
        require(
            engine.get("MemoryLimit") and engine.get("SwapLimit") and engine.get("CPUCfsQuota"),
            "Engine não suporta os limites de memória/swap/CPU.",
        )
        image_info = json.loads(docker.run("image", "inspect", image))[0]
        image_id = image_info["Id"]
        container = docker.start(image_id, release, manifest_sha256, "valid")
        base_url = wait_ready(docker, container, manifest["serving_release_id"])
        verify_container_settings(docker.inspect(container), image_id)
        require(
            request_json(base_url, "/health") == (200, {"status": "alive"}),
            "Liveness diverge do contrato.",
        )
        status, information = request_json(base_url, "/info")
        require(
            status == 200
            and information["evidence_kind"] == manifest["evidence_kind"]
            and information["model_sha256"] == manifest["model_sha256"],
            "/info diverge da release.",
        )
        runtime = json.loads(docker.run("exec", container, "python", "-I", "-c", RUNTIME_PROBE))
        score = check_score(base_url, smoke, manifest)
        invalid = {**smoke["request"], "transaction_id": True}
        require(
            request_json(base_url, "/score", invalid)[0] == 422,
            "Entrada inválida não foi rejeitada com HTTP 422.",
        )
        for _ in range(10):
            check_score(base_url, smoke, manifest)
        timings = []
        for _ in range(requests):
            start = time.perf_counter()
            check_score(base_url, smoke, manifest)
            timings.append((time.perf_counter() - start) * 1000)
        stats = json.loads(docker.run("stats", "--no-stream", "--format", "{{json .}}", container))
        docker.run("restart", "--time", "10", container)
        recovered_url = wait_ready(docker, container, manifest["serving_release_id"])
        recovered = check_score(recovered_url, smoke, manifest)
        require(recovered == score, "Reinício alterou a resposta de controle.")
        docker.remove(container)
        failures = {}
        wrong_hash = "0" * 64 if manifest_sha256 != "0" * 64 else "1" * 64
        failed = docker.start(image_id, release, wrong_hash, "wrong-manifest")
        failures["wrong_manifest_sha256"] = check_failed_start(
            docker, failed, "Release bytes changed: manifest.json"
        )
        with failure_copy(release) as copy:
            with (copy / "pipeline.skops").open("ab") as stream:
                stream.write(b"\ncontrolled-corruption")
            failed = docker.start(image_id, copy, manifest_sha256, "corrupt-model")
            failures["corrupt_model"] = check_failed_start(
                docker, failed, "Release bytes changed: pipeline.skops"
            )
        with failure_copy(release) as copy:
            (copy / "pipeline.skops").unlink()
            failed = docker.start(image_id, copy, manifest_sha256, "missing-model")
            failures["missing_model"] = check_failed_start(
                docker, failed, "No such file or directory: '/model/pipeline.skops'"
            )
        report = {
            "schema_version": 1,
            "version": "laboratory_serving_docker_check_v1",
            "status": "success",
            "recorded_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "image": {
                "requested": image,
                "id": image_id,
                "repo_digests": image_info["RepoDigests"],
                "size_bytes": image_info["Size"],
                "architecture": image_info["Architecture"],
                "os": image_info["Os"],
            },
            "engine": {
                "version": engine["ServerVersion"],
                "context": context["Name"],
                "operating_system": engine["OperatingSystem"],
                "kernel_version": engine["KernelVersion"],
                "warnings": engine.get("Warnings"),
            },
            "manifest_sha256": manifest_sha256,
            "serving_release_id": manifest["serving_release_id"],
            "evidence_kind": manifest["evidence_kind"],
            "http_score": score,
            "runtime": runtime,
            "runtime_environment": information["runtime_environment"],
            "parity_tolerance": PARITY_TOLERANCE,
            "invalid_input_http_status": 422,
            "restart_recovery": "passed",
            "failure_scenarios": failures,
            "resources": {
                "memory_limit_bytes": MEMORY_BYTES,
                "cpus": 4,
                "docker_stats_snapshot": stats,
                "peak_memory_measured": False,
            },
            "latency": {"warmup_requests": 10, **latency_summary(timings)},
            "refit": False,
            "production_promotion": False,
        }
    finally:
        try:
            docker.cleanup()
        finally:
            require(hashes(release) == original, "Os arquivos da release foram alterados.")
    report["release_files_unchanged"] = True
    report["check_script_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument(
        "--output", type=Path, required=True, help="Novo recibo; não sobrescrever."
    )
    parser.add_argument("--requests", type=int, default=100)
    args = parser.parse_args()
    try:
        require(10 <= args.requests <= 1000, "Use entre 10 e 1000 requisições sequenciais.")
        output = args.output.resolve()
        release = args.release.resolve(strict=True)
        require(not output.exists(), "Recibo já existe; escolha outro DOCKER_REPORT.")
        require(not output.is_relative_to(release), "Grave o recibo fora da release imutável.")
        report = assess(args.image, release, args.manifest_sha256, args.requests)
        save_report(output, report)
        print(json.dumps({**report, "report_path": str(output)}, indent=2, allow_nan=False))
    except (CheckFailed, OSError, KeyError, ValueError) as error:
        print(f"Check Docker falhou: {error}", file=sys.stderr)
        return 1
    return 0


def save_report(output, report):
    """Publish complete bytes atomically and refuse a concurrent overwrite."""
    content = json.dumps(report, indent=2, allow_nan=False) + "\n"
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=output.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
            os.link(temporary, output)
        finally:
            temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
