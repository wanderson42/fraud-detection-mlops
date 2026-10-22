"""Helm serving from an externally pinned qualified delivery; explicit Service switch and rollback."""

import argparse
import json
from pathlib import Path
import subprocess
import time
from urllib.error import URLError
from uuid import uuid4

from scripts.platform import kind_foundation as f
from scripts.platform import kind_serving_delivery as d
from scripts.platform import kind_tracking as tracking
from scripts.serving import blue_green_rehearsal as rollout
from scripts.serving import check_docker_runtime as checks

RELEASE = "fraud-serving"
OWNER = "fraud.serving/owner-id"
EVENTS = [
    "baseline_routed",
    "invalid_candidate_rejected_before_start_or_switch",
    "candidate_direct_gates_passed",
    "candidate_routed_on_same_url",
    "candidate_outage_observed",
    "baseline_restored_on_same_url",
]


def values(plan, *, green=False, active="blue", replicas=1):
    return {
        "ownerId": plan["owner_id"],
        "images": {r: v["image"] for r, v in plan["images"].items()},
        "activeSlot": active,
        "greenEnabled": green,
        "greenReplicas": replicas,
        "manifests": {slot: row["manifest_sha256"] for slot, row in plan["releases"].items()},
    }


def validate():
    f.run(["helm", "lint", "--strict", d.CHART, "--kube-version", "1.35.8"])
    f.run(["helm", "template", RELEASE, d.CHART, "--kube-version", "1.35.8"])
    return {"status": "serving_templates_validated", "native_Kubernetes_validated": False}


def objects(kind):
    return json.loads(
        f.kube("get", kind, "-l", "app.kubernetes.io/instance=" + RELEASE, "-o", "json").stdout
    )["items"]


def owned(plan):
    deployments = objects("deployments")
    f.require(
        {p["metadata"]["name"] for p in deployments}
        <= {RELEASE + "-" + s for s in ["blue", "green", "gateway"]},
        "Unexpected serving deployment",
    )
    for kind in ["deployments", "services", "configmaps"]:
        for item in objects(kind):
            f.require(
                item["metadata"].get("annotations", {}).get(OWNER) == plan["owner_id"],
                "Foreign serving resource; do not adopt",
            )
    return deployments


def current_values():
    return json.loads(f.helm("get", "values", RELEASE, "--output", "json").stdout)


def revision():
    rows = json.loads(f.helm("history", RELEASE, "--output", "json").stdout)
    deployed = [int(row["revision"]) for row in rows if row["status"] == "deployed"]
    f.require(len(deployed) == 1, "One deployed Helm revision required")
    return deployed[0]


def apply(plan, desired, *, install=False):
    name = d.work() / ("values-" + uuid4().hex + ".json")
    f.private_json(name, desired)
    if not install:
        owned(plan)
    args = [
        "install" if install else "upgrade",
        RELEASE,
        d.CHART,
        "--values",
        name,
        "--wait",
        "--timeout",
        "180s",
    ]
    f.helm(*args, timeout=200)


def observe(plan):
    deployments = owned(plan)
    result = {"pods": {}, "deployments": {}, "services": {}}
    replicasets = objects("replicasets")
    pods = [p for p in objects("pods") if not p["metadata"].get("deletionTimestamp")]
    for dep in deployments:
        component = dep["metadata"]["labels"]["app.kubernetes.io/component"]
        result["deployments"][component] = dep["metadata"]["uid"]
        selected = [
            p
            for p in pods
            if p["metadata"]["labels"].get("app.kubernetes.io/component") == component
        ]
        f.require(len(selected) == dep["spec"]["replicas"], "Serving pod count differs")
        f.require(
            dep["spec"]["replicas"] in (0, 1)
            and (component == "green" or dep["spec"]["replicas"] == 1),
            "Single-writer replica boundary differs",
        )
        if not selected:
            continue
        pod = selected[0]
        f.require(
            pod["spec"].get("nodeName") == f.CLUSTER + "-control-plane",
            "Serving moved to another node",
        )
        f.require(
            pod["metadata"].get("annotations", {}).get(OWNER) == plan["owner_id"],
            "Pod ownership differs",
        )
        refs = pod["metadata"].get("ownerReferences", [])
        f.require(len(refs) == 1 and refs[0].get("controller"), "Pod must have one controller")
        rs = next((r for r in replicasets if r["metadata"]["uid"] == refs[0]["uid"]), None)
        f.require(
            rs is not None
            and any(
                o["uid"] == dep["metadata"]["uid"] and o.get("controller")
                for o in rs["metadata"].get("ownerReferences", [])
            ),
            "Pod controller chain differs",
        )
        f.require(
            any(
                c["type"] == "Ready" and c["status"] == "True"
                for c in pod.get("status", {}).get("conditions", [])
            ),
            "Serving pod is not Ready",
        )
        expected = (
            {"gateway": "gateway"}
            if component == "gateway"
            else {"api": "runtime", "release": "artifacts"}
        )
        specs = {
            v["name"]: v
            for v in [*pod["spec"].get("initContainers", []), *pod["spec"]["containers"]]
        }
        statuses = {
            v["name"]: v
            for v in [
                *pod["status"].get("initContainerStatuses", []),
                *pod["status"].get("containerStatuses", []),
            ]
        }
        f.require(set(specs) == set(statuses) == set(expected), "Unexpected serving containers")
        for name, role in expected.items():
            row = plan["images"][role]
            f.require(
                specs[name]["image"] == row["image"] and statuses[name]["restartCount"] == 0,
                "Image or restart count differs",
            )
            f.require(
                tracking.image_digest(statuses[name]["imageID"])
                in {tracking.image_digest(v) for v in row["loaded_identities"]},
                "Effective image digest differs",
            )
            if name == "release":
                f.require(
                    statuses[name]["state"].get("terminated", {}).get("exitCode") == 0,
                    "Release init container failed",
                )
            else:
                f.require(statuses[name]["ready"], "Serving container is not ready")
        result["pods"][component] = {
            "name": pod["metadata"]["name"],
            "uid": pod["metadata"]["uid"],
            "images": {n: v["imageID"] for n, v in statuses.items()},
        }
    f.require({"blue", "gateway"} <= set(result["pods"]), "Baseline and gateway required")
    for service in objects("services"):
        f.require(
            service["spec"]["type"] == "ClusterIP", "Only private ClusterIP services allowed"
        )
        result["services"][service["metadata"]["name"]] = {
            "uid": service["metadata"]["uid"],
            "cluster_ip": service["spec"]["clusterIP"],
            "selector": service["spec"]["selector"],
        }
    f.require(
        set(result["services"]) == {RELEASE + "-active", RELEASE + "-gateway"},
        "Unexpected serving services",
    )
    return result


def endpoint_uids():
    slices = json.loads(
        f.kube(
            "get",
            "endpointslices",
            "-l",
            "kubernetes.io/service-name=" + RELEASE + "-active",
            "-o",
            "json",
        ).stdout
    )["items"]
    return {
        ep["targetRef"]["uid"]
        for row in slices
        for ep in row.get("endpoints", [])
        if ep.get("conditions", {}).get("ready") is True
        and ep.get("targetRef", {}).get("kind") == "Pod"
    }


def wait_endpoints(expected, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = endpoint_uids()
        if current == expected:
            return sorted(current)
        time.sleep(0.25)
    raise ValueError("Ready EndpointSlice targets differ from the requested slot")


def direct(pod, route, payload=None):
    f.require(route in {"/ready", "/info", "/score"}, "Unreviewed API route")
    raw = f.kube(
        "exec",
        pod,
        "-c",
        "api",
        "--",
        "/opt/runtime/bin/python",
        "-I",
        "-c",
        rollout.BACKEND_REQUEST,
        route,
        json.dumps(payload, allow_nan=False),
        timeout=30,
    ).stdout
    result = json.loads(raw)
    f.require(
        isinstance(result, list)
        and len(result) == 2
        and type(result[0]) is int
        and isinstance(result[1], dict),
        "Invalid API probe response",
    )
    return result


def check_api(requester, plan, slot):
    manifest = plan["releases"][slot]["manifest"]
    code, ready = requester("/ready")
    f.require(
        code == 200 and ready.get("serving_release_id") == manifest["serving_release_id"],
        "Readiness identity differs",
    )
    code, info = requester("/info")
    f.require(
        code == 200
        and all(
            info.get(key) == manifest[key]
            for key in [
                "serving_release_id",
                "model_id",
                "model_sha256",
                "feature_contract_version",
                "evidence_kind",
            ]
        ),
        "API information identity differs",
    )
    score = checks.check_score(
        None,
        plan["smoke"],
        manifest,
        requester=lambda _, route, payload=None: requester(route, payload),
    )
    code, _ = requester("/score", {})
    f.require(code == 422, "Invalid input must return 422")
    return {"score": score, "invalid_input_http_status": code}


def wait_route(url, plan, slot, timeout=60):
    deadline = time.monotonic() + timeout
    wanted = plan["releases"][slot]["manifest"]["serving_release_id"]
    while time.monotonic() < deadline:
        try:
            code, ready = checks.request_json(url, "/ready")
            if code == 200 and ready.get("serving_release_id") == wanted:
                return check_api(
                    lambda route, payload=None: checks.request_json(url, route, payload),
                    plan,
                    slot,
                )
        except URLError, TimeoutError, ConnectionError:
            pass
        time.sleep(0.25)
    raise ValueError("Gateway did not converge to the requested release identity")


def diagnostics():
    result = {}
    try:
        raw = f.kube(
            "get",
            "pods,deployments,services,endpointslices",
            "-l",
            "app.kubernetes.io/instance=" + RELEASE,
            "-o",
            "json",
            check=False,
        )
        result["resources"] = raw.stdout[-24000:]
        result["logs"] = {}
        for component in ["blue", "green", "gateway"]:
            raw = f.kube(
                "logs",
                "deployment/" + RELEASE + "-" + component,
                "--all-containers=true",
                "--tail=60",
                check=False,
            )
            result["logs"][component] = (raw.stdout + raw.stderr)[-5000:]
    except (ValueError, OSError, subprocess.SubprocessError) as diagnostic:
        result["diagnostic_error"] = type(diagnostic).__name__
    return result


def install_baseline():
    plan = d.plan()
    before = f.inventory()
    present = objects("deployments,services,configmaps")
    if present:
        owned(plan)
        f.require(
            current_values() == values(plan),
            "Existing serving release is not the prepared baseline; preserve it",
        )
    else:
        f.require(
            not objects("pods,replicasets,secrets"),
            "Foreign or incomplete serving resources exist",
        )
        apply(plan, values(plan), install=True)
    seen = observe(plan)
    wait_endpoints({seen["pods"]["blue"]["uid"]})
    check_api(
        lambda route, payload=None: direct(seen["pods"]["blue"]["name"], route, payload),
        plan,
        "blue",
    )
    f.require(f.inventory() == before, "Foundation identities changed")
    return {
        "status": "kind_serving_baseline_ready",
        "release": RELEASE,
        "revision": revision(),
        "resources": seen,
        "primary_storage_migrated": False,
        "production_promotion": False,
    }


def up():
    f.owner()
    d.work().mkdir(mode=0o700, exist_ok=True)
    f.require(d.work().stat().st_mode & 0o077 == 0, "Serving workspace must be private")
    path = d.work() / ("installation-" + uuid4().hex + ".json")
    report = {"status": "failed", "report": str(path)}
    try:
        report.update(install_baseline())
    except BaseException as exc:
        report.update(reason=str(exc)[-3000:], diagnostics=diagnostics())
        raise
    finally:
        f.private_json(path, report)
        print("Relatório da instalação: " + str(path), flush=True)
    return report


def restore(plan, baseline_revision):
    owned(plan)
    f.helm("rollback", RELEASE, str(baseline_revision), "--wait", "--timeout", "180s", timeout=200)
    f.require(current_values() == values(plan), "Helm rollback did not restore baseline values")
    seen = observe(plan)
    f.require("green" not in seen["deployments"], "Candidate deployment remains after rollback")
    wait_endpoints({seen["pods"]["blue"]["uid"]})
    return seen


def rehearse():
    plan = d.plan()
    owned(plan)
    f.require(current_values() == values(plan), "Start the gate with the prepared baseline only")
    baseline_revision = revision()
    before = observe(plan)
    foundation = f.inventory()
    attempt = uuid4().hex
    path = d.work() / ("rehearsal-" + attempt + ".json")
    result = {
        "schema": "fraud_kind_serving_rehearsal_v1",
        "status": "failed",
        "events": [],
        "scope": "qualified_synthetic_runtime_Helm_Service_blue_green_and_native_rollback_not_reference_promotion",
        "report": str(path),
        "controller_sha256": d.sha(Path(__file__)),
        "source_commit": plan["source_commit"],
        "descriptor_sha256": plan["descriptor_sha256"],
        "qualified_runtime_image_id": plan["qualified_runtime_image_id"],
        "image_receipt": plan["images"],
        "preparation_source_pins": plan["source_pins"],
        "preparation_owner_id": plan["owner_id"],
        "node_uid": plan["node_uid"],
        "gateway_requested": plan["gateway_requested"],
        "baseline_manifest_sha256": plan["releases"]["blue"]["manifest_sha256"],
        "candidate_manifest_sha256": plan["releases"]["green"]["manifest_sha256"],
        "baseline_release_id": plan["releases"]["blue"]["manifest"]["serving_release_id"],
        "candidate_release_id": plan["releases"]["green"]["manifest"]["serving_release_id"],
        "model_sha256": plan["releases"]["blue"]["manifest"]["model_sha256"],
        "artifact_carrier_built": plan["artifact_carrier_built"],
        "baseline_revision": baseline_revision,
        "before": before,
        "foundation_before": foundation,
        "runtime_rebuilt": False,
        "fit_executed": False,
        "historical_reference_accessed": False,
        "production_promotion": False,
        "permanent_production_deployment_created": False,
        "synthetic_baseline_left_installed": True,
        "zero_downtime_validated": False,
        "primary_storage_migrated": False,
        "registry_published": False,
    }
    started = time.monotonic()
    changed = False
    try:
        with d.forward(before["pods"]["gateway"]["name"]) as url:
            result["gateway_url"] = url
            result["gateway_route_scope"] = (
                "same ephemeral loopback URL via stable gateway pod and active ClusterIP Service"
            )
            wait_endpoints({before["pods"]["blue"]["uid"]})
            result["baseline_http"] = wait_route(url, plan, "blue")
            result["events"].append(EVENTS[0])
            try:
                checks.read_release(
                    Path(plan["directory"]) / "invalid",
                    plan["releases"]["blue"]["manifest_sha256"],
                )
            except checks.CheckFailed:
                pass
            else:
                raise ValueError("Invalid candidate was not rejected")
            f.require(current_values() == values(plan), "Invalid preflight changed routing")
            result["events"].append(EVENTS[1])
            changed = True
            apply(plan, values(plan, green=True))
            candidate = observe(plan)
            f.require(
                candidate["pods"]["blue"] == before["pods"]["blue"]
                and candidate["pods"]["gateway"] == before["pods"]["gateway"],
                "Candidate installation replaced baseline or gateway",
            )
            result["candidate_direct_http"] = check_api(
                lambda route, payload=None: direct(
                    candidate["pods"]["green"]["name"], route, payload
                ),
                plan,
                "green",
            )
            result["candidate"] = candidate
            result["events"].append(EVENTS[2])
            apply(plan, values(plan, green=True, active="green"))
            routed = observe(plan)
            f.require(routed["pods"] == candidate["pods"], "Route switch restarted pods")
            f.require(
                routed["services"][RELEASE + "-active"]["uid"]
                == before["services"][RELEASE + "-active"]["uid"]
                and routed["services"][RELEASE + "-active"]["cluster_ip"]
                == before["services"][RELEASE + "-active"]["cluster_ip"],
                "Active Service identity changed",
            )
            result["candidate_ready_endpoint_UIDs"] = wait_endpoints(
                {candidate["pods"]["green"]["uid"]}
            )
            result["candidate_gateway_http"] = wait_route(url, plan, "green")
            result["events"].append(EVENTS[3])
            apply(plan, values(plan, green=True, active="green", replicas=0))
            result["outage_ready_endpoint_UIDs"] = wait_endpoints(set())
            deadline = time.monotonic() + 30
            failure = None
            while time.monotonic() < deadline:
                try:
                    code, body = checks.request_json(url, "/score", plan["smoke"]["request"])
                    if code == 502 and body.get("error") == "upstream_unavailable":
                        failure = code
                        break
                except URLError, TimeoutError, ConnectionError:
                    pass
                time.sleep(0.25)
            f.require(failure == 502, "Controlled candidate outage was not observed at gateway")
            result["observed_failure_http_status"] = failure
            result["events"].append(EVENTS[4])
            rollback_start = time.monotonic()
            after = restore(plan, baseline_revision)
            result["rollback_http"] = wait_route(url, plan, "blue")
            result["rollback_observation_seconds"] = time.monotonic() - rollback_start
            f.require(
                after["pods"] == before["pods"]
                and after["services"] == before["services"]
                and after["deployments"] == before["deployments"],
                "Rollback replaced baseline/gateway or Service identities",
            )
            result["after"] = after
            result["events"].append(EVENTS[5])
        f.require(f.inventory() == foundation, "Foundation identities changed")
        # Includes archive/pins, release files and loaded CRI identities after the exercise.
        d.plan()
        f.require(result["events"] == EVENTS, "Incomplete serving event sequence")
        result.update(
            status="success",
            rollback_verified=True,
            native_Helm_rollback_validated=True,
            original_delivery_bytes_unchanged=True,
            candidate_deployment_removed=True,
            baseline_and_gateway_pods_unchanged=True,
            foundation_after=foundation,
            events_complete=result["events"] == EVENTS,
            final_revision=revision(),
        )
    except BaseException as exc:
        result["reason"] = str(exc)[-3000:]
        if changed:
            try:
                seen = restore(plan, baseline_revision)
                check_api(
                    lambda route, payload=None: direct(
                        seen["pods"]["blue"]["name"], route, payload
                    ),
                    plan,
                    "blue",
                )
                result["failure_recovery_baseline_restored"] = True
            except (
                ValueError,
                OSError,
                checks.CheckFailed,
                subprocess.SubprocessError,
            ) as recovery:
                result["failure_recovery_error"] = str(recovery)[-2000:]
        try:
            result["diagnostics"] = f.kube(
                "get",
                "pods,deployments,services,endpointslices",
                "-l",
                "app.kubernetes.io/instance=" + RELEASE,
                "-o",
                "json",
                check=False,
            ).stdout[-16000:]
            result["logs"] = {}
            for component in ["blue", "green", "gateway"]:
                logs = f.kube(
                    "logs",
                    "deployment/" + RELEASE + "-" + component,
                    "--all-containers=true",
                    "--tail=60",
                    check=False,
                )
                result["logs"][component] = (logs.stdout + logs.stderr)[-5000:]
        except (ValueError, OSError, subprocess.SubprocessError) as diagnostic:
            result["diagnostic_error"] = type(diagnostic).__name__
        raise
    finally:
        result["elapsed_seconds"] = time.monotonic() - started
        f.private_json(path, result)
        print("Relatório: " + str(path), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["validate", "prepare", "up", "status", "rehearse"])
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--expected-source-sha")
    parser.add_argument("--expected-descriptor-sha256")
    args = parser.parse_args()
    if args.command == "prepare" and not all(
        [args.directory, args.expected_source_sha, args.expected_descriptor_sha256]
    ):
        parser.error(
            "prepare requires --directory, --expected-source-sha and --expected-descriptor-sha256 from the trusted producer"
        )
    try:
        if args.command == "validate":
            value = validate()
        else:
            with f.exclusive():
                if args.command == "prepare":
                    value = d.prepare(
                        args.directory, args.expected_source_sha, args.expected_descriptor_sha256
                    )
                elif args.command == "up":
                    value = up()
                elif args.command == "status":
                    f.owner()
                    value = {"status": "inspected", "diagnostics": diagnostics()}
                else:
                    value = rehearse()
        print(json.dumps(value, indent=2))
    except (ValueError, OSError, checks.CheckFailed, subprocess.SubprocessError) as exc:
        parser.exit(1, "Serving stopped: " + str(exc) + "\n")


if __name__ == "__main__":
    main()
