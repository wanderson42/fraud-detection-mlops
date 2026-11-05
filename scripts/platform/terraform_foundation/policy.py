"""Offline plan policy and private files; no Terraform or Kubernetes execution."""

import hashlib
import json
import os
from pathlib import Path
import tempfile

NAMESPACE = "fraud-iac-foundation-v1"
RELEASE = "fraud-iac-foundation"
SECRET = "fraud-iac-foundation-credentials"
VERSION = "1.17.1"
ANNOTATION = "fraud.iac/owner-id"
ADDRESSES = {
    "kubernetes_namespace_v1.foundation",
    "kubernetes_secret_v1.credentials",
    "helm_release.foundation",
}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(path):
    path = Path(path)
    require(
        not any(x.is_symlink() for x in (path, *path.parents)), "Symlink refused in hashed inputs"
    )
    return hashlib.sha256(path.read_bytes()).hexdigest()


def private(path, value, *, replace=False):
    """Atomic private publication; new receipts are never overwritten."""
    path = Path(path)
    for item in (path, *path.parents):
        require(not item.is_symlink(), "Symlink refused in private workspace")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temp = Path(stream.name)
        try:
            os.fchmod(stream.fileno(), 0o600)
            stream.write((json.dumps(value, indent=2, sort_keys=True) + "\n").encode())
            stream.flush()
            os.fsync(stream.fileno())
            if replace:
                os.replace(temp, path)
            else:
                os.link(temp, path)
        finally:
            temp.unlink(missing_ok=True)


def read(path):
    path = Path(path)
    for item in (path, *path.parents):
        require(not item.is_symlink(), "Symlink refused in private workspace")
    require(path.is_file() and path.stat().st_mode & 0o077 == 0, "Private file required")
    return json.loads(path.read_bytes())


def plan_policy(plan, inputs, chart):
    """Permit only initial creates and later no-ops of the exact three resources."""
    require(plan.get("format_version") == "1.2", "Unsupported Terraform plan format")
    require(not plan.get("errored") and plan.get("complete", True), "Incomplete plan refused")
    require(not plan.get("deferred_changes"), "Deferred plan refused")
    require(not plan.get("resource_drift"), "Drift requires inspection")
    require(not plan.get("output_changes"), "Unexpected outputs in plan")
    changes = plan.get("resource_changes", [])
    require({x.get("address") for x in changes} == ADDRESSES, "Unexpected plan resources")
    require(len(changes) == 3, "Duplicate plan addresses")
    counts = {"create": 0, "no-op": 0}
    for item in changes:
        require(item.get("mode") == "managed", "Unexpected data resource")
        change = item["change"]
        actions = change["actions"]
        require(actions in (["create"], ["no-op"]), "Updates, replacements or deletions refused")
        require(not change.get("importing"), "Import refused")
        after = change["after"]
        name = item["address"]
        if name.startswith("kubernetes_"):
            meta = after["metadata"][0]
            require(
                meta["annotations"].get(ANNOTATION) == inputs["owner_id"], "Wrong planned owner"
            )
            if name.startswith("kubernetes_namespace"):
                require(meta["name"] == NAMESPACE, "Wrong planned namespace")
            else:
                require(meta["name"] == SECRET and meta["namespace"] == NAMESPACE, "Wrong Secret")
                require(after["data"] == inputs["credentials"], "Planned credentials differ")
        else:
            require(after["name"] == RELEASE and after["namespace"] == NAMESPACE, "Wrong release")
            require(after["chart"] == str(chart), "Wrong chart path")
            require(after["description"] == "fraud-iac:" + inputs["owner_id"], "Wrong Helm owner")
            require(after["version"] == "0.1.0", "Wrong chart version")
            require(
                after["values"] == [f'"existingSecret": "{SECRET}"\n'], "Unexpected chart values"
            )
        counts[actions[0]] += 1
    return counts
