"""Build an explicitly synthetic release to verify HTTP and wheel installation."""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import skops.io as sio
from threadpoolctl import threadpool_limits

from fraud_detection_mlops.features.feature_schema import FEATURE_COLUMNS, FEATURE_DTYPES
from fraud_detection_mlops.modeling.algorithms.model_catalog import MODEL_CATALOG
from fraud_detection_mlops.modeling.contracts.model_interface import build_model, predict_scores
from fraud_detection_mlops.serving.model_release import SmokeCase, write_release
from fraud_detection_mlops.serving.request_schema import ScoreRequest


def synthetic_release(output: Path) -> dict:
    """Fit only generated examples; this model is not the real reference."""
    frame = pd.DataFrame(np.zeros((160, len(FEATURE_COLUMNS))), columns=FEATURE_COLUMNS)
    frame["TX_AMOUNT"] = np.arange(160, dtype="float64")
    frame["TX_WEEKDAY"] = 2  # Tuesday, 2018-04-03.
    frame = frame.astype(FEATURE_DTYPES)
    model = build_model(
        MODEL_CATALOG["hist_gradient_boosting"],
        parameters={
            "classifier__max_iter": 30,
            "classifier__min_samples_leaf": 5,
        },
    )
    request = ScoreRequest.model_validate(
        {
            "schema_version": 1,
            "feature_contract_version": "gold_v1",
            "transaction_id": 0,
            "customer_id": 0,
            "terminal_id": 0,
            "tx_datetime": "2018-04-03T00:00:00.000000001",
            "feature_as_of": "2018-04-03T00:00:00.000000001",
            "features": frame.head(1).to_dict(orient="records")[0],
        }
    )
    with threadpool_limits(limits=4):
        model.fit(frame, (np.arange(160) >= 80).astype("int64"))
        smoke = SmokeCase(
            request=request,
            expected_score=float(predict_scores(model, request.feature_frame())[0]),
        )
        return write_release(
            output,
            sio.dumps(model),
            smoke,
            model_id="hist_gradient_boosting",
            source={"origin": "generated_in_memory_without_handbook"},
            parity_rows=1,
            evidence_kind="synthetic_smoke",
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(synthetic_release(args.output), indent=2))


if __name__ == "__main__":
    main()
