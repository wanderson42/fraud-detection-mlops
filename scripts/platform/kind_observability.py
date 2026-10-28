"""Provision read-only completed-replay monitoring on the owned KinD cluster."""

import argparse
import base64
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
from queue import Empty, Queue
import re
import secrets
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Thread
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener
from uuid import uuid4

from scripts.platform import dashboard_colors
from scripts.platform import kind_foundation as f
from scripts.platform import kind_observability_state as state
from scripts.platform import kind_tracking as tracking
from scripts.platform.container_images import inspect_image as cri

RELEASE = "fraud-observability"
ANNOTATION = "fraud.observability/owner-id"
CHART = f.ROOT / "infra/helm/fraud-observability"
ROLES = ("exporter", "prometheus", "grafana")
PORTS = {"exporter": 19100, "prometheus": 9090, "grafana": 3000}
SCOPE = "completed_native_replay_copy_readonly_exporter_Prometheus_Grafana_not_live_stream_or_new_model_quality"
# This controller is not included in the monitoring runtime image. Accept only
# the exact already-issued preparation when this host-only Helm fix is applied.
PRE_HELM4_FIX_CONTROLLER_SHA256 = (
    "683f8267ead449cc684f76c0fa21201e3c082771a9d9ee95f4a0ccbe8fce33ae"
)
CONTROLLER_SOURCE = "scripts/platform/kind_observability.py"
LEGACY_PROFILE = "references/kind_observability_legacy_image_pins_v1.json"
LEGACY_PROFILE_SHA256 = "7baf8c4e0280965fe2ecbb1b6f0393c6d12069f3338b5532c072289605e77553"


def work():
    return f.managed_destination(f.workspace() / "observability", "storage")


def pins():
    names = {
        "scripts/platform/container_images.py",
        "scripts/platform/dashboard_colors.py",
        LEGACY_PROFILE,
        "scripts/platform/kind_observability.py",
        "scripts/platform/kind_observability_state.py",
        "scripts/platform/kind_foundation.py",
        "references/gold_contract_v1.json",
        "infra/observability/images.lock.json",
    }
    for directory in (
        "scripts/monitoring",
        "infra/kind/observability",
        "infra/helm/fraud-observability",
    ):
        names.update(
            p.relative_to(f.ROOT).as_posix()
            for p in (f.ROOT / directory).rglob("*")
            if p.is_file() and "__pycache__" not in p.parts
        )
    return {name: state.sha(f.ROOT / name) for name in sorted(names)}


def runtime_pins():
    """Only image build inputs and registry lock; charts/controllers do not enter images."""
    names = {
        "infra/kind/observability/Dockerfile",
        "infra/kind/observability/Dockerfile.dockerignore",
        "references/gold_contract_v1.json",
        "scripts/platform/kind_observability_state.py",
        "infra/observability/images.lock.json",
    }
    names.update(
        p.relative_to(f.ROOT).as_posix() for p in (f.ROOT / "scripts/monitoring").glob("*.py")
    )
    return {name: state.sha(f.ROOT / name) for name in sorted(names)}


def initialize():
    f.owner()
    work().mkdir(mode=0o700, exist_ok=True)
    f.require(work().stat().st_mode & 0o077 == 0, "Observability workspace must be private")
    path = work() / "credentials.json"
    if not path.exists():
        f.require(not any(work().iterdir()), "Partial workspace; inspect before initializing")
        f.private_json(path, {"owner_id": uuid4().hex, "password": secrets.token_hex(24)})
    credentials()
    return {"status": "observability_initialized", "workspace": str(work())}


def credentials():
    path = f.managed_destination(work() / "credentials.json", "storage")
    f.require(path.stat().st_mode & 0o077 == 0, "Private credentials required")
    value = json.loads(path.read_text())
    f.require(
        set(value) == {"owner_id", "password"}
        and re.fullmatch(r"[a-f0-9]{32}", value["owner_id"])
        and re.fullmatch(r"[a-f0-9]{48}", value["password"]),
        "Invalid credentials",
    )
    return value


def command(args, timeout=300):
    done = f.run(args, timeout=timeout, check=False)
    f.require(
        done.returncode == 0,
        "Observability command failed:\n" + (done.stdout + done.stderr)[-6000:],
    )
    return done


def import_image(role, tag):
    f.require(
        role in ROLES and re.fullmatch("fraud-kind-observability-" + role + r":[a-f0-9]{32}", tag),
        "Invalid import tag",
    )
    image = json.loads(command(["docker", "image", "inspect", tag]).stdout)[0]
    f.require(image["Os"] == "linux" and image["Architecture"] == "amd64", "Expected linux/amd64")
    with TemporaryDirectory(prefix="image-import-", dir=work()) as temporary:
        archive = Path(temporary) / "image.tar"
        command(["docker", "image", "save", "--platform", "linux/amd64", "--output", archive, tag])
        archive.chmod(0o600)
        f.require(archive.stat().st_size > 0, "Empty image archive")
        digest, size = state.sha(archive), archive.stat().st_size
        command(["kind", "load", "image-archive", "--name", f.CLUSTER, archive])
    loaded = cri(tag)
    f.require(
        tag in {v.removeprefix("docker.io/library/") for v in loaded.get("repoTags", [])},
        "Imported tag differs",
    )
    return {
        "image": tag,
        "docker_id": image["Id"],
        "loaded_identities": [loaded["id"], *loaded.get("repoDigests", [])],
        "platform": "linux/amd64",
        "archive_sha256": digest,
        "archive_bytes": size,
        "temporary_archive_removed": True,
    }


def helm(*args, timeout=60):
    # Only non-secret values are sent to Helm in this flow. Keep native errors
    # visible instead of the generic foundation wrapper's suppressed output.
    return command(
        [
            "helm",
            *args,
            "--kubeconfig",
            f.workspace() / "kubeconfig",
            "--kube-context",
            f.CONTEXT,
            "--namespace",
            f.NAMESPACE,
        ],
        timeout=timeout,
    )


def releases():
    # Helm 4 lists every release status by default; --all was removed.
    return json.loads(helm("list", "--filter", "^" + RELEASE + "$", "--output", "json").stdout)


def build():
    initialize()
    path = work() / "images.json"
    if path.exists():
        image_record()
        return {"status": "observability_images_already_loaded"}
    value = {
        "schema": "fraud_kind_observability_images_v1",
        "node_uid": f.owner()["node_uid"],
        "owner_id": credentials()["owner_id"],
        "source_pins": runtime_pins(),
        "images": {},
    }
    lock = json.loads((f.ROOT / "infra/observability/images.lock.json").read_text())["images"]
    for role in ROLES:
        tag = "fraud-kind-observability-" + role + ":" + value["owner_id"]
        if role == "exporter":
            print("Building the stdlib-only monitoring runtime...", flush=True)
            command(
                [
                    "docker",
                    "build",
                    "--platform",
                    "linux/amd64",
                    "--file",
                    "infra/kind/observability/Dockerfile",
                    "--tag",
                    tag,
                    ".",
                ],
                timeout=900,
            )
        else:
            repository = "prom/prometheus" if role == "prometheus" else "grafana/grafana"
            pin = lock[repository]
            requested = repository + ":" + pin["tag"] + "@" + pin["digest"]
            command(["docker", "pull", "--platform", "linux/amd64", requested])
            info = json.loads(command(["docker", "image", "inspect", requested]).stdout)[0]
            f.require(
                any(v.endswith("@" + pin["digest"]) for v in info.get("RepoDigests", [])),
                "Registry image pin differs",
            )
            command(["docker", "tag", requested, tag])
        value["images"][role] = import_image(role, tag)
    f.private_json(path, value)
    return {"status": "observability_images_built_and_loaded", "record": str(path)}


def verify_image_sources(saved):
    runtime = runtime_pins()
    if saved == runtime:
        return
    raw = (f.ROOT / LEGACY_PROFILE).read_bytes()
    f.require(
        hashlib.sha256(raw).hexdigest() == LEGACY_PROFILE_SHA256, "Legacy image profile differs"
    )
    profile = json.loads(raw)
    known = profile["source_pins"]
    f.require(
        any(
            saved == {**known, CONTROLLER_SOURCE: h}
            for h in profile["accepted_host_controller_sha256"]
        ),
        "Image source pins changed; preserve receipt",
    )
    f.require(
        {name: saved.get(name) for name in runtime} == runtime,
        "Image runtime source pins changed; preserve receipt",
    )


def image_record():
    value = json.loads(f.managed_destination(work() / "images.json", "storage").read_text())
    f.require(
        value["schema"] == "fraud_kind_observability_images_v1",
        "Invalid image receipt",
    )
    verify_image_sources(value["source_pins"])
    f.require(
        value["node_uid"] == f.owner()["node_uid"]
        and value["owner_id"] == credentials()["owner_id"],
        "Image ownership differs",
    )
    for role in ROLES:
        row = value["images"][role]
        f.require(
            row["image"] == "fraud-kind-observability-" + role + ":" + value["owner_id"],
            "Image tag differs",
        )
        loaded = cri(row["image"])
        f.require(
            row["image"]
            in {v.removeprefix("docker.io/library/") for v in loaded.get("repoTags", [])}
            and loaded["id"] in row["loaded_identities"],
            "Loaded image identity differs",
        )
    return value


def prepare(source, no_other_writers):
    f.require(not session_only(), "Session-only profile cannot adopt a legacy replay snapshot")
    initialize()
    path = work() / "snapshot.json"
    f.require(not path.exists(), "Snapshot already prepared; preserve it")
    source = f.managed_destination(source.absolute(), "replay")
    target = work() / ("snapshot-" + uuid4().hex)
    result = state.capture(source, target, no_other_writers)
    value = {
        "schema": "fraud_kind_observability_snapshot_v1",
        "owner_id": credentials()["owner_id"],
        "node_uid": f.owner()["node_uid"],
        "source_directory": str(source),
        "directory": str(target),
        **result,
    }
    f.private_json(path, value)
    return {
        "status": "completed_replay_snapshot_prepared",
        "record": str(path),
        "counts": result["expected_summary"]["counts"],
        "historical_model_loaded": False,
    }


def snapshot_record():
    value = json.loads(f.managed_destination(work() / "snapshot.json", "storage").read_text())
    f.require(
        value["schema"] == "fraud_kind_observability_snapshot_v1"
        and value["owner_id"] == credentials()["owner_id"]
        and value["node_uid"] == f.owner()["node_uid"],
        "Snapshot ownership differs",
    )
    f.require(
        state.hashes(f.managed_destination(Path(value["directory"]), "storage")) == value["files"],
        "Prepared snapshot changed",
    )
    return value


def validate():
    dashboard_colors.validate()
    # Copied configurations must remain exactly those already reviewed locally.
    f.require(
        (CHART / "files/alerts.yml").read_bytes()
        == (f.ROOT / "infra/observability/alerts.yml").read_bytes(),
        "Copied alert rules differ",
    )
    for p in (f.ROOT / "infra/observability/grafana/dashboards").glob("*.json"):
        f.require(
            (CHART / "files/dashboards" / p.name).read_bytes() == p.read_bytes(),
            "Copied dashboard differs",
        )
    command(["helm", "lint", "--strict", CHART, "--kube-version", "1.35.8"])
    command(
        [
            "helm",
            "template",
            RELEASE,
            CHART,
            "--namespace",
            f.NAMESPACE,
            "--kube-version",
            "1.35.8",
        ]
    )
    return {"status": "observability_templates_validated", "native_cluster_validated": False}


def get(kind, name):
    return json.loads(
        f.kube("get", kind, name, "--ignore-not-found", "-o", "json").stdout or "null"
    )


def owned(item):
    f.require(
        item
        and item["metadata"].get("annotations", {}).get(ANNOTATION) == credentials()["owner_id"],
        "Foreign or missing observability resource; refuse adoption",
    )
    return item


def session_telemetry():
    # Optional projection does not change any accepted historical exporter image bytes.
    path = work() / "session-monitoring/enabled.json"
    if not path.exists():
        return None
    from scripts.session_monitoring.controller import settings

    return settings()


def session_only():
    path = work() / "session-only.json"
    if not path.exists():
        return False
    from scripts.platform import session_observability

    session_observability.profile()
    return True


def values(ready):
    result = {
        "ownerId": credentials()["owner_id"],
        "stateReady": ready,
        "images": {role: row["image"] for role, row in image_record()["images"].items()},
    }
    telemetry = session_telemetry()
    if session_only():
        f.require(telemetry, "Session-only profile requires a prepared receiver")
        result["legacyReplay"] = False
    if telemetry:
        result["sessionTelemetry"] = telemetry
    return result


def install(ready):
    path = work() / ("values-" + uuid4().hex + ".json")
    f.private_json(path, values(ready))
    args = ["upgrade", "--install", RELEASE, CHART, "--values", path, "--timeout", "5m"]
    # KinD's standard class can wait for a first consumer. No pods run until
    # seed imports state; waiting for all PVCs here would deadlock scheduling.
    if ready:
        args.append("--wait")
    done = helm(*args, timeout=360)
    return done


def ensure_secret():
    c = credentials()
    name = RELEASE + "-credentials"
    data = {"password": base64.b64encode(c["password"].encode()).decode()}
    item = get("secret", name)
    if item:
        owned(item)
        f.require(item["data"] == data, "Existing Grafana Secret differs")
    else:
        f.kube(
            "create",
            "-f",
            "-",
            data=json.dumps(
                {
                    "apiVersion": "v1",
                    "kind": "Secret",
                    "metadata": {"name": name, "annotations": {ANNOTATION: c["owner_id"]}},
                    "type": "Opaque",
                    "data": data,
                }
            ),
        )


def kube_args(*args):
    return [
        "kubectl",
        "--kubeconfig",
        str(f.workspace() / "kubeconfig"),
        "--cache-dir",
        str(f.workspace() / "kube-cache"),
        "--context",
        f.CONTEXT,
        "--namespace",
        f.NAMESPACE,
        *args,
    ]


def transfer(seed, record):
    manifest = (json.dumps(record["files"], sort_keys=True, separators=(",", ":")) + "\n").encode()
    digest = hashlib.sha256(manifest).hexdigest()
    with TemporaryDirectory(prefix="transfer-", dir=work()) as temporary:
        log = Path(temporary) / "stderr"
        with log.open("wb") as errors:
            process = subprocess.Popen(
                kube_args(
                    "exec",
                    "-i",
                    seed,
                    "--",
                    "python",
                    "-m",
                    "scripts.platform.kind_observability_state",
                    "receive",
                    "/state",
                    digest,
                ),
                cwd=f.ROOT,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=errors,
            )
            try:
                process.stdin.write(manifest)
                for name in state.FILES:
                    with (Path(record["directory"]) / name).open("rb") as stream:
                        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                            process.stdin.write(chunk)
                process.stdin.close()
                process.wait(timeout=180)
                raw = process.stdout.read()
                f.require(
                    process.returncode == 0, "State transfer failed: " + log.read_text()[-3000:]
                )
                result = json.loads(raw)
                f.require(
                    result == {"status": "state_imported", "files": record["files"]},
                    "Transfer result differs",
                )
            except BrokenPipeError as exc:
                process.wait(timeout=10)
                raise ValueError("State transfer rejected: " + log.read_text()[-3000:]) from exc
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
                if not process.stdin.closed:
                    process.stdin.close()
                process.stdout.close()


def seed_pod(record):
    name = RELEASE + "-seed-" + uuid4().hex[:12]
    pod = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": name,
            "labels": {"app.kubernetes.io/instance": RELEASE},
            "annotations": {ANNOTATION: credentials()["owner_id"]},
        },
        "spec": {
            "automountServiceAccountToken": False,
            "enableServiceLinks": False,
            "restartPolicy": "Never",
            "activeDeadlineSeconds": 600,
            "nodeSelector": {"kubernetes.io/hostname": f.CLUSTER + "-control-plane"},
            "securityContext": {
                "runAsNonRoot": True,
                "runAsUser": 65532,
                "runAsGroup": 65532,
                "fsGroup": 65532,
                "seccompProfile": {"type": "RuntimeDefault"},
            },
            "containers": [
                {
                    "name": "seed",
                    "image": image_record()["images"]["exporter"]["image"],
                    "imagePullPolicy": "Never",
                    "command": ["python", "-c", "import time; time.sleep(590)"],
                    "securityContext": {
                        "allowPrivilegeEscalation": False,
                        "readOnlyRootFilesystem": True,
                        "capabilities": {"drop": ["ALL"]},
                    },
                    "resources": {
                        "requests": {"cpu": "100m", "memory": "64Mi"},
                        "limits": {"cpu": "1", "memory": "256Mi"},
                    },
                    "volumeMounts": [{"name": "state", "mountPath": "/state"}],
                }
            ],
            "volumes": [
                {"name": "state", "persistentVolumeClaim": {"claimName": RELEASE + "-state"}}
            ],
        },
    }
    created = False
    try:
        f.kube("create", "-f", "-", data=json.dumps(pod))
        created = True
        f.kube("wait", "--for=condition=Ready", "pod/" + name, "--timeout=120s", timeout=130)
        transfer(name, record)
    finally:
        if created:
            f.kube("delete", "pod", name, "--wait=true", "--timeout=30s", check=False, timeout=40)


def _up():
    if session_only():
        from scripts.platform import session_observability

        return session_observability.install(session_observability.profile()["session_id"])
    validate()
    record = snapshot_record()
    before = f.inventory()
    protected = protected_inventory()
    # Check expected names even if their labels have been changed; never silently adopt.
    for kind, names in {
        "deployment": ROLES,
        "service": ROLES,
        "pvc": ("state", "prometheus", "grafana"),
        "configmap": ("config", "dashboards"),
    }.items():
        for suffix in names:
            item = get(kind, RELEASE + "-" + suffix)
            if item:
                owned(item)
    existing = releases()
    if existing:
        current = json.loads(helm("get", "values", RELEASE, "--output", "json").stdout)
        f.require(current in [values(False), values(True)], "Existing Helm values differ")
    ensure_secret()
    if not (work() / "imported.json").exists():
        f.require(
            not existing, "Partial install without import receipt; inspect retained resources"
        )
        install(False)
        seed_pod(record)
        f.private_json(
            work() / "imported.json",
            {
                "files": record["files"],
                "pvc_uid": owned(get("pvc", RELEASE + "-state"))["metadata"]["uid"],
            },
        )
    else:
        imported = json.loads((work() / "imported.json").read_text())
        f.require(
            imported["files"] == record["files"]
            and imported["pvc_uid"] == owned(get("pvc", RELEASE + "-state"))["metadata"]["uid"],
            "Imported state identity differs",
        )
    install(True)
    result = inventory()
    f.require(
        before == f.inventory() and protected == protected_inventory(),
        "Existing resource identities changed",
    )
    return {"status": "observability_ready", "inventory": result, "source_scope": SCOPE}


def up():
    path = work() / ("installation-" + uuid4().hex + ".json")
    report = {"schema": "fraud_kind_observability_installation_v1", "status": "failed"}
    try:
        result = _up()
        report.update(result)
        return result
    except BaseException as exc:
        report["reason"] = str(exc)[-3000:]
        report["diagnostics"] = diagnostics()
        raise
    finally:
        f.private_json(path, report)
        print("Relatório de instalação: " + str(path), flush=True)


def diagnostics():
    result = {}
    for role in ROLES:
        name = RELEASE + "-" + role
        done = f.kube("logs", "deployment/" + name, "--tail=40", check=False)
        result[role] = (done.stdout + done.stderr)[-4000:]
    return result


def validate_exporter_mount(pod):
    container = pod["spec"]["containers"][0]
    mount = next(
        (v for v in container.get("volumeMounts", []) if v["mountPath"] == "/state"), None
    )
    claim = next(
        (
            v.get("persistentVolumeClaim")
            for v in pod["spec"]["volumes"]
            if mount and v["name"] == mount["name"]
        ),
        None,
    )
    f.require(
        mount
        and mount.get("readOnly") is True
        and claim
        and claim.get("readOnly") is True
        and claim["claimName"] == RELEASE + "-state",
        "Exporter must mount only the owned state claim read-only",
    )


def inventory():
    images = image_record()["images"]
    result = {"pods": {}, "pvcs": {}}
    for role in ROLES:
        dep = owned(get("deployment", RELEASE + "-" + role))
        svc = owned(get("service", RELEASE + "-" + role))
        pods = json.loads(
            f.kube(
                "get",
                "pods",
                "-l",
                "app.kubernetes.io/instance=" + RELEASE + ",app.kubernetes.io/component=" + role,
                "-o",
                "json",
            ).stdout
        )["items"]
        pods = [p for p in pods if not p["metadata"].get("deletionTimestamp")]
        f.require(len(pods) == 1, "Expected one ready pod per component")
        pod = owned(pods[0])
        if role == "exporter":
            if session_only():
                from scripts.platform.session_observability import validate_receiver

                validate_receiver(pod)
            else:
                validate_exporter_mount(pod)
        refs = pod["metadata"].get("ownerReferences", [])
        f.require(
            len(refs) == 1 and refs[0].get("controller") and refs[0]["kind"] == "ReplicaSet",
            "Pod owner differs",
        )
        rs = get("replicaset", refs[0]["name"])
        f.require(
            rs
            and rs["metadata"]["uid"] == refs[0]["uid"]
            and any(
                v.get("controller") and v["uid"] == dep["metadata"]["uid"]
                for v in rs["metadata"].get("ownerReferences", [])
            ),
            "ReplicaSet ownership differs",
        )
        statuses = pod.get("status", {}).get("containerStatuses", [])
        telemetry = session_telemetry() if role == "exporter" else None
        if telemetry:
            from scripts.session_monitoring.controller import image_record as session_image

            image = session_image()
            sidecars = [s for s in statuses if s.get("name") == "session-exporter"]
            containers = [
                c for c in pod["spec"]["containers"] if c.get("name") == "session-exporter"
            ]
            f.require(
                len(sidecars) == len(containers) == 1
                and containers[0]["image"] == telemetry["image"]
                and sidecars[0].get("ready") is True
                and bool(sidecars[0].get("state", {}).get("running"))
                and tracking.image_digest(sidecars[0]["imageID"])
                in {tracking.image_digest(v) for v in image["loaded_identities"]},
                "Session telemetry image/readiness differs",
            )
        f.require(
            len(statuses) == (2 if telemetry and not session_only() else 1)
            and statuses[0].get("ready") is True
            and bool(statuses[0].get("state", {}).get("running"))
            and pod.get("status", {}).get("phase") == "Running"
            and any(
                condition.get("type") == "Ready" and condition.get("status") == "True"
                for condition in pod.get("status", {}).get("conditions", [])
            ),
            "Component is not currently ready; "
            + json.dumps(
                {
                    "component": role,
                    "pod": pod["metadata"]["name"],
                    "phase": pod.get("status", {}).get("phase"),
                    "containers": [
                        {key: status.get(key) for key in ("ready", "restartCount", "state")}
                        for status in statuses
                    ],
                },
                sort_keys=True,
            ),
        )
        cid = statuses[0]["imageID"]
        expected_image = images[role]
        if role == "exporter" and session_only():
            expected_image = session_image()
        f.require(
            pod["spec"]["containers"][0]["image"] == expected_image["image"]
            and tracking.image_digest(cid)
            in {tracking.image_digest(v) for v in expected_image["loaded_identities"]},
            "Pod image identity differs",
        )
        result["pods"][role] = {
            "pod": pod["metadata"]["name"],
            "pod_uid": pod["metadata"]["uid"],
            "deployment_uid": dep["metadata"]["uid"],
            "service_uid": svc["metadata"]["uid"],
            "image_id": cid,
        }
    for role in (
        ("prometheus", "grafana") if session_only() else ("state", "prometheus", "grafana")
    ):
        claim = owned(get("pvc", RELEASE + "-" + role))
        f.require(claim["status"]["phase"] == "Bound", "PVC not bound")
        result["pvcs"][role] = {
            "uid": claim["metadata"]["uid"],
            "volume_name": claim["spec"]["volumeName"],
        }
    return result


@contextmanager
def forward(role, port=None, *, target_port=None):
    f.require(role in ROLES, "Unknown forwarding role")
    pod = inventory()["pods"][role]["pod"]
    target = 19102 if role == "exporter" and session_only() else PORTS[role]
    if target_port is not None:
        f.require(
            role == "exporter" and target_port == 19102 and session_telemetry(),
            "Unknown optional forwarding target",
        )
        target = target_port
    local = "" if port is None else str(port)
    args = kube_args(
        "port-forward", "--address", "127.0.0.1", "pod/" + pod, local + ":" + str(target)
    )
    with subprocess.Popen(
        args, cwd=f.ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    ) as process:
        ready = Queue(maxsize=1)

        def drain():
            for line in iter(process.stdout.readline, b""):
                match = re.search(rb"Forwarding from 127\.0\.0\.1:([0-9]+) ->", line)
                if match and ready.empty():
                    ready.put_nowait("http://127.0.0.1:" + match[1].decode())

        thread = Thread(target=drain, daemon=True)
        thread.start()
        try:
            try:
                uri = ready.get(timeout=30)
            except Empty as exc:
                raise ValueError("Observability port-forward failed") from exc
            f.require(process.poll() is None, "Port-forward exited")
            yield uri
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            thread.join(timeout=5)


def http(uri, path, *, auth=False):
    headers = {}
    if auth:
        headers["Authorization"] = (
            "Basic " + base64.b64encode(("admin:" + credentials()["password"]).encode()).decode()
        )
    request = Request(uri + path, headers=headers)
    with build_opener(ProxyHandler({})).open(request, timeout=15) as response:
        return json.load(response)


def available_http(uri, path, *, auth=False):
    deadline = time.monotonic() + 60
    while True:
        try:
            return http(uri, path, auth=auth)
        except URLError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(2)


def query(uri, expression):
    result = http(uri, "/api/v1/query?" + urlencode({"query": expression}))
    f.require(
        result.get("status") == "success" and result["data"]["resultType"] == "vector",
        "Prometheus query failed",
    )
    return {
        tuple(sorted(row["metric"].items())): float(row["value"][1])
        for row in result["data"]["result"]
    }


def expected_metrics(summary):
    from scripts.monitoring.collector import exposition

    # Parse the same low-cardinality gauge/counter lines; histogram buckets are not identity checks.
    result = {}
    for line in exposition({**summary, "snapshot_timestamp": 0, "durations": {}}).splitlines():
        if line.startswith(
            (
                "fraud_monitor_collector_up ",
                "fraud_replay_complete ",
                "fraud_replay_events_total ",
                "fraud_replay_decisions_total ",
                "fraud_replay_alerts_total ",
                "fraud_feature_comparison_ready ",
                "fraud_feature_jensen_shannon{",
            )
        ):
            name, raw = line.rsplit(" ", 1)
            result[name] = float(raw)
    return result


def verify_http():
    if session_only():
        from scripts.platform import session_observability

        return session_observability.verify_http()
    record = snapshot_record()
    expected = record["expected_summary"]
    inv = inventory()
    # Verify full state bytes in the actual read-only exporter mount, not just a cached summary.
    pod = inv["pods"]["exporter"]["pod"]
    checked = json.loads(
        f.kube(
            "exec",
            pod,
            "--",
            "python",
            "-m",
            "scripts.platform.kind_observability_state",
            "verify",
            "/state/snapshot",
        ).stdout
    )
    f.require(checked["files"] == record["files"], "Imported state bytes changed")
    with (
        forward("exporter") as exporter,
        forward("prometheus") as prometheus,
        forward("grafana") as grafana,
    ):
        summary = http(exporter, "/summary")
        f.require(
            state.aggregate(summary) == expected, "Exporter summary differs from prepared source"
        )
        deadline = time.monotonic() + 90
        checks = expected_metrics(expected)
        while True:
            try:
                f.require(
                    list(query(prometheus, 'up{job="fraud-replay"}').values()) == [1.0],
                    "Scrape target not healthy",
                )
                for expr, value in checks.items():
                    actual = list(query(prometheus, expr).values())
                    f.require(
                        len(actual) == 1 and abs(actual[0] - value) <= 1e-12,
                        "Scraped metric differs: " + expr,
                    )
                break
            except ValueError, URLError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(2)
        targets = http(prometheus, "/api/v1/targets")["data"]["activeTargets"]
        telemetry = session_telemetry()
        expected_targets = {"fraud-replay": "http://" + RELEASE + "-exporter:19100/metrics"}
        if telemetry:
            expected_targets["fraud-product-session"] = (
                "http://" + RELEASE + "-exporter:19102/metrics"
            )
        f.require(
            len(targets) == len(expected_targets)
            and {t["labels"]["job"]: t["scrapeUrl"] for t in targets} == expected_targets
            and all(t["health"] == "up" for t in targets),
            "Target binding differs",
        )
        import yaml

        intended = yaml.safe_load((CHART / "files/alerts.yml").read_text())["groups"][0]["rules"]
        if telemetry:
            intended += yaml.safe_load((CHART / "files/session/alerts.yml").read_text())["groups"][
                0
            ]["rules"]
        rule_deadline = time.monotonic() + 60
        while True:
            rule_groups = http(prometheus, "/api/v1/rules")["data"]["groups"]
            rules = [r for group in rule_groups for r in group["rules"]]
            if {r["name"] for r in rules} == {r["alert"] for r in intended} and all(
                r["health"] == "ok" for r in rules
            ):
                break
            f.require(time.monotonic() < rule_deadline, "Loaded rules differ or unhealthy")
            time.sleep(2)

        for rule in intended:
            observed = next(row for row in rules if row["name"] == rule["alert"])

            def formatted(expression):
                answer = http(
                    prometheus, "/api/v1/format_query?" + urlencode({"query": expression})
                )
                f.require(answer.get("status") == "success", "PromQL formatting failed")
                return answer["data"]

            f.require(
                formatted(observed["query"]) == formatted(rule["expr"]),
                "Loaded rule expression differs",
            )
            amount = rule["for"]
            duration = int(amount[:-1]) * (60 if amount.endswith("m") else 1)
            f.require(
                observed["duration"] == duration
                and observed["labels"] == rule["labels"]
                and observed["annotations"] == rule["annotations"],
                "Loaded rule metadata differs",
            )

        datasource = http(grafana, "/api/datasources/uid/fraud-prometheus", auth=True)
        f.require(
            datasource["url"] == "http://" + RELEASE + "-prometheus:9090"
            and datasource["type"] == "prometheus",
            "Grafana datasource differs",
        )
        try:
            health = http(grafana, "/api/datasources/uid/fraud-prometheus/health", auth=True)
        except HTTPError as exc:
            raise ValueError(
                "Grafana Prometheus plugin health failed (HTTP "
                + str(exc.code)
                + "); inspect owned Grafana startup/plugin logs before retrying"
            ) from exc
        f.require(health.get("status") == "OK", "Grafana Prometheus plugin is not healthy")
        # Exercise Grafana's authenticated datasource proxy, not only its configuration.
        proxied = http(
            grafana,
            "/api/datasources/proxy/uid/fraud-prometheus/api/v1/query?"
            + urlencode({"query": 'up{job="fraud-replay"}'}),
            auth=True,
        )
        f.require(
            proxied["status"] == "success"
            and len(proxied["data"]["result"]) == 1
            and float(proxied["data"]["result"][0]["value"][1]) == 1,
            "Grafana proxy cannot query Prometheus",
        )
        dashboards = []
        for path in sorted((CHART / "files/dashboards").glob("*.json")):
            wanted = json.loads(path.read_text())
            got = available_http(grafana, "/api/dashboards/uid/" + wanted["uid"], auth=True)[
                "dashboard"
            ]
            f.require(
                got["uid"] == wanted["uid"]
                and got["title"] == wanted["title"]
                and panel_contract(got) == panel_contract(wanted),
                "Provisioned dashboard differs",
            )
            dashboard_colors.verify_provisioned(wanted, got)
            dashboards.append(wanted["uid"])
    return {
        "state_bytes_verified": True,
        "summary_matches_source": True,
        "prometheus_metrics_verified": checks,
        "scrape_target_verified": True,
        "healthy_alert_rules": len(rules),
        "grafana_datasource_proxy_verified": True,
        "dashboard_color_policy_verified": "semantic_colors_v1",
        "provisioned_dashboard_UIDs": dashboards,
        "visual_dashboard_review_validated": False,
        "alert_firing_validated": False,
        "temporary_port_forwards_removed": True,
    }


def protected_inventory():
    items = json.loads(
        f.kube("get", "pods,pvc,services,deployments,statefulsets", "-o", "json").stdout
    )["items"]
    return sorted(
        (item["kind"], item["metadata"]["name"], item["metadata"]["uid"])
        for item in items
        if item["metadata"].get("labels", {}).get("app.kubernetes.io/instance") != RELEASE
    )


def panel_contract(dashboard):
    return [
        {key: p.get(key) for key in ("id", "type", "title", "targets", "datasource")}
        for p in dashboard["panels"]
    ]


def rehearse():
    f.require(
        not session_only(), "Use the session-only rehearsal; legacy replay was not installed"
    )
    path = work() / ("rehearsal-" + uuid4().hex + ".json")
    result = {
        "schema": "fraud_kind_observability_rehearsal_v1",
        "status": "failed",
        "scope": SCOPE,
        "report": str(path),
        "production_promotion": False,
        "fit_executed": False,
        "features_recomputed": False,
        "live_ingestion_validated": False,
        "backup_restore_validated": False,
        "host_reboot_validated": False,
    }
    started = time.monotonic()
    try:
        result["controller_sha256"] = state.sha(Path(__file__))
        result["source_pins"] = pins()
        result["before"] = inventory()
        result["foundation_before"] = f.inventory()
        result["protected_before"] = protected_inventory()
        source = snapshot_record()
        result["source_files"] = source["source_files"]
        result["copied_files"] = source["files"]
        result["source_summary"] = source["expected_summary"]
        result["verification"] = verify_http()
        old = result["before"]["pods"]["exporter"]
        f.kube("delete", "pod", old["pod"], "--wait=true", "--timeout=60s", timeout=70)
        deadline = time.monotonic() + 150
        while True:
            try:
                current = inventory()
                f.require(
                    current["pods"]["exporter"]["pod_uid"] != old["pod_uid"],
                    "Exporter pod not replaced",
                )
                break
            except ValueError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(2)
        result["after_recreation_verification"] = verify_http()
        result["after"] = inventory()
        result["foundation_after"] = f.inventory()
        result["protected_after"] = protected_inventory()
        f.require(
            result["before"]["pvcs"] == result["after"]["pvcs"]
            and result["before"]["pods"]["prometheus"] == result["after"]["pods"]["prometheus"]
            and result["before"]["pods"]["grafana"] == result["after"]["pods"]["grafana"]
            and old["deployment_uid"] == result["after"]["pods"]["exporter"]["deployment_uid"]
            and result["foundation_before"] == result["foundation_after"]
            and result["protected_before"] == result["protected_after"],
            "Component or foundation identity changed",
        )
        f.require(
            state.hashes(Path(source["source_directory"])) == source["source_files"],
            "Original source changed since preparation",
        )
        result.update(
            status="success",
            original_source_bytes_unchanged=True,
            state_volume_read_only=True,
            existing_services_preserved=True,
            exporter_pod_recreation_validated=True,
        )
    except BaseException as exc:
        result["reason"] = str(exc)[-3000:]
        raise
    finally:
        result["elapsed_seconds"] = time.monotonic() - started
        f.private_json(path, result)
        print("Relatório: " + str(path), flush=True)
    return result


def status():
    f.owner()
    return {
        "status": "inspected",
        "resources": json.loads(
            f.kube(
                "get",
                "pods,pvc,services",
                "-l",
                "app.kubernetes.io/instance=" + RELEASE,
                "-o",
                "json",
            ).stdout
        )["items"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=("validate", "init", "build", "prepare", "up", "status", "rehearse", "open"),
    )
    parser.add_argument("--state", type=Path)
    parser.add_argument("--no-other-writers", action="store_true")
    parser.add_argument("--component", choices=ROLES, default="grafana")
    parser.add_argument("--port", type=int, default=13001)
    args = parser.parse_args()
    try:
        if args.action == "open":
            f.require(1024 <= args.port <= 65535, "Use an unprivileged loopback port")
            with forward(args.component, args.port) as uri:
                print("URL: " + uri + " (Ctrl+C encerra somente o acesso local)", flush=True)
                print(
                    "Grafana: usuário admin; senha no arquivo privado observability/credentials.json.",
                    flush=True,
                )
                try:
                    while True:
                        time.sleep(1)
                except KeyboardInterrupt:
                    pass
            return
        with f.exclusive():
            if args.action == "prepare":
                f.require(args.state is not None, "Explicit --state required")
                result = prepare(args.state, args.no_other_writers)
            else:
                result = {
                    "validate": validate,
                    "init": initialize,
                    "build": build,
                    "up": up,
                    "status": status,
                    "rehearse": rehearse,
                }[args.action]()
        print(json.dumps(result, indent=2))
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as exc:
        print("Observability stopped: " + str(exc), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
