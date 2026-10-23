"""Accept bounded committed source transitions without rewriting accepted receipts."""

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


# These two files describe the project/catalogue; executable policies/contracts stay pinned.
DOCUMENTARY = {"README.md", "references/persistence_catalogue_v1.json"}
REFACTOR_TRANSITION = "references/operational_refactor_source_transition_v1.json"
REFACTOR_ALLOWED = {
    "scripts/operational_round/training.py",
    "scripts/model_updates/training.py",
    "scripts/operational_round/controller.py",
    "scripts/model_updates/controller.py",
    "scripts/platform/container_images.py",
    "scripts/platform/kind_replay.py",
    "scripts/platform/kind_serving_delivery.py",
    "scripts/platform/kind_operations.py",
    "scripts/platform/source_compatibility.py",
    "scripts/platform/serving_recovery.py",
    "references/model_update_compatibility_v1.json",
}


RETIREMENT_TRANSITION = "references/synthetic_retirement_source_transition_v1.json"
RETIREMENT_ALLOWED = {
    "infra/helm/fraud-serving/README.md",
    "scripts/platform/local_access.py",
    "scripts/platform/kind_observability.py",
    "scripts/platform/kind_operations.py",
    "scripts/operational_round/serving.py",
    "scripts/model_updates/deployment.py",
    "scripts/platform/kind_serving_delivery.py",
    "scripts/platform/kind_serving_seed.py",
    "scripts/platform/kind_serving.py",
    "scripts/operational_round/controller.py",
    "scripts/platform/source_compatibility.py",
}


def _advance(saved, current, root, path, schema, allowed):
    location = root / path
    if not location.is_file():
        return saved
    data = location.read_bytes()
    declaration = json.loads(data)
    changes = declaration.get("changes", {})
    if declaration.get("schema") != schema or set(changes) != allowed:
        raise ValueError("Only the declared operational source transition is permitted")
    needed = any(
        name in saved or name in current
        for name in changes
        if saved.get(name) != current.get(name) and saved.get(name) == changes[name]["before"]
    )
    if not needed:
        return saved
    committed = subprocess.run(
        ["git", "show", "HEAD:" + path], cwd=root, capture_output=True, check=False
    )
    if committed.returncode or committed.stdout != data:
        raise ValueError(
            "Commit the reviewed source transition before reading old receipts: " + path
        )
    result = dict(saved)
    for name, hashes in changes.items():
        if name not in result and name not in current:
            continue
        if result.get(name) == hashes["before"]:
            if hashes["after"] is None:
                result.pop(name, None)
            else:
                result[name] = hashes["after"]
        elif result.get(name) not in (hashes["after"], current.get(name)):
            raise ValueError("Unreviewed operational source transition: " + name)
    return result


def accept(saved, current, root):
    """Compare execution identity; advance only exact reviewed operational changes in memory."""
    saved = {k: v for k, v in saved.items() if k not in DOCUMENTARY}
    current = {k: v for k, v in current.items() if k not in DOCUMENTARY}
    if saved == current:
        return
    root = Path(root)
    expected = dict(saved)
    for path, schema, allowed in [
        (TRANSITION, "fraud_serving_seed_source_transition_v1", ALLOWED),
        (REFACTOR_TRANSITION, "fraud_operational_refactor_source_transition_v1", REFACTOR_ALLOWED),
        (
            RETIREMENT_TRANSITION,
            "fraud_synthetic_retirement_source_transition_v1",
            RETIREMENT_ALLOWED,
        ),
    ]:
        expected = _advance(expected, current, root, path, schema, allowed)
    if expected != current:
        raise ValueError(
            "Scientific code, data contract or policy changed outside reviewed transitions"
        )
    for name in saved.keys() | current.keys():
        if (
            saved.get(name) != current.get(name)
            and current.get(name) is not None
            and hashlib.sha256((root / name).read_bytes()).hexdigest() != current[name]
        ):
            raise ValueError("Operational transition source bytes differ: " + name)
