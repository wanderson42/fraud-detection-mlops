"""Operate a committed update; mutation approval never follows from training alone."""

import argparse
import json
from pathlib import Path
import subprocess
import time
from uuid import uuid4

from scripts.model_updates import policy
from scripts.operational_round import controller as c
from scripts.operational_round import policy as p
from scripts.operational_round import serving as original_serving
from scripts.platform import kind_datasets, kind_pipeline, kind_tracking, source_compatibility
from scripts.platform import kind_foundation as f
from scripts.storage import gold_archive


def workspace():
    return f.managed_destination(f.workspace() / "model-updates", "storage")


def work(key):
    return f.managed_destination(workspace() / p.round_id(key), "storage")


def legacy_compatible(base):
    """Read the accepted initial round through the reviewed source compatibility boundary."""
    source_compatibility.accept(base["source_pins"], c.pins(), f.ROOT)
    p.require(
        base["node_uid"] == f.owner()["node_uid"]
        and base["tracking_deployment_uid"] == kind_tracking.inventory()["deployment_uid"],
        "Accepted first-round ownership changed",
    )


def pins():
    values = c.pins()
    # Scientific/runtime inputs are frozen; later documentation closure is not a model change.
    values.pop("README.md", None)
    values.pop("references/persistence_catalogue_v1.json", None)
    for name in [
        "references/model_update_policy_v1.json",
        "references/model_update_policy_search_v1.json",
        "scripts/platform/source_compatibility.py",
        "scripts/platform/serving_recovery.py",
    ]:
        values[name] = p.sha(f.ROOT / name)
    for folder in [
        "scripts/model_updates",
        "infra/kind/model-updates",
        "infra/helm/fraud-model-updates",
    ]:
        values.update(
            {
                v.relative_to(f.ROOT).as_posix(): p.sha(v)
                for v in (f.ROOT / folder).rglob("*")
                if v.is_file() and "__pycache__" not in v.parts and v.suffix != ".pyc"
            }
        )
    return values


def committed(policy_path):
    values = {**pins(), policy_path.relative_to(f.ROOT).as_posix(): p.sha(policy_path)}
    import hashlib

    for name, expected in values.items():
        result = subprocess.run(
            ["git", "show", "HEAD:" + name], cwd=f.ROOT, capture_output=True, check=False
        )
        p.require(
            result.returncode == 0 and hashlib.sha256(result.stdout).hexdigest() == expected,
            "Commit the update implementation and policy before init: " + name,
        )
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=f.ROOT, text=True).strip()
    return commit, values


def read(key):
    value = json.loads((work(key) / "update.json").read_bytes())
    current = {**pins(), value["policy_path"]: p.sha(f.ROOT / value["policy_path"])}
    source_compatibility.accept(value["source_pins"], current, f.ROOT)
    p.require(
        value["node_uid"] == f.owner()["node_uid"]
        and value["policy"] == policy.load(f.ROOT / value["policy_path"]),
        "Update implementation, policy or node changed; preserve receipts",
    )
    return value


def save(key, name, value):
    path = work(key) / name
    if path.exists():
        p.require(
            json.loads(path.read_bytes()) == value, "Immutable update receipt differs: " + name
        )
    else:
        f.private_json(path, value)
    return value


def init(key, policy_path, baseline_round, gold_bundle):
    if (work(key) / "update.json").exists():
        value = read(key)
        p.require(
            value["policy_path"] == policy_path.relative_to(f.ROOT).as_posix()
            and (not gold_bundle or gold_bundle == value["provenance"]["gold_bundle_id"]),
            "Existing update uses another policy or Gold bundle",
        )
        return value
    commit, source_pins = committed(policy_path)
    config = policy.load(policy_path)
    workspace().mkdir(mode=0o700, parents=True, exist_ok=True)
    path = work(key)
    path.mkdir(mode=0o700, exist_ok=True)
    p.require(not any(path.iterdir()), "Incomplete update directory; inspect before retry")
    state_path = workspace() / "current.json"
    if state_path.exists():
        active = json.loads(state_path.read_bytes())
    else:
        base = json.loads((c.work(baseline_round) / "round.json").read_bytes())
        legacy_compatible(base)
        plan = json.loads((c.work(baseline_round) / "serving.json").read_bytes())
        result = json.loads((c.work(baseline_round) / "promotion.json").read_bytes())
        accepted = [
            json.loads(v.read_bytes()) for v in c.work(baseline_round).glob("verification-*.json")
        ]
        p.require(
            any(
                v.get("status") == "canonical_round_complete"
                and v["model_sha256"] == result["model_sha256"]
                for v in accepted
            ),
            "Complete the initial canonical round first",
        )
        active = {
            "version": result["version"],
            "run_id": result["run_id"],
            "slot": "blue",
            "model_sha256": result["model_sha256"],
            "owner_id": base["owner_id"],
            "plan": plan,
            "paths": {s: str(c.work(baseline_round) / "carrier" / s) for s in ("blue", "green")},
        }
        p.require(
            plan["owner_id"] == base["owner_id"]
            and plan["model_sha256"] == result["model_sha256"],
            "Accepted initial serving receipt differs",
        )
        active["plan"]["slot_images"] = {
            s: {k: plan["images"][k] for k in ("runtime", "artifacts")} for s in ("blue", "green")
        }
    from scripts.model_updates.deployment import check_active

    check_active(active, config)
    if gold_bundle:
        restored = kind_datasets.transfer("restore", gold_bundle, "gold")
        directory = Path(restored["restored_directory"])
    else:
        directory, gold_bundle, _, _ = c.verified_gold(baseline_round)
    document = json.loads((directory / "manifest.json").read_bytes())
    gold = Path(gold_archive.verify_directory(directory, document)["gold_path"])
    p.require(p.sha(directory / "manifest.json") == gold_bundle, "Gold bundle hash differs")
    value = {
        "schema": "fraud_model_update_v1",
        "round_id": key,
        "owner_id": uuid4().hex,
        "baseline": active,
        "node_uid": f.owner()["node_uid"],
        "implementation_commit": commit,
        "policy_path": policy_path.relative_to(f.ROOT).as_posix(),
        "policy": config,
        "policy_sha256": p.digest(config),
        "source_pins": source_pins,
        "gold_directory": str(directory),
        "provenance": {
            "implementation_commit": commit,
            "code_pins_sha256": p.digest(source_pins),
            "gold_bundle_id": gold_bundle,
            "gold_manifest_uri": "s3://fraud-datasets/gold/v1/" + gold_bundle + "/manifest.json",
            "gold_dataset_manifest_sha256": p.sha(gold / "manifest.json"),
            "poetry_lock_sha256": p.sha(f.ROOT / "poetry.lock"),
            "training_requirements_sha256": p.sha(
                f.ROOT / "infra/kind/model-updates/requirements.txt"
            ),
        },
    }
    # Maturity is a source/event-clock condition, never a wall-clock assumption.
    validation = batches_value(value)[1]
    policy.mature(validation[2], config["comparison_as_of"])
    saved = save(key, "update.json", value)
    if not state_path.exists():
        f.private_json(state_path, active)
    return saved


def batches_value(info):
    from fraud_detection_mlops.modeling.experiments.candidate_training import load_split

    directory = Path(info["gold_directory"])
    p.require(
        p.sha(directory / "manifest.json") == info["provenance"]["gold_bundle_id"],
        "Gold bundle changed",
    )
    checked = gold_archive.verify_directory(
        directory, json.loads((directory / "manifest.json").read_bytes())
    )
    gold = Path(checked["gold_path"])
    p.require(
        p.sha(gold / "manifest.json") == info["provenance"]["gold_dataset_manifest_sha256"],
        "Dataset changed",
    )
    manifest = json.loads((gold / "manifest.json").read_bytes())
    return (
        load_split(gold, manifest, "train"),
        load_split(gold, manifest, "validation"),
        {s: [v for v in manifest["files"] if v["split"] == s] for s in ("train", "validation")},
    )


def trainer_image(key):
    info = read(key)
    value = json.loads((work(key) / "trainer-image.json").read_bytes())
    actual = c.cri(value["image"])
    p.require(
        value["source_pins"] == info["source_pins"] and actual["id"] == value["identity"],
        "Loaded update trainer differs",
    )
    return value


def build(key):
    info = read(key)
    if (work(key) / "trainer-image.json").exists():
        return trainer_image(key)
    tag = "fraud-round-trainer:" + uuid4().hex
    c.image_command(
        ["docker", "build", "--file", "infra/kind/model-updates/Dockerfile", "--tag", tag, "."],
        timeout=1200,
    )
    actual = c.load_image(tag)
    return save(
        key,
        "trainer-image.json",
        {
            "image": tag,
            "identity": actual["id"],
            "loaded_identities": [actual["id"], *actual.get("repoDigests", [])],
            "source_pins": info["source_pins"],
        },
    )


def training_result(key):
    info = read(key)
    summary = json.loads((work(key) / "training.json").read_bytes())
    p.require(
        summary["round_id"] == key
        and summary["policy"] == info["policy"]
        and summary["provenance"] == info["provenance"],
        "Training receipt identity differs",
    )
    return summary


def qualify(key, no_other_writers):
    from scripts.model_updates import registry

    p.require(no_other_writers, "Declare no other Registry writers")
    info = read(key)
    summary = training_result(key)
    if summary["status"] != "qualified":
        return save(
            key,
            "decision.json",
            {"status": "retain_champion", "reason": "absolute_quality_gate_failed"},
        )
    train_data, validation, partitions = batches_value(info)
    with kind_tracking.port_forward() as uri:
        assessment = registry.assess(
            summary, info["baseline"], uri, train_data, validation, partitions
        )
        save(key, "assessment.json", assessment)
        if not assessment["gate"]["passed"]:
            return save(
                key, "decision.json", {"status": "retain_champion", "assessment": assessment}
            )
        result = registry.register(
            summary,
            assessment,
            info["baseline"],
            uri,
            info["baseline"]["owner_id"],
            no_other_writers=no_other_writers,
        )
    return save(key, "challenger.json", result)


def verify(key):
    from scripts.model_updates.deployment import check_active

    info = read(key)
    active = (
        json.loads((workspace() / "current.json").read_bytes())
        if (workspace() / "current.json").exists()
        else info["baseline"]
    )
    result = check_active(active, info["policy"])
    result = {
        "status": "active_identity_verified",
        "version": active["version"],
        "slot": active["slot"],
        "model_sha256": active["model_sha256"],
        "http": result,
    }
    f.private_json(work(key) / ("verification-" + uuid4().hex + ".json"), result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=[
            "validate",
            "init",
            "build",
            "train",
            "qualify",
            "stage",
            "release",
            "recover",
            "rollback",
            "verify",
            "api",
        ],
    )
    parser.add_argument("--update-id", default="successor-v1")
    parser.add_argument("--policy", type=Path, default=policy.POLICY)
    parser.add_argument("--baseline-round", default="portfolio-v1")
    parser.add_argument("--gold-bundle-id")
    parser.add_argument("--approval-sha256", default="")
    parser.add_argument("--no-other-writers", action="store_true")
    args = parser.parse_args()
    try:
        if args.action == "validate":
            policy.load(args.policy.resolve())
            f.run(["helm", "lint", "--strict", f.ROOT / "infra/helm/fraud-model-updates"])
            print(
                json.dumps(
                    {
                        "status": "update_policy_and_templates_validated",
                        "native_update_validated": False,
                    }
                )
            )
            return
        with f.exclusive():
            if args.action == "init":
                value = init(
                    args.update_id, args.policy.resolve(), args.baseline_round, args.gold_bundle_id
                )
                result = {
                    "status": "update_initialized",
                    "update_id": args.update_id,
                    "commit": value["implementation_commit"],
                    "baseline_version": value["baseline"]["version"],
                }
            elif args.action == "qualify":
                result = qualify(args.update_id, args.no_other_writers)
            elif args.action in {"stage", "release", "recover", "rollback"}:
                from scripts.model_updates import deployment

                p.require(args.no_other_writers, "Declare no other Registry/route writers")
                if args.action == "stage":
                    value = deployment.stage(args.update_id)
                    result = {
                        k: value[k]
                        for k in (
                            "status",
                            "update_id",
                            "approval_sha256",
                            "before",
                            "after",
                            "gates",
                        )
                    }
                else:
                    value = deployment.release(
                        args.update_id,
                        args.approval_sha256,
                        args.no_other_writers,
                        recover=args.action == "recover",
                        rollback=args.action == "rollback",
                    )
                    result = {
                        "status": value["phase"],
                        "update_id": args.update_id,
                        "before": value["before"],
                        "after": value["after"],
                        "rollback_approval_sha256": p.digest(
                            {"action": "rollback", "release": value["approval_sha256"]}
                        ),
                    }
            elif args.action == "api":
                info = read(args.update_id)
                active = (
                    json.loads((workspace() / "current.json").read_bytes())
                    if (workspace() / "current.json").exists()
                    else info["baseline"]
                )
                from scripts.model_updates.deployment import check_active

                check_active(active, info["policy"])
                with original_serving.forward(original_serving.gateway(active["plan"])) as uri:
                    print("API: " + uri + " (Ctrl+C closes only local access)", flush=True)
                    try:
                        while True:
                            time.sleep(1)
                    except KeyboardInterrupt:
                        pass
                result = {"status": "local_access_closed"}
            else:
                result = globals()[args.action](args.update_id)
        print(json.dumps(result, indent=2, allow_nan=False))
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as exc:
        parser.exit(1, "Model update stopped: " + str(exc)[-3000:] + "\n")


def train(key):
    round_info = read(key)
    if (work(key) / "training.json").exists():
        value = training_result(key)
        execution = json.loads((work(key) / "training-job.json").read_bytes())
        current = f.kube(
            "get", "job", execution["name"], "--ignore-not-found", "-o", "json"
        ).stdout.strip()
        if current:
            job = json.loads(current)
            p.require(
                job["metadata"]["uid"] == execution["uid"]
                and job["metadata"].get("annotations", {}).get("fraud.round/owner-id")
                == round_info["owner_id"],
                "Another Job occupies the saved training identity",
            )
            f.helm("uninstall", execution["name"], "--wait", "--timeout", "90s", timeout=120)
        return value
    gold_id = round_info["provenance"]["gold_bundle_id"]
    images = kind_pipeline.images()
    own = kind_datasets.initialize()
    image = trainer_image(key)
    release = "fraud-update-train-" + round_info["owner_id"][:12]
    plan = round_info
    values = {
        "ownerId": round_info["owner_id"],
        "roundId": key,
        "trainerImage": image["image"],
        "transportImage": images["images"]["transport"]["image"],
        "datasetOwner": own["owner_id"],
        "goldBundleId": gold_id,
        "planJSON": json.dumps(plan),
        "transportScript": (f.ROOT / "infra/kind/round/transport.py").read_text(),
    }
    path = work(key) / "job-values.json"
    if path.exists():
        p.require(json.loads(path.read_bytes()) == values, "Existing Job plan differs")
    else:
        f.private_json(path, values)
    current = f.kube("get", "job", release, "--ignore-not-found", "-o", "json").stdout.strip()
    if current:
        job = json.loads(current)
        p.require(
            job["metadata"].get("annotations", {}).get("fraud.round/owner-id")
            == round_info["owner_id"],
            "Foreign training Job",
        )
    else:
        f.helm(
            "upgrade",
            "--install",
            release,
            c.CHART,
            "--values",
            path,
            "--timeout",
            "90s",
            timeout=120,
        )
    deadline = time.monotonic() + 1850
    next_progress = 0
    try:
        while True:
            job = json.loads(f.kube("get", "job", release, "-o", "json").stdout)
            if job.get("status", {}).get("succeeded") or job.get("status", {}).get("failed"):
                break
            if time.monotonic() >= next_progress:
                print("Waiting for owned training Job " + release + "...", flush=True)
                next_progress = time.monotonic() + 30
            p.require(
                time.monotonic() < deadline, "Training deadline reached; preserve Job and inspect"
            )
            time.sleep(2)
        raw = f.kube("logs", "job/" + release, "-c", "train", timeout=60).stdout
        p.require(len(raw.encode()) <= 2 * 1024 * 1024, "Training result exceeds metadata budget")
        value = c.parse_result(raw)
        p.require(
            (value["status"] == "qualified" and job.get("status", {}).get("succeeded") == 1)
            or (value["status"] == "rejected" and job.get("status", {}).get("failed") == 1),
            "Training round rejected or failed: " + json.dumps(value.get("gate", value))[-1500:],
        )
        p.require(
            value["round_id"] == key
            and value["policy_sha256"] == round_info["policy_sha256"]
            and value["provenance"]["gold_bundle_id"] == gold_id,
            "Training result identity differs",
        )
        pods = json.loads(f.kube("get", "pods", "-l", "job-name=" + release, "-o", "json").stdout)[
            "items"
        ]
        p.require(len(pods) == 1, "Expected one owned training pod")
        execution = c.check_job(
            job,
            pods[0],
            round_info["owner_id"],
            {"train": image, "download": images["images"]["transport"]},
        )
        value["job_identity"] = {
            "name": release,
            "uid": job["metadata"]["uid"],
            "image": image,
            "execution": execution,
        }
        # Keep the exact report bytes emitted by the Job; execution metadata has a separate receipt.
        job_metadata = value.pop("job_identity")
        save(key, "training-job.json", job_metadata)
        save(key, "training.json", value)
        f.helm("uninstall", release, "--wait", "--timeout", "90s", timeout=120)
        return value
    except (ValueError, OSError) as exc:
        failure = {
            "status": "failed",
            "reason": str(exc),
            "job": release,
            "diagnostics": f.kube("describe", "job", release, check=False).stdout[-4000:],
        }
        f.private_json(work(key) / ("training-failure-" + uuid4().hex + ".json"), failure)
        raise


if __name__ == "__main__":
    main()
