"""Quantify resampling sensitivity of verified saved predictions, without model access."""

from importlib.metadata import version
import json
from pathlib import Path
import platform
from typing import Annotated

import pandas as pd
from threadpoolctl import threadpool_limits
import typer

from fraud_detection_mlops.artifacts import sha256, write_json
from fraud_detection_mlops.evaluation.temporal_block_resampling import (
    VERSION,
    analyze_block_sensitivity,
)
from fraud_detection_mlops.modeling.experiments.reference_assessment_execution import (
    json_record,
    verify_assessment,
)

app = typer.Typer(no_args_is_help=True)


def analyze_saved_assessment(directory: Path, output: Path | None = None) -> dict:
    directory = Path(directory).resolve()
    destination = Path(output).resolve() if output else directory.parent / VERSION / "report.json"
    if destination == directory or directory in destination.parents:
        raise ValueError("Uncertainty output must be outside the immutable assessment directory")
    original = verify_assessment(directory)
    results = directory / "results"
    prediction_path = results / "predictions.parquet"
    manifest = json_record(results, "manifest.json")
    expected = next(r["sha256"] for r in manifest["files"] if r["path"] == prediction_path.name)
    predictions = pd.read_parquet(prediction_path)
    with threadpool_limits(limits=4):
        report = analyze_block_sensitivity(
            predictions, json_record(results, "statistical_protocol.json")
        )
    if sha256(prediction_path) != expected:
        raise ValueError("Saved predictions changed during uncertainty analysis")
    report.update(
        assessment_id=original["assessment_id"],
        assessment_git_revision=original["git_revision"],
        source_commit=original["source_commit"],
        model_sha256=original["model_sha256"],
        predictions_sha256=expected,
        analysis_implementation_sha256={
            "evaluation/temporal_block_resampling.py": sha256(
                Path(analyze_block_sensitivity.__code__.co_filename)
            ),
            "modeling/experiments/reference_uncertainty_analysis.py": sha256(Path(__file__)),
        },
        analysis_environment={
            "python": platform.python_version(),
            "numpy": version("numpy"),
            "scikit-learn": version("scikit-learn"),
        },
        status="success",
    )
    write_json(destination, report)
    return {
        "version": VERSION,
        "status": "success",
        "assessment_id": original["assessment_id"],
        "report_path": str(destination),
        "method": report["method"],
        "configurations": report["configurations"],
        "coverage_validated": False,
        "generalization_confidence_interval": None,
        "formal_temporal_hypothesis_test": False,
    }


@app.command("analyze")
def analyze_command(
    assessment_path: Path,
    output: Annotated[
        Path | None, typer.Option(help="JSON outside the original assessment")
    ] = None,
):
    """Save all reviewed block lengths with explicit exploratory interpretation."""
    typer.echo(
        json.dumps(analyze_saved_assessment(assessment_path, output), indent=2, allow_nan=False)
    )


@app.command("version")
def version_command():
    typer.echo(VERSION)


if __name__ == "__main__":
    app()
