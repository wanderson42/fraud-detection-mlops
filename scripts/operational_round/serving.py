"""Serve the new task champion and exercise blue/green on its own canonical URL."""

from contextlib import contextmanager
import json
from pathlib import Path
from queue import Empty, Queue
import re
import shutil
import subprocess
from tempfile import TemporaryDirectory
from threading import Thread
import time
from uuid import uuid4

from mlflow import MlflowClient
from mlflow.artifacts import download_artifacts
from scripts.operational_round import controller as c
from scripts.operational_round import policy as p
from scripts.platform import kind_foundation as f
from scripts.platform import kind_tracking
from scripts.serving import check_docker_runtime as checks
from scripts.serving import delivery_gate

from fraud_detection_mlops.integrations import mlflow_tracking as tracking
from fraud_detection_mlops.serving.model_release import SmokeCase, write_release
from fraud_detection_mlops.serving.request_schema import ScoreRequest

RELEASE = "fraud-detector"
CHART = f.ROOT / "infra/helm/fraud-serving"
OWNER = "fraud.serving/owner-id"


def alias_matches(key):
    info = c.read(key)
    selected = json.loads((c.work(key) / "promotion.json").read_bytes())
    p.require(
        selected["round_id"] == key and selected["policy_sha256"] == info["policy_sha256"],
        "Promotion belongs to another round",
    )
    with kind_tracking.port_forward() as uri:
        client = MlflowClient(tracking_uri=uri, registry_uri=uri)
        actual = client.get_model_version_by_alias("fraud-detector", "champion")
        p.require(
            actual.version == selected["version"]
            and actual.run_id == selected["run_id"]
            and actual.tags.get("fraud.model_sha256") == selected["model_sha256"],
            "Champion alias changed; do not serve another version",
        )
    return info, selected


def preparation(key):
    info, selected = alias_matches(key)
    value = json.loads((c.work(key) / "serving.json").read_bytes())
    p.require(
        value["round_id"] == key
        and value["owner_id"] == info["owner_id"]
        and value["model_sha256"] == selected["model_sha256"],
        "Serving receipt differs",
    )
    for slot in ("blue", "green"):
        folder = c.work(key) / "carrier" / slot
        checks.read_release(folder, value["releases"][slot]["manifest_sha256"])
        p.require(
            checks.hashes(folder) == value["releases"][slot]["files"], "Release bytes changed"
        )
    for image in value["images"].values():
        p.require(
            kind_tracking.image_digest(c.cri(image["image"])["id"])
            == kind_tracking.image_digest(image["identity"]),
            "Loaded serving image changed",
        )
    return value


def prepare(key):
    info, selected = alias_matches(key)
    root = c.work(key)
    if (root / "serving.json").exists():
        return preparation(key)
    summary = c.training_result(key)
    winner = summary["candidates"][selected["selected_model"]]
    context = f.managed_destination(root / "carrier", "storage")
    context.mkdir(mode=0o700, exist_ok=True)
    with (
        kind_tracking.port_forward() as uri,
        tracking.http_tracking(uri),
        TemporaryDirectory(dir=root) as tmp,
    ):
        client = MlflowClient(tracking_uri=uri, registry_uri=uri)
        path = Path(
            download_artifacts(
                artifact_uri=client.get_model_version_download_uri(
                    "fraud-detector", selected["version"]
                ),
                tracking_uri=uri,
                registry_uri=uri,
                dst_path=tmp,
            )
        )
        p.require(
            {v.relative_to(path).as_posix(): p.sha(v) for v in path.rglob("*") if v.is_file()}
            == winner["artifacts"],
            "Registry download differs from selected candidate",
        )
        model_bytes = (path / winner["weight_file"]).read_bytes()
        smoke = SmokeCase(
            request=ScoreRequest.model_validate(summary["control_request"]),
            expected_score=float(winner["control_score"]),
        )
        releases = {}
        for slot in ("blue", "green"):
            if (context / slot).exists():
                folder = context / slot
                manifest_sha = p.sha(folder / "manifest.json")
                checked, saved_smoke, _ = checks.read_release(folder, manifest_sha)
                p.require(
                    checked["model_sha256"] == selected["model_sha256"]
                    and checked["source"]["round_id"] == key
                    and checked["source"]["registry_version"] == selected["version"]
                    and saved_smoke == json.loads(smoke.model_dump_json()),
                    "Partial release preparation differs; preserve and inspect",
                )
                result = {"manifest_sha256": manifest_sha}
            else:
                result = write_release(
                    context / slot,
                    model_bytes,
                    smoke,
                    model_id=selected["selected_model"],
                    source={
                        "round_id": key,
                        "registered_model": "fraud-detector",
                        "registry_version": selected["version"],
                        "mlflow_run_id": selected["run_id"],
                        "policy_sha256": selected["policy_sha256"],
                        "implementation_commit": info["implementation_commit"],
                    },
                    parity_rows=winner["parity_rows"],
                    evidence_kind="verified_reference",
                )
            releases[slot] = {
                "manifest_sha256": result["manifest_sha256"],
                "manifest": json.loads((context / slot / "manifest.json").read_bytes()),
                "files": checks.hashes(context / slot),
            }
    # A fresh runtime comes from committed current code, never from a historical experiment.
    runtime = "fraud-kind-serving-runtime:" + uuid4().hex
    logs = root / "runtime-build"
    logs.mkdir(exist_ok=True)
    print("Building the current committed inference runtime...", flush=True)
    delivery_gate.build_image(f.ROOT, logs, runtime, {"commit": info["implementation_commit"]})
    # Qualify the actual newly trained model through the fresh runtime before Kubernetes.
    with checks.failure_copy(context / "blue") as readable:
        runtime_check = checks.assess(
            runtime, readable, releases["blue"]["manifest_sha256"], requests=10
        )
    shutil.copyfile(f.ROOT / "scripts/operational_round/seed.py", context / "seed.py")
    shutil.copyfile(f.ROOT / "infra/kind/serving/Dockerfile.artifacts", context / "Dockerfile")
    assets = "fraud-kind-serving-artifacts:" + uuid4().hex
    f.run(
        [
            "docker",
            "build",
            "--platform",
            "linux/amd64",
            "--file",
            context / "Dockerfile",
            "--tag",
            assets,
            context,
        ],
        timeout=300,
    )
    lock = json.loads((f.ROOT / "infra/serving/images.lock.json").read_bytes())
    gateway = "fraud-kind-serving-gateway:" + uuid4().hex
    f.run(["docker", "pull", "--platform", "linux/amd64", lock["gateway"]], timeout=300)
    f.run(["docker", "tag", lock["gateway"], gateway])
    images = {}
    for role, tag in [("runtime", runtime), ("artifacts", assets), ("gateway", gateway)]:
        loaded = c.load_image(tag)
        images[role] = {
            "image": tag,
            "identity": loaded["id"],
            "loaded_identities": [loaded["id"], *loaded.get("repoDigests", [])],
        }
    return c.save(
        key,
        "serving.json",
        {
            "schema": "fraud_operational_serving_v1",
            "round_id": key,
            "owner_id": info["owner_id"],
            "model_sha256": selected["model_sha256"],
            "registry_version": selected["version"],
            "images": images,
            "releases": releases,
            "runtime_HTTP_gate": runtime_check,
            "smoke": json.loads((context / "blue/smoke.json").read_bytes()),
            "historical_models_used": False,
            "fit_executed": False,
        },
    )


def owned(value):
    for kind in ["deployments", "services", "configmaps"]:
        rows = json.loads(
            f.kube("get", kind, "-l", "app.kubernetes.io/instance=" + RELEASE, "-o", "json").stdout
        )["items"]
        for item in rows:
            p.require(
                item["metadata"].get("annotations", {}).get(OWNER) == value["owner_id"],
                "Foreign canonical serving resources; do not adopt",
            )
    existing = json.loads(
        f.kube("get", "secrets", "-l", "owner=helm,name=" + RELEASE, "-o", "json").stdout
    )["items"]
    if existing:
        values = json.loads(f.helm("get", "values", RELEASE, "--output", "json").stdout)
        p.require(values.get("ownerId") == value["owner_id"], "Foreign canonical Helm release")
    return True


def apply(value, *, green=False, active="blue", replicas=1):
    owned(value)
    values = {
        "ownerId": value["owner_id"],
        "images": {role: v["image"] for role, v in value["images"].items()},
        "activeSlot": active,
        "greenEnabled": green,
        "greenReplicas": replicas,
        "manifests": {slot: v["manifest_sha256"] for slot, v in value["releases"].items()},
    }
    path = c.work(value["round_id"]) / ("serving-values-" + uuid4().hex + ".json")
    f.private_json(path, values)
    f.helm(
        "upgrade", "--install", RELEASE, CHART, "--values", path, "--timeout", "180s", timeout=210
    )
    for component in ["blue", "gateway"] + (["green"] if green and replicas else []):
        f.kube(
            "rollout",
            "status",
            "deployment/" + RELEASE + "-" + component,
            "--timeout=150s",
            timeout=170,
        )


def gateway(value):
    owned(value)
    rows = json.loads(
        f.kube(
            "get",
            "pods",
            "-l",
            "app.kubernetes.io/instance=" + RELEASE + ",app.kubernetes.io/component=gateway",
            "-o",
            "json",
        ).stdout
    )["items"]
    p.require(
        len(rows) == 1
        and rows[0]["metadata"].get("annotations", {}).get(OWNER) == value["owner_id"],
        "One owned canonical gateway required",
    )
    return rows[0]["metadata"]["name"]


def inventory(value):
    owned(value)

    def objects(kind):
        return json.loads(
            f.kube("get", kind, "-l", "app.kubernetes.io/instance=" + RELEASE, "-o", "json").stdout
        )["items"]

    deployments = {v["metadata"]["uid"]: v for v in objects("deployments")}
    replicas = {v["metadata"]["uid"]: v for v in objects("replicasets")}
    result = {}
    for pod in objects("pods"):
        if pod["metadata"].get("deletionTimestamp"):
            continue
        component = pod["metadata"]["labels"]["app.kubernetes.io/component"]
        refs = pod["metadata"].get("ownerReferences", [])
        p.require(
            len(refs) == 1
            and refs[0].get("controller")
            and refs[0]["kind"] == "ReplicaSet"
            and refs[0]["uid"] in replicas,
            "Canonical pod ownership differs",
        )
        parent = replicas[refs[0]["uid"]]["metadata"].get("ownerReferences", [])
        p.require(
            len(parent) == 1
            and parent[0].get("controller")
            and parent[0]["kind"] == "Deployment"
            and parent[0]["uid"] in deployments,
            "Canonical ReplicaSet ownership differs",
        )
        role = "gateway" if component == "gateway" else "runtime"
        p.require(
            component in {"blue", "green", "gateway"} and component not in result,
            "Unexpected canonical pod set",
        )
        expected = value["images"][role]
        containers = pod["spec"]["containers"]
        statuses = pod["status"].get("containerStatuses", [])
        p.require(
            len(containers) == len(statuses) == 1
            and containers[0]["image"] == expected["image"]
            and statuses[0]["ready"] is True
            and kind_tracking.image_digest(statuses[0]["imageID"])
            in {kind_tracking.image_digest(v) for v in expected["loaded_identities"]},
            "Canonical running image/readiness differs",
        )
        result[component] = {
            "pod": pod["metadata"]["name"],
            "uid": pod["metadata"]["uid"],
            "image_id": statuses[0]["imageID"],
        }
    p.require({"blue", "gateway"}.issubset(result), "Canonical service pods missing")
    return result


@contextmanager
def forward(pod):
    p.require(
        bool(re.fullmatch(r"fraud-detector-(gateway|blue|green)-[a-z0-9-]+", pod)),
        "Only the owned canonical pod may be forwarded",
    )
    port = 8080 if pod.startswith("fraud-detector-gateway-") else 8000
    args = [
        "kubectl",
        "--kubeconfig",
        str(f.workspace() / "kubeconfig"),
        "--context",
        f.CONTEXT,
        "--namespace",
        f.NAMESPACE,
        "port-forward",
        "--address",
        "127.0.0.1",
        "pod/" + pod,
        ":" + str(port),
    ]
    with subprocess.Popen(
        args, cwd=f.ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    ) as process:
        ready = Queue(maxsize=1)

        def drain():
            for line in iter(process.stdout.readline, b""):
                match = re.search(rb"Forwarding from 127\.0\.0\.1:([0-9]+) ->", line)
                if match and ready.empty():
                    ready.put_nowait("http://127.0.0.1:" + match[1].decode())

        reader = Thread(target=drain, daemon=True)
        reader.start()
        try:
            try:
                url = ready.get(timeout=30)
            except Empty as exc:
                raise ValueError("Canonical port-forward failed") from exc
            p.require(process.poll() is None, "Canonical port-forward exited")
            yield url
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            reader.join(timeout=5)


def check(url, value, slot):
    from scripts.platform.kind_serving import wait_route

    return wait_route(url, value, slot, timeout=45)


def deploy(key):
    value = prepare(key)
    apply(value)
    running = inventory(value)
    with forward(gateway(value)) as url:
        verified = check(url, value, "blue")
    return c.save(
        key,
        "deployment.json",
        {
            "status": "champion_served",
            "round_id": key,
            "registry_version": value["registry_version"],
            "model_sha256": value["model_sha256"],
            "http": verified,
            "production_promotion": False,
            "running_images": running,
        },
    )


def verify(key):
    from scripts.operational_round import promotion

    _info, selected = alias_matches(key)
    value = preparation(key)
    rollout = json.loads((c.work(key) / "rollout.json").read_bytes())
    p.require(
        rollout["status"] == "rollback_verified"
        and rollout["round_id"] == key
        and rollout["model_sha256"] == selected["model_sha256"]
        and rollout["events"]
        == [
            "candidate_direct_verified",
            "champion_blue_verified",
            "candidate_routed_same_URL",
            "candidate_outage_502_observed",
            "champion_restored_same_URL",
        ],
        "Complete the canonical round blue/green gate before final verification",
    )
    summary = c.training_result(key)
    train_data, val, parts = c.batches(key)
    with kind_tracking.port_forward() as uri, tracking.http_tracking(uri):
        checked = promotion.verify(
            MlflowClient(tracking_uri=uri, registry_uri=uri), summary, uri, val, train_data, parts
        )
    with forward(gateway(value)) as url:
        http = check(url, value, "blue")
    result = {
        "status": "canonical_round_complete",
        "rollout_receipt_sha256": p.sha(c.work(key) / "rollout.json"),
        "new_training_performed": True,
        "round_id": key,
        "registry_version": selected["version"],
        "model_sha256": selected["model_sha256"],
        "dataset_model_verification": checked,
        "http": http,
        "historical_models_used": False,
        "test_evaluated": False,
        "fit_executed": False,
        "production_promotion": False,
        "running_images": inventory(value),
    }
    f.private_json(c.work(key) / ("verification-" + uuid4().hex + ".json"), result)
    return result


def rehearse(key):
    value = preparation(key)
    events = []
    owned(value)
    try:
        apply(value, green=True)
        pods = json.loads(
            f.kube(
                "get",
                "pods",
                "-l",
                "app.kubernetes.io/instance=" + RELEASE + ",app.kubernetes.io/component=green",
                "-o",
                "json",
            ).stdout
        )["items"]
        p.require(len(pods) == 1, "One candidate pod required")
        with forward(pods[0]["metadata"]["name"]) as direct:
            check(direct, value, "green")
        events.append("candidate_direct_verified")
        with forward(gateway(value)) as url:
            check(url, value, "blue")
            events.append("champion_blue_verified")
            apply(value, green=True, active="green")
            check(url, value, "green")
            events.append("candidate_routed_same_URL")
            f.kube("scale", "deployment/" + RELEASE + "-green", "--replicas=0")
            deadline = time.monotonic() + 45
            while True:
                code, _ = checks.request_json(url, "/ready")
                if code == 502:
                    break
                p.require(time.monotonic() < deadline, "Candidate outage not observed")
                time.sleep(0.2)
            events.append("candidate_outage_502_observed")
            start = time.monotonic()
            apply(value, green=True, active="blue", replicas=0)
            response = check(url, value, "blue")
            events.append("champion_restored_same_URL")
            elapsed = time.monotonic() - start
        result = {
            "status": "rollback_verified",
            "round_id": key,
            "events": events,
            "same_weights_operational_repackage": True,
            "rollback_observation_seconds": elapsed,
            "http": response,
            "model_sha256": value["model_sha256"],
            "zero_downtime_claim": False,
        }
    finally:
        apply(value, green=True, active="blue", replicas=0)
    return c.save(key, "rollout.json", result)


def api(key):
    value = preparation(key)
    inventory(value)
    with forward(gateway(value)) as url:
        verified = check(url, value, "blue")
        print(
            json.dumps(
                {"service": "fraud-detector", "url": url, "verification": verified}, indent=2
            ),
            flush=True,
        )
        print("Ctrl+C encerra somente este acesso local.", flush=True)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    return {"status": "local_access_closed"}
