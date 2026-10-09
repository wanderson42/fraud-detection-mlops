"""Committed implementation identities shared by all experimental workflows."""

from pathlib import Path
import subprocess

from fraud_detection_mlops.artifacts import sha256
from fraud_detection_mlops.config import PROJECT_ROOT
from fraud_detection_mlops.modeling.contracts.experiment_errors import ExperimentError


def implementation_files(project_root: Path) -> list[Path]:
    """Include nested algorithm, contract, evaluation and integration modules."""
    return sorted((project_root / "fraud_detection_mlops").rglob("*.py"))


def committed_inputs(policy_path):
    paths = [
        policy_path,
        PROJECT_ROOT / "poetry.lock",
        *implementation_files(PROJECT_ROOT),
        *sorted((PROJECT_ROOT / "references").glob("*protocol*.json")),
        *sorted((PROJECT_ROOT / "references").glob("*contract*.json")),
    ]
    names = sorted({str(p.resolve().relative_to(PROJECT_ROOT.resolve())) for p in paths})
    try:
        revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, text=True
        ).strip()
        subprocess.run(
            ["git", "ls-files", "--error-unmatch", "--", *names],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "diff", "--quiet", "HEAD", "--", *names],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        raise ExperimentError(
            "Commit policy and implementation before fitting candidates"
        ) from exc
    return {"git_revision": revision, "files": {n: sha256(PROJECT_ROOT / n) for n in names}}
