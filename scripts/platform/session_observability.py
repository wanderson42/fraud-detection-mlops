"""Bootstrap the existing observability release for an identified session only."""

import argparse
import base64
import fcntl
import hashlib
import json
from pathlib import Path
import subprocess
import time
from urllib.parse import urlencode
from uuid import uuid4

from scripts.fraud_portal import session_acceptance
from scripts.platform import kind_foundation as f
from scripts.platform import kind_observability as k
from scripts.session_monitoring import controller as s

TRANSITION = "references/session_only_monitoring_source_transition_v1.json"
SOURCE = "scripts/platform/session_observability.py"
CHANGED = {
    "scripts/platform/kind_observability.py",
    "scripts/platform/source_compatibility.py",
    "scripts/session_monitoring/controller.py",
    "infra/helm/fraud-observability/templates/config.yaml",
    "infra/helm/fraud-observability/templates/storage.yaml",
    "infra/helm/fraud-observability/templates/workloads.yaml",
    "infra/helm/fraud-observability/values.yaml",
    "infra/helm/fraud-observability/values.schema.json",
}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def normalized(value):
    return json.loads(json.dumps(value))


def source_pins():
    return {name: digest(f.ROOT / name) for name in sorted({SOURCE, TRANSITION, *CHANGED})}


def committed_file(name):
    done = subprocess.run(
        ["git", "show", "HEAD:" + name], cwd=f.ROOT, capture_output=True, check=False
    )
    f.require(
        done.returncode == 0 and hashlib.sha256(done.stdout).hexdigest() == digest(f.ROOT / name),
        "Commit the reviewed implementation before bootstrap: " + name,
    )


def profile():
    path = f.managed_destination(k.work() / "session-only.json", "storage")
    value = json.loads(path.read_bytes())
    f.require(path.stat().st_mode & 0o077 == 0, "Private profile required")
    f.require(
        value.get("schema") == "fraud_session_only_observability_profile_v1"
        and value["owner_id"] == k.credentials()["owner_id"]
        and value["node_uid"] == f.owner()["node_uid"]
        and value["source_pins"] == source_pins(),
        "Session-only profile ownership/source differs; preserve it",
    )
    f.require(
        not (k.work() / "snapshot.json").exists(),
        "A legacy replay snapshot cannot share this profile",
    )
    return value


def validate_receiver(pod):
    spec = pod["spec"]
    containers = spec["containers"]
    f.require(
        len(containers) == 1 and containers[0]["name"] == "session-exporter",
        "Session-only pod must contain only the aggregate receiver",
    )
    f.require(
        containers[0].get("volumeMounts")
        == [{"name": "session-binding", "mountPath": "/binding", "readOnly": True}]
        and spec.get("automountServiceAccountToken") is False
        and spec.get("enableServiceLinks") is False
        and all(
            "hostPath" not in volume and "persistentVolumeClaim" not in volume
            for volume in spec.get("volumes", [])
        ),
        "Receiver must not mount raw replay/session data or host paths",
    )


def prepare(key):
    for name in source_pins():
        committed_file(name)
    s.committed()
    directory, value = s.session.load(key)
    session_acceptance.snapshot(value["flow_id"], value)
    binding, _ = s.collector.identity(directory)
    k.initialize()
    k.image_record()  # Reuse the already loaded, pinned Prometheus/Grafana images.
    path = k.work() / "session-only.json"
    if path.exists():
        old = profile()
        f.require(
            old["session_id"] == key and old["session_sha256"] == binding["session_sha256"],
            "Profile is bound to another session",
        )
        return old
    f.require(
        not (k.work() / "snapshot.json").exists() and not (k.work() / "imported.json").exists(),
        "Do not convert an existing legacy replay installation",
    )
    f.require(not k.releases(), "Session-only bootstrap requires a fresh observability release")
    leftovers = json.loads(
        f.kube(
            "get",
            "pods,pvc,services,deployments,configmaps",
            "-l",
            "app.kubernetes.io/instance=" + k.RELEASE,
            "-o",
            "json",
        ).stdout
    )["items"]
    f.require(not leftovers, "Existing observability resources require review; refuse adoption")
    record = {
        "schema": "fraud_session_only_observability_profile_v1",
        "owner_id": k.credentials()["owner_id"],
        "node_uid": f.owner()["node_uid"],
        "session_id": key,
        "session_sha256": binding["session_sha256"],
        "source_pins": source_pins(),
        "foundation": f.inventory(),
        "protected": normalized(k.protected_inventory()),
        "legacy_replay_installed": False,
    }
    f.private_json(path, record)
    return record


def install(key):
    before = prepare(key)
    s.build()
    s.prepare(key)
    proposed = json.loads((s.work() / "prepared.json").read_bytes())
    active_before = s.session.flow.active()
    existing = k.releases()
    for kind, roles in {
        "deployment": k.ROLES,
        "service": k.ROLES,
        "pvc": ("prometheus", "grafana"),
        "configmap": ("config", "dashboards", "session-binding"),
    }.items():
        for role in roles:
            item = k.get(kind, k.RELEASE + "-" + role)
            if item:
                k.owned(item)
    if existing:
        actual = json.loads(k.helm("get", "values", k.RELEASE, "--output", "json").stdout)
        wanted = k.values(True)
        # Enablement must match the exact retained preparation when resuming.
        wanted.update(
            legacyReplay=False,
            sessionTelemetry={
                "enabled": True,
                "image": proposed["image"],
                "binding": proposed["binding"],
            },
        )
        f.require(actual == wanted, "Retained Helm values differ; preserve resources")
    f.require(
        before["foundation"] == f.inventory()
        and before["protected"] == normalized(k.protected_inventory()),
        "Protected identities changed before installation",
    )
    k.ensure_secret()
    encoded = base64.b64encode(s.token().encode()).decode()
    secret = k.get("secret", s.SECRET)
    if secret:
        k.owned(secret)
        f.require(secret["data"] == {"token": encoded}, "Receiver token differs")
    else:
        f.kube(
            "create",
            "-f",
            "-",
            data=json.dumps(
                {
                    "apiVersion": "v1",
                    "kind": "Secret",
                    "metadata": {
                        "name": s.SECRET,
                        "annotations": {k.ANNOTATION: before["owner_id"]},
                    },
                    "type": "Opaque",
                    "data": {"token": encoded},
                }
            ),
        )
    s.atomic(s.work() / "enabled.json", proposed)
    k.validate()
    k.install(True)
    inventory = k.inventory()
    verification = verify_http()
    f.require(
        before["foundation"] == f.inventory()
        and before["protected"] == normalized(k.protected_inventory())
        and active_before == s.session.flow.active(),
        "Protected platform or active model changed",
    )
    result = {
        "status": "session_only_observability_ready",
        "binding": proposed["binding"],
        "inventory": inventory,
        "verification": verification,
        "legacy_replay_installed": False,
        "champion_modified": False,
    }
    s.atomic(s.work() / "installation.json", result)
    return result


def verify_http():
    import yaml

    profile()
    expected_target = "http://" + k.RELEASE + "-exporter:19102/metrics"
    expression = 'up{job="fraud-product-session"}'
    with k.forward("prometheus") as prometheus, k.forward("grafana") as grafana:
        deadline = time.monotonic() + 90
        while True:
            targets = k.http(prometheus, "/api/v1/targets")["data"]["activeTargets"]
            if (
                len(targets) == 1
                and targets[0]["labels"]["job"] == "fraud-product-session"
                and targets[0]["scrapeUrl"] == expected_target
                and targets[0]["health"] == "up"
            ):
                break
            f.require(time.monotonic() < deadline, "Session scrape target differs or is not ready")
            time.sleep(2)
        rules = yaml.safe_load((k.CHART / "files/session/alerts.yml").read_text())["groups"][0][
            "rules"
        ]
        while True:
            loaded = [
                rule
                for group in k.http(prometheus, "/api/v1/rules")["data"]["groups"]
                for rule in group["rules"]
            ]
            if {r["name"] for r in loaded} == {r["alert"] for r in rules} and all(
                r["health"] == "ok" for r in loaded
            ):
                break
            f.require(time.monotonic() < deadline, "Session alert rules differ or are unhealthy")
            time.sleep(2)
        for rule in rules:
            actual = next(r for r in loaded if r["name"] == rule["alert"])

            def formatted(expr):
                return k.http(prometheus, "/api/v1/format_query?" + urlencode({"query": expr}))[
                    "data"
                ]

            amount = rule["for"]
            duration = int(amount[:-1]) * (60 if amount.endswith("m") else 1)
            f.require(
                formatted(actual["query"]) == formatted(rule["expr"])
                and actual["duration"] == duration
                and actual["labels"] == rule["labels"]
                and actual["annotations"] == rule["annotations"],
                "Session rule expression/metadata differs",
            )
        datasource = k.available_http(grafana, "/api/datasources/uid/fraud-prometheus", auth=True)
        f.require(
            datasource["type"] == "prometheus"
            and datasource["url"] == "http://" + k.RELEASE + "-prometheus:9090",
            "Grafana datasource differs",
        )
        f.require(
            k.available_http(
                grafana, "/api/datasources/uid/fraud-prometheus/health", auth=True
            ).get("status")
            == "OK",
            "Grafana Prometheus plugin is unhealthy",
        )
        proxy = k.http(
            grafana,
            "/api/datasources/proxy/uid/fraud-prometheus/api/v1/query?"
            + urlencode({"query": expression}),
            auth=True,
        )
        f.require(
            proxy["status"] == "success"
            and len(proxy["data"]["result"]) == 1
            and float(proxy["data"]["result"][0]["value"][1]) == 1,
            "Grafana proxy cannot query the session target",
        )
        wanted = json.loads((k.CHART / "files/session/dashboard.json").read_bytes())
        actual = k.available_http(grafana, "/api/dashboards/uid/" + wanted["uid"], auth=True)[
            "dashboard"
        ]
        f.require(
            actual["uid"] == wanted["uid"]
            and actual["title"] == wanted["title"]
            and k.panel_contract(actual) == k.panel_contract(wanted),
            "Session dashboard differs",
        )
    return {
        "scrape_target_verified": True,
        "healthy_alert_rules": len(loaded),
        "grafana_datasource_proxy_verified": True,
        "session_dashboard_verified": True,
        "legacy_replay_verified": False,
        "visual_review_validated": False,
        "alert_firing_validated": False,
    }


def rehearse(key):
    own = profile()
    f.require(key == own["session_id"], "Explicit session does not match this profile")
    directory, binding = s.bound(key)
    _, value = s.session.load(key)
    session_acceptance.snapshot(value["flow_id"], value)
    before = session_acceptance.source_files(directory)
    active = s.session.flow.active()
    inventory, protected = k.inventory(), k.protected_inventory()
    projection = s.collector.collect(directory, binding)
    f.require(
        projection["counts"]["events"] == binding["expected_events"]
        and projection["counts"]["decisions"] == binding["expected_decisions"],
        "Complete the session before this rehearsal",
    )
    with (s.work() / "watch.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with (
            s.forward() as receiver,
            k.forward("prometheus") as prometheus,
            k.forward("grafana") as grafana,
        ):
            s.put(receiver, "/projection", projection)
            s.check_projection(receiver, prometheus, grafana, projection, True)
            print(
                "Sessão publicada; verificando expiração e retomada, sem novas inferências...",
                flush=True,
            )
            time.sleep(35)
            s.check_projection(receiver, prometheus, grafana, projection, False)
            projection = s.collector.collect(directory, binding)
            s.put(receiver, "/projection", projection)
            s.check_projection(receiver, prometheus, grafana, projection, True)
    checks = verify_http()
    f.require(
        before == session_acceptance.source_files(directory)
        and active == s.session.flow.active()
        and protected == k.protected_inventory()
        and inventory["pvcs"] == k.inventory()["pvcs"],
        "Session, model or protected resources changed",
    )
    result = {
        "schema": "fraud_session_only_monitoring_native_v1",
        "status": "success",
        "binding": binding,
        "counts": projection["counts"],
        "HTTP_latency_ms": projection["latency_ms"],
        "same_session_Grafana_validated": True,
        "stale_collection_and_resume_validated": True,
        "accepted_source_bytes_unchanged": before,
        "verification": checks,
        "legacy_replay_verified": False,
        "live_arrivals_native_validated": False,
        "visual_review_validated": False,
        "fit_executed": False,
        "champion_modified": False,
    }
    s.atomic(s.work() / ("native-session-only-" + uuid4().hex + ".json"), result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "rehearse"))
    parser.add_argument("--session-id", required=True)
    args = parser.parse_args()
    try:
        with f.exclusive():
            result = {"install": install, "rehearse": rehearse}[args.action](args.session_id)
        print(json.dumps(result, indent=2))
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as exc:
        parser.exit(1, "Session-only observability stopped: " + str(exc) + "\n")


if __name__ == "__main__":
    main()
