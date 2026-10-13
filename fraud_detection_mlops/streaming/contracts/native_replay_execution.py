"""Bind the restricted native replay to committed code and existing frozen artifacts."""

import json
from pathlib import Path
import subprocess

from fraud_detection_mlops.artifacts import sha256
from fraud_detection_mlops.data.datasets.development_scoring_dataset import regular_file
from fraud_detection_mlops.serving.model_release import load_release
from fraud_detection_mlops.streaming.contracts.customer_day_alerts import CustomerDayAlertConfig
from fraud_detection_mlops.streaming.preparation.development_alert_threshold import (
    verify as verify_threshold,
)

VERSION = "native_causal_replay_execution_v1"
PROTOCOL_PATH = "references/native_causal_replay_execution_v1.json"
PROTOCOL_SHA256 = "762d10cea3bebdcddde3d94b67846e0f239bda5fcfcd8b547cf3225058ba5ff5"
CONFIGURATION_PATH = "references/customer_day_alert_policy_v1.json"
REFERENCE_PATHS = (
    PROTOCOL_PATH,
    CONFIGURATION_PATH,
    "references/causal_replay_protocol_v1.json",
    "references/handbook_source.json",
    "references/silver_contract_v1.json",
    "references/gold_contract_v1.json",
    "references/hgb_optuna_protocol_v1.json",
    "pyproject.toml",
    "poetry.lock",
)


def execution_protocol(root: Path) -> dict:
    path = regular_file(root / PROTOCOL_PATH)
    if sha256(path) != PROTOCOL_SHA256:
        raise ValueError("Native execution protocol changed; review a separate version")
    protocol = json.loads(path.read_text())
    for name, binding in (
        ("references/causal_replay_protocol_v1.json", "causal_protocol_sha256"),
        (CONFIGURATION_PATH, "configuration_sha256"),
    ):
        if sha256(regular_file(root / name)) != protocol["bindings"][binding]:
            raise ValueError("Frozen native replay binding changed")
    config = CustomerDayAlertConfig.model_validate_json((root / CONFIGURATION_PATH).read_bytes())
    if config.policy_sha256 != protocol["bindings"]["policy_sha256"]:
        raise ValueError("Registered triage policy differs from the frozen execution")
    return protocol


def committed_inputs(root: Path) -> dict:
    """Editor settings may be staged; code, dependency lock and contracts must be committed."""
    root = root.resolve()
    paths = sorted(
        [str(p.relative_to(root)) for p in (root / "fraud_detection_mlops").rglob("*.py")]
        + list(REFERENCE_PATHS)
    )
    subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", *paths],
        cwd=root,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "diff", "--quiet", "HEAD", "--", *paths],
        cwd=root,
        check=True,
        capture_output=True,
    )
    running_root = Path(__file__).resolve().parents[3]
    for name in paths:
        if name.startswith("fraud_detection_mlops/") and sha256(
            regular_file(running_root / name)
        ) != sha256(regular_file(root / name)):
            raise ValueError("Running implementation differs from the committed project root")
    return {
        "git_revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip(),
        "files": {name: sha256(regular_file(root / name)) for name in paths},
    }


def identified_runtime(root: Path, release: Path, threshold: Path, protocol: dict):
    """Verify threshold bytes/selection and release before any selected Silver bytes."""
    receipt = verify_threshold(threshold, protocol["bindings"]["threshold_manifest_sha256"], root)
    config = CustomerDayAlertConfig.model_validate_json((threshold / "policy.json").read_bytes())
    registered = CustomerDayAlertConfig.model_validate_json(
        (root / CONFIGURATION_PATH).read_bytes()
    )
    if config != registered or receipt["policy_sha256"] != protocol["bindings"]["policy_sha256"]:
        raise ValueError("Native threshold result differs from the registered triage policy")
    reference = protocol["reference"]
    runtime = load_release(release, reference["manifest_sha256"])
    if (
        runtime.manifest.evidence_kind != "verified_reference"
        or runtime.manifest.model_sha256 != reference["model_sha256"]
        or runtime.manifest.serving_release_id != reference["serving_release_id"]
    ):
        raise ValueError("Use the identified frozen April reference")
    return runtime, config
