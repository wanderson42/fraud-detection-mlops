"""Controlled stop/start of the owned KinD stack, with a separate manual-reboot proof."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import urlencode
from uuid import UUID, uuid4

from scripts.platform import kind_foundation as f
from scripts.platform import kind_observability as monitor
from scripts.platform import kind_serving as serving
from scripts.platform import kind_serving_delivery as delivery
from scripts.platform import kind_tracking as tracking
from scripts.platform import local_access, source_compatibility

NODE = f.CLUSTER + "-control-plane"
# Dependents stop first; dependencies start first. No cluster/volume creation occurs.
START_ORDER = (
    ("statefulset", "fraud-platform-rustfs"),
    ("statefulset", "fraud-platform-postgres"),
    ("deployment", "fraud-tracking"),
    ("deployment", "fraud-observability-exporter"),
    ("deployment", "fraud-observability-prometheus"),
    ("deployment", "fraud-observability-grafana"),
    ("deployment", "fraud-serving-blue"),
    ("deployment", "fraud-serving-green"),
    ("deployment", "fraud-serving-gateway"),
    ("deployment", "fraud-detector-blue"),
    ("deployment", "fraud-detector-green"),
    ("deployment", "fraud-detector-gateway"),
)
STOP_ORDER = tuple(reversed(START_ORDER))
REQUIRED = set(STOP_ORDER) - {
    ("deployment", "fraud-detector-green"),
    *(("deployment", "fraud-serving-" + slot) for slot in ("blue", "green", "gateway")),
}
MINIMUM_INOTIFY = {"max_user_instances": 512, "max_user_watches": 524288}
SCOPE = "planned_owned_kind_stack_quiescence_same_node_volumes_and_readback_not_host_loss_recovery"


def work():
    return f.managed_destination(f.workspace() / "operations", "storage")


def initialize():
    work().mkdir(mode=0o700, exist_ok=True)
    f.require(work().stat().st_mode & 0o077 == 0, "Operations workspace must be private")


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def pins():
    names = {
        "scripts/platform/kind_operations.py",
        "scripts/platform/kind_foundation.py",
        "scripts/platform/kind_tracking.py",
        "scripts/platform/kind_tracking_storage.py",
        "scripts/platform/kind_serving.py",
        "scripts/platform/kind_serving_delivery.py",
        "scripts/platform/kind_observability.py",
        "scripts/platform/kind_observability_state.py",
        "scripts/platform/dashboard_colors.py",
        "infra/kind/operations/90-fraud-platform.conf",
    }
    from scripts.model_updates import controller as updates

    names.add("scripts/platform/local_access.py")
    return {**updates.pins(), **{name: monitor.state.sha(f.ROOT / name) for name in sorted(names)}}


def host():
    values = {
        name: int((Path("/proc/sys/fs/inotify") / name).read_text())
        for name in (*MINIMUM_INOTIFY, "max_queued_events")
    }
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    UUID(boot)
    machine = hashlib.sha256(Path("/etc/machine-id").read_bytes()).hexdigest()
    return {
        "boot_id": boot,
        "machine_id_sha256": machine,
        "inotify": values,
        "runtime_inotify_sufficient": all(
            values[name] >= minimum for name, minimum in MINIMUM_INOTIFY.items()
        ),
    }


def checked_host():
    value = host()
    f.require(
        value["runtime_inotify_sufficient"],
        "Inotify below required runtime values; follow KIND_OPERATIONS.md before starting the node",
    )
    return value


def docker_owner():
    # f.owner() needs Kubernetes. This check intentionally also works with a stopped node.
    path = f.managed_destination(f.workspace() / "owner.json", "storage")
    saved = json.loads(path.read_text())
    f.require(saved["schema"] == "fraud_kind_owner_v1", "Unknown foundation ownership receipt")
    done = monitor.command(["docker", "inspect", NODE], timeout=15)
    entries = json.loads(done.stdout)
    f.require(len(entries) == 1, "One owned node container required")
    node = entries[0]
    f.require(
        node["Id"] == saved["container_id"]
        and node["Config"].get("Labels", {}).get("io.x-k8s.kind.cluster") == f.CLUSTER,
        "Node container identity/ownership differs; refuse adoption",
    )
    f.require(node["Name"].lstrip("/") == NODE, "Node name differs")
    engine = monitor.command(["docker", "info", "--format", "{{.ID}}"], timeout=15).stdout.strip()
    f.require(bool(engine), "Docker engine identity missing")
    return {
        "container_id": node["Id"],
        "node_uid": saved["node_uid"],
        "docker_engine_id": engine,
        "running": bool(node["State"]["Running"]),
        "paused": bool(node["State"].get("Paused")),
        "restarting": bool(node["State"].get("Restarting")),
        "owner_receipt_sha256": monitor.state.sha(path),
    }


def resources():
    items = json.loads(
        f.kube(
            "get", "deployments,statefulsets,pvc,services,configmaps,secrets", "-o", "json"
        ).stdout
    )["items"]
    actual = {
        ("deployment" if item["kind"] == "Deployment" else "statefulset", item["metadata"]["name"])
        for item in items
        if item["kind"] in {"Deployment", "StatefulSet"}
    }
    f.require(
        REQUIRED <= actual <= set(STOP_ORDER),
        "Unexpected or missing workload in fraud namespace; do not stop unreviewed controllers",
    )
    result = {}
    active = active_state()
    for item in items:
        kind, name = item["kind"], item["metadata"]["name"]
        if kind == "Deployment" and name.startswith("fraud-detector-"):
            metadata = item["metadata"]
            f.require(
                metadata.get("annotations", {}).get("fraud.serving/owner-id") == active["owner_id"]
                and metadata.get("labels", {}).get("app.kubernetes.io/instance")
                == "fraud-detector"
                and metadata.get("labels", {}).get("app.kubernetes.io/component")
                == name.removeprefix("fraud-detector-"),
                "Canonical workload ownership/labels differ; refuse adoption",
            )
        spec = dict(item.get("spec", {}))
        replicas = spec.pop("replicas", None) if kind in {"Deployment", "StatefulSet"} else None
        content = {k: item[k] for k in ("data", "binaryData", "type", "immutable") if k in item}
        # Hash Secret contents locally; plaintext and base64 credentials never enter receipts.
        result[kind + "/" + name] = {
            "uid": item["metadata"]["uid"],
            "spec_sha256": digest(spec),
            "configuration_sha256": digest(content),
            "replicas": replicas,
        }
        if kind == "PersistentVolumeClaim":
            f.require(item.get("status", {}).get("phase") == "Bound", "PVC not bound: " + name)
    return result


def active_state():
    from scripts.platform.serving_recovery import current

    return current()


def serving_probe():
    # Reuse the daily-access gate: Registry, route, exact images/bytes, HTTP and latency.
    with local_access.connection("api") as checked:
        return {
            **{
                key: checked[key]
                for key in ("registry_version", "slot", "model_sha256", "serving_release_id")
            },
            # Latency must pass its budget on each run, but is not an equality fingerprint.
            "score": checked["verification"]["score"],
            "invalid_input_http_status": checked["verification"]["invalid_input_http_status"],
        }


def replica_plan(current, active):
    slot = active["slot"]
    f.require(slot in ("blue", "green"), "Unknown active serving slot")
    inactive = "green" if slot == "blue" else "blue"
    for component in (slot, "gateway"):
        key = "Deployment/fraud-detector-" + component
        f.require(
            current.get(key, {}).get("replicas") == 1, "Active serving must have one replica"
        )
    for key, item in current.items():
        count = item["replicas"]
        if count is None:
            continue
        expected = 0 if key == "Deployment/fraud-detector-" + inactive else 1
        if key.startswith("Deployment/fraud-serving-"):
            # Explicitly owned legacy rehearsal, if still installed; preserve its exact plan.
            f.require(count in (0, 1), "Unexpected legacy replica count")
        else:
            f.require(count == expected, "Finish staged rollout before operations: " + key)
    if any(key.startswith("Deployment/fraud-serving-") for key in current):
        owned = serving.owned(delivery.plan())
        found = {"Deployment/" + row["metadata"]["name"] for row in owned}
        f.require(
            found == {key for key in current if key.startswith("Deployment/fraud-serving-")},
            "Legacy serving ownership differs",
        )


def ordered(baseline, order):
    return [
        (kind, name, baseline["resources"][key]["replicas"])
        for kind, name in order
        if (key := ("Deployment" if kind == "deployment" else "StatefulSet") + "/" + name)
        in baseline["resources"]
    ]


def stable(value):
    return {
        name: {k: v for k, v in item.items() if k != "replicas"} for name, item in value.items()
    }


def no_jobs_or_bare_writers():
    jobs = json.loads(f.kube("get", "jobs", "-o", "json").stdout)["items"]
    f.require(
        not any(j.get("status", {}).get("active", 0) for j in jobs),
        "Active Jobs present; finish them before stopping",
    )
    pods = json.loads(f.kube("get", "pods", "-o", "json").stdout)["items"]
    f.require(
        not any(
            p.get("status", {}).get("phase") in {"Running", "Pending"}
            and not p["metadata"].get("deletionTimestamp")
            and not p["metadata"].get("ownerReferences")
            for p in pods
        ),
        "An unmanaged active pod is present; preserve and review it",
    )


def tracking_report(path):
    path = f.managed_destination(path.absolute(), "storage")
    result = json.loads(path.read_text())
    f.require(
        result.get("schema") == "fraud_kind_tracking_rehearsal_v1"
        and result.get("status") == "success",
        "A successful existing KinD tracking rehearsal is required",
    )
    f.require(
        result["controller_sha256"] == tracking.sha(f.ROOT / "scripts/platform/kind_tracking.py")
        and result["client_sha256"]
        == tracking.sha(f.ROOT / "scripts/platform/kind_tracking_client.py"),
        "Tracking report source identity differs",
    )
    selected = {
        key: result[key] for key in ("run_id", "model_uri", "model_artifact_uri", "model_sha256")
    }
    f.require(
        re.fullmatch(r"[a-f0-9]{32}", selected["run_id"])
        and re.fullmatch(r"models:/m-[a-f0-9]+", selected["model_uri"]),
        "Tracking record identity invalid",
    )
    f.require(
        re.fullmatch(r"[a-f0-9]{64}", selected["model_sha256"]),
        "Tracking model fingerprint invalid",
    )
    return {"file": str(path), "sha256": monitor.state.sha(path), "record": selected}


def canonical(value):
    if isinstance(value, dict):
        return {key: canonical(item) for key, item in value.items()}
    if isinstance(value, list):
        return sorted(
            (canonical(item) for item in value), key=lambda item: json.dumps(item, sort_keys=True)
        )
    return value


def probes(record):
    foundation = f.inventory()
    tracked = tracking.inventory()
    # Read existing metadata and bytes; never call the training/publication branch.
    with tracking.port_forward() as uri:
        run = monitor.http(
            uri, "/api/2.0/mlflow/runs/get?" + urlencode({"run_id": record["run_id"]})
        )
        history = monitor.http(
            uri,
            "/api/2.0/mlflow/metrics/get-history?"
            + urlencode({"run_id": record["run_id"], "metric_key": "history_probe"}),
        )
    f.require(
        run["run"]["info"]["run_id"] == record["run_id"]
        and run["run"]["info"]["status"] == "FINISHED",
        "Existing MLflow run differs",
    )
    f.require(
        sorted((m["step"], m["value"]) for m in history["metrics"]) == [(0, 0.25), (1, 0.75)],
        "Existing metric history differs",
    )
    s3 = tracking.storage_pod("verify", record)
    scored = serving_probe()
    observed = monitor.inventory()
    monitoring = monitor.verify_http()
    f.require(
        monitoring["dashboard_color_policy_verified"] == "semantic_colors_v1",
        "Expected dashboard presentation not verified",
    )
    return {
        "foundation_pvcs": foundation["pvcs"],
        "tracking_deployment_uid": tracked["deployment_uid"],
        "tracking_run_fingerprint": digest(canonical(run)),
        "tracking_history_fingerprint": digest(canonical(history)),
        "tracking_history_rows": len(history["metrics"]),
        "s3": s3,
        "serving_release_id": scored["serving_release_id"],
        "serving_model_sha256": scored["model_sha256"],
        "serving_http": scored,
        "monitoring": monitoring,
        "monitoring_pvcs": observed["pvcs"],
        "serving_inference_executed": True,
        "tracking_model_loaded": False,
        "fit_executed": False,
    }


def prepare(report_path, output=None):
    initialize()
    system = checked_host()
    source_pins = pins()
    owner = docker_owner()
    f.require(
        owner["running"] and not owner["paused"] and not owner["restarting"],
        "Baseline requires a running owned node",
    )
    f.owner()
    no_jobs_or_bare_writers()
    active = active_state()
    before = resources()
    replica_plan(before, active)
    recorded = tracking_report(report_path)
    checked = probes(recorded["record"])
    after = resources()
    f.require(before == after, "Resource/configuration identity changed during baseline reads")
    f.require(active_state() == active, "Active serving changed during baseline reads")
    path = (
        f.managed_destination(output.absolute(), "storage")
        if output
        else work() / ("baseline-" + uuid4().hex + ".json")
    )
    f.require(path.parent == work(), "Baseline must be directly inside the operations workspace")
    value = {
        "schema": "fraud_kind_operations_baseline_v2",
        "active_state_sha256": digest(active),
        "status": "prepared",
        "scope": SCOPE,
        "host": system,
        "owner": owner,
        "source_pins": source_pins,
        "resources": before,
        "tracking_report": recorded,
        "probes": checked,
        "prepared_timestamp": time.time(),
    }
    f.require(not path.exists(), "Baseline already exists; preserve it and choose a new path")
    f.require(source_pins == pins(), "Implementation changed during baseline reads")
    f.private_json(path, value)
    print("Baseline: " + str(path), flush=True)
    return path


def read_baseline(path):
    path = f.managed_destination(path.absolute(), "storage")
    f.require(
        path.parent == work() and path.is_file() and path.stat().st_mode & 0o077 == 0,
        "Private operations baseline required",
    )
    value = json.loads(path.read_text())
    f.require(
        value["schema"] == "fraud_kind_operations_baseline_v2"
        and value["status"] == "prepared"
        and value["source_pins"] == pins(),
        "Baseline schema/code identity differs; preserve receipt and prepare a new canonical baseline",
    )
    source_compatibility.accept(value["source_pins"], pins(), f.ROOT)
    f.require(
        value["active_state_sha256"] == digest(active_state()),
        "Active serving receipt changed; prepare a new baseline without editing the old one",
    )
    f.require(
        value["host"]["machine_id_sha256"] == host()["machine_id_sha256"],
        "Baseline belongs to another host",
    )
    owner = docker_owner()
    f.require(
        all(
            owner[key] == value["owner"][key]
            for key in ("container_id", "node_uid", "docker_engine_id", "owner_receipt_sha256")
        ),
        "Node/engine ownership differs; do not recreate or adopt",
    )
    return value, owner


def check_resources(baseline):
    current = resources()
    f.require(
        stable(current) == stable(baseline["resources"]),
        "Controller/PVC/service/configuration identity differs; preserve resources",
    )
    f.require(
        all(item["replicas"] in (None, 0, 1) for item in current.values()),
        "Unexpected replica count",
    )
    return current


def workload(kind, name):
    result = json.loads(f.kube("get", kind, name, "-o", "json").stdout)
    return result


def change_replica(baseline, kind, name, count):
    current = workload(kind, name)
    key = ("Deployment" if kind == "deployment" else "StatefulSet") + "/" + name
    spec = dict(current["spec"])
    replicas = spec.pop("replicas", 1)
    f.require(
        current["metadata"]["uid"] == baseline["resources"][key]["uid"]
        and digest(spec) == baseline["resources"][key]["spec_sha256"],
        "Workload changed before scaling",
    )
    f.require(replicas in (0, 1), "Unexpected replicas before scaling")
    if replicas != count:
        f.kube(
            "scale",
            kind + "/" + name,
            "--current-replicas=" + str(replicas),
            "--resource-version=" + current["metadata"]["resourceVersion"],
            "--replicas=" + str(count),
            timeout=30,
        )
    selector = ",".join(
        key + "=" + value
        for key, value in sorted(current["spec"]["selector"]["matchLabels"].items())
    )
    if count == 0:
        deadline = time.monotonic() + 180
        while True:
            pods = json.loads(f.kube("get", "pods", "-l", selector, "-o", "json").stdout)["items"]
            if not pods:
                return
            f.require(time.monotonic() < deadline, "Workload pods did not stop: " + name)
            time.sleep(1)
    else:
        f.kube("rollout", "status", kind + "/" + name, "--timeout=180s", timeout=195)


def event(action, baseline_path, operation):
    initialize()
    path = work() / (action + "-" + uuid4().hex + ".json")
    result = {
        "schema": "fraud_kind_operations_event_v1",
        "action": action,
        "status": "failed",
        "scope": SCOPE,
        "baseline": str(baseline_path),
        "baseline_sha256": monitor.state.sha(baseline_path),
        "report": str(path),
        "started_timestamp": time.time(),
        "steps": [],
    }
    try:
        operation(result)
        return result
    except BaseException as exc:
        result["error_type"] = type(exc).__name__
        result["reason"] = str(exc)[-2500:]
        raise
    finally:
        result["finished_timestamp"] = time.time()
        f.private_json(path, result)
        print("Relatório: " + str(path), flush=True)


def stop(path, no_other_writers):
    f.require(no_other_writers, "Explicit --no-other-writers declaration required before stopping")
    baseline, owner = read_baseline(path)

    def execute(result):
        result["host"] = host()
        if not owner["running"]:
            result.update(status="already_stopped", controlled_quiescence_validated=False)
            return
        f.owner()
        no_jobs_or_bare_writers()
        check_resources(baseline)
        f.require(
            serving_probe() == baseline["probes"]["serving_http"],
            "Active Registry/HTTP identity differs before stop",
        )
        for kind, name, _saved in ordered(baseline, STOP_ORDER):
            print("Parando " + name, flush=True)
            change_replica(baseline, kind, name, 0)
            result["steps"].append({"workload": kind + "/" + name, "replicas": 0})
        quiesced = check_resources(baseline)
        f.require(
            all(item["replicas"] in (None, 0) for item in quiesced.values()),
            "Workloads resumed before node stop; preserve running node",
        )
        no_jobs_or_bare_writers()
        # Verify the exact saved node ID again before issuing the only Docker mutation.
        current = docker_owner()
        f.require(current["container_id"] == owner["container_id"], "Node changed before stop")
        monitor.command(["docker", "stop", "--timeout", "120", owner["container_id"]], timeout=140)
        after = docker_owner()
        f.require(not after["running"], "Owned node did not stop")
        result.update(
            status="stopped",
            controlled_quiescence_validated=True,
            owner_after=after,
            other_writers_absent_operator_declared=True,
        )

    return event("stop", path, execute)


def wait_api():
    deadline = time.monotonic() + 300
    while True:
        done = f.kube("get", "--raw", "/readyz", "--request-timeout=5s", check=False, timeout=10)
        if done.returncode == 0 and done.stdout.strip() == "ok":
            f.owner()
            f.kube("wait", "--for=condition=Ready", "node/" + NODE, "--timeout=120s", timeout=135)
            return
        f.require(
            time.monotonic() < deadline,
            "Kubernetes API did not become ready; preserve node and inspect logs",
        )
        time.sleep(2)


def start(path, no_other_writers):
    f.require(
        no_other_writers,
        "Explicit --no-other-writers declaration required during operational recovery",
    )
    system = checked_host()
    baseline, owner = read_baseline(path)
    f.require(
        not owner["paused"] and not owner["restarting"], "Node paused or restarting; inspect first"
    )

    def execute(result):
        result["host"] = system
        if not owner["running"]:
            monitor.command(["docker", "start", owner["container_id"]], timeout=30)
        wait_api()
        check_resources(baseline)
        no_jobs_or_bare_writers()
        for kind, name, saved in ordered(baseline, START_ORDER):
            print("Restaurando " + name + " para " + str(saved), flush=True)
            change_replica(baseline, kind, name, saved)
            result["steps"].append({"workload": kind + "/" + name, "replicas": saved})
        current = check_resources(baseline)
        f.require(
            current == baseline["resources"],
            "Original replica/configuration plan was not restored",
        )
        result.update(
            status="started",
            owner_after=docker_owner(),
            original_replica_plan_restored=True,
            other_writers_absent_operator_declared=True,
        )

    return event("start", path, execute)


def latest_stop(path):
    wanted = monitor.state.sha(path)
    reports = []
    for entry in work().glob("stop-*.json"):
        value = json.loads(entry.read_text())
        if (
            value.get("baseline_sha256") == wanted
            and value.get("status") == "stopped"
            and value.get("controlled_quiescence_validated")
        ):
            reports.append(value)
    f.require(
        bool(reports), "No completed controlled stop for this baseline; no restart-cycle claim"
    )
    return max(reports, key=lambda item: item["finished_timestamp"])


def verify(path, expect_reboot=False):
    baseline, owner = read_baseline(path)
    system = checked_host()

    def execute(result):
        f.require(owner["running"], "Owned node is not running")
        f.owner()
        current = check_resources(baseline)
        f.require(current == baseline["resources"], "Original replica plan differs")
        before = latest_stop(path)
        starts = [json.loads(entry.read_text()) for entry in work().glob("start-*.json")]
        starts = [
            item
            for item in starts
            if item.get("baseline_sha256") == before["baseline_sha256"]
            and item.get("status") == "started"
            and item.get("original_replica_plan_restored")
            and item["started_timestamp"] >= before["finished_timestamp"]
            and item["host"]["boot_id"] == system["boot_id"]
        ]
        f.require(bool(starts), "No completed start after the controlled stop on this boot")
        resumed = max(starts, key=lambda item: item["finished_timestamp"])
        rebooted = system["boot_id"] != baseline["host"]["boot_id"]
        if expect_reboot:
            f.require(rebooted, "Host boot_id did not change; stop/start is not a host reboot")
            f.require(
                before["host"]["boot_id"] == baseline["host"]["boot_id"],
                "Controlled stop was not recorded before reboot",
            )
        checked = probes(baseline["tracking_report"]["record"])
        f.require(
            checked == baseline["probes"],
            "Service readback/model/monitoring differs from baseline",
        )
        f.require(
            resources() == baseline["resources"], "Resource identities changed during verification"
        )
        result.update(
            status="verified",
            host=system,
            stop_report=before["report"],
            start_report=resumed["report"],
            probes=checked,
            same_node_and_volumes=True,
            controlled_stop_start_validated=True,
            host_reboot_validated=bool(expect_reboot and rebooted),
            inotify_persistence_after_reboot_validated=bool(expect_reboot and rebooted),
            fit_executed=False,
            replay_executed=False,
            primary_storage_migrated=False,
            production_promotion=False,
            backup_restore_validated=False,
        )

    return event("verify", path, execute)


def rehearse(report_path, no_other_writers):
    f.require(
        no_other_writers,
        "FRAUD_KIND_NO_OTHER_WRITERS=1 is required for this disruptive local gate",
    )
    baseline = prepare(report_path)
    result = {
        "schema": "fraud_kind_operations_rehearsal_v1",
        "status": "failed",
        "baseline": str(baseline),
        "host_reboot_validated": False,
        "scope": SCOPE,
    }
    path = work() / ("rehearsal-" + uuid4().hex + ".json")
    try:
        result["stop"] = stop(baseline, True)
        result["start"] = start(baseline, True)
        result["verification"] = verify(baseline)
        result["status"] = "success"
        return result
    except BaseException as exc:
        result["reason"] = str(exc)[-2500:]
        # A failed rehearsal should attempt to restore the saved service plan.
        try:
            result["recovery_attempt"] = start(baseline, True)
        except (ValueError, OSError, subprocess.SubprocessError) as recovery:
            result["recovery_error"] = str(recovery)[-2500:]
        raise
    finally:
        f.private_json(path, result)
        print("Relatório do ciclo: " + str(path), flush=True)


def status():
    system = host()
    try:
        owner = docker_owner()
        result = {"status": "inspected", "host": system, "owner": owner}
        if owner["running"]:
            result["resources"] = resources()
        return result
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        return {"status": "not_ready", "host": system, "reason": str(exc)[-2500:]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=("host-check", "status", "prepare", "stop", "start", "verify", "rehearse"),
    )
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--tracking-report", type=Path)
    parser.add_argument("--no-other-writers", action="store_true")
    parser.add_argument("--expect-reboot", action="store_true")
    args = parser.parse_args()
    try:
        with f.exclusive():
            if args.action == "host-check":
                result = checked_host()
            elif args.action == "status":
                result = status()
            elif args.action in {"prepare", "rehearse"}:
                f.require(args.tracking_report is not None, "Explicit --tracking-report required")
                result = (
                    prepare(args.tracking_report, args.baseline)
                    if args.action == "prepare"
                    else rehearse(args.tracking_report, args.no_other_writers)
                )
            else:
                f.require(args.baseline is not None, "Explicit --baseline required")
                result = (
                    verify(args.baseline, args.expect_reboot)
                    if args.action == "verify"
                    else {"stop": stop, "start": start}[args.action](
                        args.baseline, args.no_other_writers
                    )
                )
        print(json.dumps(str(result) if isinstance(result, Path) else result, indent=2))
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as exc:
        print("Operations stopped: " + str(exc), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
