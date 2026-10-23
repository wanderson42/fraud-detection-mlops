"""Qualify an idempotent release carrier and maintain the same serving identity."""

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from uuid import uuid4

from scripts.model_updates import controller as updates
from scripts.model_updates import deployment
from scripts.operational_round import controller as original
from scripts.operational_round import policy as p
from scripts.platform import kind_foundation as f
from scripts.platform import kind_tracking, source_compatibility


def work(key):
    return f.managed_destination(
        f.workspace() / "serving-maintenance" / p.round_id(key), "storage"
    )


def current():
    return json.loads(
        f.managed_destination(updates.workspace() / "current.json", "storage").read_bytes()
    )


def check_same_model(before, after):
    """Only carrier images and a maintenance pointer may change; everything else stays exact."""
    left, right = copy.deepcopy(before), copy.deepcopy(after)
    for value in (left, right):
        value.pop("maintenance_receipt", None)
        value["plan"]["images"].pop("artifacts")
        for slot in ("blue", "green"):
            value["plan"]["slot_images"][slot].pop("artifacts")
    p.require(
        left == right,
        "Maintenance cannot change model, release, runtime, route or Registry identity",
    )


def resolve_active(saved):
    """Follow only finalized same-model maintenance links; old model receipts stay intact."""
    actual = current()
    cursor = actual
    for _ in range(16):
        if cursor == saved:
            return actual
        location = cursor.get("maintenance_receipt")
        p.require(location, "Active state changed outside a completed same-model maintenance")
        path = f.managed_destination(Path(location), "storage")
        p.require(
            path.is_relative_to(f.workspace() / "serving-maintenance")
            and path.name == "applied.json",
            "Foreign maintenance receipt",
        )
        receipt = json.loads(path.read_bytes())
        p.require(
            receipt["status"] == "serving_seed_maintenance_applied"
            and receipt["after_active"] == cursor
            and receipt["native_seed_restart_verified"] is True,
            "Incomplete serving maintenance",
        )
        check_same_model(receipt["before_active"], cursor)
        cursor = receipt["before_active"]
    raise ValueError("Serving maintenance chain exceeds the reviewed limit")


def committed():
    values = updates.pins()
    for name in [
        "references/serving_seed_source_transition_v1.json",
        "infra/make/serving-recovery.mk",
    ]:
        values[name] = p.sha(f.ROOT / name)
    for name, value in values.items():
        result = subprocess.run(
            ["git", "show", "HEAD:" + name], cwd=f.ROOT, capture_output=True, check=False
        )
        p.require(
            result.returncode == 0 and hashlib.sha256(result.stdout).hexdigest() == value,
            "Commit reviewed recovery implementation first: " + name,
        )
    return values


def read(key):
    result = json.loads(f.managed_destination(work(key) / "prepared.json", "storage").read_bytes())
    source_compatibility.accept(result["source_pins"], committed(), f.ROOT)
    p.require(
        p.digest({k: v for k, v in result.items() if k != "approval_sha256"})
        == result["approval_sha256"],
        "Recovery approval digest differs",
    )
    p.require(result["node_uid"] == f.owner()["node_uid"], "Recovery belongs to another KinD node")
    check_same_model(result["before_active"], result["after_active"])
    return result


def config():
    return json.loads((f.ROOT / "references/model_update_policy_v1.json").read_bytes())


def includes(expected, actual):
    """Compare declared fields while allowing API-server defaults and status fields."""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and includes(value, actual[key]) for key, value in expected.items()
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(expected) == len(actual)
            and all(includes(left, right) for left, right in zip(expected, actual, strict=True))
        )
    return expected == actual


def seed_job(image, active, folder):
    """Force init exit 17 once, then require reuse on restart over the SAME emptyDir."""
    saved_path = folder / "restart-job.json"
    saved = json.loads(saved_path.read_bytes()) if saved_path.exists() else None
    name = saved["metadata"]["name"] if saved else "fraud-seed-check-" + uuid4().hex[:12]
    p.require(name.startswith("fraud-seed-check-") and len(name) == 29, "Invalid restart Job name")
    slot = active["slot"]
    digest = active["plan"]["releases"][slot]["manifest_sha256"]
    script = (
        "from pathlib import Path; import json; from seed import seed; "
        f"r=seed(Path('/seed/{slot}'),Path('/model'),'{digest}'); "
        "print(json.dumps(r),flush=True); "
        "raise SystemExit(17 if r['status']=='canonical_release_seeded' else 0)"
    )
    security = {
        "readOnlyRootFilesystem": True,
        "allowPrivilegeEscalation": False,
        "capabilities": {"drop": ["ALL"]},
    }
    mounts = [{"name": "model", "mountPath": "/model"}]
    common = {
        "image": image["image"],
        "imagePullPolicy": "Never",
        "securityContext": security,
        "volumeMounts": mounts,
        "resources": {
            "requests": {"cpu": "50m", "memory": "64Mi"},
            "limits": {"cpu": "1", "memory": "128Mi"},
        },
    }
    labels = {"app.kubernetes.io/part-of": "fraud-platform", "fraud.seed-maintenance": name}
    job = {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": name, "namespace": f.NAMESPACE, "labels": labels},
        "spec": {
            "backoffLimit": 3,
            "activeDeadlineSeconds": 120,
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    "restartPolicy": "OnFailure",
                    "automountServiceAccountToken": False,
                    "enableServiceLinks": False,
                    "nodeSelector": {"kubernetes.io/hostname": "fraud-platform-control-plane"},
                    "securityContext": {
                        "runAsNonRoot": True,
                        "runAsUser": 10001,
                        "runAsGroup": 10001,
                        "fsGroup": 10001,
                        "seccompProfile": {"type": "RuntimeDefault"},
                    },
                    "initContainers": [
                        {**common, "name": "release", "command": ["python", "-c", script]}
                    ],
                    "containers": [
                        {**common, "name": "verify", "args": [slot, "--manifest-sha256", digest]}
                    ],
                    "volumes": [{"name": "model", "emptyDir": {"sizeLimit": "64Mi"}}],
                },
            },
        },
    }
    if saved:
        p.require(saved == job, "Saved restart Job differs from the reviewed probe")
    else:
        f.private_json(saved_path, job)
        f.kube("create", "-f", "-", data=json.dumps(job))
    deadline = time.monotonic() + 150
    print("Waiting for isolated init-container restart/reuse check...", flush=True)
    while True:
        state = json.loads(f.kube("get", "job", name, "-o", "json").stdout)
        p.require(includes(job, state), "Live restart Job differs from the reviewed probe")
        if state.get("status", {}).get("succeeded") == 1:
            break
        p.require(
            not state.get("status", {}).get("failed") and time.monotonic() < deadline,
            "Seed restart Job failed; preserve job/" + name + " and inspect its logs",
        )
        time.sleep(2)
    pods = json.loads(f.kube("get", "pods", "-l", "job-name=" + name, "-o", "json").stdout)[
        "items"
    ]
    p.require(len(pods) == 1, "Expected one isolated restart-check pod")
    pod = pods[0]
    p.require(
        includes(job["spec"]["template"]["spec"], pod["spec"])
        and pod["status"]["phase"] == "Succeeded"
        and any(
            owner.get("uid") == state["metadata"]["uid"] and owner.get("controller") is True
            for owner in pod["metadata"].get("ownerReferences", [])
        ),
        "Restart-check pod spec or Job ownership differs",
    )
    init = pod["status"]["initContainerStatuses"][0]
    probe = pod["status"]["containerStatuses"][0]
    p.require(
        init["name"] == "release"
        and init["restartCount"] == 1
        and init["state"]["terminated"]["exitCode"]
        == probe["state"]["terminated"]["exitCode"]
        == 0,
        "Seed did not survive the forced init restart",
    )
    for container in (init, probe):
        p.require(
            kind_tracking.image_digest(container["imageID"])
            in {kind_tracking.image_digest(v) for v in image["loaded_identities"]},
            "Restart-check image differs",
        )
    reused = f.kube("logs", pod["metadata"]["name"], "-c", "release").stdout
    verified = f.kube("logs", pod["metadata"]["name"], "-c", "verify").stdout
    for output in (reused, verified):
        p.require(
            json.loads(output)
            == {
                "status": "canonical_release_reused",
                "model_sha256": active["model_sha256"],
                "files_written": 0,
                "files_reused": 3,
            },
            "Seed Job logs do not prove exact-model reuse without writes",
        )
    receipt = {
        "status": "seed_restart_verified",
        "job": name,
        "job_uid": state["metadata"]["uid"],
        "pod_uid": pod["metadata"]["uid"],
        "init_restarts": init["restartCount"],
        "installation_evidence": "reviewed_forced_exit_command_one_restart_and_exact_model_reuse",
        "previous_log_required": False,
        "restarted": json.loads(reused),
        "probe": json.loads(verified),
        "same_emptyDir": True,
        "image": image,
        "production_API_interrupted": False,
    }
    receipt_path = folder / "restart-check.json"
    if receipt_path.exists():
        p.require(
            json.loads(receipt_path.read_bytes()) == receipt, "Saved restart receipt differs"
        )
    else:
        f.private_json(receipt_path, receipt)
    # Retain the bounded completed probe for diagnosis; prepare retries do not create another Job.
    # No correctness decision depends on kubelet retaining an earlier container's logs.
    return receipt


def prepare(key, no_other_writers):
    p.require(no_other_writers, "Declare no other serving/Registry writers")
    folder = work(key)
    if (folder / "prepared.json").exists():
        return read(key)
    source_pins = committed()
    node_uid = f.owner()["node_uid"]
    before = current()
    baseline_gate = deployment.check_active(before, config())
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    p.require(folder.stat().st_mode & 0o077 == 0, "Recovery workspace must be private")
    context = folder / "carrier"
    resumed = any(folder.iterdir())
    if resumed:
        p.require(
            {v.name for v in folder.iterdir()}
            <= {
                "carrier",
                "restart-job.json",
                "restart-check.json",
                "image.json",
                "preparing.json",
            }
            and (folder / "restart-job.json").is_file(),
            "Incomplete build has no restart Job; preserve the attempt for diagnosis",
        )
        p.require(not context.is_symlink(), "Carrier context must not be a symlink")
        expected_files = {"seed.py", "Dockerfile"}
        expected_files.update(
            f"{slot}/{name}"
            for slot in ("blue", "green")
            for name in ("manifest.json", "pipeline.skops", "smoke.json")
        )
        p.require(
            {v.relative_to(context).as_posix() for v in context.rglob("*") if not v.is_dir()}
            == expected_files
            and not any(v.is_symlink() for v in context.rglob("*")),
            "Carrier context contains unexpected files or links",
        )
    else:
        context.mkdir()
    for slot in ("blue", "green"):
        destination = context / slot
        if not resumed:
            destination.mkdir()
        for name in ("manifest.json", "pipeline.skops", "smoke.json"):
            source = Path(before["paths"][slot]) / name
            if resumed:
                p.require(p.sha(source) == p.sha(destination / name), "Saved release bytes differ")
            else:
                shutil.copyfile(source, destination / name)
    for source, target in (
        (f.ROOT / "scripts/operational_round/seed.py", context / "seed.py"),
        (f.ROOT / "infra/kind/serving/Dockerfile.artifacts", context / "Dockerfile"),
    ):
        if resumed:
            p.require(p.sha(source) == p.sha(target), "Saved carrier source differs")
        else:
            shutil.copyfile(source, target)
    preparing = {
        "before_active": before,
        "node_uid": node_uid,
        "seed_sha256": p.sha(context / "seed.py"),
    }
    if (folder / "preparing.json").exists():
        p.require(
            json.loads((folder / "preparing.json").read_bytes()) == preparing,
            "Recovery baseline or node changed",
        )
    else:
        f.private_json(folder / "preparing.json", preparing)
    if resumed:
        saved_job = json.loads((folder / "restart-job.json").read_bytes())
        tag = saved_job["spec"]["template"]["spec"]["initContainers"][0]["image"]
        p.require(tag.startswith("fraud-kind-serving-artifacts:"), "Unexpected carrier tag")
        print("Resuming the retained restart probe without rebuilding...", flush=True)
        loaded = original.cri(tag)
    else:
        tag = "fraud-kind-serving-artifacts:" + uuid4().hex
        print("Building the idempotent carrier with the SAME release bytes...", flush=True)
        original.image_command(
            [
                "docker",
                "build",
                "--platform",
                "linux/amd64",
                "--file",
                context / "Dockerfile",
                "--tag",
                tag,
                context,
            ],
            timeout=300,
        )
        loaded = original.load_image(tag)
    image = {
        "image": tag,
        "identity": loaded["id"],
        "loaded_identities": [loaded["id"], *loaded.get("repoDigests", [])],
        "seed_sha256": p.sha(context / "seed.py"),
    }
    if (folder / "image.json").exists():
        p.require(
            json.loads((folder / "image.json").read_bytes()) == image,
            "Recovery carrier image identity changed",
        )
    else:
        f.private_json(folder / "image.json", image)
    check = seed_job(image, before, folder)
    p.require(current() == before, "Serving state changed during recovery preparation")
    after = copy.deepcopy(before)
    after["maintenance_receipt"] = str(folder / "applied.json")
    after["plan"]["images"]["artifacts"] = image
    for slot in ("blue", "green"):
        after["plan"]["slot_images"][slot]["artifacts"] = image
    check_same_model(before, after)
    value = {
        "schema": "fraud_serving_seed_maintenance_v1",
        "maintenance_id": key,
        "node_uid": node_uid,
        "status": "prepared_awaiting_approval",
        "source_pins": source_pins,
        "before_active": before,
        "after_active": after,
        "baseline_HTTP": baseline_gate,
        "native_restart_check": check,
        "model_weights_changed": False,
        "champion_modified": False,
    }
    value["approval_sha256"] = p.digest(value)
    f.private_json(folder / "prepared.json", value)
    return value


def atomic_json(path, value):
    path = f.managed_destination(path, "storage")
    fd, name = tempfile.mkstemp(prefix=".serving-state-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def apply(key, approval, no_other_writers):
    p.require(no_other_writers, "Declare no other serving/Registry writers")
    intent = read(key)
    folder = work(key)
    p.require(
        approval == intent["approval_sha256"],
        "Use the exact reviewed carrier-maintenance approval SHA-256",
    )
    before, after = intent["before_active"], intent["after_active"]
    p.require(
        current() in (before, after), "Another deployment changed active state; do not overwrite"
    )
    deployment.files(after["plan"])
    try:
        if not (folder / "applying.json").exists():
            f.private_json(
                folder / "applying.json",
                {
                    "status": "authorized",
                    "approval_sha256": approval,
                    "before_sha256": p.digest(before),
                    "after_sha256": p.digest(after),
                },
            )
        if current() != after:
            # Reapplying the same desired state is safe after process interruption.
            desired = copy.deepcopy(after["plan"])
            desired["receipt_directory"] = str(folder)
            deployment.apply(desired, after["slot"], both=False)
        verified = deployment.check_active(after, config())
        receipt = {
            "schema": "fraud_serving_seed_maintenance_applied_v1",
            "status": "serving_seed_maintenance_applied",
            "before_active": before,
            "after_active": after,
            "approval_sha256": approval,
            "native_seed_restart_verified": True,
            "HTTP": verified,
            "runtime_image_unchanged": True,
            "release_bytes_unchanged": True,
            "model_weights_changed": False,
            "champion_modified": False,
            "same_slot_carrier_rollout": True,
            "zero_downtime_claimed": False,
        }
        path = folder / "applied.json"
        if path.exists():
            saved = json.loads(path.read_bytes())
            p.require(
                saved["before_active"] == before
                and saved["after_active"] == after
                and saved["approval_sha256"] == approval,
                "Another maintenance receipt exists",
            )
            receipt = saved
        else:
            f.private_json(path, receipt)
        p.require(current() in (before, after), "Active state changed before maintenance commit")
        atomic_json(updates.workspace() / "current.json", after)
        return {
            "status": "serving_seed_maintenance_applied",
            "receipt": str(path),
            "model_sha256": after["model_sha256"],
            "registry_version": after["version"],
            "slot": after["slot"],
            "native_seed_restart_verified": True,
            "champion_modified": False,
        }
    except Exception as exc:
        f.private_json(
            folder / ("failure-" + uuid4().hex + ".json"),
            {
                "status": "maintenance_recovery_required",
                "reason": str(exc)[-1200:],
                "resume": "Repeat the SAME apply approval after resolving the cause; preserve releases and receipts",
            },
        )
        raise


def verify(key):
    intent = read(key)
    result = resolve_active(intent["before_active"])
    p.require(result == intent["after_active"], "Maintenance is not the active serving state")
    gate = deployment.check_active(result, config())
    return {
        "status": "serving_seed_recovery_verified",
        "model_sha256": result["model_sha256"],
        "HTTP": gate,
        "receipt": str(work(key) / "applied.json"),
        "native_seed_restart_verified": True,
        "champion_modified": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "apply", "verify"])
    parser.add_argument("--maintenance-id", default="seed-restart-v1")
    parser.add_argument("--approval-sha256", default="")
    parser.add_argument("--no-other-writers", action="store_true")
    args = parser.parse_args()
    try:
        with f.exclusive():
            if args.action == "prepare":
                value = prepare(args.maintenance_id, args.no_other_writers)
                result = {
                    "status": value["status"],
                    "approval_sha256": value["approval_sha256"],
                    "image": value["after_active"]["plan"]["images"]["artifacts"],
                    "native_restart_check": value["native_restart_check"]["status"],
                    "model_sha256": value["before_active"]["model_sha256"],
                    "champion_modified": False,
                }
            elif args.action == "apply":
                result = apply(args.maintenance_id, args.approval_sha256, args.no_other_writers)
            else:
                result = verify(args.maintenance_id)
        print(json.dumps(result, indent=2))
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as exc:
        parser.exit(1, "Serving recovery stopped: " + str(exc)[-2000:] + "\n")


if __name__ == "__main__":
    main()
