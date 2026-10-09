"""Conformance checks for model contributions and their infrastructure boundary."""

import json
from pathlib import Path

from examples.model_contribution import MODEL, run, synthetic_batches
import mlflow
import numpy as np
import pandas as pd
import pytest
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import LinearSVC

from fraud_detection_mlops.features import FEATURE_COLUMNS
from fraud_detection_mlops.modeling import baseline, persistence, tracking, train
from fraud_detection_mlops.modeling.interface import (
    VERSION,
    ModelContractError,
    ModelSpec,
    build_model,
    fit_and_score,
    predict_scores,
)
from fraud_detection_mlops.modeling.models import MODEL_CATALOG


@pytest.mark.parametrize("spec", list(MODEL_CATALOG.values()) + [MODEL], ids=lambda s: s.model_id)
def test_contribution_preserves_order_and_survives_reload(spec, tmp_path):
    (X_train, y_train, _), (X_val, _, _) = synthetic_batches()
    model, scores, _ = fit_and_score(build_model(spec), X_train, y_train, X_val)
    reversed_scores = predict_scores(model, X_val.iloc[::-1])
    np.testing.assert_allclose(reversed_scores, scores[::-1], rtol=1e-12, atol=1e-12)
    persistence.save_pipeline(model, tmp_path / "model.skops", X_val, scores)
    restored = persistence.load_pipeline(tmp_path / "model.skops")
    np.testing.assert_allclose(predict_scores(restored, X_val), scores, rtol=1e-12, atol=1e-12)
    assert model.classes_.tolist() == [0, 1]
    if "scale" in model.named_steps:
        assert model.named_steps["scale"].n_samples_seen_ == len(X_train)
        np.testing.assert_allclose(model.named_steps["scale"].mean_, X_train.mean())


def test_factory_isolated_instances_overrides_and_seed():
    spec = MODEL_CATALOG["hist_gradient_boosting"]
    first = build_model(spec, parameters={"classifier__max_leaf_nodes": 9}, random_state=7)
    second = build_model(spec)
    assert first[-1].max_leaf_nodes == 9 and first[-1].random_state == 7
    assert second[-1].max_leaf_nodes == 15 and second[-1].random_state == 42
    first.fit(*synthetic_batches()[0][:2])
    assert not hasattr(second[-1], "classes_")


def test_existing_factory_predictions_match_the_fixed_baseline_policy():
    policy = json.loads(baseline.DEFAULT_CONFIG.read_text())
    classifiers = {
        "DummyClassifier": DummyClassifier,
        "LogisticRegression": LogisticRegression,
        "HistGradientBoostingClassifier": HistGradientBoostingClassifier,
    }
    (X, y, _), (evaluation, _, _) = synthetic_batches()
    for name, definition in policy["models"].items():
        steps = [("scale", StandardScaler())] if definition["preprocessor"] else []
        steps.append(
            ("classifier", classifiers[definition["classifier"]](**definition["parameters"]))
        )
        original = Pipeline(steps).fit(X, y)
        current = build_model(MODEL_CATALOG[name]).fit(X, y)
        np.testing.assert_allclose(
            current.predict_proba(evaluation),
            original.predict_proba(evaluation),
            rtol=1e-12,
            atol=1e-12,
        )


@pytest.mark.parametrize(
    "parameters",
    [
        {"classifier__unknown": 3},
        {"classifier__random_state": 8},
        {"classifier": StandardScaler()},
        {"classifier__max_depth": np.inf},
    ],
)
def test_invalid_search_configuration_is_rejected(parameters):
    with pytest.raises(ModelContractError):
        build_model(MODEL, parameters=parameters)


def test_fitted_factory_and_classifier_without_scores_are_rejected():
    X_train, y_train, _ = synthetic_batches()[0]
    fitted = MODEL.factory().fit(X_train, y_train)
    with pytest.raises(ModelContractError, match="unfitted"):
        build_model(ModelSpec("leaky_factory", lambda: fitted))
    partial = MODEL.factory()
    partial["scale"].fit(X_train)
    with pytest.raises(ModelContractError, match="unfitted"):
        build_model(ModelSpec("leaky_preprocessor", lambda: partial))
    with pytest.raises(ModelContractError, match="predict_proba"):
        build_model(ModelSpec("no_scores", lambda: Pipeline([("classifier", LinearSVC())])))


@pytest.mark.parametrize("fault", ["label_order", "feature_order", "nonfinite", "metadata"])
def test_invalid_inputs_rejected_before_fit(fault, monkeypatch):
    (X_train, y_train, _), (X_val, y_val, metadata) = synthetic_batches()
    if fault == "label_order":
        y_train = y_train.iloc[::-1]
    elif fault == "feature_order":
        X_val = X_val[FEATURE_COLUMNS[::-1]]
    elif fault == "nonfinite":
        X_train.iloc[0, 0] = np.nan
    else:
        metadata = metadata.iloc[::-1]
    monkeypatch.setattr(Pipeline, "fit", lambda *a, **k: pytest.fail("Invalid input was fitted"))
    with pytest.raises(ModelContractError):
        train.fit_candidate(
            MODEL.model_id, X_train, y_train, X_val, y_val, metadata, model_spec=MODEL
        )


@pytest.mark.parametrize("fault", ["row_count", "range", "nan", "row_sum", "classes"])
def test_bad_scores_rejected_before_publication(fault, monkeypatch, tmp_path):
    X, y, _ = synthetic_batches()[0]
    model = build_model(MODEL).fit(X, y)
    probabilities = model.predict_proba(X)
    if fault == "row_count":
        probabilities = probabilities[:-1]
    elif fault == "range":
        probabilities[0] = [-0.1, 1.1]
    elif fault == "nan":
        probabilities[0, 1] = np.nan
    elif fault == "row_sum":
        probabilities[0] = [0.2, 0.3]
    else:
        model[-1].classes_ = np.array([1, 0])
    monkeypatch.setattr(model, "predict_proba", lambda features: probabilities)
    with pytest.raises(ModelContractError):
        tracking.log_candidate(
            model,
            X,
            np.zeros(len(X)),
            name=MODEL.model_id,
            parameters={},
            metrics={},
            tags={"baseline_run_id": "invalid-output"},
            root=tmp_path / "tracking",
        )
    assert not (tmp_path / "tracking").exists()


def test_external_contribution_uses_existing_artifacts_and_native_mlflow(tmp_path):
    result = run(tmp_path / "example", tmp_path / "tracking")
    assert result["scope"] == "synthetic_integration_smoke"
    assert result["promotion_status"] == "not_promoted"
    assert result["training_rows"] == 80 and result["evaluation_rows"] == 20
    assert MODEL.model_id not in baseline.CLASSIFIERS
    assert set(baseline.load_configuration(baseline.DEFAULT_CONFIG)["models"]) == {
        "dummy_prior",
        "logistic_regression",
        "hist_gradient_boosting",
    }
    _, (features, _, _) = synthetic_batches()
    saved = persistence.load_pipeline(tmp_path / "example/model.skops")
    predictions = pd.read_parquet(tmp_path / "example/validation_predictions.parquet")
    np.testing.assert_allclose(predict_scores(saved, features), predictions.SCORE)
    with tracking.local_tracking(tmp_path / "tracking"):
        recorded = mlflow.MlflowClient().get_run(result["tracking"]["run_id"])
        assert recorded.info.status == "FINISHED"
        assert recorded.data.tags["model_interface_version"] == VERSION
        assert recorded.data.tags["promotion_status"] == "not_promoted"
        restored, _ = tracking.download_pipeline(
            result["tracking"]["model_uri"], tmp_path / "load"
        )
        np.testing.assert_allclose(predict_scores(restored, features), predictions.SCORE)
        runs = mlflow.MlflowClient().search_runs([recorded.info.experiment_id])
        assert len(runs) == 1
    assert Path(result["output"]) == tmp_path / "example"
