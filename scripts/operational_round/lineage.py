"""Dataset identity includes actual consumed rows and exact source partition hashes."""

import hashlib

import mlflow.data
import numpy as np
import pandas as pd
from scripts.operational_round.policy import digest, require


def description(frame, labels, metadata, *, context, provenance, partitions):
    require(context in {"training", "validation"}, "Only training and validation are authorized")
    require(
        frame.index.equals(labels.index)
        and frame.index.equals(metadata.index)
        and len(frame) > 0
        and frame.columns.is_unique
        and np.isfinite(frame.to_numpy()).all(),
        "Dataset alignment/values invalid",
    )
    # IDs, event/label clocks, predictors and target all contribute to the row identity.
    data = metadata.copy()
    for column in frame:
        require(column not in data, "Predictor collides with metadata")
        data[column] = frame[column]
    require("TX_FRAUD" in data and data.TX_FRAUD.equals(labels), "Dataset target differs")
    require(data.TRANSACTION_ID.is_unique, "Dataset transaction IDs must be unique")
    values = pd.util.hash_pandas_object(data, index=False, categorize=False).to_numpy(dtype="<u8")
    content_sha = hashlib.sha256(values.tobytes()).hexdigest()
    result = {
        "context": context,
        "rows": len(data),
        "target": "TX_FRAUD",
        "feature_columns": list(frame),
        "schema": {n: str(t) for n, t in data.dtypes.items()},
        "first_transaction": str(data.TX_DATETIME.min()),
        "last_transaction": str(data.TX_DATETIME.max()),
        "latest_label_available_at": str(data.LABEL_AVAILABLE_AT.max()),
        "content_rows_sha256": content_sha,
        "partitions": partitions,
        "provenance": provenance,
    }
    result["identity_sha256"] = digest(result)
    result["name"] = "fraud-" + context + "-" + result["identity_sha256"][:16]
    # MLflow's native digest fits its 36-character field; retain the full hash in the artifact.
    result["digest"] = result["identity_sha256"][:32]
    native = mlflow.data.from_pandas(
        data,
        source=provenance["gold_manifest_uri"],
        targets="TX_FRAUD",
        name=result["name"],
        digest=result["digest"],
    )
    entity = native.to_dict()
    result["native_schema"] = entity["schema"]
    result["native_source"] = entity["source"]
    result["native_source_type"] = entity["source_type"]
    result["native_profile"] = entity["profile"]
    return result, native


def inputs_match(run, expected):
    actual = {}
    for item in run.inputs.dataset_inputs:
        tags = {tag.key: tag.value for tag in item.tags}
        context = tags.get("mlflow.data.context")
        require(
            context in expected and context not in actual,
            "Unexpected or duplicate dataset context",
        )
        desired = expected[context]
        require(
            item.dataset.name == desired["name"]
            and item.dataset.digest == desired["digest"]
            and tags.get("fraud.dataset.identity_sha256") == desired["identity_sha256"]
            and tags.get("fraud.dataset.content_rows_sha256") == desired["content_rows_sha256"]
            and item.dataset.schema == desired["native_schema"]
            and item.dataset.source == desired["native_source"]
            and item.dataset.source_type == desired["native_source_type"]
            and item.dataset.profile == desired["native_profile"],
            "Native dataset identity/schema differs",
        )
        actual[context] = True
    require(set(actual) == set(expected), "Mandatory training/validation dataset Inputs missing")
