"""Prepare, apply and recover the bounded session dashboard extension."""

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import time
from uuid import uuid4

from scripts.platform import kind_foundation as f
from scripts.platform import kind_observability as k
from scripts.platform import session_observability as mode
from scripts.platform.container_images import inspect_image
from scripts.session_monitoring import controller as s

MARKER = "components.json"
FILES = ("image.json", "prepared.json", "enabled.json")
SCHEMA = "fraud_session_components_activation_v1"


def enabled():
    path = k.work() / "session-monitoring" / MARKER
    if not path.exists():
        return False
    value = json.loads(path.read_bytes())
    f.require(
        path.stat().st_mode & 0o077 == 0
        and value["schema"] == SCHEMA
        and value["owner_id"] == k.credentials()["owner_id"]
        and value["node_uid"] == f.owner()["node_uid"],
        "Component activation ownership differs",
    )
    image = json.loads((path.parent / "image.json").read_bytes())
    active = json.loads((path.parent / "enabled.json").read_bytes())
    f.require(
        image["image"] == active["image"] == value["image"]
        and active["binding"] == value["binding"],
        "Component activation image or binding differs",
    )
    return True


def capability(image):
    for name in (
        "scripts/session_monitoring/details.py",
        "scripts/session_monitoring/server.py",
        "scripts/session_monitoring/contract.py",
    ):
        f.require(
            image["source_pins"].get(name) == mode.digest(f.ROOT / name),
            "Receiver image lacks current aggregate details; use component maintenance",
        )


def work(key):
    f.require(re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", key), "Invalid maintenance ID")
    path = f.managed_destination(s.work() / "components-maintenance" / key, "storage")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    f.require(path.stat().st_mode & 0o077 == 0, "Private maintenance directory required")
    return path


def sha_value(value):
    return hashlib.sha256(s.contract.dumps(value)).hexdigest()


@contextmanager
def no_watch():
    with (s.work() / "watch.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def sources(directory):
    return mode.session_acceptance.source_files(directory)


def protected():
    # Recovery guards must remain usable when a receiver rollout is unhealthy.
    pvcs = {}
    for role in ("prometheus", "grafana"):
        item = k.owned(k.get("pvc", k.RELEASE + "-" + role))
        pvcs[role] = {"uid": item["metadata"]["uid"], "volume_name": item["spec"]["volumeName"]}
    return {
        "resources": mode.normalized(k.protected_inventory()),
        "pvcs": pvcs,
        "active": s.session.flow.active(),
    }


def build(directory):
    record = directory / "candidate-image.json"
    if record.exists():
        value = json.loads(record.read_bytes())
        f.require(value["source_pins"] == s.pins(), "Retained candidate sources differ")
        f.require(
            inspect_image(value["image"])["id"] in value["loaded_identities"],
            "Retained candidate image differs",
        )
        capability(value)
        return value
    tag = "fraud-session-monitor:" + uuid4().hex
    k.command(
        [
            "docker",
            "build",
            "--platform",
            "linux/amd64",
            "--file",
            "infra/kind/session-monitoring/Dockerfile",
            "--tag",
            tag,
            ".",
        ],
        timeout=900,
    )
    with TemporaryDirectory(prefix="candidate-", dir=directory) as temp:
        archive = Path(temp) / "image.tar"
        k.command(
            ["docker", "image", "save", "--platform", "linux/amd64", "--output", archive, tag]
        )
        archive.chmod(0o600)
        k.command(["kind", "load", "image-archive", "--name", f.CLUSTER, archive])
    # Isolated runtime import and strict extended-wire contract smoke check.
    code = "from scripts.session_monitoring import details; from scripts.session_monitoring.server import Projection; v={k:(None if k in details.NULLABLE else 0) for k in details.KEYS}; details.validate(v); print('details_contract_ready')"
    done = k.command(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--entrypoint",
            "python",
            tag,
            "-c",
            code,
        ]
    )
    f.require(
        done.stdout.strip() == "details_contract_ready", "Candidate runtime qualification failed"
    )
    loaded = inspect_image(tag)
    value = {
        "image": tag,
        "loaded_identities": [loaded["id"], *loaded.get("repoDigests", [])],
        "node_uid": f.owner()["node_uid"],
        "owner_id": k.credentials()["owner_id"],
        "source_pins": s.pins(),
    }
    capability(value)
    f.private_json(record, value)
    return value


def prepare(key, session_id):
    s.committed()
    mode.profile()
    f.require(not enabled(), "Components already enabled; preserve this activation")
    target = work(key)
    path = target / "plan.json"
    if path.exists():
        plan = read_plan(key)
        f.require(
            plan["binding"]["session_id"] == session_id, "Maintenance belongs to another session"
        )
        return approval(plan)
    directory, binding = s.bound(session_id)
    _, receipt = s.session.load(session_id)
    mode.session_acceptance.snapshot(receipt["flow_id"], receipt)
    before_hashes = sources(directory)
    observed = s.collector.collect(directory, binding, include_details=True)
    f.require(observed["producer_status"] == "complete", "Complete the session before maintenance")
    prior = {name: json.loads((s.work() / name).read_bytes()) for name in FILES}
    backup_hashes = {}
    for name in FILES:
        original = (s.work() / name).read_bytes()
        backup = target / ("before-" + name)
        if backup.exists():
            f.require(backup.read_bytes() == original, "Retained source backup differs")
        else:
            backup.write_bytes(original)
            backup.chmod(0o600)
        backup_hashes[name] = mode.digest(backup)
    live = json.loads(k.helm("get", "values", k.RELEASE, "--output", "json").stdout)
    f.require(live == k.values(True), "Live Helm values differ from owned installation")
    before = protected()
    candidate = build(target)
    desired = {
        **live,
        "sessionComponents": True,
        "sessionTelemetry": {**live["sessionTelemetry"], "image": candidate["image"]},
    }
    values = target / "candidate-values.json"
    f.private_json(values, desired)
    k.command(["helm", "lint", "--strict", k.CHART, "--values", values])
    k.command(
        ["helm", "template", k.RELEASE, k.CHART, "--namespace", f.NAMESPACE, "--values", values]
    )
    f.require(
        before == protected() and before_hashes == sources(directory),
        "Protected state changed during preparation",
    )
    plan = {
        "schema": "fraud_session_components_maintenance_v1",
        "id": key,
        "binding": binding,
        "source_pins": s.pins(),
        "before": prior,
        "before_file_sha256": backup_hashes,
        "before_values": live,
        "desired_values": desired,
        "protected": before,
        "accepted_sources": before_hashes,
        "candidate": candidate,
    }
    f.private_json(path, plan)
    return approval(plan)


def approval(plan):
    return {
        "status": "session_components_prepared_awaiting_approval",
        "maintenance_id": plan["id"],
        "binding": plan["binding"],
        "candidate_image": plan["candidate"]["image"],
        "approval_sha256": sha_value(plan),
        "rollback_approval_sha256": sha_value(
            {"action": "rollback", "plan_sha256": sha_value(plan)}
        ),
        "dashboards": [
            "fraud-kind-overview",
            "fraud-replay-ops",
            "fraud-feature-health",
            "fraud-triage-quality",
        ],
        "deployment_modified": False,
        "native_acceptance": False,
        "champion_modified": False,
    }


def read_plan(key):
    plan = json.loads((work(key) / "plan.json").read_bytes())
    f.require(
        plan["schema"] == "fraud_session_components_maintenance_v1"
        and plan["id"] == key
        and plan["source_pins"] == s.pins()
        and plan["candidate"]["node_uid"] == f.owner()["node_uid"]
        and plan["candidate"]["owner_id"] == k.credentials()["owner_id"],
        "Maintenance sources or ownership differ",
    )
    for name, wanted in plan["before_file_sha256"].items():
        f.require(mode.digest(work(key) / ("before-" + name)) == wanted, "Recovery backup changed")
    return plan


def restore_bytes(path, data):
    # Keep the exact accepted JSON bytes, including original formatting.
    temporary = path.with_name(path.name + ".recovery-" + uuid4().hex)
    try:
        with temporary.open("xb") as stream:
            temporary.chmod(0o600)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def verify_dashboards():
    paths = sorted((k.CHART / "files/components").glob("*.json"))
    f.require(len(paths) == 4, "Four reviewed component dashboards required")
    with k.forward("grafana") as grafana:
        for path in paths:
            wanted = json.loads(path.read_bytes())
            got = k.available_http(grafana, "/api/dashboards/uid/" + wanted["uid"], auth=True)[
                "dashboard"
            ]
            f.require(
                k.panel_contract(wanted) == k.panel_contract(got)
                and wanted["title"] == got["title"],
                "Component dashboard differs: " + wanted["uid"],
            )
            for a, b in zip(wanted["panels"], got["panels"], strict=True):
                f.require(
                    a.get("options") == b.get("options")
                    and a.get("fieldConfig") == b.get("fieldConfig"),
                    "Component presentation differs",
                )
    return [json.loads(path.read_bytes())["uid"] for path in paths]


def verify_metrics(uri, prom, grafana, projection):
    s.check_projection(uri, prom, grafana, projection, True)
    values = projection["details"]
    label = '{session_sha256="' + projection["binding"]["session_sha256"] + '"}'
    deadline = time.monotonic() + 90
    while True:
        actual = {
            name: list(k.query(prom, "fraud_session_detail_" + name + label).values())
            for name in values
        }
        if all(
            (
                not actual[name]
                if amount is None
                else len(actual[name]) == 1 and abs(actual[name][0] - amount) <= 1e-8
            )
            for name, amount in values.items()
        ):
            break
        f.require(
            time.monotonic() < deadline,
            "Prometheus detail values differ or unsupported values are exposed",
        )
        time.sleep(2)
    from urllib.parse import urlencode

    query = "fraud_session_detail_quality_mature_transactions" + label
    response = k.http(
        grafana,
        "/api/datasources/proxy/uid/fraud-prometheus/api/v1/query?" + urlencode({"query": query}),
        auth=True,
    )
    got = response["data"]["result"]
    f.require(
        len(got) == 1 and float(got[0]["value"][1]) == values["quality_mature_transactions"],
        "Grafana detail datasource readback differs",
    )


def verify(key):
    plan = read_plan(key)
    f.require(enabled(), "Components are not active")
    directory, binding = s.bound(plan["binding"]["session_id"])
    f.require(
        binding == plan["binding"] and plan["accepted_sources"] == sources(directory),
        "Accepted session changed",
    )
    projection = s.collector.collect(directory, binding, include_details=True)
    with (
        no_watch(),
        s.forward() as receiver,
        k.forward("prometheus") as prom,
        k.forward("grafana") as grafana,
    ):
        s.put(receiver, "/projection", projection)
        verify_metrics(receiver, prom, grafana, projection)
    dashboards = verify_dashboards()
    f.require(
        protected() == plan["protected"] and sources(directory) == plan["accepted_sources"],
        "Protected resources or accepted sources changed",
    )
    result = {
        "schema": "fraud_session_components_native_v1",
        "status": "session_components_verified",
        "binding": binding,
        "counts": projection["counts"],
        "details": projection["details"],
        "provisioned_component_UIDs": dashboards,
        "original_session_dashboard_verified": True,
        "metric_values_verified": True,
        "unsupported_metrics_absent_verified": True,
        "monitoring_PVCs_preserved": True,
        "accepted_source_bytes_unchanged": plan["accepted_sources"],
        "champion_modified": False,
        "fit_executed": False,
        "visual_review_validated": False,
        "alert_firing_validated": False,
        "live_arrivals_validated": False,
    }
    s.atomic(work(key) / "verification.json", result)
    return result


def apply(key, digest):
    plan = read_plan(key)
    f.require(digest == sha_value(plan), "Approval differs")
    with no_watch():
        f.require(protected() == plan["protected"], "Protected state changed before apply")
        current = json.loads(k.helm("get", "values", k.RELEASE, "--output", "json").stdout)
        f.require(
            current in (plan["before_values"], plan["desired_values"]),
            "Helm values changed outside maintenance",
        )
        directory, _ = s.session.load(plan["binding"]["session_id"])
        f.require(sources(directory) == plan["accepted_sources"], "Accepted session changed")
        inspect = inspect_image(plan["candidate"]["image"])
        f.require(
            inspect["id"] in plan["candidate"]["loaded_identities"], "Candidate image changed"
        )
        s.atomic(work(key) / "applying.json", {"plan_sha256": sha_value(plan)})
        for name in FILES:
            value = (
                plan["candidate"]
                if name == "image.json"
                else {
                    **plan["before"][name],
                    "image": plan["candidate"]["image"],
                    "source_pins": s.pins(),
                }
            )
            s.atomic(s.work() / name, value)
        s.atomic(
            s.work() / MARKER,
            {
                "schema": SCHEMA,
                "owner_id": k.credentials()["owner_id"],
                "node_uid": f.owner()["node_uid"],
                "binding": plan["binding"],
                "image": plan["candidate"]["image"],
            },
        )
        f.require(k.values(True) == plan["desired_values"], "Desired component values differ")
        k.install(True)
        k.inventory()
    result = verify(key)
    s.atomic(work(key) / "applied.json", result)
    return result


def rollback(key, digest):
    plan = read_plan(key)
    f.require(
        digest == sha_value({"action": "rollback", "plan_sha256": sha_value(plan)}),
        "Rollback approval differs",
    )
    f.require((work(key) / "applying.json").exists(), "No application to roll back")
    with no_watch():
        f.require(protected() == plan["protected"], "Protected state changed before rollback")
        marker = s.work() / MARKER
        if marker.exists():
            enabled()  # Verify ownership and exact active image before removing only our marker.
            marker.unlink()
        for name in FILES:
            restore_bytes(s.work() / name, (work(key) / ("before-" + name)).read_bytes())
        values = work(key) / "rollback-values.json"
        f.private_json(values, plan["before_values"]) if not values.exists() else f.require(
            json.loads(values.read_bytes()) == plan["before_values"], "Rollback values differ"
        )
        k.helm(
            "upgrade",
            "--install",
            k.RELEASE,
            k.CHART,
            "--values",
            values,
            "--wait",
            "--timeout",
            "5m",
            timeout=360,
        )
        k.inventory()
        mode.verify_http()
        f.require(protected() == plan["protected"], "Protected state changed after rollback")
    result = {
        "status": "session_components_rolled_back",
        "champion_modified": False,
        "accepted_receipts_preserved": True,
    }
    s.atomic(work(key) / "rolled-back.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "apply", "verify", "rollback"))
    parser.add_argument("--id", default="grafana-components-v1")
    parser.add_argument("--session-id", default="repro-arrival-200-v1")
    parser.add_argument("--approval-sha256")
    parser.add_argument("--no-other-writers", action="store_true")
    args = parser.parse_args()
    try:
        f.require(
            args.action == "verify" or args.no_other_writers,
            "Declare no other writers before maintenance",
        )
        with f.exclusive():
            if args.action == "prepare":
                result = prepare(args.id, args.session_id)
            elif args.action == "verify":
                result = verify(args.id)
            else:
                result = {"apply": apply, "rollback": rollback}[args.action](
                    args.id, args.approval_sha256
                )
        print(json.dumps(result, indent=2))
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(1, "Component maintenance stopped; preserve resources: " + str(exc) + "\n")


if __name__ == "__main__":
    main()
