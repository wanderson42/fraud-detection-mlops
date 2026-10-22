"""Transfer the verified historical snapshot into the owned KinD MLflow, without refit."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from uuid import uuid4

from scripts.platform import kind_foundation as f
from scripts.platform import kind_tracking as tracking
from scripts.platform.history import registry, source


def work():
    path = f.managed_destination(f.workspace() / "history", "storage")
    path.mkdir(mode=0o700, exist_ok=True)
    f.require(path.stat().st_mode & 0o077 == 0, "History workspace must be private")
    return path


def pins():
    files = [Path(__file__), *Path(__file__).with_name("history").glob("*.py")]
    names = [
        "references/frozen_candidate_v1.json",
        "references/evidence/mlflow_execution_2026-10-07.json",
        "references/evidence/final_evaluation_execution_2026-10-08.json",
        "references/evidence/laboratory_serving_native_validation_2026-10-09.json",
    ]
    return {
        str(p.relative_to(f.ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in [*files, *(f.ROOT / n for n in names)]
    }


def prepare(bundle_id, release_bundle_id, output=None):
    payload = source.prepare(f.ROOT, bundle_id, release_bundle_id)
    reference = registry.references(f.ROOT, payload)
    f.owner()
    target = tracking.inventory()
    value = {
        "schema": "fraud_kind_history_transfer_plan_v1",
        "payload": payload,
        "payload_sha256": source.fingerprint(payload),
        "reference": reference,
        "source_pins": pins(),
        "node_uid": f.owner()["node_uid"],
        "tracking_deployment_uid": target["deployment_uid"],
    }
    path = (
        f.managed_destination(output.absolute(), "storage")
        if output
        else work() / ("plan-" + uuid4().hex + ".json")
    )
    f.require(path.parent == work(), "Plan must be inside the private history workspace")
    f.private_json(path, value)
    return {
        "status": "planned",
        "plan": str(path),
        "plan_sha256": source.fingerprint(value),
        "source_counts": payload["source_counts"],
        "historical_baselines": [r["model_id"] for r in reference["models"]],
        "target_modified": False,
        "refit": False,
    }


def read_plan(path):
    path = f.managed_destination(path.absolute(), "storage")
    f.require(
        path.parent == work() and path.is_file() and path.stat().st_mode & 0o077 == 0,
        "Private history plan required",
    )
    value = json.loads(path.read_bytes())
    f.require(
        value["schema"] == "fraud_kind_history_transfer_plan_v1"
        and value["source_pins"] == pins(),
        "History plan source identity changed",
    )
    payload = value["payload"]
    current = source.prepare(f.ROOT, payload["snapshot_bundle_id"], payload["release_bundle_id"])
    f.require(
        source.fingerprint(current) == value["payload_sha256"] == source.fingerprint(payload),
        "Snapshot/plan identity changed",
    )
    f.require(
        value["reference"] == registry.references(f.ROOT, payload),
        "Reference quality-gate evidence changed",
    )
    f.require(
        value["node_uid"] == f.owner()["node_uid"]
        and value["tracking_deployment_uid"] == tracking.inventory()["deployment_uid"],
        "Destination ownership changed",
    )
    return value


def run(action, path, no_other_writers=False):
    f.require(action in {"import", "verify", "champion"}, "Unsupported history action")
    f.require(
        action == "verify" or no_other_writers,
        "Declare --no-other-writers for destination changes",
    )
    plan = read_plan(path)
    before = tracking.inventory()
    report = work() / (action + "-" + uuid4().hex + ".json")
    result = {
        "schema": "fraud_kind_history_transfer_v1",
        "status": "failed",
        "action": action,
        "plan": str(path),
        "plan_sha256": source.fingerprint(plan),
        "report": str(report),
        "refit": False,
        "historical_release_deployed": False,
        "production_promotion": False,
    }
    try:
        with TemporaryDirectory(prefix="http-", dir=work()) as temporary:
            env = tracking.client_environment(Path(temporary))
            for key in list(env):
                if key.startswith(("AWS_", "PG")) or key == "MLFLOW_BACKEND_STORE_URI":
                    env.pop(key)
            with tracking.port_forward() as uri:
                done = subprocess.run(
                    [sys.executable, "-m", "scripts.platform.history.worker"],
                    cwd=f.ROOT,
                    env=env,
                    input=json.dumps(
                        {
                            "action": action,
                            "payload": plan["payload"],
                            "reference": plan["reference"],
                            "uri": uri,
                        }
                    ),
                    stdout=subprocess.PIPE,
                    text=True,
                    timeout=1800,
                    check=False,
                )
            reply = json.loads(done.stdout)
            result["worker"] = reply
            f.require(
                done.returncode == 0 and reply.get("status") == "success",
                "History worker failed: " + reply.get("reason", "inspect report"),
            )
        after = tracking.inventory()
        f.require(before == after, "Tracking pod/deployment/image changed during history transfer")
        read_plan(path)
        result.update(
            status="success",
            destination_identities_preserved=True,
            source_snapshot_unchanged=True,
            client_database_and_AWS_environment_removed=True,
        )
        return result
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        result["reason"] = str(exc)[-2500:]
        raise
    finally:
        f.private_json(report, result)
        print("Relatório: " + str(report), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "import", "verify", "champion"))
    parser.add_argument("--bundle-id")
    parser.add_argument("--release-bundle-id")
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--no-other-writers", action="store_true")
    args = parser.parse_args()
    try:
        with f.exclusive():
            if args.action == "plan":
                f.require(
                    bool(args.bundle_id and args.release_bundle_id),
                    "Explicit snapshot and release bundle IDs required",
                )
                result = prepare(args.bundle_id, args.release_bundle_id, args.plan)
            else:
                f.require(args.plan is not None, "Explicit --plan required")
                result = run(args.action, args.plan, args.no_other_writers)
        print(json.dumps(result, indent=2, allow_nan=False))
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as exc:
        parser.exit(1, "History transfer stopped: " + str(exc) + "\n")


if __name__ == "__main__":
    main()
