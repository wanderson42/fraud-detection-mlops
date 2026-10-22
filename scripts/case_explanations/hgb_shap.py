"""Label-free adapter of the existing validation diagnostic's HGB Tree SHAP method."""

from importlib.metadata import version

import numpy as np
import pandas as pd
from scipy.special import expit
from scripts.product_flow.http_scoring import checked_response
import shap
from sklearn.ensemble import HistGradientBoostingClassifier
from threadpoolctl import threadpool_limits

from fraud_detection_mlops.evaluation.validation_diagnostics import DiagnosticError
from fraud_detection_mlops.features.feature_schema import FEATURE_COLUMNS
from fraud_detection_mlops.serving.request_schema import ScoreRequest


def explain(runtime, cases):
    """Reconstruct recorded scores, then explain the exact stored requests without labels."""
    model = runtime.pipeline
    if len(model.steps) != 1 or type(model[-1]) is not HistGradientBoostingClassifier:
        raise DiagnosticError("Case explanations require the untransformed binary HGB pipeline")
    requests = [ScoreRequest.model_validate(c["request"]) for c in cases]
    for case, request in zip(cases, requests, strict=True):
        checked_response(case["response"], request, runtime.manifest)
    features = pd.concat([request.feature_frame() for request in requests], ignore_index=True)
    scores = np.array([c["response"]["score"] for c in cases], dtype="float64")
    with threadpool_limits(limits=4):
        predicted = np.array([runtime.score(request).score for request in requests])
        if not np.allclose(predicted, scores, rtol=1e-12, atol=1e-12):
            raise DiagnosticError("Stored HTTP scores do not match the pinned release")
        explainer = shap.TreeExplainer(
            model[-1], model_output="raw", feature_perturbation="tree_path_dependent"
        )
        explanations = explainer(features, check_additivity=True)
        values = np.asarray(explanations.values, dtype="float64")
        base = np.broadcast_to(explanations.base_values, (len(cases),)).astype("float64")
        margins = np.asarray(model.decision_function(features), dtype="float64")
    if (
        values.shape != features.shape
        or list(features.columns) != FEATURE_COLUMNS
        or not np.isfinite(values).all()
        or not np.isfinite(base).all()
        or not np.isfinite(margins).all()
        or not np.allclose(base + values.sum(axis=1), margins, rtol=1e-7, atol=1e-7)
        or not np.allclose(expit(margins), scores, rtol=1e-12, atol=1e-12)
    ):
        raise DiagnosticError("Case SHAP additivity or score reconstruction failed")
    return [
        {
            "status": "verified",
            "method": "TreeExplainer",
            "method_version": version("shap"),
            "output": "raw_log_odds",
            "feature_perturbation": "tree_path_dependent",
            "background": "training_path_counts_in_pinned_model_no_external_dataset",
            "feature_order": FEATURE_COLUMNS,
            "base_log_odds": float(base[i]),
            "raw_log_odds": float(margins[i]),
            "reconstructed_score": float(expit(base[i] + values[i].sum())),
            "contributions": {name: float(values[i, j]) for j, name in enumerate(FEATURE_COLUMNS)},
            "max_absolute_score_error": float(abs(predicted[i] - scores[i])),
            "additivity_absolute_error": float(abs(base[i] + values[i].sum() - margins[i])),
        }
        for i in range(len(cases))
    ]
