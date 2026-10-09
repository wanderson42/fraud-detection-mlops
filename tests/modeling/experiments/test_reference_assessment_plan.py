import json
from pathlib import Path
import shutil
import subprocess

import pytest
from typer.testing import CliRunner

from fraud_detection_mlops.config import PROJECT_ROOT
from fraud_detection_mlops.modeling.contracts.temporal_assessment_protocol import (
    POLICY_PATH,
    AssessmentProtocolError,
)
from fraud_detection_mlops.modeling.experiments.reference_temporal_assessment import (
    app,
    prepare_assessment_plan,
)


@pytest.fixture
def plan_checkout(tmp_path):
    policy = json.loads((PROJECT_ROOT / POLICY_PATH).read_text())
    names = [str(POLICY_PATH), "poetry.lock", *(x["path"] for x in policy["bindings"].values())]
    for name in names:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(PROJECT_ROOT / name, target)
    # This fixture's implementation file participates in the commit check.
    source = tmp_path / "fraud_detection_mlops/fixture_implementation.py"
    source.parent.mkdir(parents=True)
    source.write_text('"""Synthetic plan-only implementation fixture."""\n')
    return tmp_path


def test_plan_does_not_read_native_rows_or_load_models_even_with_invalid_reserved_artifacts(
    plan_checkout, monkeypatch
):
    native = plan_checkout / "data/processed/handbook/transactions/2018-09-02.parquet"
    native.parent.mkdir(parents=True)
    native.write_bytes(b"not parquet; reading this is unauthorized")
    original = Path.open
    opened = []

    def recorded_open(path, *args, **kwargs):
        if path.is_relative_to(plan_checkout / "data"):
            pytest.fail("The preparation opened native data or model artifacts")
        opened.append(path.relative_to(plan_checkout).as_posix())
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", recorded_open)
    report = prepare_assessment_plan(plan_checkout)
    assert len(opened) == 4
    assert all(p.startswith("references/") and p.endswith(".json") for p in opened)
    assert report["windows"]["assessment"]["days"] == 14
    assert report["windows"]["operational_replay"]["days"] == 15
    assert report["model"]["loaded"] is False
    assert report["current_authorization"]["native_data_reads"] is False
    assert report["future_candidate_confirmation"]["authorized"] is False
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize(
    "path",
    [
        str(POLICY_PATH),
        "references/frozen_candidate_v1.json",
        "references/hgb_optuna_protocol_v1.json",
        "references/evidence/hgb_optuna_author_report_2026-10-09.json",
    ],
)
def test_changed_policy_or_reference_binding_blocks_the_plan(plan_checkout, path):
    with (plan_checkout / path).open("a") as handle:
        handle.write("\n")
    with pytest.raises(AssessmentProtocolError):
        prepare_assessment_plan(plan_checkout)


def test_committed_plan_rejects_untracked_modified_and_deleted_inputs(plan_checkout):
    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=plan_checkout, check=True, capture_output=True, text=True
        )

    git("init")
    git("config", "user.name", "Synthetic Test")
    git("config", "user.email", "synthetic@example.invalid")
    git("add", "poetry.lock")
    git("commit", "-m", "Initial fixture")
    with pytest.raises(AssessmentProtocolError, match="Commit protocol"):
        prepare_assessment_plan(plan_checkout, require_committed=True)

    git("add", "references", "fraud_detection_mlops")
    git("commit", "-m", "Prespecified assessment fixture")
    report = prepare_assessment_plan(plan_checkout, require_committed=True)
    assert report["commitment"]["git_revision"] == git("rev-parse", "HEAD").stdout.strip()
    assert report["current_authorization"]["native_data_reads"] is False
    (plan_checkout / "fraud_detection_mlops/fixture_implementation.py").write_text(
        "CHANGED = True\n"
    )
    with pytest.raises(AssessmentProtocolError, match="Commit protocol"):
        prepare_assessment_plan(plan_checkout, require_committed=True)
    (plan_checkout / "fraud_detection_mlops/fixture_implementation.py").unlink()
    with pytest.raises(AssessmentProtocolError, match="Commit protocol"):
        prepare_assessment_plan(plan_checkout, require_committed=True)


def test_cli_has_only_preparation_and_cannot_open_a_reserved_evaluation(plan_checkout):
    runner = CliRunner()
    result = runner.invoke(app, ["plan", "--project-root", str(plan_checkout)])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["status"] == "plan_prepared_only"
    assert runner.invoke(app, ["run", "--project-root", str(plan_checkout)]).exit_code != 0
