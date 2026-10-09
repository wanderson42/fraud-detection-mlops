"""Synthetic integration smoke check; this model is not an approved candidate."""

import argparse
import json
from pathlib import Path
from uuid import uuid4

import numpy as np
import pandas as pd
from sklearn.naive_bayes import GaussianNB
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits

from fraud_detection_mlops.artifacts import sha256
from fraud_detection_mlops.features import FEATURE_COLUMNS, LABEL_DELAY_DAYS
from fraud_detection_mlops.modeling import tracking, train
from fraud_detection_mlops.modeling.interface import VERSION, ModelSpec


def gaussian_nb() -> Pipeline:
    """A contributor writes only a complete, unfitted estimator factory."""
    return Pipeline([("scale", StandardScaler()), ("classifier", GaussianNB(var_smoothing=1e-9))])


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


def run(output: Path, tracking_root: Path | None = None) -> dict:
    """Use the existing fit, artifact and optional native tracking integration."""
    if output.exists():
        raise FileExistsError("Choose a new output directory for this smoke check")
    run_id = uuid4().hex
    (X_train, y_train, _), (X_val, y_val, metadata) = synthetic_batches()
    with threadpool_limits(limits=4):
        model, scores, metrics, timings = train.fit_candidate(
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
        train.save_candidate(output, model, X_val, scores, metadata, measurement)
        recorded = None
        if tracking_root is not None:
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
                experiment_name="fraud-model-interface-smoke",
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tracking-root", type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.output, args.tracking_root), indent=2))
