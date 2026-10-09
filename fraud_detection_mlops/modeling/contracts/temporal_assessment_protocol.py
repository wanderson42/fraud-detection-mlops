"""Immutable preparation policy and reference identity; no native data/model access."""

import hashlib
import json
from pathlib import Path

POLICY_PATH = Path("references/reference_assessment_protocol_v1.json")
# The v1 snapshot is reviewed as a whole; changes require a new policy identity.
POLICY_SHA256 = "b548521f0d54d2be47f47918f47d3f0c834bc8234da07c56f9be1d3be83edced"


class AssessmentProtocolError(ValueError):
    """The declared policy or its historical reference binding changed."""


def load_assessment_protocol(project_root: Path) -> tuple[dict, dict]:
    """Read only the policy and three bound JSON records from the checkout."""
    root = Path(project_root)
    try:
        raw = (root / POLICY_PATH).read_bytes()
        if hashlib.sha256(raw).hexdigest() != POLICY_SHA256:
            raise AssessmentProtocolError("Assessment v1 policy changed; review a new version")
        policy = json.loads(raw)
        records = {}
        for name, binding in policy["bindings"].items():
            raw = (root / binding["path"]).read_bytes()
            if hashlib.sha256(raw).hexdigest() != binding["sha256"]:
                raise AssessmentProtocolError(f"Assessment binding changed: {name}")
            records[name] = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AssessmentProtocolError(
            "Cannot read assessment policy and bound JSON records"
        ) from exc
    freeze, development, result = (
        records[name] for name in ("frozen_reference", "development_policy", "development_result")
    )
    summary = result["summary"]
    if (
        policy["reference"]["model_uri"] != freeze["model_uri"]
        or policy["reference"]["model_sha256"] != freeze["model"]["model_sha256"]
        or policy["source_commit"] != freeze["source"]["commit"]
        or policy["source_commit"] != development["source_commit"]
        or policy["windows"]["assessment"] != development["reserved"]["confirmation"]
        or policy["windows"]["operational_replay"] != development["reserved"]["operational_replay"]
        or summary["decision"] != "retain_reference"
        or summary["candidate_for_confirmation_review"] is not None
        or summary["budget_finished"] is not True
    ):
        raise AssessmentProtocolError(
            "Reference identity, reserved windows or selection do not reconcile"
        )
    return policy, records
