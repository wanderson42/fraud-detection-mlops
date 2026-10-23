"""Bind a previously qualified delivery to KinD images; never rebuild or fit the API/model."""

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
from queue import Empty, Queue
import re
import shutil
import subprocess
from tempfile import TemporaryDirectory
from threading import Thread
from uuid import uuid4

from scripts.platform import kind_foundation as f
from scripts.platform import kind_tracking as tracking
from scripts.platform import source_compatibility
from scripts.platform.container_images import inspect_image as cri
from scripts.serving import blue_green_rehearsal as rollout
from scripts.serving import check_docker_runtime as checks
from scripts.serving import delivery_gate as delivery

CHART = f.ROOT / "infra/helm/fraud-serving"
ROLES = ("runtime", "artifacts", "gateway")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def work():
    return f.managed_destination(f.workspace() / "serving", "storage")


def pins():
    names = {
        "scripts/platform/container_images.py",
        "scripts/platform/source_compatibility.py",
        "scripts/platform/kind_serving.py",
        "scripts/platform/kind_serving_delivery.py",
        "scripts/platform/kind_serving_seed.py",
        "scripts/platform/kind_foundation.py",
        "scripts/platform/kind_tracking.py",
        "scripts/serving/delivery_gate.py",
        "scripts/serving/check_docker_runtime.py",
        "scripts/serving/blue_green_rehearsal.py",
    }
    for folder in ["infra/helm/fraud-serving", "infra/kind/serving"]:
        names.update(
            p.relative_to(f.ROOT).as_posix() for p in (f.ROOT / folder).rglob("*") if p.is_file()
        )
    return {name: sha(f.ROOT / name) for name in sorted(names)}


def image_command(args, *, timeout):
    completed = f.run(args, timeout=timeout, check=False)
    f.require(
        completed.returncode == 0,
        "Serving image command failed ("
        + " ".join(map(str, args[:3]))
        + "):\n"
        + (completed.stdout + completed.stderr)[-6000:],
    )
    return completed


def load(tag):
    f.require(
        re.fullmatch(r"fraud-kind-serving-(runtime|artifacts|gateway):[a-f0-9]{32}", tag),
        "Only controller-created serving tags may be imported",
    )
    image = json.loads(f.run(["docker", "image", "inspect", tag]).stdout)[0]
    f.require(
        image["Os"] == "linux" and image["Architecture"] == "amd64", "Expected linux/amd64 image"
    )
    # Docker's containerd store can retain an index that references unavailable platforms.
    # Export only the node's platform before ctr imports the archive with --all-platforms.
    with TemporaryDirectory(prefix="image-import-", dir=work()) as temporary:
        archive = f.managed_destination(Path(temporary) / "linux-amd64.tar", "storage")
        image_command(
            ["docker", "image", "save", "--platform", "linux/amd64", "--output", archive, tag],
            timeout=300,
        )
        f.require(archive.is_file() and archive.stat().st_size > 0, "Image export is empty")
        archive.chmod(0o600)
        exported = {
            "platform": "linux/amd64",
            "import_method": "platform_archive",
            "archive_sha256": sha(archive),
            "archive_bytes": archive.stat().st_size,
            "temporary_archive_removed": True,
        }
        image_command(
            ["kind", "load", "image-archive", "--name", f.CLUSTER, archive],
            timeout=300,
        )
    status = cri(tag)
    f.require(
        tag in {s.removeprefix("docker.io/library/") for s in status.get("repoTags", [])},
        "Loaded tag differs",
    )
    return {
        "image": tag,
        "docker_id": image["Id"],
        "loaded_identities": [status["id"], *status.get("repoDigests", [])],
        **exported,
    }


def validate_images(record):
    f.require(set(record["images"]) == set(ROLES), "Incomplete serving image set")
    for role, image in record["images"].items():
        f.require(
            re.fullmatch(r"fraud-kind-serving-" + role + r":[a-f0-9]{32}", image["image"]),
            "Unexpected image tag",
        )
        current = cri(image["image"])
        f.require(
            image["image"]
            in {s.removeprefix("docker.io/library/") for s in current.get("repoTags", [])},
            "Image is not loaded",
        )
        f.require(
            tracking.image_digest(current["id"])
            in {tracking.image_digest(s) for s in image["loaded_identities"]},
            "Loaded image identity changed",
        )


def stage(source, destination):
    blue, green, invalid = rollout.stage_releases(source, destination)
    blue_pin = sha(blue / "manifest.json")
    bm, smoke, _ = checks.read_release(blue, blue_pin)
    gm, _, _ = checks.read_release(green, sha(green / "manifest.json"))
    f.require(
        bm["evidence_kind"] == gm["evidence_kind"] == "synthetic_smoke",
        "Only qualified synthetic releases accepted",
    )
    f.require(
        bm["model_sha256"] == gm["model_sha256"]
        and bm["serving_release_id"] != gm["serving_release_id"],
        "Repackage changed weights or not identity",
    )
    # Same pin, but corrupted payload: it must fail before any deployment or route change.
    try:
        checks.read_release(invalid, blue_pin)
    except checks.CheckFailed:
        invalid_rejected = True
    else:
        raise ValueError("Invalid candidate unexpectedly accepted")
    from scripts.platform.kind_serving_seed import seed

    for slot in ["blue", "green"]:
        probe = destination / (slot + "-seed-check")
        probe.mkdir()
        seed(destination / slot, probe, sha(destination / slot / "manifest.json"))
        shutil.rmtree(probe)
    return {
        "invalid_rejected": invalid_rejected,
        "smoke": smoke,
        "releases": {
            slot: {
                "manifest_sha256": sha(destination / slot / "manifest.json"),
                "manifest": json.loads((destination / slot / "manifest.json").read_text()),
                "files": checks.hashes(destination / slot),
            }
            for slot in ["blue", "green"]
        },
    }


def prepare(directory, expected_source_sha, expected_descriptor_sha256):
    # Verify all bytes/pins before accessing Docker, KinD or creating Kubernetes resources.
    directory = f.managed_destination(directory.absolute(), "serving")
    delivery.verify(directory, expected_source_sha, expected_descriptor_sha256)
    owner = f.owner()
    work().mkdir(mode=0o700, exist_ok=True)
    f.require(work().stat().st_mode & 0o077 == 0, "Serving workspace must be private")
    if (work() / "prepared.json").exists():
        saved = plan()
        f.require(
            saved["source_directory"] == str(directory)
            and saved["source_commit"] == expected_source_sha
            and saved["descriptor_sha256"] == expected_descriptor_sha256,
            "Another delivery is already prepared; preserve it",
        )
        return {"status": "serving_already_prepared"}
    descriptor = json.loads((directory / "delivery.json").read_text())
    identity = uuid4().hex
    destination = f.managed_destination(work() / ("prepare-" + identity), "storage")
    destination.mkdir(mode=0o700)
    result = {
        "schema": "fraud_kind_serving_preparation_v1",
        "owner_id": identity,
        "node_uid": owner["node_uid"],
        "source_directory": str(directory),
        "source_commit": expected_source_sha,
        "descriptor_sha256": expected_descriptor_sha256,
        "source_pins": pins(),
        "directory": str(destination),
        "images": {},
        "runtime_rebuilt": False,
        "fit_executed": False,
        "historical_reference_accessed": False,
        "registry_published": False,
        "artifact_carrier_built": False,
        "qualified_runtime_image_id": descriptor["image"]["id"],
    }
    imported = False
    original_tag = descriptor["exported_local_tag"]
    f.require(
        re.fullmatch(r"fraud-serving-delivery:[a-f0-9]{32}", original_tag), "Invalid archive tag"
    )
    try:
        result.update(stage(directory / "release", destination))
        existing = f.run(["docker", "image", "inspect", original_tag], check=False)
        if existing.returncode == 0:
            f.require(
                json.loads(existing.stdout)[0]["Id"] == descriptor["image"]["id"],
                "Existing archive tag differs; preserve it",
            )
        else:
            imported = True
            completed = f.run(
                [
                    "docker",
                    "image",
                    "load",
                    "--platform",
                    "linux/amd64",
                    "--input",
                    directory / "image.tar",
                ],
                timeout=180,
                check=False,
            )
            f.require(completed.returncode == 0, "Qualified image archive load failed")
        image = json.loads(f.run(["docker", "image", "inspect", original_tag]).stdout)[0]
        f.require(
            image["Id"] == descriptor["image"]["id"]
            and (image["Config"].get("Labels") or {}).get("org.opencontainers.image.revision")
            == expected_source_sha,
            "Loaded runtime ID or source revision differs",
        )
        runtime = "fraud-kind-serving-runtime:" + identity
        f.run(["docker", "tag", original_tag, runtime])
        result["images"]["runtime"] = load(runtime)
        context = destination / "carrier"
        context.mkdir(mode=0o755)
        for slot in ["blue", "green"]:
            shutil.copytree(destination / slot, context / slot)
        shutil.copyfile(f.ROOT / "scripts/platform/kind_serving_seed.py", context / "seed.py")
        shutil.copyfile(f.ROOT / "infra/kind/serving/Dockerfile.artifacts", context / "Dockerfile")
        shutil.copyfile(
            f.ROOT / "infra/kind/serving/Dockerfile.artifacts.dockerignore",
            context / "Dockerfile.dockerignore",
        )
        artifacts = "fraud-kind-serving-artifacts:" + identity
        print(
            "Building the release carrier; the qualified API image is reused unchanged...",
            flush=True,
        )
        done = f.run(
            ["docker", "build", "--file", context / "Dockerfile", "--tag", artifacts, context],
            timeout=300,
            check=False,
        )
        f.require(
            done.returncode == 0, "Carrier build failed: " + (done.stdout + done.stderr)[-5000:]
        )
        result["artifact_carrier_built"] = True
        result["images"]["artifacts"] = load(artifacts)
        f.run(["docker", "pull", "--platform", "linux/amd64", rollout.GATEWAY_IMAGE], timeout=300)
        gateway_info = json.loads(
            f.run(["docker", "image", "inspect", rollout.GATEWAY_IMAGE]).stdout
        )[0]
        expected = rollout.GATEWAY_IMAGE.split("@", 1)[1]
        f.require(
            any(s.endswith("@" + expected) for s in gateway_info.get("RepoDigests", [])),
            "Gateway registry pin differs",
        )
        gateway = "fraud-kind-serving-gateway:" + identity
        f.run(["docker", "tag", rollout.GATEWAY_IMAGE, gateway])
        result["images"]["gateway"] = load(gateway)
        result["gateway_requested"] = rollout.GATEWAY_IMAGE
        delivery.verify(directory, expected_source_sha, expected_descriptor_sha256)
    except BaseException as exc:
        result["reason"] = str(exc)[-3000:]
        f.private_json(destination / "failure.json", result)
        raise
    finally:
        if imported:
            # Remove only the archive tag introduced here, if loading created it.
            present = f.run(["docker", "image", "inspect", original_tag], check=False)
            if present.returncode == 0:
                cleaned = f.run(["docker", "image", "rm", original_tag], check=False)
                f.require(
                    cleaned.returncode == 0,
                    "Owned archive tag cleanup failed; preserve preparation",
                )
    f.private_json(work() / "prepared.json", result)
    return {
        "status": "qualified_runtime_and_release_carrier_loaded",
        "record": str(work() / "prepared.json"),
        "source_commit": expected_source_sha,
    }


def plan():
    value = json.loads(f.managed_destination(work() / "prepared.json", "storage").read_text())
    f.require(
        value.get("schema") == "fraud_kind_serving_preparation_v1", "Invalid serving preparation"
    )
    source_compatibility.accept(value["source_pins"], pins(), f.ROOT)
    f.require(
        value["node_uid"] == f.owner()["node_uid"],
        "Serving sources or node changed; preserve preparation",
    )
    directory = f.managed_destination(Path(value["directory"]), "storage")
    for slot in ["blue", "green"]:
        checks.read_release(directory / slot, value["releases"][slot]["manifest_sha256"])
        f.require(
            checks.hashes(directory / slot) == value["releases"][slot]["files"],
            "Staged release changed",
        )
    delivery.verify(
        Path(value["source_directory"]), value["source_commit"], value["descriptor_sha256"]
    )
    f.require(
        value["images"]["runtime"]["docker_id"] == value["qualified_runtime_image_id"],
        "Runtime is not the qualified image",
    )
    descriptor = json.loads((Path(value["source_directory"]) / "delivery.json").read_text())
    blue, green = value["releases"]["blue"], value["releases"]["green"]
    f.require(
        value["qualified_runtime_image_id"] == descriptor["image"]["id"]
        and blue["manifest_sha256"] == descriptor["release_manifest_sha256"]
        and blue["manifest"]["model_sha256"] == descriptor["model_sha256"]
        and blue["manifest"]["serving_release_id"] == descriptor["serving_release_id"],
        "Preparation differs from the externally pinned delivery",
    )
    f.require(
        all(
            blue["files"][name] == green["files"][name]
            for name in ["pipeline.skops", "smoke.json"]
        )
        and blue["manifest"]["serving_release_id"] != green["manifest"]["serving_release_id"],
        "Candidate is not a same-weights operational repackage",
    )
    validate_images(value)
    return value


@contextmanager
def forward(pod):
    f.require(
        re.fullmatch(r"fraud-serving-gateway-[a-z0-9-]+", pod),
        "Only the owned gateway may be forwarded",
    )
    args = [
        "kubectl",
        "--kubeconfig",
        str(f.workspace() / "kubeconfig"),
        "--cache-dir",
        str(f.workspace() / "kube-cache"),
        "--context",
        f.CONTEXT,
        "--namespace",
        f.NAMESPACE,
        "port-forward",
        "--address",
        "127.0.0.1",
        "pod/" + pod,
        ":8080",
    ]
    with subprocess.Popen(
        args, cwd=f.ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    ) as process:
        ready = Queue(maxsize=1)

        def drain():
            for line in iter(process.stdout.readline, b""):
                match = re.search(rb"Forwarding from 127\.0\.0\.1:([0-9]+) -> 8080", line)
                if match and ready.empty():
                    ready.put_nowait("http://127.0.0.1:" + match[1].decode())

        reader = Thread(target=drain, daemon=True)
        reader.start()
        try:
            try:
                url = ready.get(timeout=30)
            except Empty as exc:
                raise ValueError("Gateway port-forward failed") from exc
            f.require(process.poll() is None, "Gateway port-forward exited")
            yield url
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            reader.join(timeout=5)
