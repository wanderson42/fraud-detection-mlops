import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import joblib
import mlflow
import mlflow.sklearn
import numpy as np
import pandas as pd
import pytest
from scipy.special import expit
from sklearn.pipeline import Pipeline
from typer.testing import CliRunner

from fraud_detection_mlops.artifacts import sha256, write_json
from fraud_detection_mlops.features import FEATURE_COLUMNS
from fraud_detection_mlops.modeling import diagnostics, persistence, tracking, train


@pytest.fixture(scope="module")
def diagnostic_source(prepared_baseline, tmp_path_factory):
    root = tmp_path_factory.mktemp("diagnostic-source")
    shutil.copytree(prepared_baseline / "gold", root / "gold")
    for name in ("inventory.json", "protocol.json"):
        shutil.copyfile(prepared_baseline / name, root / name)
    result = train.run_baseline(
        root / "gold",
        root / "experiments",
        inventory_path=root / "inventory.json",
        protocol_path=root / "protocol.json",
        tracking_root=root / "tracking",
    )
    return root, result


def execute(root, result, **kwargs):
    return diagnostics.run_diagnostics(
        Path(result["baseline_path"]),
        result["tracking"]["hist_gradient_boosting"]["model_uri"],
        gold_root=root / "gold",
        tracking_root=root / "tracking",
        output_root=root / "diagnostics",
        inventory_path=root / "inventory.json",
        protocol_path=root / "protocol.json",
        repeats=2,
        sample_size=20,
        **kwargs,
    )


def test_daily_errors_use_customer_aggregation_ties_and_no_threshold():
    frame = pd.DataFrame(
        {
            "TRANSACTION_ID": np.arange(107),
            "CUSTOMER_ID": [*range(101), 0, *range(5)],
            "TX_DATETIME": pd.to_datetime(["2018-05-06"] * 102 + ["2018-05-07"] * 5),
            "TX_FRAUD": [0] * 100 + [1, 1] + [0] * 5,
            "SCORE": [0.5] * 107,
        }
    )
    measured = diagnostics.daily_diagnostics(frame)
    first = measured.iloc[0]
    assert first.customers == 101
    assert first.fraudulent_customers == 2  # Includes customer 0's second transaction.
    assert first.fraudulent_customers_in_alerts == 1
    assert first.genuine_customers_in_alerts == 99
    assert first.missed_fraudulent_customers == 1  # Customer 100 loses the score tie.
    assert first.customer_recall_at_100 == 0.5
    assert first.precision_at_100 == 0.01
    assert pd.isna(measured.iloc[1].customer_recall_at_100)
    assert pd.isna(measured.iloc[1].roc_auc)


def test_shap_reconstructs_scores_and_illustrative_cases_do_not_bias_global_mean(
    diagnostic_source,
):
    root, result = diagnostic_source
    directory = Path(result["baseline_path"])
    manifest = json.loads((directory / "manifest.json").read_text())
    X, meta, scores, _, _ = diagnostics.load_validation(directory, manifest, root / "gold")
    model = persistence.load_pipeline(directory / "models/hist_gradient_boosting/model.skops")
    from threadpoolctl import threadpool_limits

    with threadpool_limits(limits=4):
        summary, local, _ = diagnostics.explain_sample(model, X, meta, scores, sample_size=20)
        again, repeat, _ = diagnostics.explain_sample(model, X, meta, scores, sample_size=20)
    pd.testing.assert_frame_equal(summary, again)
    pd.testing.assert_frame_equal(local, repeat)
    assert local.UNIFORM_SAMPLE.sum() == 20
    assert 20 <= len(local) <= 23
    assert "lowest_scored_fraud" in ";".join(local.CASE)
    contributions = local[["SHAP_" + c for c in FEATURE_COLUMNS]].to_numpy()
    assert np.allclose(expit(local.BASE_LOG_ODDS + contributions.sum(1)), local.SCORE)
    expected = np.abs(contributions[local.UNIFORM_SAMPLE]).mean(0)
    assert np.allclose(summary.set_index("feature").loc[FEATURE_COLUMNS].iloc[:, 0], expected)


@pytest.mark.parametrize("historical_export", [False, True])
def test_existing_model_is_inspected_without_fit_or_train_test_reads(
    diagnostic_source, monkeypatch, tmp_path, historical_export
):
    root, result = diagnostic_source
    result = {**result, "tracking": dict(result["tracking"])}
    directory = Path(result["baseline_path"])
    if historical_export:
        directory = tmp_path / "historical"
        shutil.copytree(result["baseline_path"], directory)
        manifest = json.loads((directory / "manifest.json").read_text())
        for name in train.CLASSIFIERS:
            part = directory / "models" / name
            model = persistence.load_pipeline(part / "model.skops")
            joblib.dump(model, part / "model.joblib")
            (part / "model.skops").unlink()
        manifest["version"] = "baseline_v1"
        manifest.pop("model_format")
        manifest.pop("tracking")
        manifest["files"] = [
            {
                "path": name,
                "size_bytes": (directory / name).stat().st_size,
                "sha256": sha256(directory / name),
            }
            for name in train.baseline.expected_paths("baseline_v1")
        ]
        write_json(directory / "manifest.json", manifest)
        # Reproduce the old save_model + log_artifacts structure. This is a fixture,
        # not a migration implementation, and MLmodel has no embedded run_id.
        native = persistence.load_pipeline(
            Path(result["baseline_path"]) / "models/hist_gradient_boosting/model.skops"
        )
        exported = tmp_path / "exported"
        mlflow.sklearn.save_model(
            native,
            str(exported),
            serialization_format="skops",
            skops_trusted_types=sorted(persistence.TRUSTED_TYPES),
            pyfunc_predict_fn="predict_proba",
            pip_requirements=[],
            signature=mlflow.models.infer_signature(
                pd.DataFrame(np.zeros((2, len(FEATURE_COLUMNS))), columns=FEATURE_COLUMNS),
                np.zeros((2, 2)),
            ),
        )
        assert mlflow.models.Model.load(exported / "MLmodel").run_id is None
        client = mlflow.MlflowClient(tracking_uri=tracking.tracking_uri(root / "tracking"))
        experiment = client.get_experiment_by_name(tracking.EXPERIMENT)
        run = client.create_run(
            experiment.experiment_id,
            tags={
                "baseline_run_id": manifest["run_id"],
                "model_id": "hist_gradient_boosting",
                "baseline_manifest_sha256": sha256(directory / "manifest.json"),
                "source_commit": manifest["source"]["commit"],
                "gold_manifest_sha256": manifest["gold_manifest_sha256"],
            },
        )
        client.log_artifacts(run.info.run_id, str(exported), "model")
        client.set_terminated(run.info.run_id)
        result["baseline_path"] = str(directory)
        result["tracking"]["hist_gradient_boosting"] = {
            "model_uri": f"runs:/{run.info.run_id}/model"
        }
    before = {p.relative_to(directory): sha256(p) for p in directory.rglob("*") if p.is_file()}
    monkeypatch.setattr(Pipeline, "fit", lambda *a, **kw: pytest.fail("model was refitted"))
    monkeypatch.setattr(
        joblib, "load", lambda *a, **kw: pytest.fail("historical joblib was loaded")
    )
    client = mlflow.MlflowClient(tracking_uri=tracking.tracking_uri(root / "tracking"))
    experiment = client.get_experiment_by_name(tracking.EXPERIMENT)
    runs_before = len(client.search_runs([experiment.experiment_id]))
    gold_dir = root / "gold" / ("b" * 40) / "gold_v1"
    gold_manifest = json.loads((gold_dir / "manifest.json").read_text())
    hidden = []
    for item in gold_manifest["files"]:
        if item["split"] != "validation":
            path = gold_dir / item["path"]
            target = path.with_suffix(".not-available")
            path.rename(target)
            hidden.append((path, target))
    try:
        checked = execute(root, result)
    finally:
        for path, target in hidden:
            target.rename(path)
    output = Path(checked["diagnostics_path"])
    assert diagnostics.verify_diagnostics(output)["verified_outputs"] == 7
    report = json.loads((output / "report.json").read_text())
    assert report["uniform_shap_rows"] == 20
    assert all(
        report[k] is False
        for k in ("test_evaluated", "refit", "feature_selection", "hypothesis_test")
    )
    assert set(pd.read_csv(output / "daily.csv").model_id) == set(train.CLASSIFIERS)
    assert len(pd.read_csv(output / "permutation.csv")) == 19
    assert len(client.search_runs([experiment.experiment_id])) == runs_before
    assert before == {
        p.relative_to(directory): sha256(p) for p in directory.rglob("*") if p.is_file()
    }
    assert (output / "importance.png").stat().st_size > 1000
    assert (output / "case_lowest_scored_fraud.png").stat().st_size > 1000
    response = CliRunner().invoke(diagnostics.app, ["verify", str(output)])
    assert response.exit_code == 0, response.output
    (output / "daily.csv").write_text("corrupted\n")
    with pytest.raises(diagnostics.DiagnosticError, match="integrity"):
        diagnostics.verify_diagnostics(output)


def test_wrong_candidate_is_rejected_before_permutation_and_leaves_failed_audit(
    diagnostic_source, monkeypatch
):
    root, result = diagnostic_source
    wrong = {
        **result,
        "tracking": {"hist_gradient_boosting": result["tracking"]["logistic_regression"]},
    }
    monkeypatch.setattr(
        diagnostics,
        "permutation_importance",
        lambda *a, **kw: pytest.fail("wrong model inspected"),
    )
    with pytest.raises(diagnostics.DiagnosticError, match="completed baseline candidate"):
        execute(root, wrong)
    parent = root / "diagnostics" / ("b" * 40)
    audits = [json.loads(p.read_text()) for p in (parent / "runs").glob("*.json")]
    assert any(a["status"] == "failed" for a in audits)
    assert not list(parent.glob(".diagnostics-*"))


def test_score_mismatch_stops_before_importance_computation(diagnostic_source, monkeypatch):
    root, result = diagnostic_source

    def mismatch(*args):
        raise persistence.PersistenceError("changed validation scores")

    monkeypatch.setattr(persistence, "check_scores", mismatch)
    monkeypatch.setattr(
        diagnostics,
        "permutation_importance",
        lambda *a, **kw: pytest.fail("scores not reconciled"),
    )
    with pytest.raises(persistence.PersistenceError, match="changed validation"):
        execute(root, result)


def test_nonadditive_explanation_is_rejected(diagnostic_source, monkeypatch):
    root, result = diagnostic_source
    directory = Path(result["baseline_path"])
    manifest = json.loads((directory / "manifest.json").read_text())
    X, meta, scores, _, _ = diagnostics.load_validation(directory, manifest, root / "gold")
    model = persistence.load_pipeline(directory / "models/hist_gradient_boosting/model.skops")
    monkeypatch.setattr(
        diagnostics.shap,
        "TreeExplainer",
        lambda *a, **kw: (
            lambda sample, **kw2: SimpleNamespace(
                values=np.full(sample.shape, np.nan),
                base_values=np.zeros(len(sample)),
            )
        ),
    )
    with pytest.raises(diagnostics.DiagnosticError, match="SHAP additivity"):
        diagnostics.explain_sample(model, X, meta, scores, sample_size=20)
