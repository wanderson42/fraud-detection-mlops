"""Native MLflow bytes and saved evaluation/validation parity, using generated data."""

from importlib.metadata import version
import json
from pathlib import Path

import mlflow
import pandas as pd
import pytest
from sklearn.ensemble import HistGradientBoostingClassifier
from threadpoolctl import threadpool_limits
from typer.testing import CliRunner

from fraud_detection_mlops.artifacts import sha256, write_json
from fraud_detection_mlops.features.feature_schema import FEATURE_COLUMNS, METADATA_DTYPES
from fraud_detection_mlops.integrations import mlflow_tracking as tracking
from fraud_detection_mlops.integrations import serving_release_export as export
from fraud_detection_mlops.modeling.contracts.model_interface import predict_scores
from fraud_detection_mlops.modeling.experiments import candidate_training as training
from fraud_detection_mlops.modeling.experiments import final_holdout_evaluation as evaluation
from fraud_detection_mlops.modeling.experiments.experiment_artifacts import file_records
from fraud_detection_mlops.serving.model_release import ServingReleaseError, load_release


@pytest.fixture
def export_source(baseline_source, monkeypatch):
    root = baseline_source
    gold = next((root / "gold").rglob("manifest.json")).parent
    manifest = json.loads((gold / "manifest.json").read_text())
    train, labels, _ = training.load_split(gold, manifest, "train")
    features, _, metadata = training.load_split(gold, manifest, "validation")
    with threadpool_limits(limits=4):
        model = training.make_model("hist_gradient_boosting").fit(train, labels)
        scores = predict_scores(model, features)
        logged = tracking.log_candidate(
            model,
            features,
            scores,
            name="hist_gradient_boosting",
            parameters={},
            metrics={},
            tags={"baseline_run_id": "synthetic"},
            root=root / "tracking",
        )
    with tracking.local_tracking(root / "tracking"):
        path = Path(
            mlflow.artifacts.download_artifacts(
                artifact_uri=logged["model_uri"], dst_path=str(root / "download")
            )
        )
    native = mlflow.models.Model.load(path / "MLmodel")
    raw = (path / native.flavors["sklearn"]["pickled_model"]).read_bytes()
    predictions = root / "baseline/models/hist_gradient_boosting/validation_predictions.parquet"
    predictions.parent.mkdir(parents=True)
    metadata.assign(SCORE=scores).to_parquet(predictions, index=False)
    bound = [gold / "manifest.json", predictions] + [
        gold / r["path"] for r in manifest["files"] if r["split"] == "validation"
    ]
    policy = json.loads(
        (
            Path(evaluation.PROJECT_ROOT) / "references/final_evaluation_protocol_v1.json"
        ).read_text()
    )
    policy["reference"].update(
        model_uri=logged["model_uri"], gold_manifest_sha256=sha256(gold / "manifest.json")
    )
    policy["holdout"].update(start="2018-05-20", end_exclusive="2018-05-21", expected_rows=4)
    receipt = {
        "schema_version": 1,
        "version": "freeze_v1",
        "refit": False,
        "feature_columns": FEATURE_COLUMNS,
        "policy": policy,
        "gold_path": str(gold.relative_to(root)),
        "tracking_root": "tracking",
        "model_uri": logged["model_uri"],
        "model": {
            "mlflow_run_id": logged["run_id"],
            "serialization_format": "skops",
            "mlmodel_sha256": sha256(path / "MLmodel"),
            "model_sha256": sha256(path / native.flavors["sklearn"]["pickled_model"]),
        },
        "model_parameters": {"parameters": model[-1].get_params()},
        "environment": {
            p: version(p) for p in ("mlflow", "skops", "scikit-learn", "numpy", "pandas")
        },
        "bound_files": {str(p.relative_to(root)): sha256(p) for p in bound},
    }
    directory = root / "evaluation"
    write_json(directory / "frozen_candidate.json", receipt)
    # Already saved synthetic evaluation outputs; the exporter must not score a holdout.
    stamps = pd.to_datetime(
        [
            "2018-05-20T01:00:00",
            "2018-05-20T02:00:00",
            "2018-05-20T03:00:00",
            "2018-05-20T04:00:00",
        ]
    )
    saved = (
        pd.DataFrame(
            {
                "TRANSACTION_ID": [0, 1, 2, 3],
                "CUSTOMER_ID": [1, 1, 2, 3],
                "TERMINAL_ID": [0] * 4,
                "TX_DATETIME": stamps,
                "LABEL_AVAILABLE_AT": stamps + pd.Timedelta(days=7),
                "TX_FRAUD": [0, 1, 1, 0],
            }
        )
        .astype(METADATA_DTYPES)
        .assign(SCORE=[0.1, 0.9, 0.9, 0.1])
    )
    saved.to_parquet(directory / "test_predictions.parquet", index=False)
    comparison, daily = evaluation.measurements(saved)
    report = {
        "schema_version": 1,
        "version": evaluation.EVALUATION_VERSION,
        "freeze_sha256": sha256(directory / "frozen_candidate.json"),
        "model_uri": logged["model_uri"],
        "comparison": comparison,
        "gate": evaluation.acceptance(comparison[evaluation.MODEL_ID], policy),
        "test_evaluated": True,
        "refit": False,
        "formal_superiority_claim": False,
    }
    write_json(directory / "report.json", report)
    daily.to_csv(directory / "daily.csv", index=False)
    (directory / "model_card.md").write_text(evaluation.model_card(receipt, report))
    write_json(
        directory / "manifest.json",
        {
            "schema_version": 1,
            "version": evaluation.EVALUATION_VERSION,
            "identity": {"freeze_sha256": report["freeze_sha256"]},
            "files": file_records(directory, evaluation.OUTPUTS),
        },
    )
    for record in manifest["files"]:
        if record["split"] in ("train", "test"):
            (gold / record["path"]).unlink()
    monkeypatch.setattr(
        HistGradientBoostingClassifier, "fit", lambda *a, **k: pytest.fail("Export fitted a model")
    )
    return root, directory, predictions, raw


def test_export_preserves_native_bytes_and_validation_scores_without_new_runs(export_source):
    root, directory, _, raw = export_source
    client = mlflow.MlflowClient(tracking_uri=tracking.tracking_uri(root / "tracking"))
    experiment = client.get_experiment_by_name(tracking.EXPERIMENT)
    before = {r.info.run_id for r in client.search_runs([experiment.experiment_id])}
    result = export.export_reference(directory, root / "release", project_root=root)
    assert result["parity_rows"] == 160
    assert result["refit"] is False and result["production_promotion"] is False
    assert (root / "release/pipeline.skops").read_bytes() == raw
    assert (
        load_release(root / "release", result["manifest_sha256"]).manifest.evidence_kind
        == "verified_reference"
    )
    assert {r.info.run_id for r in client.search_runs([experiment.experiment_id])} == before
    with pytest.raises(ServingReleaseError, match="new release directory"):
        export.export_reference(directory, root / "release", project_root=root)


def test_validation_drift_is_rejected_before_publication(export_source):
    root, directory, predictions, _ = export_source
    predictions.write_bytes(b"changed")
    with pytest.raises(ServingReleaseError, match="Frozen artifact changed"):
        export.export_reference(directory, root / "release", project_root=root)
    assert not (root / "release").exists()


def test_failed_saved_gate_cannot_export(export_source, monkeypatch):
    root, directory, _, _ = export_source
    original = export.verify_evaluation

    def rejected(path):
        result = original(path)
        result["gate"]["passed"] = False
        return result

    monkeypatch.setattr(export, "verify_evaluation", rejected)
    with pytest.raises(ServingReleaseError, match="not eligible"):
        export.export_reference(directory, root / "release", project_root=root)
    assert not (root / "release").exists()


def test_export_command_help_does_not_touch_data():
    response = CliRunner().invoke(export.app, ["export", "--help"])
    assert response.exit_code == 0, response.output
    assert "--output" in response.stdout and "--project-root" in response.stdout
