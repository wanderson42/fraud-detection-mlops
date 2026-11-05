"""Review exact Terraform plans for an isolated namespace on the owned local cluster."""

import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
from uuid import uuid4

from scripts.platform import kind_foundation as f
from scripts.platform.terraform_foundation import policy as p

ROOT = Path(__file__).resolve().parents[3]
CONFIG = ROOT / "infra/terraform/local-foundation"
CHART = ROOT / "infra/helm/fraud-platform"


def workspace():
    return f.managed_destination(ROOT / "data/storage/terraform/local-foundation/v1", "storage")


def files():
    paths = list(CONFIG.glob("*.tf")) + [CONFIG / ".terraform.lock.hcl"]
    paths += [x for x in CHART.rglob("*") if x.is_file()]
    paths += list(Path(__file__).parent.glob("*.py"))
    paths += [ROOT / "infra/kind/images.lock.json", ROOT / "scripts/platform/kind_foundation.py"]
    return {str(x.relative_to(ROOT)): p.sha(x) for x in sorted(paths)}


def command(args, *, cwd=None, timeout=900, codes=(0,)):
    """Never expose provider output or secret-bearing subprocess exceptions."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("TF_", "KUBE_", "HELM_"))}
    env.update(TF_IN_AUTOMATION="1", CHECKPOINT_DISABLE="1", TF_INPUT="0")
    done = subprocess.run(
        [str(x) for x in args],
        cwd=cwd or ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if done.returncode not in codes:
        p.private(
            workspace() / ("failure-" + uuid4().hex + ".json"),
            {
                "argv": [str(x) for x in args],
                "stdout": done.stdout,
                "stderr": done.stderr,
                "returncode": done.returncode,
            },
        )
        raise ValueError("Command failed; private diagnostics preserved; inspect workspace")
    return done


def tf(work, *args, codes=(0,)):
    p.require(
        json.loads(command(["terraform", "version", "-json"]).stdout)["terraform_version"]
        == p.VERSION,
        "Use pinned Terraform " + p.VERSION,
    )
    return command(["terraform", "-chdir=" + str(work / "root"), *args], codes=codes)


def kube(*args, data=None, codes=(0,)):
    done = f.run(
        [
            "kubectl",
            "--kubeconfig",
            str(f.workspace() / "kubeconfig"),
            "--context",
            f.CONTEXT,
            "--namespace",
            p.NAMESPACE,
            *args,
        ],
        data=data,
        check=False,
        timeout=660,
    )
    p.require(
        done.returncode in codes, "Isolated Kubernetes command failed; inspect private workspace"
    )
    return done


def accepted(session):
    from scripts.platform import host_supervision as h
    from scripts.session_monitoring import components

    check = h.check(session)
    return {k: check[k] for k in ("binding", "counts", "accepted_sources_unchanged")} | {
        "protected": components.protected(),
    }


def resources():
    return json.loads(
        kube("get", "namespace", p.NAMESPACE, "--ignore-not-found", "-o", "json").stdout or "{}"
    )


def state_resources(work):
    path = work / "root/terraform.tfstate"
    if not path.exists():
        return {}
    value = p.read(path)
    result = {}
    for row in value.get("resources", []):
        p.require(
            row.get("module") is None and row["mode"] == "managed", "Unexpected state module"
        )
        address = row["type"] + "." + row["name"]
        p.require(address in p.ADDRESSES and address not in result, "Unexpected state address")
        p.require(len(row["instances"]) == 1, "Unexpected state instances")
        result[address] = row["instances"][0]["attributes"]
    return result


def ownership(work, item):
    """No automatic recovery by importing a resource that appeared outside state."""
    ns = resources()
    state = state_resources(work)
    if not state:
        p.require(not ns, "Namespace exists without Terraform ownership; do not adopt")
        return
    p.require("kubernetes_namespace_v1.foundation" in state and ns, "Namespace/state missing")
    p.require(
        ns["metadata"]["annotations"].get(p.ANNOTATION) == item["owner_id"],
        "Namespace owner differs",
    )
    known = state["kubernetes_namespace_v1.foundation"]["metadata"][0]
    p.require(ns["metadata"]["uid"] == known["uid"], "Namespace UID differs")
    for kind, name, address in [
        ("secret", p.SECRET, "kubernetes_secret_v1.credentials"),
        ("secret", "sh.helm.release.v1." + p.RELEASE + ".v1", "helm_release.foundation"),
    ]:
        live = json.loads(
            kube("get", kind, name, "--ignore-not-found", "-o", "json").stdout or "{}"
        )
        p.require(
            bool(live) == (address in state), "Resource/state ambiguity; preserve and inspect"
        )
        if address.startswith("kubernetes_secret") and live:
            p.require(
                live["metadata"]["uid"] == state[address]["metadata"][0]["uid"],
                "Secret UID differs",
            )


def capacity():
    mem = dict(re.findall(r"^(\w+):\s+(\d+) kB", Path("/proc/meminfo").read_text(), re.MULTILINE))
    available = int(mem["MemAvailable"]) * 1024
    free = shutil.disk_usage(ROOT).free
    p.require(
        available >= 2 * 1024**3 and free >= 8 * 1024**3,
        "Require 2 GiB available host memory and 8 GiB free disk; inspect capacity",
    )
    return {
        "host_mem_available_bytes": available,
        "filesystem_free_bytes": free,
        "scope": "host availability, not measured per-container consumption",
    }


@contextmanager
def locked():
    with f.exclusive():
        work = workspace()
        work.mkdir(parents=True, exist_ok=True, mode=0o700)
        work.chmod(0o700)
        path = f.managed_destination(work / "operation.lock", "storage")
        with path.open("a") as lock:
            path.chmod(0o600)
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError("Another local Terraform operation is running") from None
            yield work


def load(work):
    item = p.read(work / "owner.json")
    p.require(item.get("schema") == "fraud_local_iac_owner_v1", "Unknown IaC ownership schema")
    p.require(
        bool(re.fullmatch(r"[a-f0-9]{32}", item.get("owner_id", ""))), "Invalid IaC owner ID"
    )
    p.require(
        item["source_pins"] == files(), "IaC sources changed; inspect, do not refresh ownership"
    )
    p.require(item["cluster"] == f.owner(), "Cluster identity differs")
    target = work / "root"
    current_names = {
        str(x.relative_to(target))
        for x in target.rglob("*")
        if x.is_file()
        and (
            x.is_relative_to(target / "chart")
            or x.suffix == ".tf"
            or x.name.endswith((".tf.json", ".tfvars", ".tfvars.json"))
            or x.name == ".terraform.lock.hcl"
        )
    }
    p.require(
        current_names == set(item["workspace_pins"]), "Extra or missing private configuration"
    )
    for name, digest in item["workspace_pins"].items():
        path = f.managed_destination(work / "root" / name, "storage")
        p.require(p.sha(path) == digest, "Private Terraform configuration changed")
    p.require(accepted(item["session_id"]) == item["accepted_before"], "Accepted session changed")
    ownership(work, item)
    return item


def initialize(work, session):
    if (work / "owner.json").exists():
        item = load(work)
        p.require(item["session_id"] == session, "Different accepted session")
        return {"status": "iac_already_initialized"}
    p.require(not (work / "root").exists(), "Partial initialization; preserve and inspect")
    p.require(not resources(), "Namespace already exists; adoption refused")
    available = capacity()
    cluster = f.owner()
    baseline = accepted(session)
    pins = files()
    p.require((CONFIG / ".terraform.lock.hcl").is_file(), "Committed provider lock required")
    target = work / "root"
    target.mkdir(mode=0o700)
    for source in CONFIG.iterdir():
        if source.suffix == ".tf" or source.name == ".terraform.lock.hcl":
            shutil.copyfile(source, target / source.name)
    shutil.copytree(CHART, target / "chart")
    owner_id = uuid4().hex
    inputs = {
        "kubeconfig": str(f.workspace() / "kubeconfig"),
        "owner_id": owner_id,
        "credentials": {
            k: secrets.token_hex(24)
            for k in ("postgres-password", "rustfs-access-key", "rustfs-secret-key")
        },
    }
    p.private(target / "private.auto.tfvars.json", inputs)
    item = {
        "schema": "fraud_local_iac_owner_v1",
        "owner_id": owner_id,
        "session_id": session,
        "source_pins": pins,
        "cluster": cluster,
        "accepted_before": baseline,
        "capacity": available,
        "workspace_pins": {
            str(x.relative_to(target)): p.sha(x) for x in target.rglob("*") if x.is_file()
        },
    }
    p.private(work / "owner.json", item)
    return {
        "status": "iac_initialized_not_applied",
        "namespace": p.NAMESPACE,
        "capacity": available,
    }


def validate(work):
    tf(work, "init", "-input=false", "-lockfile=readonly", "-no-color")
    tf(work, "fmt", "-check", "-recursive")
    tf(work, "validate", "-no-color")
    return {"status": "iac_configuration_validated", "native_acceptance": False}


def plan(work):
    load(work)
    capacity()
    validate(work)
    key = uuid4().hex
    path = work / "root" / ("plan-" + key)
    tf(
        work,
        "plan",
        "-input=false",
        "-no-color",
        "-lock-timeout=0s",
        "-out=" + str(path),
        codes=(0, 2),
    )
    path.chmod(0o600)
    value = json.loads(tf(work, "show", "-json", str(path)).stdout)
    inputs = p.read(work / "root/private.auto.tfvars.json")
    counts = p.plan_policy(value, inputs, work / "root/chart")
    receipt = {
        "schema": "fraud_local_iac_plan_v1",
        "plan_id": key,
        "plan_sha256": p.sha(path),
        "owner_sha256": p.sha(work / "owner.json"),
        "state_sha256": p.sha(work / "root/terraform.tfstate")
        if (work / "root/terraform.tfstate").exists()
        else None,
        "counts": counts,
        "namespace": p.NAMESPACE,
        "release": p.RELEASE,
    }
    p.private(work / ("plan-" + key + ".json"), receipt)
    return {"status": "iac_plan_review_required", **receipt}


def apply(work, plan_id, expected):
    load(work)
    p.require(re.fullmatch(r"[a-f0-9]{32}", plan_id or ""), "Explicit plan ID required")
    receipt = p.read(work / ("plan-" + plan_id + ".json"))
    path = work / "root" / ("plan-" + plan_id)
    p.require(p.sha(path) == expected == receipt["plan_sha256"], "Reviewed plan digest differs")
    p.require(p.sha(work / "owner.json") == receipt["owner_sha256"], "Plan owner differs")
    state = work / "root/terraform.tfstate"
    p.require(
        (p.sha(state) if state.exists() else None) == receipt["state_sha256"], "Stale plan state"
    )
    if state.exists():
        backup = work / ("state-before-" + uuid4().hex + ".json")
        p.private(backup, p.read(state))
    value = json.loads(tf(work, "show", "-json", str(path)).stdout)
    p.plan_policy(value, p.read(work / "root/private.auto.tfvars.json"), work / "root/chart")
    capacity()
    tf(work, "apply", "-input=false", "-no-color", "-lock-timeout=0s", str(path))
    if state.exists():
        state.chmod(0o600)
    item = load(work)
    p.private(
        work / ("apply-" + plan_id + ".json"),
        {
            "status": "applied_not_natively_accepted",
            "plan_sha256": expected,
            "owner_id": item["owner_id"],
        },
    )
    return {"status": "iac_applied_requires_readbacks", "namespace": p.NAMESPACE}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=["init", "validate", "preflight", "plan", "apply", "verify", "rehearse"]
    )
    parser.add_argument("--session-id", default="clean-install-arrival-v1")
    parser.add_argument("--plan-id")
    parser.add_argument("--expected-plan-sha256")
    parser.add_argument("--no-other-writers", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    try:
        with locked() as work:
            if args.action == "init":
                result = initialize(work, args.session_id)
            elif args.action == "apply":
                p.require(args.no_other_writers, "Declare no other writers for apply")
                result = apply(work, args.plan_id, args.expected_plan_sha256)
            elif args.action in {"verify", "rehearse"}:
                from scripts.platform.terraform_foundation import native

                item = load(work)
                p.require(
                    args.action != "rehearse" or args.no_other_writers,
                    "Declare no other writers for pod recreation",
                )
                result = native.verify(work, item, recreate=args.action == "rehearse")
            else:
                load(work)
                if args.action == "validate":
                    result = validate(work)
                elif args.action == "plan":
                    result = plan(work)
                else:
                    result = {"status": "iac_preflight_verified", "capacity": capacity()}
        print(json.dumps(result, indent=2))
    except ValueError, OSError, subprocess.SubprocessError, KeyError, json.JSONDecodeError:
        raise SystemExit(
            "IaC stopped; private details withheld. Preserve state/receipts; inspect status."
        ) from None


if __name__ == "__main__":
    main()
