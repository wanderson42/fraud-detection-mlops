"""Coordinate two Helm Jobs on the owned KinD foundation; never replace historical sources."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import time
from uuid import uuid4

from scripts.platform import kind_datasets as datasets
from scripts.platform import kind_foundation as f
from scripts.platform import kind_tracking as tracking
from scripts.storage import archive

CHART = f.ROOT / "infra/helm/fraud-data-jobs"


def work():
    return f.managed_destination(f.workspace() / "pipeline", "storage")


def initialize():
    own = datasets.initialize()
    work().mkdir(mode=0o700, exist_ok=True)
    f.require(work().stat().st_mode & 0o077 == 0, "Pipeline workspace must be private")
    return own


def pins():
    names = {"poetry.lock", "infra/observability/storage-requirements.txt"}
    for directory, suffix in [
        ("fraud_detection_mlops", ".py"),
        ("scripts/storage", ".py"),
        ("infra/kind/pipeline", ""),
        ("infra/helm/fraud-data-jobs", ""),
    ]:
        names.update(
            p.relative_to(f.ROOT).as_posix()
            for p in (f.ROOT / directory).rglob("*")
            if p.is_file() and (not suffix or p.suffix == suffix)
        )
    names.update(
        "scripts/platform/" + n
        for n in [
            "kind_pipeline_compute.py",
            "kind_pipeline_transport.py",
            "kind_datasets_worker.py",
        ]
    )
    names.update(
        "references/" + n
        for n in [
            "handbook_source.json",
            "silver_contract_v1.json",
            "gold_contract_v1.json",
            "temporal_protocol_v1.json",
        ]
    )
    return {n: archive.digest(f.ROOT / n) for n in sorted(names)}


def build():
    initialize()
    path = work() / "images.json"
    if path.exists():
        images()
        return {"status": "pipeline_images_already_loaded", "record": str(path)}
    record = {
        "schema": "fraud_kind_pipeline_images_v1",
        "node_uid": f.owner()["node_uid"],
        "source_pins": pins(),
        "images": {},
        "registry_published": False,
    }
    for role in ["transport", "science"]:
        tag = "fraud-kind-pipeline-" + role + ":" + uuid4().hex
        print("Building and loading the locked " + role + " image...", flush=True)
        completed = f.run(
            [
                "docker",
                "build",
                "--file",
                "infra/kind/pipeline/Dockerfile." + role,
                "--tag",
                tag,
                ".",
            ],
            timeout=900,
            check=False,
        )
        f.require(
            completed.returncode == 0,
            "Pipeline image build failed: " + (completed.stdout + completed.stderr)[-6000:],
        )
        docker = json.loads(f.run(["docker", "image", "inspect", tag]).stdout)[0]
        f.run(["kind", "load", "docker-image", "--name", f.CLUSTER, tag], timeout=300)
        loaded = json.loads(
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
        f.require(
            tag in {n.removeprefix("docker.io/library/") for n in loaded.get("repoTags", [])},
            "Loaded pipeline image tag differs",
        )
        record["images"][role] = {
            "image": tag,
            "docker_id": docker["Id"],
            "loaded_identities": [loaded["id"], *loaded.get("repoDigests", [])],
        }
    f.private_json(path, record)
    return {
        "status": "pipeline_images_built_and_loaded",
        "images": record["images"],
        "record": str(path),
    }


def images():
    record = json.loads(f.managed_destination(work() / "images.json", "storage").read_text())
    f.require(
        record.get("schema") == "fraud_kind_pipeline_images_v1"
        and record["source_pins"] == pins(),
        "Pipeline image inputs changed; preserve receipts for review",
    )
    f.require(
        record["node_uid"] == f.owner()["node_uid"], "Pipeline images belong to another node"
    )
    f.require(set(record["images"]) == {"science", "transport"}, "Incomplete image receipt")
    for role, row in record["images"].items():
        f.require(
            re.fullmatch(r"fraud-kind-pipeline-" + role + r":[a-f0-9]{32}", row["image"]),
            "Invalid pipeline image tag",
        )
        f.require(bool(row["loaded_identities"]), "Missing pipeline runtime image identities")
        loaded = json.loads(
            f.run(
                [
                    "docker",
                    "exec",
                    f.CLUSTER + "-control-plane",
                    "crictl",
                    "--runtime-endpoint",
                    "unix:///run/containerd/containerd.sock",
                    "inspecti",
                    row["image"],
                ]
            ).stdout
        )["status"]
        f.require(
            row["image"]
            in {tag.removeprefix("docker.io/library/") for tag in loaded.get("repoTags", [])},
            "Pipeline image is not loaded under its recorded tag",
        )
        f.require(
            tracking.image_digest(loaded["id"])
            in {tracking.image_digest(v) for v in row["loaded_identities"]},
            "Loaded pipeline image changed",
        )
    return record


def validate():
    f.run(["helm", "lint", "--strict", CHART, "--kube-version", "1.35.8"])
    f.run(
        [
            "helm",
            "template",
            "fraud-chain-validation",
            CHART,
            "--namespace",
            f.NAMESPACE,
            "--kube-version",
            "1.35.8",
        ]
    )
    return {"status": "pipeline_templates_validated", "native_jobs_validated": False}


def job_inventory(release, stage, run_id, owner_id, image_record):
    name = release + "-" + stage
    job = json.loads(f.kube("get", "job", name, "-o", "json").stdout)
    annotations = job["metadata"].get("annotations", {})
    f.require(
        annotations.get("fraud.pipeline/run-id") == run_id
        and annotations.get("fraud.pipeline/owner-id") == owner_id
        and job["metadata"].get("labels", {}).get("app.kubernetes.io/instance") == release,
        "Job ownership differs",
    )
    pods = json.loads(
        f.kube("get", "pods", "-l", "batch.kubernetes.io/job-name=" + name, "-o", "json").stdout
    )["items"]
    f.require(len(pods) == 1, "Expected exactly one pipeline pod without retries")
    pod = pods[0]
    f.require(
        any(
            r.get("controller") and r["uid"] == job["metadata"]["uid"]
            for r in pod["metadata"].get("ownerReferences", [])
        ),
        "Pod belongs to another Job",
    )
    f.require(pod["status"].get("phase") == "Succeeded", "Pipeline pod did not succeed")
    expected = {"download": "transport", "compute": "science", "publish": "transport"}
    containers = {c["name"]: c for c in pod["spec"]["initContainers"] + pod["spec"]["containers"]}
    states = {
        c["name"]: c
        for c in pod["status"].get("initContainerStatuses", [])
        + pod["status"].get("containerStatuses", [])
    }
    f.require(set(containers) == set(states) == set(expected), "Pipeline containers differ")
    for name, role in expected.items():
        row = image_record["images"][role]
        f.require(containers[name]["image"] == row["image"], "Job image tag differs")
        f.require(
            states[name].get("state", {}).get("terminated", {}).get("exitCode") == 0,
            "Job container failed",
        )
        f.require(
            tracking.image_digest(states[name]["imageID"])
            in {tracking.image_digest(v) for v in row["loaded_identities"]},
            "Job runtime image identity differs",
        )
    return {
        "job": job["metadata"]["name"],
        "job_uid": job["metadata"]["uid"],
        "pod": pod["metadata"]["name"],
        "pod_uid": pod["metadata"]["uid"],
        "images": {n: c["imageID"] for n, c in states.items()},
    }


def await_job(name):
    deadline = time.monotonic() + 1860
    announce = 0
    while time.monotonic() < deadline:
        job = json.loads(f.kube("get", "job", name, "-o", "json").stdout)
        conditions = job.get("status", {}).get("conditions", [])
        if any(
            c["type"] in {"Failed", "FailureTarget"} and c["status"] == "True" for c in conditions
        ):
            raise ValueError("Job failed: " + name + "; resources preserved, inspect the report")
        if any(c["type"] == "Complete" and c["status"] == "True" for c in conditions):
            return
        if time.monotonic() >= announce:
            print("Waiting for " + name + "...", flush=True)
            announce = time.monotonic() + 30
        time.sleep(2)
    raise ValueError("Job deadline exceeded; resources preserved")


def diagnostic(release):
    pods = json.loads(
        f.kube(
            "get", "pods", "-l", "app.kubernetes.io/instance=" + release, "-o", "json", check=False
        ).stdout
        or '{"items": []}'
    )["items"]
    values = []
    for pod in pods:
        row = {
            "pod": pod["metadata"]["name"],
            "phase": pod.get("status", {}).get("phase"),
            "init_states": pod.get("status", {}).get("initContainerStatuses", []),
            "container_states": pod.get("status", {}).get("containerStatuses", []),
            "logs": {},
        }
        for container in ["download", "compute", "publish"]:
            raw = f.kube("logs", row["pod"], "-c", container, "--tail=30", check=False).stdout
            row["logs"][container] = raw[-5000:]
        values.append(row)
    return values


def wait_removed(release):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        remaining = json.loads(
            f.kube(
                "get", "jobs,pods", "-l", "app.kubernetes.io/instance=" + release, "-o", "json"
            ).stdout
        )["items"]
        if not remaining:
            return
        time.sleep(1)
    raise ValueError("Temporary pipeline resources remain; inspect the dedicated release")


def normalize(semantic):
    return {k: v for k, v in semantic.items() if k not in {"silver_path", "gold_path"}}


def validate_stage(value, stage, run_id, input_id, owner_id):
    f.require(
        value.get("schema") == "fraud_kind_pipeline_stage_v1"
        and value.get("status") == "published_verified"
        and value.get("stage") == stage
        and value.get("run_id") == run_id
        and value.get("input_bundle_id") == input_id
        and value.get("dataset_owner_id") == owner_id,
        "Published receipt belongs to another Job",
    )
    output = archive.bundle_id(value["output_bundle_id"])
    computation = value["computation"]
    f.require(
        computation.get("status") == "computed_verified"
        and computation["run_id"] == run_id
        and computation["stage"] == stage
        and computation["input_bundle_id"] == input_id
        and computation["output_bundle_id"] == output,
        "Producer receipt binding differs",
    )
    reference_names = {
        "references/handbook_source.json",
        "references/silver_contract_v1.json",
        "references/gold_contract_v1.json",
        "references/temporal_protocol_v1.json",
    }
    f.require(
        set(computation["reference_hashes"]) == reference_names,
        "Unexpected producer reference paths",
    )
    f.require(
        computation["reference_hashes"]
        == {n: archive.digest(f.ROOT / n) for n in reference_names},
        "Producer reference pins differ",
    )
    f.require(
        computation.get("input_bundle_unchanged") is True,
        "Producer input preservation not confirmed",
    )
    f.require(
        computation["source_worker_sha256"]
        == archive.digest(f.ROOT / "scripts/platform/kind_pipeline_compute.py"),
        "Producer worker source differs",
    )
    publication = value["publication"]
    f.require(
        publication.get("status") == "success"
        and publication["bundle_id"] == output
        and publication["bucket"] == "fraud-datasets"
        and publication["prefix"] == stage + "/v1/" + output + "/",
        "Output publication differs",
    )
    raw = archive.encoded(
        {
            k: v
            for k, v in value.items()
            if k not in {"remote_receipt_key", "remote_receipt_sha256"}
        }
    )
    f.require(
        value["remote_receipt_sha256"] == hashlib.sha256(raw).hexdigest()
        and value["remote_receipt_key"] == f"platform-jobs/v1/{run_id}/{stage}/receipt.json",
        "Remote completion envelope differs",
    )
    return output


def rehearse():
    own = initialize()
    image_record = images()
    before = f.inventory()
    candidates = [r for r in datasets.available()["bundles"] if r["layer"] == "bronze"]
    f.require(len(candidates) == 1, "Select a unique prepared Bronze bundle before running Jobs")
    input_id = candidates[0]["bundle_id"]
    run_id = uuid4().hex
    release = "fraud-chain-" + run_id[:12]
    result = {
        "schema": "fraud_kind_pipeline_rehearsal_v1",
        "status": "failed",
        "scope": "isolated_Bronze_Silver_Gold_Helm_Jobs_and_S3_outputs_not_primary_cutover_or_model_evaluation",
        "run_id": run_id,
        "release": release,
        "source_bronze_bundle_id": input_id,
        "report": str(work() / ("rehearsal-" + run_id + ".json")),
        "controller_sha256": archive.digest(Path(__file__)),
        "image_receipt": image_record,
        "foundation_before": before,
        "stages": {},
        "temporary_jobs_removed": False,
        "fit_executed": False,
        "features_recomputed": None,
        "primary_storage_migrated": False,
        "historical_sources_mounted": False,
        "production_promotion": False,
        "project_wide_storage_inventory_complete": False,
        "new_model_quality_evidence": False,
    }
    values = {
        "runId": run_id,
        "ownerId": own["owner_id"],
        "images": {n: r["image"] for n, r in image_record["images"].items()},
        "jobs": [],
    }
    touched = False
    try:
        validate()
        existing = json.loads(
            f.kube(
                "get",
                "secrets,jobs,pods",
                "-l",
                "app.kubernetes.io/instance=" + release,
                "-o",
                "json",
            ).stdout
        )["items"]
        helm_records = json.loads(
            f.kube("get", "secrets", "-l", "owner=helm,name=" + release, "-o", "json").stdout
        )["items"]
        f.require(
            not existing and not helm_records, "Pipeline release already exists; do not adopt it"
        )
        for stage in ["silver", "gold"]:
            result["phase"] = stage
            values["jobs"].append({"stage": stage, "inputBundleId": input_id})
            path = work() / ("values-" + run_id + "-" + stage + ".json")
            f.private_json(path, values)
            touched = True
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
            await_job(release + "-" + stage)
            job = job_inventory(release, stage, run_id, own["owner_id"], image_record)
            raw = f.kube("logs", job["pod"], "-c", "publish").stdout
            f.require(len(raw.encode()) <= 2 * 1024 * 1024, "Job result exceeds metadata budget")
            value = json.loads(raw)
            output_id = validate_stage(value, stage, run_id, input_id, own["owner_id"])
            # Separate host-side download; the Job output emptyDir is never mounted here.
            restored = datasets.transfer("restore", output_id, stage)
            f.require(
                normalize(restored["semantic_verification"])
                == normalize(value["computation"]["semantic_verification"]),
                "Independent restore differs from Job computation",
            )
            result["stages"][stage] = {
                "job": job,
                "result": value,
                "independent_restore": restored,
            }
            if stage == "gold":
                result["features_recomputed"] = True
            input_id = output_id
        result["foundation_after"] = f.inventory()
        f.require(
            before == result["foundation_after"],
            "Foundation identities changed during pipeline Jobs",
        )
        result["foundation_identities_preserved"] = True
        # Only this random Helm release is removed; immutable S3 outputs and host receipts remain.
        f.helm("uninstall", release, "--wait", "--timeout", "90s", timeout=120)
        wait_removed(release)
        result.update(status="success", phase="complete", temporary_jobs_removed=True)
        return result
    except BaseException as exc:
        result.update(error_type=type(exc).__name__, reason=str(exc)[:1000])
        if touched:
            try:
                result["diagnostics"] = diagnostic(release)
            except Exception as diagnostic_error:  # noqa: BLE001 — do not mask the original failure
                result["diagnostic_error_type"] = type(diagnostic_error).__name__
        raise
    finally:
        f.private_json(Path(result["report"]), result)
        print("Relatório dos Jobs: " + result["report"], flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["validate", "build", "rehearse"])
    args = parser.parse_args()
    try:
        with f.exclusive():
            result = {"validate": validate, "build": build, "rehearse": rehearse}[args.command]()
        print(json.dumps(result, indent=2, allow_nan=False))
    except (OSError, ValueError) as exc:
        parser.exit(1, f"KinD pipeline stopped: {exc}\\n")


if __name__ == "__main__":
    main()
