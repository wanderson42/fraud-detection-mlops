"""Stage a pinned inactive slot, then explicitly release with recoverable receipts."""

import copy
import json
from pathlib import Path
import shutil
import time
from uuid import uuid4

from mlflow import MlflowClient
from mlflow.artifacts import download_artifacts
import numpy as np
from scripts.model_updates import controller as u
from scripts.model_updates import transaction
from scripts.operational_round import controller as c
from scripts.operational_round import serving as old
from scripts.operational_round.policy import digest, require, sha
from scripts.platform import kind_foundation as f
from scripts.platform import kind_tracking
from scripts.serving import check_docker_runtime as checks

from fraud_detection_mlops.integrations import mlflow_tracking as tracking
from fraud_detection_mlops.serving.model_release import SmokeCase, write_release
from fraud_detection_mlops.serving.request_schema import ScoreRequest

CHART = f.ROOT / "infra/helm/fraud-model-updates"


def alias(uri):
    return (
        MlflowClient(tracking_uri=uri, registry_uri=uri)
        .get_model_version_by_alias("fraud-detector", "champion")
        .version
    )


def route():
    value = json.loads(f.kube("get", "service", "fraud-detector-active", "-o", "json").stdout)
    return value["spec"]["selector"]["app.kubernetes.io/component"]


def files(plan):
    for slot in ("blue", "green"):
        folder = Path(plan["paths"][slot])
        checks.read_release(folder, plan["releases"][slot]["manifest_sha256"])
        require(
            checks.hashes(folder) == plan["releases"][slot]["files"],
            "Staged release bytes changed",
        )
    for image in [
        plan["images"]["gateway"],
        *[i for s in plan["slot_images"].values() for i in s.values()],
    ]:
        require(
            kind_tracking.image_digest(c.cri(image["image"])["id"])
            == kind_tracking.image_digest(image["identity"]),
            "Staged runtime/carrier image changed",
        )


def running(plan, slot):
    old.owned(plan)
    pods = json.loads(
        f.kube(
            "get",
            "pods",
            "-l",
            "app.kubernetes.io/instance=fraud-detector,app.kubernetes.io/component=" + slot,
            "-o",
            "json",
        ).stdout
    )["items"]
    pods = [v for v in pods if not v["metadata"].get("deletionTimestamp")]
    require(
        len(pods) == 1
        and pods[0]["metadata"].get("annotations", {}).get(old.OWNER) == plan["owner_id"],
        "One owned ready slot required",
    )
    pod = pods[0]
    for field, statuses, role in [
        ("containers", "containerStatuses", "runtime"),
        ("initContainers", "initContainerStatuses", "artifacts"),
    ]:
        expected = plan["slot_images"][slot][role]
        items, actual = pod["spec"][field], pod["status"].get(statuses, [])
        require(
            len(items) == len(actual) == 1
            and items[0]["image"] == expected["image"]
            and kind_tracking.image_digest(actual[0]["imageID"])
            in {kind_tracking.image_digest(v) for v in expected["loaded_identities"]},
            "Actual slot runtime/carrier differs",
        )
        if role == "runtime":
            require(actual[0]["ready"] is True, "Slot not ready")
    return pod["metadata"]["name"]


def gate(url, plan, slot, policy):
    from scripts.platform.kind_serving import wait_route

    smoke = json.loads((Path(plan["paths"][slot]) / "smoke.json").read_bytes())
    scoped = {**plan, "smoke": smoke}
    checked = wait_route(url, scoped, slot, timeout=60)
    timings = []
    manifest = plan["releases"][slot]["manifest"]
    for _ in range(policy["http_measurements"]):
        start = time.monotonic()
        checks.check_score(
            None,
            smoke,
            manifest,
            requester=lambda _, path, payload=None: checks.request_json(url, path, payload),
        )
        timings.append((time.monotonic() - start) * 1000)
    p95 = float(np.percentile(timings, 95))
    require(p95 <= policy["maximum_HTTP_p95_ms"], "HTTP p95 exceeds the predeclared budget")
    return {**checked, "p95_ms": p95, "measurements": len(timings)}


def check_active(active, policy):
    plan = active["plan"]
    plan["paths"] = active["paths"]
    files(plan)
    require(route() == active["slot"], "Serving route differs from the pinned incumbent")
    running(plan, active["slot"])
    with kind_tracking.port_forward() as uri:
        require(alias(uri) == active["version"], "Champion is not the pinned active version")
        version = MlflowClient(tracking_uri=uri, registry_uri=uri).get_model_version(
            "fraud-detector", active["version"]
        )
        require(
            version.run_id == active["run_id"]
            and version.tags.get("fraud.model_sha256") == active["model_sha256"],
            "Incumbent Registry metadata differs",
        )
    with old.forward(old.gateway(plan)) as url:
        result = gate(url, plan, active["slot"], policy)
    require(
        result["score"]["model_sha256"] == active["model_sha256"], "Active HTTP weights differ"
    )
    return result


def apply(plan, active, *, both):
    old.owned(plan)
    values = {
        "ownerId": plan["owner_id"],
        "images": {k: v["image"] for k, v in plan["images"].items()},
        "slotImages": {
            s: {k: v["image"] for k, v in images.items()}
            for s, images in plan["slot_images"].items()
        },
        "activeSlot": active,
        "greenEnabled": True,
        "blueReplicas": 1 if both or active == "blue" else 0,
        "greenReplicas": 1 if both or active == "green" else 0,
        "manifests": {s: v["manifest_sha256"] for s, v in plan["releases"].items()},
    }
    path = Path(plan["receipt_directory"]) / ("helm-values-" + uuid4().hex + ".json")
    f.private_json(path, values)
    f.helm(
        "upgrade",
        "--install",
        "fraud-detector",
        CHART,
        "--values",
        path,
        "--timeout",
        "180s",
        timeout=210,
    )
    for component in (["blue", "green"] if both else [active]) + ["gateway"]:
        f.kube(
            "rollout",
            "status",
            "deployment/fraud-detector-" + component,
            "--timeout=150s",
            timeout=170,
        )


def stage(key):
    info = u.read(key)
    path = u.work(key)
    if (path / "stage.json").exists():
        intent = json.loads((path / "stage.json").read_bytes())
        require(
            digest({k: v for k, v in intent.items() if k != "approval_sha256"})
            == intent["approval_sha256"],
            "Staged approval identity changed",
        )
        files(intent["plan"])
        return intent
    from scripts.platform.serving_recovery import resolve_active

    baseline = resolve_active(info["baseline"])
    check_active(baseline, info["policy"])
    selected = json.loads((path / "challenger.json").read_bytes())
    summary = u.training_result(key)
    candidate = summary["candidates"][selected["model_id"]]
    plan = copy.deepcopy(baseline["plan"])
    plan["paths"] = copy.deepcopy(baseline["paths"])
    plan["receipt_directory"] = str(path)
    before_slot = baseline["slot"]
    after_slot = "green" if before_slot == "blue" else "blue"
    carrier = path / "carrier"
    carrier.mkdir(mode=0o700, exist_ok=True)
    with kind_tracking.port_forward() as uri, tracking.http_tracking(uri):
        client = MlflowClient(tracking_uri=uri, registry_uri=uri)
        version = client.get_model_version_by_alias("fraud-detector", "challenger")
        require(
            version.version == selected["version"] and version.run_id == selected["run_id"],
            "Challenger changed",
        )
        downloaded = Path(
            download_artifacts(
                artifact_uri=client.get_model_version_download_uri(
                    "fraud-detector", version.version
                ),
                tracking_uri=uri,
                registry_uri=uri,
                dst_path=str(path / "downloaded"),
            )
        )
    require(
        {
            v.relative_to(downloaded).as_posix(): sha(v)
            for v in downloaded.rglob("*")
            if v.is_file()
        }
        == candidate["artifacts"],
        "Challenger bytes changed",
    )
    folder = carrier / after_slot
    smoke = SmokeCase(
        request=ScoreRequest.model_validate(summary["control_request"]),
        expected_score=candidate["control_score"],
    )
    if not folder.exists():
        write_release(
            folder,
            (downloaded / candidate["weight_file"]).read_bytes(),
            smoke,
            model_id=selected["model_id"],
            source={
                "registered_model": "fraud-detector",
                "round_id": key,
                "registry_version": selected["version"],
                "mlflow_run_id": selected["run_id"],
                "policy_sha256": info["policy_sha256"],
            },
            parity_rows=candidate["parity_rows"],
            evidence_kind="verified_reference",
        )
    manifest = json.loads((folder / "manifest.json").read_bytes())
    require(
        manifest["model_sha256"] == selected["model_sha256"]
        and manifest["source"]["round_id"] == key
        and manifest["source"]["registry_version"] == selected["version"]
        and json.loads((folder / "smoke.json").read_bytes())
        == json.loads(smoke.model_dump_json()),
        "Partial staging differs",
    )
    if not (carrier / before_slot).exists():
        shutil.copytree(baseline["paths"][before_slot], carrier / before_slot)
    shutil.copyfile(f.ROOT / "scripts/operational_round/seed.py", carrier / "seed.py")
    shutil.copyfile(f.ROOT / "infra/kind/serving/Dockerfile.artifacts", carrier / "Dockerfile")
    tag = "fraud-kind-serving-artifacts:" + uuid4().hex
    c.image_command(
        [
            "docker",
            "build",
            "--platform",
            "linux/amd64",
            "--file",
            carrier / "Dockerfile",
            "--tag",
            tag,
            carrier,
        ],
        timeout=300,
    )
    loaded = c.load_image(tag)
    # Keep the active slot's exact pod template; only the inactive carrier changes.
    plan["slot_images"][after_slot]["artifacts"] = {
        "image": tag,
        "identity": loaded["id"],
        "loaded_identities": [loaded["id"], *loaded.get("repoDigests", [])],
    }
    plan["releases"][after_slot] = {
        "manifest": manifest,
        "manifest_sha256": sha(folder / "manifest.json"),
        "files": checks.hashes(folder),
    }
    plan["paths"][after_slot] = str(folder)
    files(plan)
    active_pod = running(plan, before_slot)
    apply(plan, before_slot, both=True)
    require(
        running(plan, before_slot) == active_pod, "Staging unexpectedly replaced the active pod"
    )
    with old.forward(running(plan, after_slot)) as url:
        candidate_gate = gate(url, plan, after_slot, info["policy"])
    with old.forward(old.gateway(plan)) as url:
        baseline_gate = gate(url, plan, before_slot, info["policy"])
    with kind_tracking.port_forward() as uri:
        require(
            alias(uri) == baseline["version"] and route() == before_slot,
            "Staging changed the active identity",
        )
    intent = {
        "status": "staged_awaiting_explicit_approval",
        "update_id": key,
        "policy_sha256": info["policy_sha256"],
        "plan": plan,
        "before": {
            k: baseline[k] for k in ("version", "run_id", "model_sha256", "slot", "owner_id")
        },
        "after": {**selected, "slot": after_slot, "owner_id": baseline["owner_id"]},
        "gates": {"baseline": baseline_gate, "candidate": candidate_gate},
        "traffic_changed": False,
        "champion_changed": False,
    }
    intent["approval_sha256"] = digest(intent)
    return u.save(key, "stage.json", intent)


class Backend:
    def __init__(self, intent, policy, uri, url):
        self.intent, self.policy, self.uri, self.url = intent, policy, uri, url
        self.plan = intent["plan"]

    def alias(self):
        return alias(self.uri)

    def route(self):
        old.owned(self.plan)
        return route()

    def set_route(self, slot):
        require(
            self.route() in (self.intent["before"]["slot"], self.intent["after"]["slot"]),
            "Foreign route",
        )
        f.kube(
            "patch",
            "service",
            "fraud-detector-active",
            "--type=merge",
            "-p",
            json.dumps(
                {
                    "spec": {
                        "selector": {
                            "app.kubernetes.io/instance": "fraud-detector",
                            "app.kubernetes.io/component": slot,
                        }
                    }
                }
            ),
        )

    def set_alias(self, version, expected):
        require(self.alias() == expected, "Concurrent alias change")
        client = MlflowClient(tracking_uri=self.uri, registry_uri=self.uri)
        client.set_registered_model_alias("fraud-detector", "champion", version)
        require(self.alias() == version, "Alias readback failed")

    def check(self, identity):
        running(self.plan, identity["slot"])
        result = gate(self.url, self.plan, identity["slot"], self.policy)
        require(
            result["score"]["model_sha256"] == identity["model_sha256"], "Served model differs"
        )

    def finalize(self, identity):
        apply(self.plan, identity["slot"], both=False)
        self.check(identity)
        active = {
            **{k: identity[k] for k in ("version", "run_id", "model_sha256", "slot", "owner_id")},
            "plan": self.plan,
            "paths": self.plan["paths"],
        }
        f.private_json(u.workspace() / "current.json", active)
        if identity["version"] == self.intent["after"]["version"]:
            client = MlflowClient(tracking_uri=self.uri, registry_uri=self.uri)
            challenger = client.get_registered_model("fraud-detector").aliases.get("challenger")
            require(
                challenger in (None, identity["version"]), "Foreign challenger during finalization"
            )
            if challenger:
                client.delete_registered_model_alias("fraud-detector", "challenger")

    def prepare_recovery(self, identity):
        f.kube("scale", "deployment/fraud-detector-" + identity["slot"], "--replicas=1")
        f.kube(
            "rollout",
            "status",
            "deployment/fraud-detector-" + identity["slot"],
            "--timeout=150s",
            timeout=170,
        )


def release(key, approval, no_other_writers, *, recover=False, rollback=False):
    info = u.read(key)
    intent = json.loads((u.work(key) / "stage.json").read_bytes())
    require(
        intent["approval_sha256"]
        == digest({k: v for k, v in intent.items() if k != "approval_sha256"})
        and intent["policy_sha256"] == info["policy_sha256"],
        "Approval payload changed",
    )
    require(no_other_writers, "Declare no other Registry/route writers")
    files(intent["plan"])
    journal_path = u.work(key) / "release.json"

    def persist(value):
        events = u.work(key) / "release-events"
        events.mkdir(mode=0o700, exist_ok=True)
        f.private_json(events / (str(time.time_ns()) + "-" + uuid4().hex + ".json"), value)
        f.private_json(journal_path, value)

    with kind_tracking.port_forward() as uri, old.forward(old.gateway(intent["plan"])) as url:
        backend = Backend(intent, info["policy"], uri, url)
        if journal_path.exists():
            journal = json.loads(journal_path.read_bytes())
            require(
                all(journal.get(k) == v for k, v in intent.items()),
                "Another or modified release journal",
            )
            if recover:
                require(
                    journal["phase"] != "active", "Use explicit rollback for an active release"
                )
                return transaction.restore(journal, backend, persist)
            if rollback:
                require(
                    approval
                    == digest({"action": "rollback", "release": intent["approval_sha256"]}),
                    "Approve the exact rollback digest",
                )
                require(
                    journal["phase"] == "active"
                    and backend.alias() == intent["after"]["version"]
                    and backend.route() == intent["after"]["slot"],
                    "Active release changed before rollback",
                )
                return transaction.restore(journal, backend, persist)
            if journal["phase"] == "active":
                require(
                    backend.alias() == intent["after"]["version"]
                    and backend.route() == intent["after"]["slot"],
                    "Active receipt differs",
                )
                backend.check(intent["after"])
                return journal
            require(False, "Interrupted/rolled-back release; use recover or review a new update")
        require(not recover and not rollback, "No authorized release journal to recover")
        # Recheck the actual challenger before accepting approval.
        client = MlflowClient(tracking_uri=uri, registry_uri=uri)
        challenger = client.get_model_version_by_alias("fraud-detector", "challenger")
        require(
            challenger.version == intent["after"]["version"]
            and challenger.run_id == intent["after"]["run_id"]
            and challenger.tags.get("fraud.model_sha256") == intent["after"]["model_sha256"],
            "Challenger changed after staging",
        )
        with old.forward(running(intent["plan"], intent["after"]["slot"])) as direct:
            gate(direct, intent["plan"], intent["after"]["slot"], info["policy"])
        return transaction.activate(
            intent, backend, persist, approval=approval, no_other_writers=no_other_writers
        )
