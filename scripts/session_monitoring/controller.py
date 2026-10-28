"""Operate one session projection through the existing owned observability release."""

import argparse
import base64
import fcntl
import hashlib
import json
from pathlib import Path
import secrets
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from urllib.request import ProxyHandler, Request, build_opener
from uuid import uuid4

from scripts.platform import kind_foundation as f
from scripts.platform import kind_observability as k
from scripts.platform import source_compatibility
from scripts.platform.container_images import inspect_image
from scripts.product_session import controller as session
from scripts.product_session import reader
from scripts.session_monitoring import collector, contract

SECRET = k.RELEASE + "-session-token"


def work():
    path = f.managed_destination(k.work() / "session-monitoring", "storage")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    f.require(path.stat().st_mode & 0o077 == 0, "Private monitoring workspace required")
    return path


def pins():
    names = [
        p
        for prefix in (
            "scripts/session_monitoring",
            "infra/kind/session-monitoring",
            "infra/helm/fraud-observability",
        )
        for p in (f.ROOT / prefix).rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    ]
    names += [
        f.ROOT / "scripts/platform/kind_observability.py",
        f.ROOT / "scripts/platform/source_compatibility.py",
        f.ROOT / "references/session_monitoring_source_transition_v1.json",
    ]
    return {p.relative_to(f.ROOT).as_posix(): reader.sha(p) for p in sorted(names)}


def committed():
    for name, wanted in pins().items():
        done = subprocess.run(
            ["git", "show", "HEAD:" + name], cwd=f.ROOT, capture_output=True, check=False
        )
        f.require(
            done.returncode == 0 and hashlib.sha256(done.stdout).hexdigest() == wanted,
            "Commit reviewed monitoring implementation: " + name,
        )


def atomic(path, value):
    session.atomic(path, value)


def token():
    path = work() / "token.json"
    if not path.exists():
        f.private_json(
            path, {"owner_id": k.credentials()["owner_id"], "token": secrets.token_hex(32)}
        )
    value = json.loads(f.managed_destination(path, "storage").read_bytes())
    f.require(
        path.stat().st_mode & 0o077 == 0
        and value["owner_id"] == k.credentials()["owner_id"]
        and len(value["token"]) == 64,
        "Private token ownership differs",
    )
    return value["token"]


def reviewed_sources(saved):
    source_compatibility.accept(saved, pins(), f.ROOT)
    return True


def image_record():
    value = json.loads((work() / "image.json").read_bytes())
    f.require(
        reviewed_sources(value["source_pins"])
        and value["node_uid"] == f.owner()["node_uid"]
        and value["owner_id"] == k.credentials()["owner_id"],
        "Monitoring image/source identity differs",
    )
    loaded = inspect_image(value["image"])
    f.require(
        loaded["id"] in value["loaded_identities"]
        and value["image"]
        in {v.removeprefix("docker.io/library/") for v in loaded.get("repoTags", [])},
        "Monitoring image no longer loaded",
    )
    return value


def build():
    committed()
    if (work() / "image.json").exists():
        return {"status": "session_monitor_image_reused", "image": image_record()["image"]}
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
    info = json.loads(k.command(["docker", "image", "inspect", tag]).stdout)[0]
    f.require(info["Os"] == "linux" and info["Architecture"] == "amd64", "Expected linux/amd64")
    with TemporaryDirectory(prefix="session-image-", dir=work()) as temp:
        archive = Path(temp) / "image.tar"
        k.command(
            ["docker", "image", "save", "--platform", "linux/amd64", "--output", archive, tag]
        )
        archive.chmod(0o600)
        k.command(["kind", "load", "image-archive", "--name", f.CLUSTER, archive])
    loaded = inspect_image(tag)
    value = {
        "image": tag,
        "loaded_identities": [loaded["id"], *loaded.get("repoDigests", [])],
        "node_uid": f.owner()["node_uid"],
        "owner_id": k.credentials()["owner_id"],
        "source_pins": pins(),
    }
    f.private_json(work() / "image.json", value)
    image_record()
    return {"status": "session_monitor_image_built_and_loaded", "image": tag}


def settings():
    path = work() / "enabled.json"
    if not path.exists():
        return None
    value = json.loads(f.managed_destination(path, "storage").read_bytes())
    f.require(
        reviewed_sources(value["source_pins"])
        and value["owner_id"] == k.credentials()["owner_id"],
        "Enabled session monitoring source/owner differs",
    )
    contract.binding(value["binding"])
    f.require(
        value["image"] == json.loads((work() / "image.json").read_bytes())["image"],
        "Enabled image differs",
    )
    return {"enabled": True, "image": value["image"], "binding": value["binding"]}


def prepare(key, previous_sha=None):
    committed()
    directory, _ = session.load(key)
    binding, _ = collector.identity(directory)
    value = {
        "binding": binding,
        "source_pins": pins(),
        "owner_id": k.credentials()["owner_id"],
        "image": image_record()["image"],
    }
    path = work() / "prepared.json"
    if path.exists():
        existing = json.loads(path.read_bytes())
        if existing["binding"] == binding:
            f.require(
                reviewed_sources(existing["source_pins"]) and existing["image"] == value["image"],
                "Prepared implementation changed",
            )
            return {"status": "session_monitor_already_prepared", "binding": binding}
        active = settings()
        f.require(
            active
            and previous_sha == active["binding"]["session_sha256"]
            and existing["binding"] == active["binding"],
            "To switch sessions, supply the exact installed previous session SHA; finish or inspect pending installation first",
        )
        archive = work() / ("binding-" + previous_sha + ".json")
        if not archive.exists():
            f.private_json(archive, existing)
        value["replaces_session_sha256"] = previous_sha
        atomic(path, value)
    else:
        f.require(previous_sha is None, "No previous installed session to replace")
        f.private_json(path, value)
    return {"status": "session_monitor_prepared", "binding": binding}


def up():
    if k.session_only():
        from scripts.platform import session_observability

        return session_observability.install(session_observability.profile()["session_id"])
    committed()
    active_before = session.flow.active()
    proposed = json.loads((work() / "prepared.json").read_bytes())
    directory, _ = session.load(proposed["binding"]["session_id"])
    f.require(
        collector.identity(directory)[0] == proposed["binding"]
        and reviewed_sources(proposed["source_pins"]),
        "Prepared source changed",
    )
    f.require(proposed["image"] == image_record()["image"], "Prepared image differs")
    previous = settings()
    # No unrelated Helm settings may be silently overwritten. Resume partial same binding.
    current = json.loads(k.helm("get", "values", k.RELEASE, "--output", "json").stdout)
    base = k.values(True)
    base.pop("sessionTelemetry", None)
    desired = {
        **base,
        "sessionTelemetry": {
            "enabled": True,
            "image": proposed["image"],
            "binding": proposed["binding"],
        },
    }
    f.require(
        current in (base, desired, {**base, "sessionTelemetry": previous} if previous else base),
        "Existing observability values differ",
    )
    activation = work() / ("activation-" + proposed["binding"]["session_sha256"] + ".json")
    if activation.exists():
        before = json.loads(activation.read_bytes())
        f.require(before["prepared"] == proposed, "Retained activation belongs to another source")
    else:
        before = {
            "prepared": proposed,
            "protected": json.loads(json.dumps(k.protected_inventory())),
            "pvcs": k.inventory()["pvcs"],
        }
        atomic(activation, before)
    f.require(
        before["protected"] == json.loads(json.dumps(k.protected_inventory())),
        "Protected identities changed before retry",
    )
    encoded = base64.b64encode(token().encode()).decode()
    existing = k.get("secret", SECRET)
    if existing:
        k.owned(existing)
        f.require(existing["data"] == {"token": encoded}, "Owned monitoring token differs")
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
                        "name": SECRET,
                        "labels": {"app.kubernetes.io/instance": k.RELEASE},
                        "annotations": {k.ANNOTATION: proposed["owner_id"]},
                    },
                    "type": "Opaque",
                    "data": {"token": encoded},
                }
            ),
        )
    other = k.get("configmap", k.RELEASE + "-session-binding")
    if other:
        k.owned(other)
    if previous:
        f.require(
            previous["binding"] == proposed["binding"]
            or previous["binding"]["session_sha256"] == proposed.get("replaces_session_sha256"),
            "Active binding differs; refuse silent replacement",
        )
    atomic(work() / "enabled.json", proposed)
    # Retain enablement on failure; retry up, do not roll back unrelated workloads.
    k.install(True)
    after = k.inventory()
    f.require(
        before["protected"] == json.loads(json.dumps(k.protected_inventory()))
        and before["pvcs"] == after["pvcs"],
        "Protected resources or PVC identities changed",
    )
    legacy = k.verify_http()
    f.require(
        session.flow.active() == active_before,
        "Active serving receipt changed during installation",
    )
    result = {
        "status": "session_monitor_installed",
        "binding": proposed["binding"],
        "monitoring_pvcs_preserved": True,
        "legacy_replay_readbacks_verified": legacy["scrape_target_verified"],
        "champion_modified": False,
    }
    atomic(work() / "installation.json", result)
    return result


def forward():
    return k.forward("exporter", target_port=19102)


def put(uri, path, value):
    request = Request(
        uri + path,
        data=contract.dumps(value),
        method="PUT",
        headers={"Authorization": "Bearer " + token(), "Content-Type": "application/json"},
    )
    with build_opener(ProxyHandler({})).open(request, timeout=15) as response:
        f.require(json.load(response) == {"accepted": True}, "Receiver rejected aggregate")


def bound(key):
    enabled = settings()
    f.require(
        enabled and enabled["binding"]["session_id"] == key,
        "Explicit session does not match installed monitor",
    )
    directory, _ = session.load(key)
    return directory, enabled["binding"]


def watch(key, interval=5):
    f.require(1 <= interval <= 10, "Watch interval must be 1..10 seconds")
    directory, binding = bound(key)
    with (work() / "watch.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with forward() as uri:
            print(json.dumps({"status": "observing_session", "binding": binding}), flush=True)
            while True:
                try:
                    put(uri, "/projection", collector.collect(directory, binding))
                except ValueError, OSError, KeyError, TypeError:
                    put(uri, "/invalidate", {"binding": binding})
                    raise
                time.sleep(interval)


def check_projection(uri, prom, graf, value, valid):
    state = k.http(uri, "/summary")
    f.require(
        state["binding"] == value["binding"] and state["source_valid"] is valid,
        "Receiver identity/freshness differs",
    )
    label = '{session_sha256="' + value["binding"]["session_sha256"] + '"}'
    expected = {"source_valid": int(valid)}
    if valid:
        expected.update({name: value["counts"][name] for name in contract.COUNTS})
        expected.update(
            {"http_latency_" + name + "_ms": v for name, v in value["latency_ms"].items()}
        )
    deadline = time.monotonic() + 60
    while True:
        actual = {
            name: list(k.query(prom, "fraud_session_" + name + label).values())
            for name in expected
        }
        if all(
            len(actual[name]) == 1 and abs(actual[name][0] - amount) <= 1e-8
            for name, amount in expected.items()
        ):
            break
        f.require(time.monotonic() < deadline, "Prometheus session values differ")
        time.sleep(2)
    wanted = json.loads((k.CHART / "files/session/dashboard.json").read_bytes())
    got = k.available_http(graf, "/api/dashboards/uid/" + wanted["uid"], auth=True)["dashboard"]
    f.require(k.panel_contract(got) == k.panel_contract(wanted), "Session dashboard differs")
    for before, after in zip(wanted["panels"], got["panels"], strict=True):
        f.require(
            before.get("fieldConfig") == after.get("fieldConfig")
            and before.get("options") == after.get("options"),
            "Session presentation differs",
        )
    from urllib.parse import urlencode

    query = "fraud_session_source_valid" + label
    proxy = k.http(
        graf,
        "/api/datasources/proxy/uid/fraud-prometheus/api/v1/query?" + urlencode({"query": query}),
        auth=True,
    )
    f.require(
        len(proxy["data"]["result"]) == 1
        and float(proxy["data"]["result"][0]["value"][1]) == int(valid),
        "Grafana session datasource differs",
    )


def rehearse(key):
    if k.session_only():
        from scripts.platform import session_observability

        return session_observability.rehearse(key)
    active_before = session.flow.active()
    directory, binding = bound(key)
    accepted = [
        directory / name
        for name in (
            "session.json",
            "input.json",
            "checkpoint.sqlite",
            "native-scale-rehearsal.json",
        )
    ]
    f.require(
        all(p.is_file() for p in accepted),
        "Use the completed accepted expanded session for this rehearsal",
    )
    before = {p.name: reader.sha(p) for p in accepted}
    protected, inventory = k.protected_inventory(), k.inventory()
    value = collector.collect(directory, binding)
    f.require(
        value["counts"]["events"] == binding["expected_events"]
        and value["counts"]["decisions"] == binding["expected_decisions"],
        "Complete the source session first",
    )
    with (work() / "watch.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with forward() as uri, k.forward("prometheus") as prom, k.forward("grafana") as graf:
            put(uri, "/projection", value)
            check_projection(uri, prom, graf, value, True)
            print(
                "Coleta publicada; verificando expiração e retomada (sem pontuar novamente)...",
                flush=True,
            )
            time.sleep(35)
            check_projection(uri, prom, graf, value, False)
            value = collector.collect(directory, binding)
            put(uri, "/projection", value)
            check_projection(uri, prom, graf, value, True)
    f.require(before == {p.name: reader.sha(p) for p in accepted}, "Accepted source bytes changed")
    f.require(
        protected == k.protected_inventory() and inventory["pvcs"] == k.inventory()["pvcs"],
        "Protected identities changed",
    )
    legacy = k.verify_http()
    f.require(
        session.flow.active() == active_before, "Active serving receipt changed during rehearsal"
    )
    result = {
        "schema": "fraud_session_monitoring_native_v1",
        "active_serving_receipt_sha256": hashlib.sha256(contract.dumps(active_before)).hexdigest(),
        "status": "success",
        "binding": binding,
        "counts": value["counts"],
        "HTTP_latency_ms": value["latency_ms"],
        "same_session_Grafana_validated": True,
        "stale_collection_and_resume_validated": True,
        "legacy_replay_verified": legacy,
        "accepted_source_bytes_unchanged": before,
        "live_arrivals_native_validated": False,
        "visual_review_validated": False,
        "fit_executed": False,
        "champion_modified": False,
    }
    atomic(work() / ("native-" + uuid4().hex + ".json"), result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("build", "prepare", "up", "watch", "rehearse"))
    parser.add_argument("--session-id", default="arrival-2k-v1")
    parser.add_argument("--previous-session-sha256")
    parser.add_argument("--interval", type=float, default=5)
    args = parser.parse_args()
    try:
        if args.action == "watch":
            watch(args.session_id, args.interval)
            return
        with f.exclusive():
            functions = {
                "build": build,
                "prepare": lambda: prepare(args.session_id, args.previous_session_sha256),
                "up": up,
                "rehearse": lambda: rehearse(args.session_id),
            }
            print(json.dumps(functions[args.action](), indent=2))
    except KeyboardInterrupt:
        print("Observador encerrado; a fonte expirará em até 30 s. Serviços preservados.")
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print("Session monitoring stopped: " + str(exc), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
