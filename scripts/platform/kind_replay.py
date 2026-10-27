"""Recreate one owned replay pod with an open SQLite transaction and verify PVC recovery."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time
from uuid import uuid4

from scripts.platform import kind_foundation as f
from scripts.platform import kind_tracking as tracking
from scripts.platform.container_images import inspect_image as loaded

CHART = f.ROOT / "infra/helm/fraud-replay"


def work():
    return f.managed_destination(f.workspace() / "replay", "storage")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def initialize():
    f.owner()
    work().mkdir(mode=0o700, exist_ok=True)
    f.require(work().stat().st_mode & 0o077 == 0, "Replay workspace must be private")


def pins():
    names = {"poetry.lock", "examples/serving_smoke.py", "scripts/platform/kind_replay_worker.py"}
    for directory, suffix in [
        ("fraud_detection_mlops", ".py"),
        ("infra/kind/replay", ""),
        ("infra/helm/fraud-replay", ""),
    ]:
        names.update(
            p.relative_to(f.ROOT).as_posix()
            for p in (f.ROOT / directory).rglob("*")
            if p.is_file() and (not suffix or p.suffix == suffix)
        )
    return {name: sha(f.ROOT / name) for name in sorted(names)}


def image_record():
    record = json.loads(f.managed_destination(work() / "image.json", "storage").read_text())
    f.require(record.get("schema") == "fraud_kind_replay_image_v1", "Invalid replay image record")
    f.require(record["source_pins"] == pins(), "Replay image sources changed; preserve receipt")
    f.require(record["node_uid"] == f.owner()["node_uid"], "Replay image belongs to another node")
    f.require(
        re.fullmatch(r"fraud-kind-replay:[a-f0-9]{32}", record["image"]), "Invalid image tag"
    )
    current = loaded(record["image"])
    f.require(
        record["image"]
        in {s.removeprefix("docker.io/library/") for s in current.get("repoTags", [])},
        "Replay image tag is not loaded",
    )
    f.require(
        tracking.image_digest(current["id"])
        in {tracking.image_digest(s) for s in record["loaded_identities"]},
        "Replay image identity changed",
    )
    return record


def build():
    initialize()
    if (work() / "image.json").exists():
        image_record()
        return {"status": "replay_image_already_loaded"}
    tag = "fraud-kind-replay:" + uuid4().hex
    print("Building and loading the locked replay runtime...", flush=True)
    done = f.run(
        ["docker", "build", "--file", "infra/kind/replay/Dockerfile", "--tag", tag, "."],
        timeout=900,
        check=False,
    )
    f.require(done.returncode == 0, "Replay build failed: " + (done.stdout + done.stderr)[-6000:])
    image = json.loads(f.run(["docker", "image", "inspect", tag]).stdout)[0]
    f.run(["kind", "load", "docker-image", "--name", f.CLUSTER, tag], timeout=300)
    status = loaded(tag)
    f.require(
        tag in {s.removeprefix("docker.io/library/") for s in status.get("repoTags", [])},
        "Loaded tag differs",
    )
    record = {
        "schema": "fraud_kind_replay_image_v1",
        "image": tag,
        "docker_id": image["Id"],
        "loaded_identities": [status["id"], *status.get("repoDigests", [])],
        "source_pins": pins(),
        "node_uid": f.owner()["node_uid"],
        "registry_published": False,
    }
    f.private_json(work() / "image.json", record)
    return {"status": "replay_image_built_and_loaded", "image": tag}


def validate():
    f.run(["helm", "lint", "--strict", CHART, "--kube-version", "1.35.8"])
    f.run(["helm", "template", "fraud-replay-validation", CHART, "--kube-version", "1.35.8"])
    return {"status": "replay_templates_validated", "native_recovery_validated": False}


def objects(kind, release):
    return json.loads(
        f.kube("get", kind, "-l", "app.kubernetes.io/instance=" + release, "-o", "json").stdout
    )["items"]


def inventory(release, run_id):
    claims = objects("pvc", release)
    deployments = objects("deployment", release)
    f.require(len(claims) == len(deployments) == 1, "One owned PVC and Deployment required")
    pvc, deployment = claims[0], deployments[0]
    for item in [pvc, deployment]:
        f.require(
            item["metadata"].get("annotations", {}).get("fraud.replay/run-id") == run_id,
            "Replay resource ownership differs",
        )
    f.require(
        pvc["metadata"]["name"] == release + "-state" and pvc["status"]["phase"] == "Bound",
        "Replay PVC is not the expected bound claim",
    )
    f.require(deployment["metadata"]["name"] == release, "Replay Deployment name differs")
    return {
        "pvc_uid": pvc["metadata"]["uid"],
        "volume_name": pvc["spec"]["volumeName"],
        "deployment_uid": deployment["metadata"]["uid"],
    }


def pod_for(release, run_id, record):
    pods = [p for p in objects("pods", release) if not p["metadata"].get("deletionTimestamp")]
    f.require(len(pods) <= 1, "More than one active replay pod")
    if not pods:
        return None
    pod = pods[0]
    f.require(
        pod["metadata"].get("annotations", {}).get("fraud.replay/run-id") == run_id,
        "Pod run identity differs",
    )
    spec = pod["spec"]
    f.require(
        spec.get("nodeName") in (None, f.CLUSTER + "-control-plane"), "Pod moved to another node"
    )
    f.require(
        len(spec["containers"]) == 1 and spec["containers"][0]["image"] == record["image"],
        "Replay pod image differs",
    )
    replicas = objects("replicasets", release)
    owners = pod["metadata"].get("ownerReferences", [])
    f.require(len(owners) == 1, "Pod controller differs")
    replica = next((r for r in replicas if r["metadata"]["uid"] == owners[0]["uid"]), None)
    state = inventory(release, run_id)
    f.require(
        replica is not None
        and any(
            o["uid"] == state["deployment_uid"] and o.get("controller")
            for o in replica["metadata"].get("ownerReferences", [])
        ),
        "Pod ownership chain differs",
    )
    statuses = pod.get("status", {}).get("containerStatuses", [])
    if statuses:
        status = statuses[0]
        f.require(
            status.get("restartCount", 0) == 0,
            "Unexpected container restart; preserve diagnostics",
        )
        f.require("terminated" not in status["state"], "Replay worker exited")
        if status.get("imageID"):
            f.require(
                tracking.image_digest(status["imageID"])
                in {tracking.image_digest(v) for v in record["loaded_identities"]},
                "Runtime image digest differs",
            )
    return pod


def wait_marker(release, run_id, record, filename, previous_uid=None, timeout=180):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pods = objects("pods", release)
        if not pods:
            time.sleep(1)
            continue
        # The PVC can still be Pending until scheduling completes.
        if not all(
            p.get("status", {}).get("phase") == "Running"
            for p in pods
            if not p["metadata"].get("deletionTimestamp")
        ):
            time.sleep(1)
            continue
        pod = pod_for(release, run_id, record)
        if pod and pod["metadata"]["uid"] != previous_uid:
            path = "/state/run/" + filename
            script = (
                "from pathlib import Path; p=Path("
                + repr(path)
                + "); print(p.read_text() if p.exists() else 'null')"
            )
            output = f.kube(
                "exec",
                pod["metadata"]["name"],
                "-c",
                "replay",
                "--",
                "python",
                "-c",
                script,
                timeout=15,
                check=False,
            )
            if output.returncode == 0:
                value = json.loads(output.stdout)
                if value is not None:
                    f.require(
                        value["run_id"] == run_id, "Worker marker belongs to another rehearsal"
                    )
                    return pod, value
        time.sleep(1)
    raise ValueError("Replay phase timed out; preserve the owned resources and inspect report")


def rehearse():
    initialize()
    record = image_record()
    run_id = uuid4().hex
    release = "fraud-replay-" + run_id[:12]
    report_path = work() / ("rehearsal-" + run_id + ".json")
    result = {
        "schema": "fraud_kind_replay_rehearsal_v1",
        "status": "failed",
        "run_id": run_id,
        "scope": "synthetic_inflight_SQLite_transaction_and_PVC_pod_recreation_not_native_history_migration",
        "release": release,
        "report": str(report_path),
        "controller_sha256": sha(Path(__file__)),
        "historical_sources_mounted": False,
        "primary_storage_migrated": False,
        "production_promotion": False,
        "backup_restore_validated": False,
        "cluster_recreation_validated": False,
        "external_alerts_published": False,
        "retained_rehearsal_pvc": release + "-state",
        "image_receipt": record,
    }
    started = time.monotonic()
    try:
        before = f.inventory()
        result["foundation_before"] = before
        for kind in ["pods", "deployments", "replicasets", "pvc", "secrets"]:
            f.require(not objects(kind, release), "New release collides with existing resources")
        values = work() / ("values-" + run_id + ".json")
        f.private_json(values, {"runId": run_id, "image": record["image"]})
        result["phase"] = "install"
        f.helm("install", release, CHART, "--values", values, "--timeout", "180s", timeout=200)
        result["phase"] = "await_open_transaction"
        pod, pause = wait_marker(release, run_id, record, "interrupted.json")
        f.require(
            pause["phase"] == "transaction_open" and pause["before"]["counts"]["events"] == 8,
            "Worker did not reach the controlled transaction",
        )
        old = pod["metadata"]
        state = inventory(release, run_id)
        result.update(
            before=state,
            interrupted_pod={"name": old["name"], "uid": old["uid"]},
            interruption=pause,
        )
        result["phase"] = "terminate_owned_pod"
        # One-second kubelet termination, with deletion acknowledged; never force-delete or delete by broad selector.
        f.kube(
            "delete",
            "pod",
            old["name"],
            "--grace-period=1",
            "--wait=true",
            "--timeout=60s",
            timeout=70,
        )
        result["phase"] = "await_resumed_ledger"
        new, worker = wait_marker(release, run_id, record, "result.json", previous_uid=old["uid"])
        f.require(
            worker["status"] == "success"
            and worker["worker_sha256"] == sha(f.ROOT / "scripts/platform/kind_replay_worker.py"),
            "Worker success or source identity differs",
        )
        for key in [
            "inflight_transaction_rollback_validated",
            "all_schema_and_table_rows_equal",
            "duplicate_redelivery_without_rescoring_validated",
        ]:
            f.require(worker.get(key) is True, "Missing recovery guarantee: " + key)
        after = inventory(release, run_id)
        f.require(state == after, "Replay PVC or Deployment identity changed")
        result.update(
            after=after,
            resumed_pod={"name": new["metadata"]["name"], "uid": new["metadata"]["uid"]},
            worker=worker,
        )
        f.require(f.inventory() == before, "Foundation resources changed")
        result["foundation_after"] = before
        result["phase"] = "remove_owned_deployment"
        f.helm("uninstall", release, "--wait", "--timeout", "90s", timeout=100)
        deadline = time.monotonic() + 60
        while objects("pods", release) and time.monotonic() < deadline:
            time.sleep(1)
        f.require(
            not objects("pods", release) and not objects("deployments", release),
            "Replay compute still exists",
        )
        retained = objects("pvc", release)
        f.require(
            len(retained) == 1 and retained[0]["metadata"]["uid"] == state["pvc_uid"],
            "Retained checkpoint PVC differs",
        )
        result.update(
            status="success",
            phase="complete",
            pod_recreation_validated=True,
            pvc_identity_preserved=True,
            temporary_compute_removed=True,
            checkpoint_pvc_retained=True,
        )
    except BaseException as exc:
        result["reason"] = str(exc)[-4000:]
        try:
            diagnostics = f.kube(
                "get",
                "pods,pvc,deployments",
                "-l",
                "app.kubernetes.io/instance=" + release,
                "-o",
                "json",
                check=False,
            )
            result["diagnostics"] = diagnostics.stdout[-16000:]
            logs = f.kube(
                "logs",
                "-l",
                "app.kubernetes.io/instance=" + release,
                "--all-containers=true",
                "--tail=80",
                check=False,
            )
            result["worker_logs"] = (logs.stdout + logs.stderr)[-10000:]
        except (ValueError, OSError, subprocess.TimeoutExpired) as diagnostic_error:
            result["diagnostic_error"] = type(diagnostic_error).__name__
        raise
    finally:
        result["elapsed_seconds"] = time.monotonic() - started
        f.private_json(report_path, result)
        print("Relatório: " + str(report_path), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["validate", "build", "rehearse"])
    args = parser.parse_args()
    try:
        if args.command == "validate":
            value = validate()
        else:
            with f.exclusive():
                value = {"build": build, "rehearse": rehearse}[args.command]()
        print(json.dumps(value, indent=2))
    except (ValueError, OSError) as exc:
        parser.exit(1, "Replay stopped: " + str(exc) + "\n")


if __name__ == "__main__":
    main()
