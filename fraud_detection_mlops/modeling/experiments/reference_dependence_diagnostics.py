"""Inspect a verified saved assessment; read no Silver, tracking or replay."""

from pathlib import Path
from typing import Annotated

import pandas as pd
import typer

from fraud_detection_mlops.artifacts import sha256, write_json
from fraud_detection_mlops.evaluation.temporal_dependence_diagnostics import (
    VERSION,
    diagnose_dependence,
)
from fraud_detection_mlops.modeling.experiments.reference_assessment_execution import (
    json_record,
    verify_assessment,
)

app = typer.Typer(no_args_is_help=True)


def inspect_assessment(directory: Path, output: Path | None = None) -> dict:
    directory = Path(directory).resolve()
    destination = (
        Path(output).resolve()
        if output is not None
        else directory.parent / VERSION / "report.json"
    )
    if destination == directory or directory in destination.parents:
        raise ValueError("Diagnostics output must be outside the immutable assessment directory")
    report = verify_assessment(directory)
    results = directory / "results"
    path = results / "predictions.parquet"
    manifest = json_record(results, "manifest.json")
    expected_hash = next(r["sha256"] for r in manifest["files"] if r["path"] == path.name)
    predictions = pd.read_parquet(path)
    if sha256(path) != expected_hash:
        raise ValueError("Saved predictions changed during diagnostics")
    diagnosis = diagnose_dependence(predictions, json_record(results, "statistical_protocol.json"))
    diagnosis.update(
        assessment_id=report["assessment_id"],
        assessment_git_revision=report["git_revision"],
        source_commit=report["source_commit"],
        model_sha256=report["model_sha256"],
        predictions_sha256=expected_hash,
        status="success",
    )
    write_json(destination, diagnosis)
    return {
        "version": VERSION,
        "status": "success",
        "assessment_id": report["assessment_id"],
        "days": diagnosis["days"],
        "rows": diagnosis["rows"],
        "report_path": str(destination),
        "generalization_confidence_interval": None,
        "formal_temporal_hypothesis_test": False,
        "resampling_method_selected": None,
    }


@app.command("inspect")
def inspect_command(
    assessment_path: Path,
    output: Annotated[Path | None, typer.Option(help="JSON path outside the assessment")] = None,
):
    """Save descriptive diagnostics after validating the original assessment bundle."""
    import json

    typer.echo(json.dumps(inspect_assessment(assessment_path, output), indent=2, allow_nan=False))


@app.command("version")
def version_command():
    """Show diagnostics version without reading any artifacts."""
    typer.echo(VERSION)


if __name__ == "__main__":
    app()
