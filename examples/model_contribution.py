"""Synthetic integration smoke check; this model is not an approved candidate."""

import argparse
from contextlib import redirect_stdout
import json
from pathlib import Path
import sys
from uuid import uuid4

from mlflow.exceptions import MlflowException
import numpy as np
import pandas as pd
from sklearn.naive_bayes import GaussianNB
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits

from fraud_detection_mlops.artifacts import sha256
from fraud_detection_mlops.features.feature_schema import FEATURE_COLUMNS, LABEL_DELAY_DAYS
from fraud_detection_mlops.integrations import mlflow_tracking as tracking
from fraud_detection_mlops.modeling.contracts.model_interface import VERSION, ModelSpec
from fraud_detection_mlops.modeling.experiments import (
    candidate_training,
)
from fraud_detection_mlops.storage.managed_paths import managed_destination


def gaussian_nb() -> Pipeline:
    """A contributor writes only a complete, unfitted estimator factory."""
    return Pipeline([("scale", StandardScaler()), ("classifier", GaussianNB(var_smoothing=1e-9))])


HTTP_EXPERIMENT = "fraud-model-interface-smoke-http-v1"

MODEL = ModelSpec("gaussian_nb_example", gaussian_nb)


def synthetic_batches():
    """Small in-memory batches; no access to Handbook data or consumed holdouts."""
    batches = []
    for offset, rows, day in ((0, 80, "2018-01-01"), (80, 20, "2018-01-10")):
        index = pd.Index(np.arange(offset, offset + rows), name="event")
        labels = pd.Series((index.to_numpy() % 5 == 0).astype("int64"), index=index)
        features = pd.DataFrame(0.0, index=index, columns=FEATURE_COLUMNS)
        features["TX_AMOUNT"] = np.where(labels, 150.0, 10.0)
        features["TX_WEEKDAY"] = float(pd.Timestamp(day).isoweekday())
        metadata = pd.DataFrame(
            {
                "TRANSACTION_ID": index.to_numpy(),
                "CUSTOMER_ID": index.to_numpy() % 10,
                "TERMINAL_ID": index.to_numpy() % 3,
                "TX_DATETIME": pd.Timestamp(day) + pd.to_timedelta(np.arange(rows), unit="m"),
                "TX_FRAUD": labels,
            },
            index=index,
        )
        metadata["LABEL_AVAILABLE_AT"] = metadata.TX_DATETIME + pd.Timedelta(days=LABEL_DELAY_DAYS)
        batches.append((features, labels, metadata))
    return batches


def run(output: Path, tracking_root: Path | None = None, *, http_uri: str | None = None) -> dict:
    """Use the existing fit, artifact and optional native tracking integration."""
    if tracking_root is not None and http_uri is not None:
        raise tracking.TrackingError("Choose --tracking-root or --tracking-uri, not both")
    if http_uri is not None:
        http_uri = tracking.validate_http_uri(http_uri)
    if output.exists():
        raise FileExistsError("Choose a new output directory for this smoke check")
    run_id = uuid4().hex
    (X_train, y_train, _), (X_val, y_val, metadata) = synthetic_batches()
    with threadpool_limits(limits=4):
        model, scores, metrics, timings = candidate_training.fit_candidate(
            MODEL.model_id,
            X_train,
            y_train,
            X_val,
            y_val,
            metadata,
            model_spec=MODEL,
            parameters={"classifier__var_smoothing": 1e-8},
            random_state=42,
        )
        measurement = {
            "model_id": MODEL.model_id,
            "run_id": run_id,
            "model_interface_version": VERSION,
            "scope": "synthetic_integration_smoke",
            "promotion_status": "not_promoted",
            "configuration": {k: v for k, v in model.get_params().items() if "__" in k},
            "random_state": 42,
            "example_code_sha256": sha256(Path(__file__)),
            "feature_columns": list(X_train.columns),
            "fit_rows": len(X_train),
            "metrics": metrics,
            **timings,
        }
        candidate_training.save_candidate(output, model, X_val, scores, metadata, measurement)
        recorded = None
        if tracking_root is not None or http_uri is not None:
            recorded = tracking.log_candidate(
                model,
                X_val,
                scores,
                name=MODEL.model_id,
                parameters=measurement["configuration"],
                metrics={k: v for k, v in metrics.items() if k != "daily_customer_metrics"},
                tags={
                    "experiment_run_id": run_id,
                    "example_code_sha256": measurement["example_code_sha256"],
                    "scope": "synthetic_integration_smoke",
                    "promotion_status": "not_promoted",
                },
                root=tracking_root,
                **({"http_uri": http_uri} if http_uri is not None else {}),
                experiment_name=HTTP_EXPERIMENT
                if http_uri is not None
                else "fraud-model-interface-smoke",
                artifacts=(output / "metrics.json",),
            )
    return {
        "model_id": MODEL.model_id,
        "run_id": run_id,
        "model_interface_version": VERSION,
        "scope": "synthetic_integration_smoke",
        "output": str(output),
        "training_rows": len(X_train),
        "evaluation_rows": len(X_val),
        "tracking": recorded,
        "promotion_status": "not_promoted",
        "status": "success",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    destinations = parser.add_mutually_exclusive_group()
    destinations.add_argument("--tracking-root", type=Path)
    destinations.add_argument("--tracking-uri", dest="http_uri")
    args = parser.parse_args()
    try:
        output = managed_destination(args.output, "examples")
        tracking_root = (
            managed_destination(args.tracking_root, "tracking")
            if args.tracking_root is not None
            else None
        )
        http_uri = tracking.validate_http_uri(args.http_uri) if args.http_uri is not None else None
    except ValueError as exc:
        parser.error(str(exc))
    try:
        with redirect_stdout(sys.stderr):
            result = run(
                output, tracking_root, **({"http_uri": http_uri} if http_uri is not None else {})
            )
    except (ValueError, OSError, MlflowException) as exc:
        parser.exit(1, f"Contribution failed: {exc}\n")
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
