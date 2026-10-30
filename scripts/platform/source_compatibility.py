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


MONITORING_TRANSITION = "references/session_monitoring_source_transition_v1.json"
MONITORING_ALLOWED = {
    "scripts/platform/kind_observability.py",
    "scripts/platform/source_compatibility.py",
}


RUNTIME_TRANSITION = "references/runtime_maintenance_source_transition_v1.json"
RUNTIME_ALLOWED = {
    "docker/serving/Dockerfile",
    "scripts/platform/serving_recovery.py",
    "scripts/platform/source_compatibility.py",
    "scripts/platform/runtime_maintenance.py",
    "scripts/session_monitoring/controller.py",
    "infra/make/serving-recovery.mk",
}


PROBE_FORWARD_TRANSITION = "references/runtime_probe_forward_source_transition_v1.json"
PROBE_FORWARD_ALLOWED = {
    "scripts/platform/runtime_maintenance.py",
    "scripts/platform/source_compatibility.py",
}


RUNTIME_TAG_TRANSITION = "references/runtime_tag_source_transition_v1.json"
RUNTIME_TAG_ALLOWED = {
    "scripts/platform/runtime_maintenance.py",
    "scripts/platform/source_compatibility.py",
}


BYTECODE_TRANSITION = "references/python_bytecode_source_transition_v1.json"
BYTECODE_ALLOWED = {
    "infra/kind/tracking/Dockerfile",
    "infra/kind/round/Dockerfile",
    "scripts/platform/kind_tracking.py",
    "scripts/platform/source_compatibility.py",
}


TRACKING_BASELINE_TRANSITION = "references/tracking_baseline_source_transition_v1.json"
TRACKING_BASELINE_ALLOWED = {
    "scripts/platform/kind_operations.py",
    "scripts/platform/source_compatibility.py",
}


# Aggregate counts keep their units; this exact change accepts valid multi-day
# customer-day alerts. It does not claim old receiver images contain the fix.
SESSION_COUNTS_TRANSITION = "references/session_counts_source_transition_v1.json"
SESSION_COUNTS_ALLOWED = {
    "scripts/session_monitoring/contract.py",
    "scripts/platform/source_compatibility.py",
}


SESSION_BUSY_TRANSITION = "references/session_busy_operational_source_transition_v1.json"
SESSION_BUSY_ALLOWED = {
    "scripts/product_session/reader.py",
    "scripts/product_session/configuration.py",
    "scripts/session_monitoring/collector.py",
    "scripts/platform/source_compatibility.py",
}


SESSION_ONLY_TRANSITION = "references/session_only_monitoring_source_transition_v1.json"
SESSION_ONLY_ALLOWED = {
    "scripts/platform/kind_observability.py",
    "scripts/platform/source_compatibility.py",
    "scripts/session_monitoring/controller.py",
    "infra/helm/fraud-observability/templates/config.yaml",
    "infra/helm/fraud-observability/templates/storage.yaml",
    "infra/helm/fraud-observability/templates/workloads.yaml",
    "infra/helm/fraud-observability/values.yaml",
    "infra/helm/fraud-observability/values.schema.json",
}


COMPONENTS_TRANSITION = "references/session_components_source_transition_v1.json"
COMPONENTS_ALLOWED = {
    "scripts/session_monitoring/controller.py",
    "infra/kind/session-monitoring/Dockerfile",
    "infra/kind/session-monitoring/Dockerfile.dockerignore",
    "scripts/session_monitoring/collector.py",
    "scripts/session_monitoring/components.py",
    "scripts/session_monitoring/contract.py",
    "scripts/platform/kind_observability.py",
    "infra/helm/fraud-observability/files/components/overview.json",
    "infra/helm/fraud-observability/templates/config.yaml",
    "infra/helm/fraud-observability/files/components/quality.json",
    "infra/helm/fraud-observability/files/components/operations.json",
    "infra/helm/fraud-observability/values.yaml",
    "scripts/platform/session_observability.py",
    "infra/helm/fraud-observability/files/components/features.json",
    "scripts/session_monitoring/server.py",
    "scripts/platform/source_compatibility.py",
    "scripts/session_monitoring/details.py",
    "infra/helm/fraud-observability/values.schema.json",
}


PRESENTATION_TRANSITION = "references/session_dashboard_presentation_transition_v1.json"
PRESENTATION_ALLOWED = {
    "infra/helm/fraud-observability/files/components/quality.json",
    "infra/helm/fraud-observability/files/components/features.json",
    "infra/helm/fraud-observability/files/components/overview.json",
    "infra/helm/fraud-observability/files/components/operations.json",
    "scripts/platform/source_compatibility.py",
}


LIFECYCLE_PROFILE_TRANSITION = "references/lifecycle_profile_source_transition_v1.json"
LIFECYCLE_PROFILE_ALLOWED = {
    "scripts/platform/kind_operations.py",
    "scripts/platform/source_compatibility.py",
}

EVENT_SOURCE_TRANSITION = "references/session_event_source_transition_v1.json"
EVENT_SOURCE_ALLOWED = {
    "scripts/product_flow/event_source.py",
    "scripts/product_flow/inputs.py",
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
        (
            MONITORING_TRANSITION,
            "fraud_session_monitoring_source_transition_v1",
            MONITORING_ALLOWED,
        ),
        (
            RUNTIME_TRANSITION,
            "fraud_runtime_maintenance_source_transition_v1",
            RUNTIME_ALLOWED,
        ),
        (
            PROBE_FORWARD_TRANSITION,
            "fraud_runtime_probe_forward_source_transition_v1",
            PROBE_FORWARD_ALLOWED,
        ),
        (
            RUNTIME_TAG_TRANSITION,
            "fraud_runtime_tag_source_transition_v1",
            RUNTIME_TAG_ALLOWED,
        ),
        (
            BYTECODE_TRANSITION,
            "fraud_python_bytecode_source_transition_v1",
            BYTECODE_ALLOWED,
        ),
        (
            TRACKING_BASELINE_TRANSITION,
            "fraud_tracking_baseline_source_transition_v1",
            TRACKING_BASELINE_ALLOWED,
        ),
        (
            SESSION_COUNTS_TRANSITION,
            "fraud_session_counts_source_transition_v1",
            SESSION_COUNTS_ALLOWED,
        ),
        (
            SESSION_BUSY_TRANSITION,
            "fraud_session_busy_operational_source_transition_v1",
            SESSION_BUSY_ALLOWED,
        ),
        (
            SESSION_ONLY_TRANSITION,
            "fraud_session_only_monitoring_source_transition_v1",
            SESSION_ONLY_ALLOWED,
        ),
        (
            COMPONENTS_TRANSITION,
            "fraud_session_components_source_transition_v1",
            COMPONENTS_ALLOWED,
        ),
        (
            PRESENTATION_TRANSITION,
            "fraud_session_dashboard_presentation_transition_v1",
            PRESENTATION_ALLOWED,
        ),
        (
            LIFECYCLE_PROFILE_TRANSITION,
            "fraud_lifecycle_profile_source_transition_v1",
            LIFECYCLE_PROFILE_ALLOWED,
        ),
        (
            EVENT_SOURCE_TRANSITION,
            "fraud_session_event_source_transition_v1",
            EVENT_SOURCE_ALLOWED,
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
