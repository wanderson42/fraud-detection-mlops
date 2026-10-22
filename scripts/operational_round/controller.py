"""Operate one new Bronze-to-champion cycle on the owned KinD platform."""

import argparse
import json
from pathlib import Path
import subprocess
import time
from uuid import uuid4

from mlflow import MlflowClient
from scripts.operational_round import policy as p
from scripts.platform import kind_datasets, kind_pipeline, kind_tracking
from scripts.platform import kind_foundation as f
from scripts.storage import gold_archive

ROOT = f.ROOT
CHART = ROOT / "infra/helm/fraud-round-training"


def work(key):
    path = f.managed_destination(f.workspace() / "rounds" / p.round_id(key), "storage")
    return path


def pins():
    names = {
        "poetry.lock",
        "pyproject.toml",
        "README.md",
        ".dockerignore",
        "docker/serving/Dockerfile",
        "infra/serving/images.lock.json",
        "infra/kind/serving/Dockerfile.artifacts",
        "references/persistence_catalogue_v1.json",
        "infra/observability/storage-requirements.txt",
        "scripts/platform/kind_foundation.py",
        "scripts/platform/kind_tracking.py",
        "scripts/platform/kind_datasets.py",
        "scripts/platform/kind_datasets_worker.py",
        "scripts/platform/kind_pipeline.py",
        "scripts/platform/kind_pipeline_compute.py",
        "scripts/platform/kind_pipeline_transport.py",
        "scripts/platform/kind_serving.py",
        "scripts/serving/delivery_gate.py",
        "scripts/serving/check_docker_runtime.py",
        "references/operational_round_policy_v1.json",
        "references/baseline_protocol_v1.json",
        "references/temporal_protocol_v1.json",
        "references/handbook_source.json",
        "references/gold_contract_v1.json",
        "references/silver_contract_v1.json",
    }
    for folder in [
        "scripts/operational_round",
        "fraud_detection_mlops",
        "infra/kind/round",
        "infra/helm/fraud-round-training",
        "infra/helm/fraud-serving",
        "infra/helm/fraud-data-jobs",
        "infra/kind/pipeline",
        "scripts/storage",
    ]:
        names.update(
            v.relative_to(ROOT).as_posix()
            for v in (ROOT / folder).rglob("*")
            if v.is_file() and "__pycache__" not in v.parts and v.suffix != ".pyc"
        )
    return {name: p.sha(ROOT / name) for name in sorted(names)}


def committed():
    values = pins()
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    for name, value in values.items():
        data = subprocess.run(
            ["git", "show", "HEAD:" + name], cwd=ROOT, capture_output=True, check=False
        )
        import hashlib

        p.require(
            data.returncode == 0 and hashlib.sha256(data.stdout).hexdigest() == value,
            "Commit the reviewed round implementation/policy before init: " + name,
        )
    return commit, values


def read(key):
    value = json.loads((work(key) / "round.json").read_bytes())
    p.require(
        value["source_pins"] == pins()
        and value["policy"] == p.load()
        and value["node_uid"] == f.owner()["node_uid"]
        and value["tracking_deployment_uid"] == kind_tracking.inventory()["deployment_uid"],
        "Round code, policy, node or tracking ownership changed",
    )
    return value


def init(key):
    commit, source_pins = committed()
    own = f.owner()
    tracking = kind_tracking.inventory()
    path = work(key)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    p.require(path.stat().st_mode & 0o077 == 0, "Round workspace must be private")
    if (path / "round.json").exists():
        read(key)
        return {"status": "round_already_initialized", "round_id": key}
    p.require(not any(path.iterdir()), "Incomplete round workspace; preserve and inspect")
    policy = p.load()
    with kind_tracking.port_forward() as uri:
        client = MlflowClient(tracking_uri=uri, registry_uri=uri)
        existing = list(client.search_registered_models(filter_string="name = 'fraud-detector'"))
        p.require(
            not existing,
            "First canonical round requires a fresh fraud-detector Registry; preserve existing task",
        )
    value = {
        "schema": "fraud_operational_round_v1",
        "round_id": key,
        "owner_id": uuid4().hex,
        "implementation_commit": commit,
        "source_pins": source_pins,
        "policy": policy,
        "policy_sha256": p.digest(policy),
        "node_uid": own["node_uid"],
        "tracking_deployment_uid": tracking["deployment_uid"],
        "initial_champion_alias": None,
    }
    f.private_json(path / "round.json", value)
    return {
        "status": "round_initialized",
        "round_id": key,
        "implementation_commit": commit,
        "historical_models_used": False,
        "fit_executed": False,
    }


def save(key, name, value):
    path = work(key) / name
    if path.exists():
        p.require(
            json.loads(path.read_bytes()) == value, "Receipt already exists with another identity"
        )
    else:
        f.private_json(path, value)
    return value


def data(key):
    read(key)
    if (work(key) / "data.json").exists():
        value = json.loads((work(key) / "data.json").read_bytes())
        verified_gold(key)
        return value
    # The data producers and their pinned images are reused unchanged.
    kind_pipeline.build()
    result = kind_pipeline.rehearse()
    p.require(
        result["status"] == "success"
        and result["features_recomputed"]
        and result["stages"]["silver"]["result"]["input_bundle_id"]
        == result["source_bronze_bundle_id"]
        and result["stages"]["gold"]["result"]["input_bundle_id"]
        == result["stages"]["silver"]["result"]["output_bundle_id"],
        "New Bronze/Silver/Gold Job lineage incomplete",
    )
    return save(key, "data.json", result)


def verified_gold(key):
    result = json.loads((work(key) / "data.json").read_bytes())
    p.require(
        result["status"] == "success" and result["features_recomputed"], "New data chain required"
    )
    stage = result["stages"]["gold"]
    directory = f.managed_destination(
        Path(stage["independent_restore"]["restored_directory"]), "storage"
    )
    identity = stage["result"]["output_bundle_id"]
    p.require(p.sha(directory / "manifest.json") == identity, "New Gold bundle identity differs")
    document = json.loads((directory / "manifest.json").read_bytes())
    checked = gold_archive.verify_directory(directory, document)
    return directory, identity, Path(checked["gold_path"]), result


def build(key):
    read(key)
    if (work(key) / "trainer-image.json").exists():
        return trainer_image(key)
    tag = "fraud-round-trainer:" + uuid4().hex
    image_command(
        ["docker", "build", "--file", "infra/kind/round/Dockerfile", "--tag", tag, "."],
        timeout=1200,
    )
    loaded = load_image(tag)
    return save(
        key,
        "trainer-image.json",
        {
            "image": tag,
            "source_pins": pins(),
            "node_uid": f.owner()["node_uid"],
            "identity": loaded["id"],
            "loaded_identities": [loaded["id"], *loaded.get("repoDigests", [])],
        },
    )


def load_image(tag):
    # Use a single-platform export, avoiding incomplete Docker manifest indexes.
    from tempfile import TemporaryDirectory

    with TemporaryDirectory(prefix="round-image-", dir=f.workspace()) as tmp:
        archive_path = Path(tmp) / "image.tar"
        image_command(
            ["docker", "image", "save", "--platform=linux/amd64", "--output", archive_path, tag],
            timeout=300,
        )
        image_command(
            ["kind", "load", "image-archive", "--name", f.CLUSTER, archive_path], timeout=300
        )
    return cri(tag)


def image_command(args, *, timeout):
    result = f.run(args, timeout=timeout, check=False)
    p.require(
        result.returncode == 0,
        "Image command failed ("
        + " ".join(map(str, args[:3]))
        + "):\n"
        + (result.stdout + result.stderr)[-6000:],
    )
    return result


def cri(tag):
    return json.loads(
        f.run(
            [
                "docker",
                "exec",
                f.CLUSTER + "-control-plane",
                "crictl",
                "--runtime-endpoint",
                "unix:///run/containerd/containerd.sock",
                "inspecti",
                tag,
            ]
        ).stdout
    )["status"]


def trainer_image(key):
    value = json.loads((work(key) / "trainer-image.json").read_bytes())
    loaded = cri(value["image"])
    p.require(
        value["source_pins"] == pins()
        and value["node_uid"] == f.owner()["node_uid"]
        and kind_tracking.image_digest(value["identity"])
        == kind_tracking.image_digest(loaded["id"]),
        "Trainer image identity changed",
    )
    return value


def parse_result(raw):
    from scripts.operational_round.training import MARKER

    matches = [line[len(MARKER) :] for line in raw.splitlines() if line.startswith(MARKER)]
    p.require(
        len(matches) == 1, "Exactly one bounded training result required; inspect owned Job logs"
    )
    return json.loads(matches[0])


def check_job(job, pod, owner_id, images):
    p.require(
        job["metadata"].get("annotations", {}).get("fraud.round/owner-id") == owner_id,
        "Training Job owner differs",
    )
    refs = pod["metadata"].get("ownerReferences", [])
    p.require(
        len(refs) == 1
        and refs[0].get("controller")
        and refs[0]["kind"] == "Job"
        and refs[0]["uid"] == job["metadata"]["uid"],
        "Training pod does not belong to Job",
    )
    p.require(
        pod["spec"]["automountServiceAccountToken"] is False
        and pod["spec"]["enableServiceLinks"] is False
        and not any("hostPath" in v for v in pod["spec"]["volumes"]),
        "Unexpected training pod privileges",
    )
    for field, statuses, name in (
        ("containers", "containerStatuses", "train"),
        ("initContainers", "initContainerStatuses", "download"),
    ):
        containers = pod["spec"][field]
        p.require(
            len(containers) == 1 and containers[0]["name"] == name,
            "Unexpected training pod containers",
        )
        expected = images[name]
        p.require(containers[0]["image"] == expected["image"], "Training pod image tag differs")
        actual = next(v["imageID"] for v in pod["status"][statuses] if v["name"] == name)
        p.require(
            kind_tracking.image_digest(actual)
            in {kind_tracking.image_digest(v) for v in expected["loaded_identities"]},
            "Training pod runtime image differs",
        )
        if name == "train":
            p.require(
                not any(
                    v["name"].startswith(("AWS_", "PG")) or v["name"] == "MLFLOW_BACKEND_STORE_URI"
                    for v in containers[0].get("env", [])
                ),
                "Training pod received storage/database credentials",
            )
    return {
        "job_uid": job["metadata"]["uid"],
        "pod_uid": pod["metadata"]["uid"],
        "pod": pod["metadata"]["name"],
        "image_identities_verified": True,
    }


def train(key):
    round_info = read(key)
    if (work(key) / "training.json").exists():
        return json.loads((work(key) / "training.json").read_bytes())
    _, gold_id, _, data_result = verified_gold(key)
    images = kind_pipeline.images()
    own = kind_datasets.initialize()
    image = trainer_image(key)
    release = "fraud-train-" + round_info["owner_id"][:12]
    plan = {
        **round_info,
        "provenance": {
            "implementation_commit": round_info["implementation_commit"],
            "code_pins_sha256": p.digest(round_info["source_pins"]),
            "poetry_lock_sha256": p.sha(ROOT / "poetry.lock"),
            "training_requirements_sha256": p.sha(ROOT / "infra/kind/round/requirements.txt"),
            "bronze_bundle_id": data_result["source_bronze_bundle_id"],
            "silver_bundle_id": data_result["stages"]["silver"]["result"]["output_bundle_id"],
            "gold_bundle_id": gold_id,
            "gold_manifest_uri": "s3://fraud-datasets/gold/v1/" + gold_id + "/manifest.json",
            "pipeline_run_id": data_result["run_id"],
            "pipeline_receipt_sha256": p.sha(work(key) / "data.json"),
        },
    }
    values = {
        "ownerId": round_info["owner_id"],
        "roundId": key,
        "trainerImage": image["image"],
        "transportImage": images["images"]["transport"]["image"],
        "datasetOwner": own["owner_id"],
        "goldBundleId": gold_id,
        "planJSON": json.dumps(plan),
        "transportScript": (ROOT / "infra/kind/round/transport.py").read_text(),
    }
    path = work(key) / "job-values.json"
    if path.exists():
        p.require(json.loads(path.read_bytes()) == values, "Existing Job plan differs")
    else:
        f.private_json(path, values)
    current = f.kube("get", "job", release, "--ignore-not-found", "-o", "json").stdout.strip()
    if current:
        job = json.loads(current)
        p.require(
            job["metadata"].get("annotations", {}).get("fraud.round/owner-id")
            == round_info["owner_id"],
            "Foreign training Job",
        )
    else:
        f.helm(
            "upgrade",
            "--install",
            release,
            CHART,
            "--values",
            path,
            "--timeout",
            "90s",
            timeout=120,
        )
    deadline = time.monotonic() + 1850
    next_progress = 0
    try:
        while True:
            job = json.loads(f.kube("get", "job", release, "-o", "json").stdout)
            if job.get("status", {}).get("succeeded") or job.get("status", {}).get("failed"):
                break
            if time.monotonic() >= next_progress:
                print("Waiting for owned training Job " + release + "...", flush=True)
                next_progress = time.monotonic() + 30
            p.require(
                time.monotonic() < deadline, "Training deadline reached; preserve Job and inspect"
            )
            time.sleep(2)
        raw = f.kube("logs", "job/" + release, "-c", "train", timeout=60).stdout
        p.require(len(raw.encode()) <= 2 * 1024 * 1024, "Training result exceeds metadata budget")
        value = parse_result(raw)
        p.require(
            job.get("status", {}).get("succeeded") == 1 and value["status"] == "qualified",
            "Training round rejected or failed: " + json.dumps(value.get("gate", value))[-1500:],
        )
        p.require(
            value["round_id"] == key
            and value["policy_sha256"] == round_info["policy_sha256"]
            and value["provenance"]["gold_bundle_id"] == gold_id,
            "Training result identity differs",
        )
        pods = json.loads(f.kube("get", "pods", "-l", "job-name=" + release, "-o", "json").stdout)[
            "items"
        ]
        p.require(len(pods) == 1, "Expected one owned training pod")
        execution = check_job(
            job,
            pods[0],
            round_info["owner_id"],
            {"train": image, "download": images["images"]["transport"]},
        )
        value["job_identity"] = {
            "name": release,
            "uid": job["metadata"]["uid"],
            "image": image,
            "execution": execution,
        }
        # Keep the exact report bytes emitted by the Job; execution metadata has a separate receipt.
        job_metadata = value.pop("job_identity")
        save(key, "training-job.json", job_metadata)
        save(key, "training.json", value)
        f.helm("uninstall", release, "--wait", "--timeout", "90s", timeout=120)
        return value
    except (ValueError, OSError) as exc:
        failure = {
            "status": "failed",
            "reason": str(exc),
            "job": release,
            "diagnostics": f.kube("describe", "job", release, check=False).stdout[-4000:],
        }
        f.private_json(work(key) / ("training-failure-" + uuid4().hex + ".json"), failure)
        raise


def batches(key):
    from fraud_detection_mlops.modeling.experiments.candidate_training import load_split

    _, _, gold, _ = verified_gold(key)
    manifest = json.loads((gold / "manifest.json").read_bytes())
    parts = {
        split: [row for row in manifest["files"] if row["split"] == split]
        for split in ("train", "validation")
    }
    return load_split(gold, manifest, "train"), load_split(gold, manifest, "validation"), parts


def promote(key, no_other_writers):
    from scripts.operational_round import promotion

    p.require(no_other_writers, "Declare --no-other-writers for Registry writes")
    info = read(key)
    summary = training_result(key)
    train_data, val, parts = batches(key)
    with kind_tracking.port_forward() as uri:
        result = promotion.promote(
            summary,
            uri,
            val,
            expected_alias=info["initial_champion_alias"],
            owner_id=info["owner_id"],
            training=train_data,
            partitions=parts,
        )
    return save(key, "promotion.json", result)


def training_result(key):
    info = read(key)
    result = json.loads((work(key) / "training.json").read_bytes())
    _, gold_id, gold, chain = verified_gold(key)
    require_provenance = {
        "implementation_commit": info["implementation_commit"],
        "code_pins_sha256": p.digest(info["source_pins"]),
        "poetry_lock_sha256": p.sha(ROOT / "poetry.lock"),
        "training_requirements_sha256": p.sha(ROOT / "infra/kind/round/requirements.txt"),
        "bronze_bundle_id": chain["source_bronze_bundle_id"],
        "silver_bundle_id": chain["stages"]["silver"]["result"]["output_bundle_id"],
        "gold_bundle_id": gold_id,
        "gold_manifest_uri": "s3://fraud-datasets/gold/v1/" + gold_id + "/manifest.json",
        "pipeline_run_id": chain["run_id"],
        "pipeline_receipt_sha256": p.sha(work(key) / "data.json"),
        "gold_dataset_manifest_sha256": p.sha(gold / "manifest.json"),
    }
    p.require(
        result["round_id"] == key
        and result["policy"] == info["policy"]
        and result["policy_sha256"] == info["policy_sha256"]
        and result["provenance"] == require_provenance,
        "Training receipt does not bind this round and its new data chain",
    )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=[
            "validate",
            "init",
            "data",
            "build",
            "train",
            "promote",
            "deploy",
            "verify",
            "rehearse",
            "api",
        ],
    )
    parser.add_argument("--round-id", default="portfolio-v1")
    parser.add_argument("--no-other-writers", action="store_true")
    args = parser.parse_args()
    try:
        if args.action == "validate":
            p.load()
            f.run(["helm", "lint", "--strict", CHART])
            print(
                json.dumps(
                    {
                        "status": "policy_and_training_templates_validated",
                        "native_round_validated": False,
                    }
                )
            )
            return
        with f.exclusive():
            if args.action in {"deploy", "verify", "rehearse", "api"}:
                from scripts.operational_round import serving

                result = getattr(serving, args.action)(args.round_id)
            elif args.action == "promote":
                result = promote(args.round_id, args.no_other_writers)
            else:
                result = globals()[args.action](args.round_id)
        print(json.dumps(result, indent=2, allow_nan=False))
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as exc:
        parser.exit(1, "Operational round stopped: " + str(exc)[-3000:] + "\n")


if __name__ == "__main__":
    main()
