"""Bounded blue-green/rollback drill, with NGINX and the restored reference.

Never replaces an existing service. B repackages the same weights for an
observable release identity; no fitting, scientific promotion or tracking access.
"""

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
from tempfile import mkdtemp
import time
from urllib.error import URLError
from uuid import uuid4

from scripts.serving import check_docker_runtime as checks

from fraud_detection_mlops.storage.managed_paths import managed_destination

ROOT = Path(__file__).resolve().parents[2]
GATEWAY_IMAGE = (
    "nginx:1.30.6-alpine@sha256:970364228b1e4f932abc22d321932816782cca8a20b85a77552a7cfe9acfb3b5"
)
EXPECTED_EVENTS = [
    "baseline_routed",
    "invalid_candidate_rejected_before_start_or_switch",
    "candidate_direct_gates_passed",
    "candidate_routed_on_same_url",
    "candidate_outage_observed",
    "baseline_restored_on_same_url",
]
SCOPE = "isolated_same_weights_release_repackage_NGINX_switch_and_rollback_not_production_CD"


def save(path, value):
    raw = (json.dumps(value, indent=2, allow_nan=False) + "\n").encode()
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)


def approved_inputs(root):
    previous = json.loads(
        (
            root / "references/evidence/storage_release_reload_author_validation_2026-10-15.json"
        ).read_bytes()
    )
    native = json.loads(
        (
            root
            / "references/evidence/laboratory_serving_docker_native_validation_2026-10-09.json"
        ).read_bytes()
    )["docker_check"]
    directory = root / previous["restored_release_path_relative_to_repo"]
    checks.require(
        directory == directory.resolve(), "Restored release ancestors must not be symlinks"
    )
    manifest, smoke, _ = checks.read_release(directory, previous["manifest_sha256"])
    checks.require(
        manifest["serving_release_id"]
        == previous["serving_release_id"]
        == native["serving_release_id"]
        and previous["manifest_sha256"] == native["manifest_sha256"]
        and manifest["model_sha256"]
        == previous["model_sha256"]
        == native["http_score"]["model_sha256"]
        and smoke["expected_score"] == previous["expected_score"],
        "Approved release/control identities differ",
    )
    return directory, previous["manifest_sha256"], native["image"]["id"]


def stage_releases(source, workspace):
    """New operational identity only; original bytes/permissions are preserved."""
    blue, green, invalid = (workspace / name for name in ["blue", "green", "invalid"])
    for directory in [blue, green, invalid]:
        directory.mkdir(mode=0o755)
        for name in checks.RELEASE_FILES:
            shutil.copyfile(source / name, directory / name)
            (directory / name).chmod(0o644)
    manifest = json.loads((green / "manifest.json").read_bytes())
    manifest["serving_release_id"] = uuid4().hex
    manifest["source"] = {
        **manifest["source"],
        "operational_repackage_of": json.loads((blue / "manifest.json").read_bytes())[
            "serving_release_id"
        ],
        "purpose": "same_weights_blue_green_rollback_drill",
    }
    (green / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    with (invalid / "pipeline.skops").open("ab") as stream:
        stream.write(b"controlled-integrity-failure")
    return blue, green, invalid


def configuration(backend):
    checks.require(
        re.fullmatch(r"fraud-serving-check-[a-f0-9]{32}-[a-z]+", backend),
        "Invalid private backend identity",
    )
    return f"""pid /tmp/nginx.pid;
error_log stderr warn;
worker_processes 1;
events {{ worker_connections 128; }}
http {{
  access_log off;
  client_body_temp_path /tmp/client;
  proxy_temp_path /tmp/proxy;
  fastcgi_temp_path /tmp/fastcgi;
  uwsgi_temp_path /tmp/uwsgi;
  scgi_temp_path /tmp/scgi;
  server {{
    listen 8080;
    server_name _;
    client_max_body_size 128k;
    location / {{
      proxy_pass http://{backend}:8000;
      proxy_http_version 1.1;
      proxy_set_header Connection "";
      proxy_connect_timeout 1s;
      proxy_read_timeout 5s;
      proxy_send_timeout 5s;
      proxy_next_upstream off;
      proxy_intercept_errors on;
      error_page 502 503 504 =502 @unavailable;
    }}
    location @unavailable {{
      default_type application/json;
      return 502 '{{"error":"upstream_unavailable"}}';
    }}
  }}
}}
"""


def write_configuration(directory, backend):
    pending = directory / "nginx.pending"
    pending.write_text(configuration(backend))
    pending.chmod(0o644)
    os.replace(pending, directory / "nginx.conf")


BACKEND_REQUEST = """
import json, sys
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener
route, payload = sys.argv[1], json.loads(sys.argv[2])
data = None if payload is None else json.dumps(payload, allow_nan=False).encode()
request = Request('http://127.0.0.1:8000' + route, data=data,
                  headers={'Content-Type': 'application/json'})
try:
    with build_opener(ProxyHandler({})).open(request, timeout=5) as response:
        result = [response.status, json.load(response)]
except HTTPError as error:
    with error:
        result = [error.code, json.load(error)]
print(json.dumps(result, allow_nan=False))
"""


class BackendHTTP:
    """HTTP probe in an owned backend; no published backend port or host routing."""

    def __init__(self, docker, container):
        self.docker, self.container = docker, container

    def request(self, route, payload=None):
        checks.require(route in {"/ready", "/info", "/score"}, "Unreviewed backend probe route")
        raw = self.docker.run(
            "exec",
            self.container,
            "/opt/runtime/bin/python",
            "-I",
            "-c",
            BACKEND_REQUEST,
            route,
            json.dumps(payload, allow_nan=False),
        )
        result = json.loads(raw)
        checks.require(
            isinstance(result, list)
            and len(result) == 2
            and type(result[0]) is int
            and isinstance(result[1], dict),
            "Backend probe did not return status/body JSON",
        )
        return result[0], result[1]


def request_json(target, route, payload=None):
    if isinstance(target, BackendHTTP):
        return target.request(route, payload)
    return checks.request_json(target, route, payload)


def wait_backend(docker, name, release_id, timeout=60):
    client = BackendHTTP(docker, name)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = docker.inspect(name)["State"]
        if not state["Running"]:
            raise checks.CheckFailed(
                "Private backend exited before readiness: "
                + docker.run("logs", name, check=False)[-2000:]
            )
        if state.get("Health", {}).get("Status") == "healthy":
            status, ready = client.request("/ready")
            checks.require(
                status == 200 and ready.get("serving_release_id") == release_id,
                "Private backend readiness identity differs",
            )
            return client
        time.sleep(0.2)
    raise checks.CheckFailed("Private backend HEALTHCHECK not healthy within 60s")


class Gateway:
    def __init__(self, docker, image, network, directory, backend):
        self.docker, self.directory = docker, directory
        self.name = "fraud-serving-gateway-" + uuid4().hex
        directory.mkdir(mode=0o755)
        write_configuration(directory, backend)
        docker.containers.append(self.name)
        docker.run(
            "create",
            "--name",
            self.name,
            "--pull",
            "never",
            "--network",
            "name=" + network + "-edge,gw-priority=1",
            "--network",
            network,
            "--user",
            "101:101",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges=true",
            "--memory",
            "134217728",
            "--memory-swap",
            "134217728",
            "--cpus",
            "0.5",
            "--pids-limit",
            "32",
            "--restart",
            "no",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=16777216,mode=1777",
            "--publish",
            "127.0.0.1::8080",
            "--mount",
            f"type=bind,source={directory},target=/rollout,readonly",
            "--entrypoint",
            "nginx",
            image,
            "-c",
            "/rollout/nginx.conf",
            "-e",
            "stderr",
            "-g",
            "daemon off;",
        )
        docker.run("start", self.name)
        info = docker.inspect(self.name)
        checks.require(
            info["Image"] == image and info["HostConfig"]["ReadonlyRootfs"],
            "Gateway image/rootfs differ",
        )
        checks.require(
            set(info["NetworkSettings"]["Networks"]) == {network, network + "-edge"},
            "Gateway must join exactly its backend and loopback-entry networks",
        )
        mounts = info["Mounts"]
        checks.require(
            len(mounts) == 1 and mounts[0]["Destination"] == "/rollout" and not mounts[0]["RW"],
            "Gateway mounts differ",
        )
        ports = info["NetworkSettings"]["Ports"]["8080/tcp"]
        checks.require(
            bool(ports) and len(ports) == 1 and ports[0]["HostIp"] == "127.0.0.1",
            "Gateway exposed outside loopback",
        )
        self.url = "http://127.0.0.1:" + ports[0]["HostPort"]

    def select(self, backend):
        write_configuration(self.directory, backend)
        self.docker.run(
            "exec", self.name, "nginx", "-t", "-e", "stderr", "-c", "/rollout/nginx.conf"
        )
        self.docker.run(
            "exec", self.name, "nginx", "-s", "reload", "-e", "stderr", "-c", "/rollout/nginx.conf"
        )


def gate(url, manifest, smoke):
    status, ready = request_json(url, "/ready")
    checks.require(
        status == 200 and ready.get("serving_release_id") == manifest["serving_release_id"],
        "Readiness identity differs",
    )
    status, information = request_json(url, "/info")
    checks.require(
        status == 200
        and information.get("model_sha256") == manifest["model_sha256"]
        and information.get("serving_release_id") == manifest["serving_release_id"],
        "Runtime model identity differs",
    )
    score = checks.check_score(url, smoke, manifest, requester=request_json)
    checks.require(
        request_json(url, "/score", {**smoke["request"], "transaction_id": True})[0] == 422,
        "Input rejection contract differs",
    )
    return score


def await_gate(url, manifest, smoke, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            return gate(url, manifest, smoke)
        except checks.CheckFailed, URLError, TimeoutError, ConnectionError:
            time.sleep(0.1)
    raise checks.CheckFailed("Gateway did not serve the selected release within the bounded gate")


def switch_or_rollback(gateway, candidate, previous):
    """Candidate direct gates must pass first; any switch/post-switch failure rolls back."""
    name, url, manifest, smoke = candidate
    old_name, old_url, old_manifest, old_smoke = previous
    gate(url, manifest, smoke)
    gate(old_url, old_manifest, old_smoke)
    try:
        gateway.select(name)
        return await_gate(gateway.url, manifest, smoke)
    except Exception:
        gateway.select(old_name)
        await_gate(gateway.url, old_manifest, old_smoke)
        raise


def recover_if_unhealthy(gateway, current, previous):
    try:
        gate(gateway.url, current[2], current[3])
        return False
    except checks.CheckFailed, URLError, TimeoutError, ConnectionError:
        gate(previous[1], previous[2], previous[3])
        gateway.select(previous[0])
        await_gate(gateway.url, previous[2], previous[3])
        return True


def local_images(docker, serving_image):
    context = json.loads(docker.run("context", "inspect"))[0]
    endpoint = os.environ.get("DOCKER_HOST") or context["Endpoints"]["docker"]["Host"]
    checks.require(endpoint.startswith("unix://"), "Use the local Linux Docker Unix socket")
    engine = json.loads(docker.run("info", "--format", "{{json .}}"))
    checks.require(
        engine["OSType"] == "linux"
        and all(engine.get(k) is True for k in ["MemoryLimit", "SwapLimit", "CpuCfsQuota"]),
        "Local Linux resource controls required",
    )
    images = [
        json.loads(docker.run("image", "inspect", image))[0]
        for image in [serving_image, GATEWAY_IMAGE]
    ]
    checks.require(images[0]["Id"] == serving_image, "Serving image pin differs")
    checks.require(
        all(i["Os"] == "linux" and i["Architecture"] == "amd64" for i in images),
        "This bounded native rehearsal requires linux/amd64 images",
    )
    return images


def exercise(docker, image, gateway_image, network, workspace, source, digest, report):
    manifest, smoke, before = checks.read_release(source, digest)
    blue, green, invalid = stage_releases(source, workspace)
    green_digest = checks.hashes(green)["manifest.json"]
    green_manifest, green_smoke, green_hashes = checks.read_release(green, green_digest)
    save(workspace.parent / "candidate-manifest.json", green_manifest)
    report.update(
        baseline_manifest_sha256=digest,
        candidate_manifest_sha256=green_digest,
        baseline_release_id=manifest["serving_release_id"],
        candidate_release_id=green_manifest["serving_release_id"],
        model_sha256=manifest["model_sha256"],
    )

    def start(directory, pin, label, expected, control):
        name = docker.start(image, directory, pin, label, network=network, publish=False)
        information = docker.inspect(name)
        checks.verify_container_settings(information, image)
        checks.require(
            set(information["NetworkSettings"]["Networks"]) == {network},
            "Backend outside private network",
        )
        checks.require(
            not any(information["NetworkSettings"].get("Ports", {}).values()),
            "Backend ports must not be published",
        )
        url = wait_backend(docker, name, expected["serving_release_id"])
        gate(url, expected, control)
        invalid_payload = {**control["request"], "transaction_id": True}
        checks.require(
            request_json(url, "/score", invalid_payload)[0] == 422,
            "Input contract gate failed",
        )
        return name, url, expected, control

    baseline = start(blue, digest, "blue", manifest, smoke)
    gateway = Gateway(docker, gateway_image, network, workspace / "gateway", baseline[0])
    await_gate(gateway.url, manifest, smoke)
    report["events"].append("baseline_routed")
    try:
        checks.read_release(invalid, digest)
    except checks.CheckFailed as error:
        checks.require(
            "Hash do modelo diverge" in str(error),
            "Invalid candidate rejected for unexpected cause",
        )
    else:
        raise checks.CheckFailed("Corrupt candidate was accepted")
    gate(gateway.url, manifest, smoke)
    report["events"].append("invalid_candidate_rejected_before_start_or_switch")
    candidate = start(green, green_digest, "green", green_manifest, green_smoke)
    report["events"].append("candidate_direct_gates_passed")
    promoted = switch_or_rollback(gateway, candidate, baseline)
    report["events"].append("candidate_routed_on_same_url")
    report["candidate_score"] = promoted["score"]
    # Controlled outage only on the candidate created by this invocation.
    docker.run("stop", "--time", "2", candidate[0])
    status, _ = checks.request_json(gateway.url, "/score", smoke["request"])
    checks.require(status == 502, "Injected candidate outage was not visible through the gateway")
    report["observed_failure_http_status"] = status
    report["events"].append("candidate_outage_observed")
    began = time.monotonic()
    checks.require(
        recover_if_unhealthy(gateway, candidate, baseline), "Rollback was not triggered"
    )
    report["rollback_observation_seconds"] = time.monotonic() - began
    report["rollback_score"] = gate(gateway.url, manifest, smoke)["score"]
    report["events"].append("baseline_restored_on_same_url")
    checks.require(
        checks.hashes(source) == before and checks.hashes(green) == green_hashes,
        "Release bytes changed during rollout",
    )
    report.update(
        release_bytes_unchanged=True,
        rollback_verified=True,
        gateway_url=gateway.url,
        gateway_route_scope="same ephemeral loopback URL during this invocation",
    )


@contextmanager
def private_network(docker, name, report):
    try:
        for target, internal in [(name, True), (name + "-edge", False)]:
            docker.run(
                "network",
                "create",
                "--driver",
                "bridge",
                *(["--internal"] if internal else []),
                "--label",
                "fraud.rollout=" + name,
                target,
            )
            value = json.loads(docker.run("network", "inspect", target))[0]
            checks.require(
                value["Internal"] is internal and value["Labels"].get("fraud.rollout") == name,
                "Rollout network role/ownership differs",
            )
        yield
    except BaseException:
        report["container_diagnostics"] = []
        for container in docker.containers:
            try:
                report["container_diagnostics"].append(
                    {
                        "container": container,
                        "state": docker.inspect(container)["State"],
                        "network_settings": docker.inspect(container)["NetworkSettings"],
                        "port_bindings": docker.inspect(container)["HostConfig"].get(
                            "PortBindings"
                        ),
                        "logs": docker.run("logs", container, check=False)[-2000:],
                    }
                )
            except checks.CheckFailed:
                report["container_diagnostics"].append(
                    {"container": container, "inspection_failed": True}
                )
        raise
    finally:
        docker.cleanup()
        # A partial network create can still have succeeded on the daemon.
        raw = docker.run(
            "network", "ls", "--filter", "label=fraud.rollout=" + name, "--format", "{{.Name}}"
        )
        if raw:
            owned = raw.splitlines()
            checks.require(
                set(owned) <= {name, name + "-edge"},
                "Unexpected owned network; preserve for inspection",
            )
            for target in owned:
                docker.run("network", "rm", target)
        checks.require(not docker.containers, "Temporary container cleanup incomplete")
        report["temporary_resources_removed"] = True


def rehearse(root=ROOT):
    identity = uuid4().hex
    destination = managed_destination(root / "data/serving/rollouts" / identity, "serving")
    destination.mkdir(parents=True, mode=0o700, exist_ok=False)
    receipt = destination / "result.json"
    report = {
        "schema": "fraud_blue_green_rehearsal_v1",
        "status": "failed",
        "scope": SCOPE,
        "events": [],
        "refit": False,
        "production_promotion": False,
        "model_weights_changed": False,
        "existing_services_replaced": False,
        "native_CI_CD_pipeline_validated": False,
        "zero_downtime_validated": False,
        "temporary_resources_removed": False,
        "report": str(receipt),
        "controller_code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "runtime_checker_code_sha256": hashlib.sha256(
            Path(checks.__file__).read_bytes()
        ).hexdigest(),
        "gateway_requested": GATEWAY_IMAGE,
    }
    started = time.monotonic()
    try:
        source, digest, image = approved_inputs(root)
        before = checks.hashes(source)
        docker = checks.Docker()
        images = local_images(docker, image)
        report["images"] = [
            {"id": i["Id"], "repo_digests": i.get("RepoDigests", [])} for i in images
        ]
        network = "fraud-serving-rollout-" + identity
        stage = Path(mkdtemp(prefix="stage-", dir=destination))
        stage.chmod(0o755)
        try:
            with private_network(docker, network, report):
                exercise(docker, image, images[1]["Id"], network, stage, source, digest, report)
        finally:
            if report["temporary_resources_removed"]:
                shutil.rmtree(stage)
            else:
                report["preserved_stage_for_cleanup"] = str(stage)
        checks.require(checks.hashes(source) == before, "Original restored release changed")
        checks.require(
            report.get("rollback_verified") is True
            and report.get("release_bytes_unchanged") is True
            and report["events"] == EXPECTED_EVENTS,
            "Incomplete rollout/rollback evidence",
        )
        report["status"] = "success"
    except BaseException as error:
        report["error_type"] = type(error).__name__
        if isinstance(error, checks.CheckFailed):
            report["reason"] = str(error)
        raise
    finally:
        report["elapsed_seconds"] = time.monotonic() - started
        save(receipt, report)
        print("Relatório: " + str(receipt))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["rehearse"])
    parser.parse_args()
    try:
        print(json.dumps(rehearse(), indent=2, allow_nan=False))
    except (checks.CheckFailed, OSError, ValueError) as error:
        raise SystemExit("Blue-green rehearsal stopped: " + str(error)) from error


if __name__ == "__main__":
    main()
