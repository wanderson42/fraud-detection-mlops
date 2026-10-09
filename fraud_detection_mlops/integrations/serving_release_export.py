"""Export the evaluated reference bytes, checking validation parity without fitting."""

import hashlib
from importlib.metadata import version
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated

import mlflow
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from sklearn.ensemble import HistGradientBoostingClassifier
from threadpoolctl import threadpool_limits
import typer

from fraud_detection_mlops.artifacts import sha256
from fraud_detection_mlops.features.feature_schema import (
    FEATURE_ARROW_SCHEMA,
    FEATURE_COLUMNS,
    FEATURE_DTYPES,
    METADATA_DTYPES,
)
from fraud_detection_mlops.integrations import mlflow_tracking as tracking
from fraud_detection_mlops.integrations.skops_persistence import load_pipeline_bytes
from fraud_detection_mlops.modeling.contracts.model_interface import predict_scores
from fraud_detection_mlops.modeling.experiments.final_holdout_evaluation import verify_evaluation
from fraud_detection_mlops.serving.model_release import (
    ServingReleaseError,
    SmokeCase,
    write_release,
)
from fraud_detection_mlops.serving.request_schema import ScoreRequest

app = typer.Typer(no_args_is_help=True)


@app.callback()
def main():
    """Export an evaluated reference for laboratory serving."""


def frozen_file(root: Path, receipt: dict, name: str) -> Path:
    path = (root / name).resolve()
    if not path.is_relative_to(root) or str(path.relative_to(root)) != name:
        raise ServingReleaseError("Frozen artifact path escapes the declared project root")
    if sha256(path) != receipt["bound_files"].get(name):
        raise ServingReleaseError(f"Frozen artifact changed: {name}")
    return path


def payload_from_record(features: dict, metadata: dict) -> dict:
    stamp = pd.Timestamp(metadata["TX_DATETIME"]).isoformat()
    return {
        "schema_version": 1,
        "feature_contract_version": "gold_v1",
        "transaction_id": int(metadata["TRANSACTION_ID"]),
        "customer_id": int(metadata["CUSTOMER_ID"]),
        "terminal_id": int(metadata["TERMINAL_ID"]),
        "tx_datetime": stamp,
        "feature_as_of": stamp,
        "features": features,
    }


def validation_parity(root: Path, receipt: dict, model) -> tuple[SmokeCase, int]:
    """Read only pinned validation partitions and the saved validation scores."""
    gold = (root / receipt["gold_path"]).resolve()
    manifest_name = str((gold / "manifest.json").relative_to(root))
    manifest_path = frozen_file(root, receipt, manifest_name)
    if sha256(manifest_path) != receipt["policy"]["reference"]["gold_manifest_sha256"]:
        raise ServingReleaseError("Frozen Gold identity mismatch")
    manifest = json.loads(manifest_path.read_text())
    partitions = []
    for item in manifest["files"]:
        if item["split"] == "validation":
            path = frozen_file(root, receipt, str((gold / item["path"]).relative_to(root)))
            if sha256(path) != item["sha256"]:
                raise ServingReleaseError("Validation partition identity mismatch")
            if not pq.ParquetFile(path).schema_arrow.equals(
                FEATURE_ARROW_SCHEMA, check_metadata=False
            ):
                raise ServingReleaseError("Validation physical schema changed")
            partitions.append(path)
    if not partitions:
        raise ServingReleaseError("Missing frozen validation partitions")
    names = [
        name
        for name in receipt["bound_files"]
        if name.endswith("/models/hist_gradient_boosting/validation_predictions.parquet")
    ]
    if len(names) != 1:
        raise ServingReleaseError("Expected one pinned reference validation prediction file")
    predictions = pd.read_parquet(frozen_file(root, receipt, names[0]))
    # Preserve physical integer features; the training loader normalizes its matrix to floats.
    frame = (
        pd.concat(
            [
                pd.read_parquet(p, columns=list(METADATA_DTYPES) + FEATURE_COLUMNS)
                for p in partitions
            ],
            ignore_index=True,
        )
        .sort_values(["TX_DATETIME", "TRANSACTION_ID"])
        .reset_index(drop=True)
    )
    features = frame[FEATURE_COLUMNS].astype(FEATURE_DTYPES)
    metadata = frame[list(METADATA_DTYPES)].astype(METADATA_DTYPES)
    if metadata.empty or not metadata.equals(predictions[list(METADATA_DTYPES)]):
        raise ServingReleaseError("Validation IDs, clocks or population changed")
    smoke = None
    for start in range(0, len(features), 512):
        part = features.iloc[start : start + 512].reset_index(drop=True)
        meta = metadata.iloc[start : start + 512].to_dict(orient="records")
        requests = [
            ScoreRequest.model_validate_json(
                json.dumps(payload_from_record(record, event), allow_nan=False, sort_keys=True)
            )
            for record, event in zip(part.to_dict(orient="records"), meta, strict=True)
        ]
        restored = pd.DataFrame(
            [r.features.model_dump() for r in requests], columns=FEATURE_COLUMNS
        ).astype(FEATURE_DTYPES)
        pd.testing.assert_frame_equal(part, restored)
        scores = predict_scores(model, restored)
        expected = predictions.SCORE.iloc[start : start + len(part)].to_numpy()
        if not np.allclose(scores, expected, rtol=1e-12, atol=1e-12):
            raise ServingReleaseError("JSON serving scores differ from saved validation scores")
        if smoke is None:
            smoke = SmokeCase(request=requests[0], expected_score=float(expected[0]))
    return smoke, len(features)


def export_reference(evaluation_path: Path, output: Path, *, project_root: Path) -> dict:
    """Read the completed evaluation and existing MLflow run; never log or refit."""
    root = project_root.resolve()
    if output.exists():
        raise ServingReleaseError(
            "Choose a new release directory; existing releases are immutable"
        )
    checked = verify_evaluation(evaluation_path)
    gate = checked["gate"]
    if (
        gate["passed"] is not True
        or gate["production_promotion"] is not False
        or gate["scope"] != "offline_candidate_for_laboratory_serving"
    ):
        raise ServingReleaseError("The saved evaluation is not eligible for laboratory review")
    receipt_path = evaluation_path / "frozen_candidate.json"
    receipt = json.loads(receipt_path.read_text())
    if (
        receipt["version"] != "freeze_v1"
        or receipt["schema_version"] != 1
        or receipt["refit"] is not False
        or checked["freeze_sha256"] != sha256(receipt_path)
        or receipt["feature_columns"] != FEATURE_COLUMNS
        or receipt["policy"]["reference"]["model_id"] != "hist_gradient_boosting"
        or receipt["policy"]["reference"]["model_uri"] != receipt["model_uri"]
        or receipt["model"]["serialization_format"] != "skops"
    ):
        raise ServingReleaseError("Unsupported frozen reference")
    for package, expected in receipt["environment"].items():
        if version(package) != expected:
            raise ServingReleaseError(f"Restore the frozen package version: {package}")
    store = root / receipt["tracking_root"]
    if not store.resolve().is_relative_to(root) or not (store / "mlflow.db").is_file():
        raise ServingReleaseError("Restore the existing local MLflow store")
    with tracking.local_tracking(store), TemporaryDirectory(prefix="fraud-serving-export-") as tmp:
        run = mlflow.MlflowClient().get_run(receipt["model"]["mlflow_run_id"])
        if run.info.status != "FINISHED":
            raise ServingReleaseError("Frozen model run is not FINISHED")
        path = Path(
            mlflow.artifacts.download_artifacts(artifact_uri=receipt["model_uri"], dst_path=tmp)
        )
        if sha256(path / "MLmodel") != receipt["model"]["mlmodel_sha256"]:
            raise ServingReleaseError("Frozen MLmodel metadata changed")
        metadata = mlflow.models.Model.load(path / "MLmodel")
        flavor = metadata.flavors.get("sklearn", {})
        model_path = (path / flavor.get("pickled_model", "")).resolve()
        if (
            flavor.get("serialization_format") != "skops"
            or model_path.parent != path.resolve()
            or metadata.signature is None
            or metadata.signature.inputs.input_names() != FEATURE_COLUMNS
        ):
            raise ServingReleaseError("Unexpected frozen model format or feature signature")
        data = model_path.read_bytes()
        if hashlib.sha256(data).hexdigest() != receipt["model"]["model_sha256"]:
            raise ServingReleaseError("Frozen model bytes changed")
        model = load_pipeline_bytes(data)
        if (
            len(model.steps) != 1
            or type(model[-1]) is not HistGradientBoostingClassifier
            or any(
                model[-1].get_params().get(k) != v
                for k, v in receipt["model_parameters"]["parameters"].items()
            )
        ):
            raise ServingReleaseError("Frozen model parameters changed")
        with threadpool_limits(limits=4):
            smoke, rows = validation_parity(root, receipt, model)
            return write_release(
                output,
                data,
                smoke,
                model_id="hist_gradient_boosting",
                source={
                    "model_uri": receipt["model_uri"],
                    "freeze_sha256": sha256(receipt_path),
                    "evaluation_manifest_sha256": sha256(evaluation_path / "manifest.json"),
                },
                parity_rows=rows,
                evidence_kind="verified_reference",
            )


@app.command("export")
def export(
    evaluation_path: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
    output: Annotated[Path, typer.Option()],
    project_root: Annotated[Path, typer.Option(exists=True, file_okay=False)],
):
    try:
        result = export_reference(evaluation_path, output, project_root=project_root)
    except Exception as exc:
        typer.echo(f"Serving export failed: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(json.dumps(result, indent=2))


if __name__ == "__main__":
    app()
