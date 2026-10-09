import json
from pathlib import Path
import shutil

import pytest
from tests.modeling.experiments.test_reference_assessment_execution import (
    assessment_source,
    synthetic_reference,
)
from typer.testing import CliRunner

from fraud_detection_mlops.artifacts import sha256
from fraud_detection_mlops.modeling.experiments import (
    reference_uncertainty_analysis as uncertainty,
)

__all__ = ["assessment_source", "synthetic_reference"]


def test_native_bundle_analysis_uses_saved_predictions_without_model_or_silver(assessment_source):
    s = assessment_source
    s.run()
    before = {
        str(p.relative_to(s.directory)): sha256(p) for p in s.directory.rglob("*") if p.is_file()
    }
    calls = s.calls.copy()
    shutil.rmtree(s.silver_root)
    result = uncertainty.analyze_saved_assessment(s.directory)
    assert s.calls == calls
    assert result["coverage_validated"] is False
    assert len(result["configurations"]) == 4
    report = json.loads(Path(result["report_path"]).read_text())
    assert report["predictions_sha256"] == before["results/predictions.parquet"]
    assert report["interpretation"]["formal_temporal_hypothesis_test"] is False
    assert {
        str(p.relative_to(s.directory)): sha256(p) for p in s.directory.rglob("*") if p.is_file()
    } == before
    for row in result["configurations"]:
        assert row["metrics"]["pooled_average_precision"]["observed"] == pytest.approx(
            report["configurations"][0]["metrics"]["pooled_average_precision"]["observed"]
        )
        assert row["metrics"]["mean_daily_customer_recall_at_100"]["defined_day_counts_min"] < 14


def test_corrupt_prediction_bundle_blocks_output(assessment_source, tmp_path):
    s = assessment_source
    s.run()
    (s.directory / "results/predictions.parquet").write_bytes(b"invalid")
    output = tmp_path / "uncertainty.json"
    with pytest.raises(ValueError):
        uncertainty.analyze_saved_assessment(s.directory, output)
    assert not output.exists()


def test_original_assessment_cannot_be_overwritten(tmp_path, monkeypatch):
    monkeypatch.setattr(
        uncertainty, "verify_assessment", lambda *a: pytest.fail("Unexpected input read")
    )
    with pytest.raises(ValueError, match="outside"):
        uncertainty.analyze_saved_assessment(tmp_path, tmp_path / "results/report.json")


def test_cli_help_and_version_do_not_access_native_data():
    runner = CliRunner()
    assert runner.invoke(uncertainty.app, ["--help"]).exit_code == 0
    assert runner.invoke(uncertainty.app, ["version"]).stdout.strip() == uncertainty.VERSION
