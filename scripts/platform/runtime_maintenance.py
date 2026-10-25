"""Maintain a qualified API runtime in the SAME slot without changing model or Registry."""

import argparse
import copy
import json
from pathlib import Path
import subprocess
from uuid import uuid4

from scripts.platform import kind_operations as operations
from scripts.platform import serving_recovery as recovery
from scripts.platform import source_compatibility

f, p = recovery.f, recovery.p
deployment = recovery.deployment


def runtime_only(before, after):
    left, right = copy.deepcopy(before), copy.deepcopy(after)
    for value in (left, right):
        value.pop("maintenance_receipt", None)
        value["plan"]["images"].pop("runtime")
        for slot in ("blue", "green"):
            value["plan"]["slot_images"][slot].pop("runtime")
    p.require(
        left == right, "Runtime maintenance cannot change weights, release, route or Registry"
    )


def rollback_digest(intent):
    return p.digest({"action": "runtime-rollback", "maintenance": intent["approval_sha256"]})


def committed():
    values = recovery.committed()
    for name, expected in operations.pins().items():
        if values.get(name) == expected:
            continue
        found = subprocess.run(
            ["git", "show", "HEAD:" + name], cwd=f.ROOT, capture_output=True, check=False
        )
        p.require(
            found.returncode == 0
            and recovery.hashlib.sha256(found.stdout).hexdigest() == expected,
            "Commit reviewed operational maintenance code first: " + name,
        )
        values[name] = expected
    return values


def read(key):
    folder = recovery.work(key)
    value = json.loads((folder / "runtime-prepared.json").read_bytes())
    source_compatibility.accept(value["source_pins"], committed(), f.ROOT)
    p.require(
        value["node_uid"] == f.owner()["node_uid"], "Runtime maintenance belongs to another node"
    )
    p.require(
        value["schema"] == "fraud_runtime_maintenance_v1"
        and value["approval_sha256"]
        == p.digest({k: v for k, v in value.items() if k != "approval_sha256"}),
        "Runtime approval payload changed",
    )
    runtime_only(value["before_active"], value["after_active"])
    baseline = f.managed_destination(Path(value["baseline_path"]), "storage")
    p.require(p.sha(baseline) == value["baseline_sha256"], "Original baseline changed")
    qualification = f.managed_destination(Path(value["qualification_path"]), "serving")
    p.require(
        p.sha(qualification) == value["qualification_sha256"], "Docker qualification changed"
    )
    p.require(
        json.loads((folder / "runtime-probe.json").read_bytes()) == value["native_probe"],
        "Native probe receipt changed",
    )
    return value


def validate_chain(receipt, cursor, path):
    """A completed maintenance link preserves model/alias identity and binds its intent."""
    key = path.parent.name
    intent = read(key)
    before, after = intent["before_active"], intent["after_active"]
    if path.name == "applied.json":
        authorization = json.loads((path.parent / "runtime-authorized.json").read_bytes())
        p.require(
            authorization
            == {"approval_sha256": intent["approval_sha256"], "target_sha256": p.digest(after)},
            "Runtime authorization differs",
        )
        expected_before, expected_after, status = before, after, "runtime_maintenance_applied"
    else:
        unpublished = receipt.get("rollback_from_unpublished_attempt") is True
        p.require(
            unpublished == (not (path.parent / "applied.json").exists()),
            "Rollback attempt publication history differs",
        )
        authorization = json.loads((path.parent / "rollback-authorized.json").read_bytes())
        p.require(
            authorization["approval_sha256"] == rollback_digest(intent),
            "Rollback authorization differs",
        )
        expected_before = before if unpublished else after
        expected_after = copy.deepcopy(before)
        expected_after["maintenance_receipt"] = str(path)
        status = "runtime_maintenance_rolled_back"
        p.require(
            authorization
            == {
                "approval_sha256": rollback_digest(intent),
                "target_sha256": p.digest(expected_after),
            },
            "Rollback target authorization differs",
        )
    p.require(
        receipt["status"] == status
        and receipt["before_active"] == expected_before
        and receipt["after_active"] == cursor == expected_after
        and receipt["approval_sha256"] == intent["approval_sha256"]
        and receipt["native_runtime_probe_verified"] is True,
        "Incomplete or foreign runtime maintenance link",
    )
    runtime_only(expected_before, expected_after)


def qualification_report(path, before):
    from scripts.serving import check_docker_runtime as checks

    report = json.loads(path.read_bytes())
    slot = before["slot"]
    release = before["plan"]["releases"][slot]
    manifest = release["manifest"]
    p.require(
        report.get("status") == "success"
        and report.get("version") == "laboratory_serving_docker_check_v1"
        and report["image"]["os"] == "linux"
        and report["image"]["architecture"] == "amd64"
        and report["manifest_sha256"] == release["manifest_sha256"]
        and report["serving_release_id"] == manifest["serving_release_id"]
        and report["http_score"]["model_sha256"] == before["model_sha256"]
        and report["runtime_environment"] == manifest["runtime_environment"]
        and report["release_files_unchanged"] is True
        and report["restart_recovery"] == "passed"
        and report["invalid_input_http_status"] == 422
        and report["runtime"]["readonly_write_probes"] == "passed"
        and report["runtime"]["uid"] == report["runtime"]["gid"] == 10001
        and all(
            report["failure_scenarios"][name]["startup_rejected"] is True
            for name in ("wrong_manifest_sha256", "corrupt_model", "missing_model")
        )
        and report["check_script_sha256"]
        == p.sha(f.ROOT / "scripts/serving/check_docker_runtime.py"),
        "Docker qualification does not match the current release and reviewed checker",
    )
    smoke = json.loads((Path(before["paths"][slot]) / "smoke.json").read_bytes())
    p.require(
        abs(report["http_score"]["score"] - smoke["expected_score"]) <= checks.PARITY_TOLERANCE,
        "Qualified control score differs",
    )
    return report


def pod_get(name):
    value = f.kube("get", "pod", name, "--ignore-not-found=true", "-o", "json").stdout.strip()
    return json.loads(value) if value else None


def isolated_probe(folder, after, templates):
    spec_path = folder / "runtime-probe-spec.json"
    receipt_path = folder / "runtime-probe.json"
    slot = after["slot"]
    image = after["plan"]["slot_images"][slot]["runtime"]
    if not spec_path.exists():
        spec = copy.deepcopy(templates[slot]["spec"]["template"]["spec"])
        p.require(
            len(spec["containers"]) == len(spec["initContainers"]) == 1
            and {v["name"] for v in spec["volumes"]} == {"model", "tmp"}
            and all(set(v) == {"name", "emptyDir"} for v in spec["volumes"])
            and spec["automountServiceAccountToken"] is False,
            "Isolated probe requires the canonical emptyDir-only pod template",
        )
        spec["containers"][0]["image"] = image["image"]
        spec["restartPolicy"] = "Never"
        spec["activeDeadlineSeconds"] = 240
        name = "fraud-runtime-check-" + uuid4().hex[:12]
        desired = {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {
                "name": name,
                "namespace": f.NAMESPACE,
                "labels": {"fraud.runtime-maintenance": name},
                "annotations": {"fraud.serving/owner-id": after["owner_id"]},
            },
            "spec": spec,
        }
        f.private_json(spec_path, desired)
    desired = json.loads(spec_path.read_bytes())
    name = desired["metadata"]["name"]
    p.require(
        name.startswith("fraud-runtime-check-")
        and desired["spec"]["containers"][0]["image"] == image["image"]
        and desired["spec"]["initContainers"][0]["image"]
        == after["plan"]["slot_images"][slot]["artifacts"]["image"]
        and "app.kubernetes.io/instance" not in desired["metadata"]["labels"],
        "Probe spec or isolation identity differs",
    )
    live = pod_get(name)
    uid_path = folder / "runtime-probe-uid.json"
    if receipt_path.exists():
        receipt = json.loads(receipt_path.read_bytes())
        p.require(
            receipt["status"] == "runtime_probe_verified" and receipt["pod"] == name,
            "Foreign runtime probe receipt",
        )
    else:
        if live is None:
            p.require(
                not uid_path.exists(),
                "Probe disappeared; preserve diagnostics and review another maintenance ID",
            )
            f.kube("create", "-f", "-", data=json.dumps(desired))
            live = pod_get(name)
        p.require(recovery.includes(desired, live), "Foreign or modified isolated probe")
        if not uid_path.exists():
            f.private_json(uid_path, {"uid": live["metadata"]["uid"]})
        p.require(
            json.loads(uid_path.read_bytes())["uid"] == live["metadata"]["uid"],
            "Probe UID changed",
        )
        f.kube("wait", "--for=condition=Ready", "pod/" + name, "--timeout=150s", timeout=170)
        live = pod_get(name)
        p.require(recovery.includes(desired, live), "Probe changed while waiting")
        for field, role in (
            ("containerStatuses", "runtime"),
            ("initContainerStatuses", "artifacts"),
        ):
            statuses = live["status"][field]
            expected = after["plan"]["slot_images"][slot][role]
            p.require(
                len(statuses) == 1
                and recovery.kind_tracking.image_digest(statuses[0]["imageID"])
                in {recovery.kind_tracking.image_digest(v) for v in expected["loaded_identities"]},
                "Probe image identity differs",
            )
        with deployment.old.forward(name) as url:
            gate = deployment.gate(url, after["plan"], slot, recovery.config())
        receipt = {
            "status": "runtime_probe_verified",
            "pod": name,
            "uid": live["metadata"]["uid"],
            "HTTP": gate,
            "runtime_image": image,
            "canonical_route_changed": False,
        }
        f.private_json(receipt_path, receipt)
    live = pod_get(name)
    if live is not None:
        p.require(
            live["metadata"]["uid"] == receipt["uid"] and recovery.includes(desired, live),
            "Refuse cleanup of a foreign probe",
        )
        f.kube("delete", "pod", name, "--wait=true", "--timeout=30s")
    p.require(pod_get(name) is None, "Isolated probe cleanup incomplete")
    return receipt


def prepare(key, qualification, baseline, no_other_writers):
    p.require(no_other_writers, "Declare no other serving/Registry writers")
    folder = recovery.work(key)
    if (folder / "runtime-prepared.json").exists():
        return read(key)
    pins = committed()
    operations.check(baseline)
    baseline = f.managed_destination(baseline.absolute(), "storage")
    qualification = f.managed_destination(qualification.absolute(), "serving")
    before = recovery.current()
    report = qualification_report(qualification, before)
    image_id = report["image"]["id"]
    local = json.loads(f.run(["docker", "image", "inspect", image_id]).stdout)[0]
    p.require(
        local["Id"] == image_id and local["Os"] == "linux" and local["Architecture"] == "amd64",
        "Qualified local image identity/platform differs",
    )
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    p.require(folder.stat().st_mode & 0o077 == 0, "Private maintenance workspace required")
    draft_path = folder / "runtime-preparing.json"
    if not draft_path.exists():
        templates = {
            s: json.loads(f.kube("get", "deployment", "fraud-detector-" + s, "-o", "json").stdout)
            for s in ("blue", "green")
        }
        f.private_json(
            draft_path,
            {
                "before_active": before,
                "node_uid": f.owner()["node_uid"],
                "source_pins": pins,
                "baseline_path": str(baseline),
                "baseline_sha256": p.sha(baseline),
                "qualification_path": str(qualification),
                "qualification_sha256": p.sha(qualification),
                "local_image_id": image_id,
                "tag": "fraud-runtime-maintenance:" + uuid4().hex,
                "templates": {
                    s: {"metadata": {"uid": v["metadata"]["uid"]}, "spec": v["spec"]}
                    for s, v in templates.items()
                },
            },
        )
    draft = json.loads(draft_path.read_bytes())
    p.require(
        draft["before_active"] == before
        and draft["local_image_id"] == image_id
        and draft["source_pins"] == pins
        and draft["node_uid"] == f.owner()["node_uid"]
        and draft["baseline_sha256"] == p.sha(baseline)
        and draft["qualification_sha256"] == p.sha(qualification),
        "Partial runtime preparation changed",
    )
    recovery.original.image_command(["docker", "image", "tag", image_id, draft["tag"]], timeout=30)
    loaded = recovery.original.load_image(draft["tag"])
    image = {
        "image": draft["tag"],
        "identity": loaded["id"],
        "loaded_identities": [loaded["id"], *loaded.get("repoDigests", [])],
        "docker_identity": image_id,
    }
    after = copy.deepcopy(before)
    after["maintenance_receipt"] = str(folder / "applied.json")
    after["plan"]["images"]["runtime"] = image
    for slot in ("blue", "green"):
        after["plan"]["slot_images"][slot]["runtime"] = image
    runtime_only(before, after)
    deployment.files(after["plan"])
    probe = isolated_probe(folder, after, draft["templates"])
    p.require(recovery.current() == before, "Active state changed during runtime preparation")
    deployment.check_active(copy.deepcopy(before), recovery.config())
    intent = {
        **draft,
        "schema": "fraud_runtime_maintenance_v1",
        "maintenance_id": key,
        "status": "runtime_prepared_awaiting_approval",
        "after_active": after,
        "native_probe": probe,
        "champion_modified": False,
        "model_weights_changed": False,
        "same_slot_rollout": True,
        "zero_downtime_claimed": False,
    }
    intent["approval_sha256"] = p.digest(intent)
    f.private_json(folder / "runtime-prepared.json", intent)
    return intent


def live_guard(intent):
    """Permit only the old/new runtime field during an interrupted Helm operation."""
    before, after = intent["before_active"], intent["after_active"]
    deployment.old.owned(after["plan"])
    helm_values = json.loads(f.helm("get", "values", "fraud-detector", "--output", "json").stdout)
    p.require(helm_values.get("ownerId") == before["owner_id"], "Canonical Helm ownership changed")
    p.require(deployment.route() == before["slot"], "Foreign route; refuse runtime maintenance")
    with recovery.kind_tracking.port_forward() as uri:
        p.require(
            deployment.alias(uri) == before["version"], "Champion changed during maintenance"
        )
    baseline = json.loads(Path(intent["baseline_path"]).read_bytes())
    current = operations.resources()

    def history(key):
        return key.startswith("Secret/sh.helm.release.v1.fraud-detector.v")

    p.require(
        {k for k in current if not history(k)}
        == {k for k in baseline["resources"] if not history(k)},
        "Foreign resource set during maintenance",
    )
    for key, expected in baseline["resources"].items():
        # Helm may update/prune only its own revision Secrets under its normal
        # history policy; runtime rollback uses pinned image/plan receipts.
        if history(key):
            continue
        actual = current.get(key)
        p.require(
            actual is not None and actual["uid"] == expected["uid"],
            "Owned resource replaced: " + key,
        )
        if key not in {"Deployment/fraud-detector-blue", "Deployment/fraud-detector-green"}:
            p.require(actual == expected, "Unrelated resource changed: " + key)
    for slot, template in intent["templates"].items():
        live = json.loads(
            f.kube("get", "deployment", "fraud-detector-" + slot, "-o", "json").stdout
        )
        actual, expected = copy.deepcopy(live["spec"]), copy.deepcopy(template["spec"])
        candidate = actual["template"]["spec"]["containers"][0].pop("image")
        old_image = expected["template"]["spec"]["containers"][0].pop("image")
        p.require(
            live["metadata"]["uid"] == template["metadata"]["uid"]
            and actual == expected
            and candidate in {old_image, after["plan"]["slot_images"][slot]["runtime"]["image"]},
            "Foreign slot template; refuse overwrite",
        )


def change(key, approval, no_other_writers, *, rollback=False):
    p.require(no_other_writers, "Declare no other serving/Registry writers")
    intent = read(key)
    p.require(
        approval == (rollback_digest(intent) if rollback else intent["approval_sha256"]),
        "Use the exact reviewed runtime approval digest",
    )
    before, after = intent["before_active"], intent["after_active"]
    folder = recovery.work(key)
    target = copy.deepcopy(before if rollback else after)
    receipt_path = folder / ("rolled-back.json" if rollback else "applied.json")
    unpublished = False
    if rollback:
        target["maintenance_receipt"] = str(receipt_path)
        authorization = json.loads((folder / "runtime-authorized.json").read_bytes())
        p.require(
            authorization
            == {"approval_sha256": intent["approval_sha256"], "target_sha256": p.digest(after)},
            "No authorized runtime attempt to roll back",
        )
        applied_path = folder / "applied.json"
        unpublished = not applied_path.exists()
        if not unpublished:
            validate_chain(json.loads(applied_path.read_bytes()), after, applied_path)
        else:
            p.require(recovery.current() != after, "Published runtime is missing its receipt")
    allowed = (before, after, target) if rollback else (before, after)
    p.require(
        recovery.current() in allowed, "Another maintenance/deployment changed current state"
    )
    deployment.files(before["plan"])
    deployment.files(after["plan"])
    live_guard(intent)
    journal = folder / ("rollback-authorized.json" if rollback else "runtime-authorized.json")
    authorization = {"approval_sha256": approval, "target_sha256": p.digest(target)}
    if not journal.exists():
        f.private_json(journal, authorization)
    p.require(
        json.loads(journal.read_bytes()) == authorization, "Another authorization journal exists"
    )
    if recovery.current() != target:
        desired = copy.deepcopy(target["plan"])
        desired["receipt_directory"] = str(folder)
        deployment.apply(desired, target["slot"], both=False)
    HTTP = deployment.check_active(copy.deepcopy(target), recovery.config())
    receipt = {
        "schema": "fraud_runtime_maintenance_applied_v1",
        "status": "runtime_maintenance_rolled_back" if rollback else "runtime_maintenance_applied",
        "before_active": (before if unpublished else after) if rollback else before,
        "after_active": target,
        "approval_sha256": intent["approval_sha256"],
        "native_runtime_probe_verified": True,
        "HTTP": HTTP,
        "rollback_from_unpublished_attempt": unpublished,
        "champion_modified": False,
        "release_bytes_unchanged": True,
        "zero_downtime_claimed": False,
    }
    if receipt_path.exists():
        saved = json.loads(receipt_path.read_bytes())
        validate_chain(saved, target, receipt_path)
        receipt = saved
    else:
        f.private_json(receipt_path, receipt)
    p.require(recovery.current() in allowed, "Active state changed before runtime publication")
    recovery.atomic_json(recovery.updates.workspace() / "current.json", target)
    return {
        "status": receipt["status"],
        "receipt": str(receipt_path),
        "model_sha256": target["model_sha256"],
        "champion_modified": False,
    }


def verify(key):
    intent = read(key)
    active = recovery.resolve_active(intent["before_active"])
    rolled = copy.deepcopy(intent["before_active"])
    rolled["maintenance_receipt"] = str(recovery.work(key) / "rolled-back.json")
    p.require(active in (intent["after_active"], rolled), "Another deployment is active")
    HTTP = deployment.check_active(copy.deepcopy(active), recovery.config())
    live_guard(intent)
    baseline = json.loads(Path(intent["baseline_path"]).read_bytes())
    observed = operations.probes(baseline["tracking_report"]["record"])
    p.require(
        observed == baseline["probes"],
        "Functional/monitoring readbacks differ after runtime maintenance",
    )
    result = {
        "schema": "fraud_runtime_maintenance_verification_v1",
        "status": "runtime_maintenance_verified",
        "active_state_sha256": p.digest(active),
        "HTTP": HTTP,
        "functional_readbacks_verified": True,
        "rollback_executed": active == rolled,
        "model_sha256": active["model_sha256"],
        "champion_modified": False,
        "new_baseline_written": False,
    }
    f.private_json(recovery.work(key) / ("runtime-verify-" + uuid4().hex + ".json"), result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "apply", "rollback", "verify"])
    parser.add_argument("--maintenance-id", default="api-trim-v1")
    parser.add_argument("--docker-report", type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--approval-sha256", default="")
    parser.add_argument("--no-other-writers", action="store_true")
    args = parser.parse_args()
    try:
        with f.exclusive():
            if args.action == "prepare":
                p.require(
                    args.docker_report is not None and args.baseline is not None,
                    "Provide Docker report and current baseline",
                )
                value = prepare(
                    args.maintenance_id, args.docker_report, args.baseline, args.no_other_writers
                )
                result = {
                    k: value[k]
                    for k in (
                        "status",
                        "approval_sha256",
                        "model_weights_changed",
                        "champion_modified",
                    )
                }
                result.update(
                    runtime_image=value["after_active"]["plan"]["images"]["runtime"],
                    native_probe=value["native_probe"],
                    rollback_approval_sha256=rollback_digest(value),
                    same_slot_rollout=True,
                    zero_downtime_claimed=False,
                )
            elif args.action == "verify":
                result = verify(args.maintenance_id)
            else:
                result = change(
                    args.maintenance_id,
                    args.approval_sha256,
                    args.no_other_writers,
                    rollback=args.action == "rollback",
                )
        print(json.dumps(result, indent=2))
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as exc:
        parser.exit(1, "Runtime maintenance stopped: " + str(exc)[-2000:] + "\n")


if __name__ == "__main__":
    main()
