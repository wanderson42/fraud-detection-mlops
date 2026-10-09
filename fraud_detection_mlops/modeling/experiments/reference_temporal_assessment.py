"""Prepare the frozen reference's assessment plan without reading reserved rows."""

from datetime import date
import json
from pathlib import Path
import subprocess
from typing import Annotated

import typer

from fraud_detection_mlops.config import PROJECT_ROOT
from fraud_detection_mlops.modeling.contracts.temporal_assessment_protocol import (
    POLICY_PATH,
    POLICY_SHA256,
    AssessmentProtocolError,
    load_assessment_protocol,
)

app = typer.Typer(no_args_is_help=True)


def _committed_revision(root: Path, policy: dict) -> str:
    names = sorted(
        {
            str(POLICY_PATH),
            "poetry.lock",
            *(item["path"] for item in policy["bindings"].values()),
            *(str(p.relative_to(root)) for p in (root / "fraud_detection_mlops").rglob("*.py")),
        }
    )
    try:
        top = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"], cwd=root, text=True, stderr=subprocess.PIPE
        ).strip()
        if Path(top).resolve() != root:
            raise AssessmentProtocolError("Project root must be the checkout root")
        revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True, stderr=subprocess.PIPE
        ).strip()
        subprocess.run(
            ["git", "ls-files", "--error-unmatch", "--", *names],
            cwd=root,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [
                "git",
                "diff",
                "--quiet",
                "HEAD",
                "--",
                *names,
                ":(glob)fraud_detection_mlops/**/*.py",
            ],
            cwd=root,
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise AssessmentProtocolError(
            "Commit protocol, bound records, implementation and lockfile before using a committed plan"
        ) from exc
    return revision


def prepare_assessment_plan(
    project_root: Path = PROJECT_ROOT, *, require_committed: bool = False
) -> dict:
    """Inspect checkout metadata only; committing the plan does not authorize execution."""
    root = Path(project_root).resolve()
    policy, records = load_assessment_protocol(root)
    try:
        revision = _committed_revision(root, policy)
        commitment = {"verified": True, "git_revision": revision}
    except AssessmentProtocolError as exc:
        if require_committed:
            raise
        commitment = {"verified": False, "git_revision": None, "reason": str(exc)}
    windows = {}
    for name, window in policy["windows"].items():
        start, end = (date.fromisoformat(window[k]) for k in ("start", "end_exclusive"))
        windows[name] = {**window, "days": (end - start).days, "native_rows_read": 0}
    return {
        "version": policy["version"],
        "status": "plan_prepared_only",
        "protocol_sha256": POLICY_SHA256,
        "commitment": commitment,
        "model": {
            **policy["reference"],
            "feature_columns": records["frozen_reference"]["feature_columns"],
            "loaded": False,
        },
        "development_selection": {
            "decision": records["development_result"]["summary"]["decision"],
            "eligible_candidate": None,
            "source": "bound_author_report_not_native_study_verification",
        },
        "windows": windows,
        "time_policy": policy["time_policy"],
        "reporting": policy["reporting"],
        "conditional_queue_reference": policy["conditional_queue_reference"],
        "future_candidate_confirmation": policy["future_candidate_confirmation"],
        "current_authorization": policy["current_authorization"],
        "bound_json_records": policy["bindings"],
    }


@app.callback()
def assessment_commands():
    """Review the statistical plan before implementing reserved-data execution."""


@app.command("plan")
def plan_command(
    project_root: Annotated[Path, typer.Option()] = PROJECT_ROOT,
    require_committed: Annotated[bool, typer.Option()] = False,
):
    try:
        report = prepare_assessment_plan(project_root, require_committed=require_committed)
    except AssessmentProtocolError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(json.dumps(report, indent=2, allow_nan=False))


if __name__ == "__main__":
    app()
