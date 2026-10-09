"""New causal feature data and authorized folds; no access to reserved evaluation rows."""

from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import duckdb
import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from fraud_detection_mlops.artifacts import sha256, write_json
from fraud_detection_mlops.config import PROJECT_ROOT
from fraud_detection_mlops.data.contracts.silver_contract import DEFAULT_CONTRACT
from fraud_detection_mlops.data.contracts.transaction_schema import ARROW_SCHEMA as SILVER_SCHEMA
from fraud_detection_mlops.data.datasets.silver_dataset import DEFAULT_OUTPUT
from fraud_detection_mlops.data.ingestion.handbook_inventory import (
    DEFAULT_INVENTORY,
    load_inventory,
)
from fraud_detection_mlops.features.causal_history import compute_features
from fraud_detection_mlops.features.feature_schema import DTYPES, FEATURE_COLUMNS, METADATA_DTYPES
from fraud_detection_mlops.features.feature_schema import FEATURE_ARROW_SCHEMA as ARROW_SCHEMA
from fraud_detection_mlops.features.feature_validation import check_feature_values
from fraud_detection_mlops.modeling.algorithms.model_catalog import (
    DEFAULT_MODEL_PARAMETERS as BASELINE_PARAMETERS,
)
from fraud_detection_mlops.modeling.experiments.experiment_artifacts import (
    check_records,
    file_records,
)
from fraud_detection_mlops.modeling.experiments.experiment_provenance import committed_inputs

DEFAULT_POLICY = PROJECT_ROOT / "references/hgb_optuna_protocol_v1.json"
DEFAULT_DEVELOPMENT = PROJECT_ROOT / "data/processed/handbook"


class DevelopmentError(ValueError):
    """The study's temporal authorization or data do not reconcile."""


def window_days(window):
    start, end = (date.fromisoformat(window[k]) for k in ("start", "end_exclusive"))
    if end <= start:
        raise DevelopmentError("Expected a nonempty half-open date interval")
    return [str(start + timedelta(days=i)) for i in range((end - start).days)]


def load_policy(path=DEFAULT_POLICY):
    policy = json.loads(Path(path).read_text())
    budget = policy["budget"]
    if (
        policy["version"] != "optuna_v1"
        or policy["scope"] != "temporal_development_only"
        or policy["model_id"] != "hist_gradient_boosting"
        or policy["features"] != "all_19_gold_v1_in_canonical_order"
        or policy["reference_parameters"] != BASELINE_PARAMETERS["hist_gradient_boosting"]
        or policy["label_delay_days"] != 7
        or policy["history_days"] != 14
        or len(policy["folds"]) != 3
        or type(budget["max_trials"]) is not int
        or not 1 <= budget["max_trials"] <= 20
        or budget["reference_fits"] != 3
        or budget["max_fit_attempts"] != 3 + 3 * budget["max_trials"]
        or budget["max_threads"] != 4
        or budget["random_state"] != 42
        or policy["objective"] != "mean_of_three_validation_average_precisions"
        or policy["comparison"]
        != "reference_parameters_refitted_on_each_identical_training_window"
        or any(
            policy[k] is not False
            for k in (
                "pruning",
                "early_stopping",
                "row_sampling",
                "feature_selection",
                "formal_superiority_claim",
                "confirmation_evaluated",
                "original_test_reused",
            )
        )
        or policy["sampler"]
        != {
            "name": "TPE",
            "n_startup_trials": 5,
            "seed_rule": "random_state_plus_trial_number",
            "multivariate": False,
            "constant_liar": False,
        }
    ):
        raise DevelopmentError("Unsupported model, resource budget or study scope")
    expected_keys = {
        "classifier__" + key
        for key in (
            "learning_rate",
            "max_iter",
            "max_leaf_nodes",
            "min_samples_leaf",
            "l2_regularization",
        )
    }
    if set(policy["search_space"]) != expected_keys:
        raise DevelopmentError("Only the declared five HGB hyperparameters may change")
    for name, spec in policy["search_space"].items():
        if spec["type"] != (
            "float"
            if name in ("classifier__learning_rate", "classifier__l2_regularization")
            else "categorical"
        ):
            raise DevelopmentError("Hyperparameter distribution has the wrong type")
        if spec["type"] == "float":
            if (
                not all(np.isfinite(spec[k]) and spec[k] > 0 for k in ("low", "high"))
                or spec["high"] <= spec["low"]
                or spec["log"] is not True
            ):
                raise DevelopmentError("Invalid logarithmic search interval")
        elif spec["type"] == "categorical":
            choices = spec["choices"]
            if (
                not choices
                or len(set(choices)) != len(choices)
                or any(type(x) is not int or x <= 0 for x in choices)
            ):
                raise DevelopmentError("Invalid categorical search interval")
        else:
            raise DevelopmentError("Unsupported search distribution")
        if name == "classifier__max_iter" and (
            spec["type"] != "categorical" or max(spec["choices"]) > 300
        ):
            raise DevelopmentError("Iteration budget exceeds 300")
    gate = policy["development_gate"]
    if (
        gate["requires_complete_budget"] is not True
        or gate["promotion"] is not False
        or any(
            not np.isfinite(gate[k]) or not 0 < gate[k] <= 1
            for k in (
                "minimum_absolute_mean_ap_gain",
                "minimum_absolute_mean_precision_at_100_gain",
                "maximum_fold_ap_loss",
            )
        )
    ):
        raise DevelopmentError("Invalid development gate")
    reserved = policy["reserved"]
    for window in reserved.values():
        window_days(window)
    if (
        len(window_days(reserved["confirmation_train"])) != 28
        or len(window_days(reserved["confirmation"])) != 14
        or reserved["confirmation_train"]["start"] < policy["context_start"]
        or reserved["confirmation"]["start"] < "2018-09-02"
        or reserved["confirmation_train"]["end_exclusive"] > reserved["confirmation"]["start"]
        or (
            date.fromisoformat(reserved["confirmation"]["start"])
            - date.fromisoformat(reserved["confirmation_train"]["end_exclusive"])
        ).days
        < 7
        or reserved["confirmation"]["end_exclusive"] > reserved["operational_replay"]["start"]
        or reserved["operational_replay"]["end_exclusive"] > "2018-10-01"
        or policy["context_start"] < "2018-05-27"
        or policy["development_end_exclusive"] > reserved["confirmation_train"]["end_exclusive"]
    ):
        raise DevelopmentError("Consumed test or reserved evaluation periods are not authorized")
    previous = None
    for i, fold in enumerate(policy["folds"], 1):
        train, validation = fold["train"], fold["validation"]
        if (
            fold["id"] != f"fold_{i}"
            or len(window_days(train)) != 28
            or len(window_days(validation)) != 7
            or date.fromisoformat(train["start"]) - timedelta(days=14)
            < date.fromisoformat(policy["context_start"])
            or (
                date.fromisoformat(validation["start"])
                - date.fromisoformat(train["end_exclusive"])
            ).days
            < 7
            or validation["end_exclusive"] > policy["development_end_exclusive"]
            or (previous is not None and previous > validation["start"])
        ):
            raise DevelopmentError("Invalid rolling windows, history, gap or validation overlap")
        previous = validation["end_exclusive"]
    return policy


def selected_days(policy):
    return sorted(
        {
            day
            for fold in policy["folds"]
            for split in ("train", "validation")
            for day in window_days(fold[split])
        }
    )


def prepare_data(
    silver_root=DEFAULT_OUTPUT,
    output_root=DEFAULT_DEVELOPMENT,
    *,
    policy_path=DEFAULT_POLICY,
    inventory_path=DEFAULT_INVENTORY,
    contract_path=DEFAULT_CONTRACT,
):
    policy = load_policy(policy_path)
    provenance = committed_inputs(policy_path)
    inventory = load_inventory(inventory_path)
    if inventory["source"]["commit"] != policy["source_commit"]:
        raise DevelopmentError("Policy and source commit differ")
    source = Path(silver_root) / policy["source_commit"] / "silver_v1"
    manifest_path = source / "manifest.json"
    manifest_digest = sha256(manifest_path)
    manifest = json.loads(manifest_path.read_text())
    if (
        manifest.get("source") != inventory["source"]
        or manifest.get("layer") != "silver"
        or manifest.get("schema_version") != 1
        or manifest.get("contract_version") != "silver_v1"
        or manifest.get("contract_sha256") != sha256(contract_path)
        or manifest.get("inventory_sha256") != sha256(inventory_path)
    ):
        raise DevelopmentError("Silver source, inventory or contract differs")
    days = window_days(
        {"start": policy["context_start"], "end_exclusive": policy["development_end_exclusive"]}
    )
    records = [
        record
        for record in manifest["files"]
        if policy["context_start"]
        <= record["input_filename"][:10]
        < policy["development_end_exclusive"]
    ]
    if [record["input_filename"][:10] for record in records] != days:
        raise DevelopmentError("Incomplete authorized Silver history")
    if any(
        record["path"] != f"transactions/tx_date={day}/part-00000.parquet"
        for day, record in zip(days, records, strict=True)
    ):
        raise DevelopmentError("Silver paths must stay inside authorized dates")
    inventory_by_day = {item["date"]: item for item in inventory["files"]}
    check_records(source, records)
    for day, record in zip(days, records, strict=True):
        item = inventory_by_day[day]
        parquet = pq.ParquetFile(source / record["path"])
        if (
            record["path"] != f"transactions/tx_date={day}/part-00000.parquet"
            or record["input_filename"] != item["filename"]
            or record["input_size_bytes"] != item["size_bytes"]
            or record["input_git_blob_sha1"] != item["git_blob_sha1"]
            or not parquet.schema_arrow.equals(SILVER_SCHEMA, check_metadata=False)
            or parquet.metadata.num_rows != record["rows"]
            or record["rows"] <= 0
        ):
            raise DevelopmentError("Authorized Silver partition does not reconcile")
    identity = {
        "policy_sha256": sha256(policy_path),
        "silver_manifest_sha256": manifest_digest,
        "implementation": provenance["files"],
    }
    run_id = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    parent = Path(output_root) / policy["source_commit"] / "development_gold_v2"
    destination = parent / run_id
    if destination.exists():
        return verify_data(destination, policy_path=policy_path)
    parent.mkdir(parents=True, exist_ok=True)
    with (
        TemporaryDirectory(prefix=".preparing-", dir=parent) as temporary,
        duckdb.connect() as connection,
    ):
        stage = Path(temporary) / "dataset"
        stage.mkdir()
        connection.execute("SET threads=4")
        connection.execute("SET memory_limit='2GB'")
        connection.read_parquet(
            [str(source / r["path"]) for r in records], hive_partitioning=False
        ).create_view("transactions")
        wrong = connection.execute(
            "SELECT COUNT(*) - COUNT(DISTINCT TRANSACTION_ID) FROM transactions"
        ).fetchone()[0]
        if wrong:
            raise DevelopmentError("Duplicate transaction IDs in development context")
        for day, record in zip(days, records, strict=True):
            connection.read_parquet(
                str(source / record["path"]), hive_partitioning=False
            ).create_view("source_part", replace=True)
            if connection.execute(
                "SELECT COUNT(*) FROM source_part WHERE TX_DATETIME IS NULL OR CAST(TX_DATETIME AS DATE) != ?",
                [day],
            ).fetchone()[0]:
                raise DevelopmentError("Silver timestamp outside its partition")
        compute_features(connection)
        if check_feature_values(connection, "gold_features"):
            raise DevelopmentError("Causal feature values do not meet the Gold contract")
        outputs = []
        for day in selected_days(policy):
            frame = (
                connection.execute(
                    "SELECT "
                    + ", ".join(DTYPES)
                    + " FROM gold_features WHERE CAST(TX_DATETIME AS DATE) = ? ORDER BY TX_DATETIME, TRANSACTION_ID",
                    [day],
                )
                .fetchdf()
                .astype(DTYPES)
            )
            path = f"transactions/{day}.parquet"
            (stage / "transactions").mkdir(exist_ok=True)
            frame.to_parquet(
                stage / path, engine="pyarrow", compression="zstd", index=False, version="2.6"
            )
            outputs.append({**file_records(stage, [path])[0], "date": day, "rows": len(frame)})
        check_records(source, records)
        if (
            sha256(manifest_path) != manifest_digest
            or committed_inputs(policy_path)["files"] != provenance["files"]
        ):
            raise DevelopmentError("Inputs changed during preparation")
        write_json(
            stage / "manifest.json",
            {
                "version": "development_gold_v2",
                "run_id": run_id,
                "source_commit": policy["source_commit"],
                **identity,
                "provenance": provenance,
                "source_files": records,
                "feature_columns": FEATURE_COLUMNS,
                "files": outputs,
                "confirmation_evaluated": False,
                "original_test_reused": False,
            },
        )
        verify_data(stage, policy_path=policy_path)
        stage.rename(destination)
    return verify_data(destination, policy_path=policy_path)


def verify_data(directory, *, policy_path=DEFAULT_POLICY):
    directory = Path(directory)
    policy = load_policy(policy_path)
    manifest = json.loads((directory / "manifest.json").read_text())
    if (
        manifest["version"] != "development_gold_v2"
        or manifest["source_commit"] != policy["source_commit"]
        or manifest["policy_sha256"] != sha256(policy_path)
        or manifest["feature_columns"] != FEATURE_COLUMNS
        or manifest["confirmation_evaluated"] is not False
        or manifest["original_test_reused"] is not False
        or [r["date"] for r in manifest["files"]] != selected_days(policy)
        or [r["path"] for r in manifest["files"]]
        != [f"transactions/{day}.parquet" for day in selected_days(policy)]
        or {str(p.relative_to(directory)) for p in (directory / "transactions").rglob("*.parquet")}
        != {r["path"] for r in manifest["files"]}
        or manifest["run_id"]
        != hashlib.sha256(
            json.dumps(
                {
                    "policy_sha256": manifest["policy_sha256"],
                    "silver_manifest_sha256": manifest["silver_manifest_sha256"],
                    "implementation": manifest["implementation"],
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
    ):
        raise DevelopmentError("Development dataset identity or authorized days differ")
    check_records(directory, manifest["files"])
    with duckdb.connect() as connection:
        paths = []
        for record in manifest["files"]:
            path = directory / record["path"]
            parquet = pq.ParquetFile(path)
            if (
                not parquet.schema_arrow.equals(ARROW_SCHEMA, check_metadata=False)
                or parquet.metadata.num_rows != record["rows"]
            ):
                raise DevelopmentError("Development schema or row count differs")
            connection.read_parquet(str(path), hive_partitioning=False).create_view(
                "part", replace=True
            )
            if (
                check_feature_values(connection, "part")
                or connection.execute(
                    "SELECT COUNT(*) FROM part WHERE CAST(TX_DATETIME AS DATE) != ?",
                    [record["date"]],
                ).fetchone()[0]
            ):
                raise DevelopmentError("Invalid development features or date")
            paths.append(str(path))
        connection.read_parquet(paths, hive_partitioning=False).create_view("all_parts")
        rows, distinct = connection.execute(
            "SELECT COUNT(*), COUNT(DISTINCT TRANSACTION_ID) FROM all_parts"
        ).fetchone()
        if rows != distinct or rows != sum(r["rows"] for r in manifest["files"]):
            raise DevelopmentError("Development transaction identity differs")
    return {
        "development_path": str(directory.resolve()),
        "verified_partitions": len(paths),
        "rows": rows,
        "confirmation_evaluated": False,
        "status": "success",
    }


def load_fold(directory, manifest, fold):
    """Load only declared training and validation, enforcing label availability at fit time."""
    result = []
    for split in ("train", "validation"):
        authorized = set(window_days(fold[split]))
        paths = [
            str(Path(directory) / r["path"]) for r in manifest["files"] if r["date"] in authorized
        ]
        if len(paths) != len(authorized):
            raise DevelopmentError("Fold has missing daily partitions")
        with duckdb.connect() as connection:
            connection.read_parquet(paths, hive_partitioning=False).create_view("selected")
            frame = connection.execute(
                "SELECT "
                + ", ".join(list(METADATA_DTYPES) + FEATURE_COLUMNS)
                + " FROM selected ORDER BY TX_DATETIME, TRANSACTION_ID"
            ).fetchdf()
        metadata = frame[list(METADATA_DTYPES)].astype(METADATA_DTYPES)
        if (
            split == "train"
            and not (metadata.LABEL_AVAILABLE_AT < pd.Timestamp(fold["validation"]["start"])).all()
        ):
            raise DevelopmentError("Training label unavailable at fit cutoff")
        result.append(
            (frame[FEATURE_COLUMNS].astype("float64"), metadata.TX_FRAUD.copy(), metadata)
        )
    if set(result[0][2].TRANSACTION_ID) & set(result[1][2].TRANSACTION_ID):
        raise DevelopmentError("Training and validation transactions overlap")
    return tuple(result)
