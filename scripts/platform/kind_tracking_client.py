"""Synthetic MLflow proxy client; credentials and model state never cross processes."""

from contextlib import redirect_stdout
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory
from uuid import uuid4


def require(ok, message):
    if not ok:
        raise ValueError(message)


def batch():
    import numpy as np
    import pandas as pd

    x = np.arange(40, dtype="float64")
    frame = pd.DataFrame({"TX_AMOUNT": x + 1.0, "TX_HOUR": x % 24})
    return frame, (x % 5 == 0).astype("int64")


def execute(action, uri, record=None):
    import mlflow
    from mlflow import MlflowClient
    import numpy as np

    from fraud_detection_mlops.integrations import mlflow_tracking as tracking

    require(not any(k.startswith("AWS_") for k in os.environ), "Client must receive no AWS env")
    require(
        not any(k in os.environ for k in ("PGPASSWORD", "MLFLOW_BACKEND_STORE_URI")),
        "Client must receive no database credentials",
    )
    require(bool(re.fullmatch(r"http://127\.0\.0\.1:[0-9]+", uri)), "Loopback proxy required")
    frame, labels = batch()
    with tracking.http_tracking(uri):
        client = MlflowClient()
        if action == "write":
            from sklearn.naive_bayes import GaussianNB
            from sklearn.pipeline import Pipeline
            from sklearn.preprocessing import StandardScaler

            identity = uuid4().hex
            model = Pipeline([("scale", StandardScaler()), ("classifier", GaussianNB())])
            model.fit(frame, labels)
            scores = model.predict_proba(frame)[:, 1]
            record = tracking.log_candidate(
                model,
                frame,
                scores,
                name="kind_gaussian_nb_smoke",
                parameters={"rows": len(frame)},
                metrics={"smoke": 1.0},
                tags={
                    "experiment_run_id": identity,
                    "scope": "synthetic_kind_tracking_not_historical_training",
                },
                http_uri=uri,
                experiment_name="fraud-kind-smoke-" + identity,
            )
            client.log_metric(record["run_id"], "history_probe", 0.25, step=0)
            client.log_metric(record["run_id"], "history_probe", 0.75, step=1)
            record.update(expected_scores=scores.tolist(), schema="fraud_kind_model_probe_v1")
        else:
            require(
                action == "read"
                and record
                and record.get("schema") == "fraud_kind_model_probe_v1",
                "Invalid reload record",
            )
        require(bool(re.fullmatch(r"[a-f0-9]{32}", record["run_id"])), "Invalid run ID")
        require(
            bool(re.fullmatch(r"models:/m-[a-f0-9]+", record["model_uri"])), "Invalid model ID"
        )
        run = client.get_run(record["run_id"])
        require(run.info.status == "FINISHED", "Run is not finished")
        require(run.info.artifact_uri == record["run_artifact_uri"], "Run artifact URI differs")
        tracking.require_proxy_artifacts(run.info.artifact_uri)
        logged = client.get_logged_model(record["model_uri"].removeprefix("models:/"))
        require(logged.source_run_id == record["run_id"], "Model lineage differs")
        require(logged.artifact_location == record["model_artifact_uri"], "Model URI differs")
        history = sorted(
            client.get_metric_history(record["run_id"], "history_probe"), key=lambda m: m.step
        )
        require(
            [(m.step, m.value) for m in history] == [(0, 0.25), (1, 0.75)],
            "Metric history differs",
        )
        require(run.data.params.get("rows") == "40", "Run parameter differs")
        with TemporaryDirectory(prefix="model-", dir=os.environ["TMPDIR"]) as temporary:
            _, model_path = tracking.download_pipeline(
                record["model_uri"], Path(temporary), feature_columns=list(frame.columns)
            )
            fingerprint = hashlib.sha256((model_path / "model.skops").read_bytes()).hexdigest()
            if action == "read":
                require(fingerprint == record["model_sha256"], "Downloaded model hash differs")
            restored = mlflow.pyfunc.load_model(str(model_path))
            probabilities = np.asarray(restored.predict(frame))
            expected = np.asarray(record["expected_scores"])
            require(
                probabilities.shape == (40, 2) and np.isfinite(probabilities).all(),
                "Invalid prediction shape or values",
            )
            error = float(np.max(np.abs(probabilities[:, 1] - expected)))
            require(error <= 1e-12, "Prediction parity differs")
        return {
            **record,
            "model_sha256": fingerprint,
            "max_absolute_score_error": error,
            "metric_history_rows": len(history),
            "status": "success",
            "fit_executed": action == "write",
        }


if __name__ == "__main__":
    try:
        request = json.load(sys.stdin)
        with redirect_stdout(sys.stderr):
            result = execute(request["action"], request["uri"], request.get("record"))
        print(json.dumps(result), flush=True)
    except Exception as exc:  # noqa: BLE001 — subprocess JSON boundary, no credential traceback
        reason = re.sub(r"(https?://)[^/@\s]+:[^/@\s]+@", r"\1[redacted]@", str(exc))[-1000:]
        print(
            json.dumps({"status": "failed", "error_type": type(exc).__name__, "reason": reason}),
            flush=True,
        )
        raise SystemExit(1) from None
