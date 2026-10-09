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
    reference_dependence_diagnostics as diagnostics,
)

__all__ = ["assessment_source", "synthetic_reference"]


def test_saved_diagnostics_work_without_sources_or_model_and_preserve_assessment(
    assessment_source,
):
    s = assessment_source
    s.run()
    hashes = {
        str(p.relative_to(s.directory)): sha256(p) for p in s.directory.rglob("*") if p.is_file()
    }
    calls = s.calls.copy()
    shutil.rmtree(s.silver_root)
    result = diagnostics.inspect_assessment(s.directory)
    assert result["days"] == 14 and result["rows"] == 84
    assert s.calls == calls
    report = json.loads(Path(result["report_path"]).read_text())
    assert report["predictions_sha256"] == hashes["results/predictions.parquet"]
    assert report["entity_recurrence"]["customers"]["unique_entities"] == 5
    assert report["entity_recurrence"]["customers"]["entities_seen_on_multiple_days"] == 5
    assert (
        report["daily_metric_autocorrelation"]["average_precision"]["undefined_reason"]
        == "undefined_daily_values"
    )
    assert {
        str(p.relative_to(s.directory)): sha256(p) for p in s.directory.rglob("*") if p.is_file()
    } == hashes


def test_corrupted_saved_predictions_block_diagnostics_and_output(assessment_source, tmp_path):
    s = assessment_source
    s.run()
    (s.directory / "results/predictions.parquet").write_bytes(b"corrupt")
    output = tmp_path / "diagnostics.json"
    with pytest.raises(ValueError):
        diagnostics.inspect_assessment(s.directory, output)
    assert not output.exists()


def test_output_inside_assessment_is_rejected_before_any_read(tmp_path, monkeypatch):
    monkeypatch.setattr(
        diagnostics, "verify_assessment", lambda *a: pytest.fail("Unexpected read")
    )
    with pytest.raises(ValueError, match="outside"):
        diagnostics.inspect_assessment(tmp_path, tmp_path / "results/report.json")


def test_cli_entry_points():
    runner = CliRunner()
    assert runner.invoke(diagnostics.app, ["--help"]).exit_code == 0
    result = runner.invoke(diagnostics.app, ["version"])
    assert result.exit_code == 0 and result.stdout.strip() == diagnostics.VERSION
