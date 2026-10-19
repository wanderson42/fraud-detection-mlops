"""Real HTTP/proxy/model reload using the pinned runtime; PostgreSQL remains a native gate."""

from copy import deepcopy
import os
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("FRAUD_TRACKING_RUNTIME_TEST") != "1",
    reason="Explicit pinned tracking runtime required",
)


@pytest.fixture(scope="module")
def restore_control(model_server):
    import mlflow
    from mlflow.models import infer_signature
    import numpy as np
    import pandas as pd
    from scripts.tracking.capture import sha
    from sklearn.naive_bayes import GaussianNB
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    from fraud_detection_mlops.features.feature_schema import FEATURE_COLUMNS
    from fraud_detection_mlops.integrations.skops_persistence import TRUSTED_TYPES

    uri, workspace, _ = model_server
    features = pd.DataFrame(np.zeros((20, len(FEATURE_COLUMNS))), columns=FEATURE_COLUMNS)
    features["TX_AMOUNT"] = np.arange(20, dtype=float)
    model = Pipeline([("scale", StandardScaler()), ("classifier", GaussianNB())]).fit(
        features, np.arange(20) % 2
    )
    scores = model.predict_proba(features)
    previous = mlflow.get_tracking_uri()
    mlflow.set_tracking_uri(uri)
    try:
        with mlflow.start_run() as run:
            logged = mlflow.sklearn.log_model(
                model,
                name="model",
                serialization_format="skops",
                skops_trusted_types=sorted(TRUSTED_TYPES),
                pyfunc_predict_fn="predict_proba",
                signature=infer_signature(features, scores),
            )
        with TemporaryDirectory(dir=workspace) as temporary:
            path = Path(
                mlflow.artifacts.download_artifacts(
                    artifact_uri=logged.model_uri, dst_path=temporary
                )
            )
            model_sha = sha(path / "model.skops")
    finally:
        mlflow.set_tracking_uri(previous)
    return uri, {
        "model_uri": logged.model_uri,
        "run_id": run.info.run_id,
        "model_sha256": model_sha,
        "columns": list(FEATURE_COLUMNS),
        "rows": features.to_numpy().tolist(),
        "scores": scores[:, 1].tolist(),
    }


@pytest.mark.parametrize("bad", [None, "hash", "score", "run", "columns"])
def test_restored_model_client_uses_http_and_external_controls(
    restore_control, tmp_path, monkeypatch, bad
):
    import mlflow
    from mlflow import MlflowClient
    from scripts.tracking import restore_worker as worker

    uri, original = restore_control
    control = deepcopy(original)
    if bad == "hash":
        control["model_sha256"] = "0" * 64
    if bad == "score":
        control["scores"][0] += 0.1
    if bad == "run":
        control["run_id"] = "0" * 32
    if bad == "columns":
        control["columns"].reverse()
    for name in list(os.environ):
        if name.startswith(("AWS_", "TARGET_PG", "POSTGRES_", "TRACKING_PG")):
            monkeypatch.delenv(name)
    monkeypatch.setenv("MLFLOW_ENABLE_PROXY_MULTIPART_DOWNLOAD", "false")
    monkeypatch.setenv("MLFLOW_ENABLE_PROXY_MULTIPART_UPLOAD", "false")
    previous = mlflow.get_tracking_uri()
    mlflow.set_tracking_uri(uri)
    try:
        if bad:
            with pytest.raises(ValueError):
                worker.reload_model(MlflowClient(tracking_uri=uri), control, tmp_path)
        else:
            result = worker.reload_model(MlflowClient(tracking_uri=uri), control, tmp_path)
            assert result["model_reload_validated"] and result["max_absolute_score_error"] == 0
    finally:
        mlflow.set_tracking_uri(previous)
