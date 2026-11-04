"""Resume explicitly identified Silver/Gold Jobs; never adopt an ambiguous attempt."""

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from uuid import uuid4

from scripts.platform import kind_datasets as datasets
from scripts.platform import kind_foundation as f
from scripts.platform import kind_pipeline as k
from scripts.storage import archive
import yaml

STAGES = ("silver", "gold")


def directory(run_id):
    f.require(
        bool(re.fullmatch(r"[a-f0-9]{32}", run_id)), "Use a 32-character hex pipeline run ID"
    )
    return f.managed_destination(k.work() / "runs" / run_id, "storage")


def load(path):
    f.managed_destination(path, "storage")
    f.require(path.is_file() and 0 < path.stat().st_size <= 4 * 1024 * 1024, "Invalid run receipt")
    f.require(path.stat().st_mode & 0o077 == 0, "Run receipts must be private")
    return json.loads(path.read_bytes())


def seal(path, value):
    """Publish a complete immutable receipt atomically, including after process death."""
    f.managed_destination(path, "storage")
    if path.exists():
        f.require(load(path) == value, "Existing run receipt differs: " + path.name)
        return
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".pending-")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def locked(run_id):
    root = directory(run_id)
    for path in (root.parent, root):
        f.managed_destination(path, "storage")
        path.mkdir(mode=0o700, exist_ok=True)
        f.require(path.stat().st_mode & 0o077 == 0, "Pipeline run directory must be private")
    target = f.managed_destination(root / "operation.lock", "storage")
    with target.open("a") as stream:
        os.chmod(target, 0o600)
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("Another invocation owns this pipeline run") from exc
        yield root


def pins():
    names = (
        "scripts/platform/pipeline_runs.py",
        "scripts/platform/kind_pipeline.py",
        "scripts/platform/kind_foundation.py",
        "scripts/platform/kind_datasets.py",
        "scripts/platform/source_compatibility.py",
    )
    return {name: archive.digest(f.ROOT / name) for name in names}


def committed():
    result = pins()
    for name, digest in result.items():
        raw = subprocess.run(
            ["git", "show", "HEAD:" + name], cwd=f.ROOT, capture_output=True, check=False
        )
        f.require(
            raw.returncode == 0 and hashlib.sha256(raw.stdout).hexdigest() == digest,
            "Commit reviewed pipeline sources before preparing a run: " + name,
        )
    return result


def resources(release):
    return json.loads(
        f.kube(
            "get", "jobs,pods", "-l", "app.kubernetes.io/instance=" + release, "-o", "json"
        ).stdout
    )["items"]


def helm_records(release):
    return json.loads(
        f.kube("get", "secrets", "-l", "owner=helm,name=" + release, "-o", "json").stdout
    )["items"]


def prepare(run_id, bronze_id=None):
    k.initialize()
    with locked(run_id) as root:
        source_pins = committed()
        if (root / "plan.json").exists():
            plan = checked_plan(root, run_id)
            f.require(
                bronze_id is None or bronze_id == plan["source_bronze_bundle_id"],
                "Existing run uses another Bronze bundle",
            )
            return plan
        f.require(
            not any(p.name != "operation.lock" for p in root.iterdir()),
            "Incomplete run directory; preserve and inspect",
        )
        available = [
            row["bundle_id"] for row in datasets.available()["bundles"] if row["layer"] == "bronze"
        ]
        if bronze_id is None:
            f.require(len(available) == 1, "Select a unique prepared Bronze bundle")
            bronze_id = available[0]
        bronze_id = archive.bundle_id(bronze_id)
        f.require(bronze_id in available, "Prepare the selected Bronze bundle first")
        release = "fraud-chain-" + run_id[:12]
        f.require(
            not resources(release) and not helm_records(release),
            "Run release already exists without a local plan; do not adopt it",
        )
        plan = {
            "schema": "fraud_pipeline_run_plan_v1",
            "run_id": run_id,
            "release": release,
            "source_bronze_bundle_id": bronze_id,
            "owner_id": datasets.initialize()["owner_id"],
            "source_pins": source_pins,
            "images": k.images(),
            "foundation_before": f.inventory(),
        }
        seal(root / "plan.json", plan)
        return plan


def checked_plan(root, run_id):
    plan = load(root / "plan.json")
    f.require(
        plan["schema"] == "fraud_pipeline_run_plan_v1"
        and plan["run_id"] == run_id
        and plan["release"] == "fraud-chain-" + run_id[:12],
        "Pipeline plan identity differs",
    )
    f.require(
        plan["source_pins"] == pins() and plan["images"] == k.images(),
        "Run source/image identity changed; preserve attempt",
    )
    f.require(
        plan["owner_id"] == datasets.initialize()["owner_id"]
        and plan["foundation_before"] == f.inventory(),
        "Run foundation ownership changed",
    )
    archive.bundle_id(plan["source_bronze_bundle_id"])
    return plan


def values(plan, inputs):
    return {
        "runId": plan["run_id"],
        "ownerId": plan["owner_id"],
        "images": {key: row["image"] for key, row in plan["images"]["images"].items()},
        "jobs": [
            {"stage": stage, "inputBundleId": identity} for stage, identity in inputs.items()
        ],
    }


def subset(expected, actual):
    """Accept API defaults but require the complete rendered configuration unchanged."""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and subset(value, actual[key]) for key, value in expected.items()
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(expected) == len(actual)
            and all(subset(a, b) for a, b in zip(expected, actual, strict=True))
        )
    return expected == actual


def check_job(plan, stage, job, rendered):
    metadata = job["metadata"]
    matches = [row for row in rendered if row["metadata"]["name"] == metadata["name"]]
    f.require(len(matches) == 1, "Unexpected rendered Job identity")
    expected = matches[0]
    f.require(
        bool(metadata.get("uid"))
        and subset(expected["metadata"], metadata)
        and subset(expected["spec"], job["spec"]),
        "Pipeline Job plan/ownership differs",
    )
    spec = job["spec"]["template"]["spec"]
    f.require(
        not any(spec.get(key) for key in ("hostNetwork", "hostPID", "hostIPC")),
        "Unexpected host privileges in Job",
    )
    for container in spec["initContainers"] + spec["containers"]:
        f.require(
            not any(container.get(key) for key in ("command", "envFrom", "lifecycle")),
            "Unexpected pipeline container overrides",
        )
    return {
        "run_id": plan["run_id"],
        "stage": stage,
        "job": metadata["name"],
        "uid": metadata["uid"],
    }


def read_job(name):
    raw = f.kube("get", "job", name, "--ignore-not-found", "-o", "json").stdout.strip()
    return json.loads(raw) if raw else None


def ensure_job(root, plan, stage, inputs):
    path = root / ("values-" + stage + ".json")
    seal(path, values(plan, inputs))
    raw = f.run(
        [
            "helm",
            "template",
            plan["release"],
            k.CHART,
            "--values",
            path,
            "--namespace",
            f.NAMESPACE,
            "--kube-version",
            "1.35.8",
        ]
    ).stdout
    rendered = [row for row in yaml.safe_load_all(raw) if row]
    name = plan["release"] + "-" + stage
    job = read_job(name)
    dispatched = root / ("dispatch-" + stage + ".json")
    if job is None:
        f.require(
            not dispatched.exists(),
            "Dispatched Job is absent; ambiguous state, preserve and inspect",
        )
        # Earlier stages must still have their sealed UIDs; never recreate a lost Job.
        for previous in list(inputs)[:-1]:
            row = read_job(plan["release"] + "-" + previous)
            f.require(
                row is not None
                and row["metadata"]["uid"] == load(root / ("job-" + previous + ".json"))["uid"],
                "Earlier Job disappeared or was replaced",
            )
        if helm_records(plan["release"]):
            prior = dict(list(inputs.items())[:-1])
            f.require(
                prior
                and json.loads(f.helm("get", "values", plan["release"], "-o", "json").stdout)
                == values(plan, prior),
                "Helm release values differ; do not upgrade another attempt",
            )
        seal(dispatched, {"values_sha256": archive.digest(path)})
        f.helm(
            "upgrade",
            "--install",
            plan["release"],
            k.CHART,
            "--values",
            path,
            "--timeout",
            "90s",
            timeout=120,
        )
        job = read_job(name)
        f.require(job is not None, "Dispatched Job not visible; preserve and resume")
    else:
        f.require(
            dispatched.exists() and load(dispatched) == {"values_sha256": archive.digest(path)},
            "Job exists without matching dispatch",
        )
    seal(root / ("job-" + stage + ".json"), check_job(plan, stage, job, rendered))
    return name


def verify_stage(root, plan, stage, input_id):
    record = load(root / ("stage-" + stage + ".json"))
    f.require(record["plan_sha256"] == archive.digest(root / "plan.json"), "Stage plan differs")
    output_id = k.validate_stage(
        record["result"], stage, plan["run_id"], input_id, plan["owner_id"]
    )
    saved = load(root / ("job-" + stage + ".json"))
    f.require(
        saved["run_id"] == plan["run_id"]
        and saved["stage"] == stage
        and saved["job"] == plan["release"] + "-" + stage
        and record["job"]["job_uid"] == saved["uid"],
        "Stage Job UID/binding differs",
    )
    restored = record["independent_restore"]
    candidate = f.managed_destination(Path(restored["restored_directory"]), "storage")
    relative = candidate.relative_to(datasets.work())
    f.require(
        len(relative.parts) == 2
        and relative.parts[0].startswith("restore-")
        and relative.parts[1] == "restored",
        "Unexpected independent restore path",
    )
    _, _, verified = datasets.verify_directory(candidate, output_id, datasets.LAYERS[stage])
    f.require(
        k.normalize(verified)
        == k.normalize(record["result"]["computation"]["semantic_verification"]),
        "Saved restored payloads differ from Job output",
    )
    return record, output_id


def check_remaining(root, plan):
    jobs = {
        load(root / ("job-" + stage + ".json"))["job"]: load(root / ("job-" + stage + ".json"))[
            "uid"
        ]
        for stage in STAGES
    }
    remaining = resources(plan["release"])
    if not (root / "cleanup.json").exists():
        actual = {
            row["metadata"]["name"]: row["metadata"]["uid"]
            for row in remaining
            if row["kind"] == "Job"
        }
        f.require(actual == jobs, "A verified Job disappeared before cleanup intent")
    for resource in remaining:
        if resource["kind"] == "Job":
            f.require(
                jobs.get(resource["metadata"]["name"]) == resource["metadata"]["uid"],
                "Foreign Job in run release; no cleanup",
            )
        else:
            f.require(
                resource["kind"] == "Pod"
                and any(
                    ref.get("controller") and ref["uid"] in jobs.values()
                    for ref in resource["metadata"].get("ownerReferences", [])
                ),
                "Foreign pod in run release; no cleanup",
            )


def result_document(root, plan, stages):
    return {
        "schema": "fraud_kind_pipeline_rehearsal_v1",
        "status": "success",
        "phase": "complete",
        "scope": "identified_resumable_Bronze_Silver_Gold_Jobs",
        "run_id": plan["run_id"],
        "release": plan["release"],
        "source_bronze_bundle_id": plan["source_bronze_bundle_id"],
        "report": str(root / "result.json"),
        "controller_sha256": archive.digest(Path(__file__)),
        "plan_sha256": archive.digest(root / "plan.json"),
        "image_receipt": plan["images"],
        "foundation_before": plan["foundation_before"],
        "foundation_after": plan["foundation_before"],
        "foundation_identities_preserved": True,
        "stages": stages,
        "temporary_jobs_removed": True,
        "features_recomputed": True,
        "fit_executed": False,
        "primary_storage_migrated": False,
        "historical_sources_mounted": False,
        "production_promotion": False,
        "project_wide_storage_inventory_complete": False,
        "new_model_quality_evidence": False,
    }


def execute(run_id, *, stop_after=None):
    f.require(stop_after in (None, *STAGES), "Unknown stop-after stage")
    with locked(run_id) as root:
        plan = checked_plan(root, run_id)
        inputs, stages = {}, {}
        input_id = plan["source_bronze_bundle_id"]
        try:
            for stage in STAGES:
                inputs[stage] = input_id
                target = root / ("stage-" + stage + ".json")
                if not target.exists():
                    f.require(
                        not (root / "cleanup.json").exists(), "Incomplete run after cleanup intent"
                    )
                    name = ensure_job(root, plan, stage, inputs)
                    k.await_job(name)
                    job = k.job_inventory(
                        plan["release"], stage, run_id, plan["owner_id"], plan["images"]
                    )
                    raw = f.kube("logs", job["pod"], "-c", "publish").stdout
                    f.require(
                        len(raw.encode()) <= 2 * 1024 * 1024, "Job result exceeds metadata budget"
                    )
                    value = json.loads(raw)
                    output_id = k.validate_stage(value, stage, run_id, input_id, plan["owner_id"])
                    restored = datasets.transfer("restore", output_id, stage)
                    f.require(
                        k.normalize(restored["semantic_verification"])
                        == k.normalize(value["computation"]["semantic_verification"]),
                        "Independent restore differs from Job computation",
                    )
                    seal(
                        target,
                        {
                            "plan_sha256": archive.digest(root / "plan.json"),
                            "job": job,
                            "result": value,
                            "independent_restore": restored,
                        },
                    )
                record, input_id = verify_stage(root, plan, stage, inputs[stage])
                stages[stage] = {
                    key: value for key, value in record.items() if key != "plan_sha256"
                }
                if stop_after == stage and not (root / "result.json").exists():
                    return {
                        "status": "pipeline_run_paused",
                        "run_id": run_id,
                        "after_stage": stage,
                    }
            f.require(plan["foundation_before"] == f.inventory(), "Foundation changed during run")
            expected = result_document(root, plan, stages)
            if (root / "result.json").exists():
                f.require(load(root / "result.json") == expected, "Final pipeline result differs")
                f.require(
                    not resources(plan["release"]) and not helm_records(plan["release"]),
                    "Resources reappeared after completed run",
                )
                return expected
            check_remaining(root, plan)
            seal(root / "cleanup.json", {"plan_sha256": archive.digest(root / "plan.json")})
            if helm_records(plan["release"]):
                f.require(
                    json.loads(f.helm("get", "values", plan["release"], "-o", "json").stdout)
                    == values(plan, inputs),
                    "Helm release values differ; no cleanup",
                )
                f.helm("uninstall", plan["release"], "--wait", "--timeout", "90s", timeout=120)
            k.wait_removed(plan["release"])
            f.require(not helm_records(plan["release"]), "Helm records remain after cleanup")
            seal(root / "result.json", expected)
            return expected
        except BaseException as exc:
            seal(
                root / ("failure-" + uuid4().hex + ".json"),
                {"error_type": type(exc).__name__, "reason": str(exc)[:1000], "run_id": run_id},
            )
            raise


def status(run_id):
    root = directory(run_id)
    plan = load(root / "plan.json")
    return {
        "run_id": run_id,
        "plan_sha256": archive.digest(root / "plan.json"),
        "release": plan["release"],
        "completed_stages": [
            stage for stage in STAGES if (root / ("stage-" + stage + ".json")).exists()
        ],
        "result_present": (root / "result.json").exists(),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "run", "status"))
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--bronze-bundle-id")
    parser.add_argument("--stop-after", choices=STAGES)
    args = parser.parse_args()
    try:
        if args.action == "status":
            result = status(args.run_id)
        else:
            with f.exclusive():
                result = (
                    prepare(args.run_id, args.bronze_bundle_id)
                    if args.action == "prepare"
                    else execute(args.run_id, stop_after=args.stop_after)
                )
        print(json.dumps(result, indent=2, allow_nan=False))
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        parser.exit(1, "Pipeline run stopped: " + str(exc) + "\n")


if __name__ == "__main__":
    main()
