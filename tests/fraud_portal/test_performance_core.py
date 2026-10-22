"""Real scores, native Inputs and model-independent quality evidence; never alter Registry."""
# ruff: noqa: F811

import copy
from pathlib import Path

from mlflow import MlflowClient
import numpy as np
import pytest
from scripts.fraud_portal.performance import collect, metrics
from scripts.operational_round import policy, training
from tests.operational_round.test_policy_and_training import batches, http_server  # noqa: F401


def test_score_adapter_accepts_contract_shapes_and_refuses_invalid_values():
    assert metrics.scores([0.1, 0.8], 2).tolist() == [0.1, 0.8]
    assert metrics.scores([[0.9, 0.1], [0.2, 0.8]], 2).tolist() == [0.1, 0.8]
    for value in [[2.0, 0.1], [np.nan, 0.2], [[0.1], [0.2]], [0.1]]:
        with pytest.raises(ValueError):
            metrics.scores(value, 2)


def test_queue_and_threshold_have_distinct_units_and_do_not_use_labels_to_select(batches):
    X, y, meta = copy.deepcopy(batches[1])
    config = {
        "schema_version": 1,
        "version": "first_crossing_customer_day_v1",
        "model_sha256": "a" * 64,
        "serving_release_id": "b" * 32,
        "score_threshold": 0.5,
        "daily_customer_budget": 2,
    }
    values = np.linspace(0.0, 0.95, len(y))
    report = metrics.measure(y, values, meta, 0.5)
    queue = metrics.queue_policy(X, meta, values, config, "some_future_algorithm")
    assert queue["alerts"] == 2
    assert sum(
        report["threshold_transactions"][k]
        for k in ["true_positive", "false_positive", "false_negative", "true_negative"]
    ) == len(y)
    meta.TX_FRAUD = 1 - meta.TX_FRAUD
    after = metrics.queue_policy(X, meta, values, config, "some_future_algorithm")
    assert (
        after["alerts"] == queue["alerts"] and after["customers_days"] == queue["customers_days"]
    )
    assert metrics.rate(0, 0) is None


def test_HTTP_report_recomputes_all_candidates_and_can_bind_non_HGB_model(
    batches, http_server, tmp_path
):
    config = policy.load()
    config["minimum_daily_customer_precision_at_100"] = 0.1
    provenance = {
        "gold_bundle_id": "a" * 64,
        "gold_manifest_uri": "s3://fraud-datasets/gold/v1/" + "a" * 64 + "/manifest.json",
        "implementation_commit": "d" * 40,
    }
    parts = {"train": [{"sha256": "b" * 64}], "validation": [{"sha256": "c" * 64}]}
    summary = training.candidates(
        *batches,
        provenance,
        parts,
        round_key="portal-quality",
        uri=http_server,
        policy=config,
        work=tmp_path,
    )
    client = MlflowClient(tracking_uri=http_server, registry_uri=http_server)
    client.create_registered_model("fraud-portal-tests")
    name = "logistic_regression"
    candidate = summary["candidates"][name]
    version = client.create_model_version(
        "fraud-portal-tests",
        candidate["model_uri"],
        run_id=candidate["run_id"],
        tags={"fraud.model_sha256": candidate["model_sha256"]},
        model_id=candidate["logged_model_id"],
    )
    snapshot = {
        "model_id": name,
        "model_sha256": candidate["model_sha256"],
        "serving_release_id": "e" * 32,
        "alert_policy": {
            "schema_version": 1,
            "version": "first_crossing_customer_day_v1",
            "model_sha256": candidate["model_sha256"],
            "serving_release_id": "e" * 32,
            "score_threshold": 0.5,
            "daily_customer_budget": 2,
        },
    }
    before = client.get_registered_model("fraud-portal-tests").aliases
    value = collect.collect(
        client, http_server, version, snapshot, "f" * 64, lambda report: (batches[1], {})
    )
    assert value["model_id"] == name and len(value["candidates"]) == 3
    assert next(c for c in value["candidates"] if c["is_snapshot_model"])["model_id"] == name
    assert (
        value["model_fit_executed"] is False and value["independent_holdout_confirmation"] is False
    )
    assert client.get_registered_model("fraud-portal-tests").aliases == before
    changed = copy.deepcopy(batches[1])
    changed[0].iloc[0, 0] += 1
    with pytest.raises(ValueError, match="Conteúdo da validação"):
        collect.collect(
            client, http_server, version, snapshot, "f" * 64, lambda report: (changed, {})
        )


def test_validation_loader_authenticates_partitions_without_opening_train_or_test(
    batches, tmp_path
):
    import json

    from scripts.fraud_portal import data

    gold = tmp_path / "data/gold"
    gold.mkdir(parents=True)
    X, _y, meta = batches[1]
    frame = meta.copy()
    for name in X:
        frame[name] = X[name]
    frame.to_parquet(gold / "validation.parquet", index=False)
    (gold / "train.parquet").write_text("must never be parsed")
    (gold / "test.parquet").write_text("must never be parsed")
    parts = [
        {
            "path": "validation.parquet",
            "split": "validation",
            "sha256": data.sha(gold / "validation.parquet"),
        }
    ]
    (gold / "manifest.json").write_text(
        json.dumps(
            {
                "files": parts
                + [
                    {"path": "train.parquet", "split": "train"},
                    {"path": "test.parquet", "split": "test"},
                ]
            }
        )
    )
    outer = tmp_path / "manifest.json"
    outer.write_text(
        json.dumps(
            {
                "files": [
                    {"path": "data/gold/manifest.json", "sha256": data.sha(gold / "manifest.json")}
                ]
            }
        )
    )
    summary = {
        "provenance": {
            "gold_bundle_id": data.sha(outer),
            "gold_dataset_manifest_sha256": data.sha(gold / "manifest.json"),
        },
        "lineage": {"datasets": {"validation": {"partitions": parts}}},
    }
    batch, hashes = collect.validation_batch(summary, tmp_path)
    assert len(batch[0]) == len(X) and len(hashes) == 3
    (gold / "validation.parquet").write_text("changed")
    with pytest.raises(ValueError, match="Partição"):
        collect.validation_batch(summary, tmp_path)


def test_customer_day_recall_uses_first_crossing_and_later_labels_only():
    import pandas as pd

    metadata = pd.DataFrame(
        {
            "TRANSACTION_ID": [1, 2, 3, 4],
            "CUSTOMER_ID": [10, 10, 20, 30],
            "TERMINAL_ID": [1] * 4,
            "TX_DATETIME": pd.date_range("2018-09-01", periods=4, freq="h"),
            "TX_FRAUD": [0, 1, 0, 1],
        }
    )
    features = pd.DataFrame({"TX_AMOUNT": [10.0] * 4})
    config = {
        "schema_version": 1,
        "version": "first_crossing_customer_day_v1",
        "model_sha256": "a" * 64,
        "serving_release_id": "b" * 32,
        "score_threshold": 0.5,
        "daily_customer_budget": 2,
    }
    result = metrics.queue_policy(
        features, metadata, [0.6, 0.99, 0.7, 0.8], config, "linear_or_tree"
    )
    assert (result["true_positive"], result["false_positive"], result["false_negative"]) == (
        1,
        1,
        1,
    )
    assert result["precision"] == result["recall"] == 0.5


def test_performance_publication_is_idempotent_and_verified_offline(
    batches, http_server, tmp_path, monkeypatch
):
    from contextlib import nullcontext
    import json
    from types import SimpleNamespace

    from scripts.fraud_portal import controller, data
    from scripts.fraud_portal.performance import reader

    # The HTTP test above already exercises the real model/Inputs collector. This test
    # isolates publication, binding and recovery without another round of model fitting.
    root = tmp_path / "repo"
    (root / "references").mkdir(parents=True)
    (root / "references/operational_round_policy_v1.json").write_text(
        json.dumps({"registered_model": "task"})
    )
    (root / "data/storage/kind-platform/portal").mkdir(parents=True)
    snapshot = {
        "flow_id": "flow",
        "model_id": "any_algorithm",
        "model_sha256": "a" * 64,
        "serving_release_id": "b" * 32,
        "registry_version_at_capture": "2",
    }
    receipt = {"manifest_sha256": "c" * 64}
    report = {
        "schema": "fraud_portal_performance_v1",
        "model_sha256": snapshot["model_sha256"],
        "serving_release_id": snapshot["serving_release_id"],
        "portal_manifest_sha256": receipt["manifest_sha256"],
        "registry": {"version": "2"},
        "dataset": {"rows": 2},
        "candidates": [
            {
                "model_id": "any_algorithm",
                "model_sha256": "a" * 64,
                "is_snapshot_model": True,
                "metrics": {
                    "rows": 2,
                    "average_precision": 0.8,
                    "roc_auc": 0.9,
                    "daily_customer_precision_at_100": 0.5,
                },
                "curves": {"roc": {"x": [0, 1], "y": [0, 1]}},
            }
        ],
    }
    monkeypatch.setattr(controller, "config", lambda key: (receipt, snapshot, {}))
    monkeypatch.setattr(controller, "ROOT", root)
    monkeypatch.setattr(
        collect.f, "managed_destination", lambda path, area: data.checked_path(path)
    )
    monkeypatch.setattr(
        collect.flow, "read", lambda key: {"release_manifest": {"model_sha256": "a" * 64}}
    )
    monkeypatch.setattr(collect.flow, "target", lambda value: nullcontext())
    monkeypatch.setattr(collect.kind_tracking, "port_forward", lambda: nullcontext(http_server))
    client = SimpleNamespace(
        get_model_version=lambda *a: SimpleNamespace(version="2"),
        get_model_version_by_alias=lambda *a: SimpleNamespace(version="2"),
    )
    monkeypatch.setattr(collect, "MlflowClient", lambda **kwargs: client)
    monkeypatch.setattr(collect, "collect", lambda *args: report)
    first = collect.prepare(root, "portal", "quality", "round", True, tmp_path)
    directory = Path(first["directory"])
    before = (directory / "manifest.json").read_bytes()
    assert collect.prepare(root, "portal", "quality", "round", True, tmp_path) == first
    assert (directory / "manifest.json").read_bytes() == before
    assert controller.performance_config("portal")[1] == report
    assert reader.load(directory, first["manifest_sha256"], "c" * 64, snapshot)[0] == report
    with pytest.raises(ValueError, match="PRODUCT_NO_OTHER_WRITERS"):
        collect.prepare(root, "portal", "quality", "round", False, tmp_path)
