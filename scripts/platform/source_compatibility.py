"""Accept one committed operational-only source transition without rewriting old receipts."""

import hashlib
import json
from pathlib import Path
import subprocess

TRANSITION = "references/serving_seed_source_transition_v1.json"
ALLOWED = {
    "scripts/operational_round/seed.py",
    "scripts/model_updates/controller.py",
    "scripts/model_updates/deployment.py",
    "scripts/product_flow/controller.py",
    "scripts/platform/source_compatibility.py",
    "scripts/platform/serving_recovery.py",
}


def accept(saved, current, root):
    if saved == current:
        return
    root = Path(root)
    data = (root / TRANSITION).read_bytes()
    committed = subprocess.run(
        ["git", "show", "HEAD:" + TRANSITION], cwd=root, capture_output=True, check=False
    )
    if committed.returncode or committed.stdout != data:
        raise ValueError("Commit the reviewed serving seed transition before reading old receipts")
    transition = json.loads(data)
    if (
        transition.get("schema") != "fraud_serving_seed_source_transition_v1"
        or set(transition["changes"]) != ALLOWED
    ):
        raise ValueError("Only the declared serving seed operational transition is permitted")
    expected = dict(saved)
    for name, hashes in transition["changes"].items():
        if name not in saved and name not in current:
            continue  # Product-only source is not a dependency of the model-update receipt.
        if saved.get(name) != hashes["before"] or current.get(name) != hashes["after"]:
            raise ValueError("Unreviewed operational source transition: " + name)
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != hashes["after"]:
            raise ValueError("Operational transition source bytes differ: " + name)
        expected[name] = hashes["after"]
    if expected != current:
        raise ValueError(
            "Scientific code, data contract or policy changed outside the seed transition"
        )
