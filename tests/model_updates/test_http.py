"""Real MLflow HTTP: tuning lineage, acceptance/rejection and challenger isolation."""

from examples.model_contribution import synthetic_batches
import mlflow
from mlflow import MlflowClient
import pandas as pd
import pytest
from scripts.model_updates import policy, registry, training
from scripts.model_updates.search import choose
from scripts.operational_round import promotion

from fraud_detection_mlops.integrations import mlflow_tracking as tracking


def batches():
    train, val = synthetic_batches()
    stamps = [pd.Timestamp("2018-01-01")] * 40 + [pd.Timestamp("2018-01-10")] * 40
    train[2]["TX_DATETIME"] = pd.to_datetime(stamps) + pd.to_timedelta(range(80), unit="m")
    val[2]["TX_DATETIME"] = pd.Timestamp("2018-01-20") + pd.to_timedelta(range(20), unit="m")
    for X, _, meta in [train, val]:
        meta["LABEL_AVAILABLE_AT"] = meta.TX_DATETIME + pd.Timedelta(days=7)
        meta["CUSTOMER_ID"] = meta.TRANSACTION_ID
        X["TX_WEEKDAY"] = meta.TX_DATETIME.dt.isocalendar().day.astype("float64")
        X["TX_HOUR"] = meta.TX_DATETIME.dt.hour.astype("float64")
    return train, val


def config():
    p = policy.load()
    p["minimum_daily_customer_precision_at_100"] = 0.1
    p["comparison_as_of"] = "2018-01-28T00:00:00"
    p["inner_windows"] = {
        "fit_start": "2018-01-01",
        "fit_end_exclusive": "2018-01-02",
        "selection_start": "2018-01-10",
        "selection_end_exclusive": "2018-01-11",
    }
    return p


def provenance():
    return {
        "gold_bundle_id": "a" * 64,
        "gold_manifest_uri": "s3://fraud-datasets/gold/v1/" + "a" * 64 + "/manifest.json",
        "implementation_commit": "d" * 40,
    }


def parts():
    return {
        "train": [
            {"date": "2018-01-01", "sha256": "b" * 64},
            {"date": "2018-01-10", "sha256": "c" * 64},
        ],
        "validation": [{"date": "2018-01-20", "sha256": "e" * 64}],
    }


def test_real_http_grid_inputs_relative_gate_and_challenger_without_champion_change(
    http_server, tmp_path, monkeypatch
):
    p = config()
    p["optimization"]["logistic_regression"] = {
        "mode": "grid",
        "parameters": {},
        "space": {"classifier__C": [0.1, 1.0]},
        "trials": 2,
        "timeout_seconds": 60,
    }
    train, val = batches()
    summary = training.candidates(
        train,
        val,
        provenance(),
        parts(),
        round_key="http-update",
        uri=http_server,
        policy=p,
        work=tmp_path,
    )
    assert summary["status"] == "qualified"
    assert len(summary["optimization"]["logistic_regression"]["trials"]) == 2
    client = MlflowClient(tracking_uri=http_server, registry_uri=http_server)
    for trial in summary["optimization"]["logistic_regression"]["trials"]:
        run = client.get_run(trial["run_id"])
        assert len(run.inputs.dataset_inputs) == 2
        assert run.data.tags["outer_comparison_used"] == "false"
    owner = "f" * 32
    client.create_registered_model(
        "fraud-detector", tags={"fraud.task": "fraud-detector", "fraud.owner_id": owner}
    )
    dummy = summary["candidates"]["dummy_prior"]
    initial = client.create_model_version(
        "fraud-detector",
        source=dummy["model_uri"],
        model_id=dummy["logged_model_id"],
        run_id=dummy["run_id"],
        tags={"fraud.model_sha256": dummy["model_sha256"]},
    )
    client.set_registered_model_alias("fraud-detector", "champion", initial.version)
    baseline = {
        "version": initial.version,
        "run_id": dummy["run_id"],
        "model_sha256": dummy["model_sha256"],
    }
    assessed = registry.assess(summary, baseline, http_server, train, val, parts())
    assert assessed["gate"]["passed"]
    selected = registry.register(
        summary, assessed, baseline, http_server, owner, no_other_writers=True
    )
    assert (
        client.get_model_version_by_alias("fraud-detector", "champion").version == initial.version
    )
    assert (
        client.get_model_version_by_alias("fraud-detector", "challenger").version
        == selected["version"]
    )
    assert (
        registry.register(summary, assessed, baseline, http_server, owner, no_other_writers=True)
        == selected
    )
    better = {k: selected[k] for k in ["version", "run_id", "model_sha256"]}
    rejected = registry.assess(summary, better, http_server, train, val, parts())
    assert rejected["gate"]["decision"] == "retain_champion"
    with pytest.raises(ValueError, match="qualified"):
        registry.register(summary, rejected, better, http_server, owner, no_other_writers=True)
    # The original independent verifier still accepts the publication protocol.
    with tracking.http_tracking(http_server):
        assert len(promotion.verify(client, summary, http_server, val, train, parts())) == 3
    exercise_real_API_release_and_rollback(
        summary, dummy, baseline, selected, client, http_server, tmp_path, monkeypatch
    )


def test_optuna_is_optional_and_only_sees_training_subwindows(http_server, tmp_path):
    p = config()
    p["optimization"]["logistic_regression"] = {
        "mode": "optuna",
        "parameters": {},
        "space": {"classifier__C": [0.1, 1.0]},
        "trials": 2,
        "timeout_seconds": 60,
    }
    train, _ = batches()
    with tracking.http_tracking(http_server):
        client = MlflowClient(tracking_uri=http_server)
        eid = client.create_experiment("optuna-fixture")
        with mlflow.start_run(experiment_id=eid) as parent:
            chosen, evidence = choose(
                "logistic_regression",
                train,
                provenance(),
                parts()["train"],
                p,
                http_server,
                "optuna-fixture",
                parent.info.run_id,
                tmp_path,
            )
    assert chosen["classifier__C"] in [0.1, 1.0]
    assert evidence["outer_comparison_used"] is False
    assert evidence["inner_datasets"]["training"]["rows"] == 40
    assert evidence["inner_datasets"]["validation"]["rows"] == 40
    assert max(t["number"] for t in evidence["trials"]) == 1


def exercise_real_API_release_and_rollback(
    summary, dummy, baseline, selected, client, uri, tmp_path, monkeypatch
):
    from contextlib import ExitStack
    import copy
    from pathlib import Path

    from fastapi.testclient import TestClient
    from mlflow.artifacts import download_artifacts
    from scripts.model_updates import transaction

    from fraud_detection_mlops.serving.http_service import create_app
    from fraud_detection_mlops.serving.model_release import SmokeCase, write_release
    from fraud_detection_mlops.serving.request_schema import ScoreRequest

    with ExitStack() as stack, tracking.http_tracking(uri):
        apps = {}
        for slot, item, version in [
            ("blue", dummy, baseline["version"]),
            ("green", summary["candidates"][selected["model_id"]], selected["version"]),
        ]:
            path = Path(
                download_artifacts(
                    artifact_uri=item["model_artifact_uri"],
                    tracking_uri=uri,
                    dst_path=str(tmp_path / ("api-" + slot)),
                )
            )
            folder = tmp_path / ("release-" + slot)
            result = write_release(
                folder,
                (path / item["weight_file"]).read_bytes(),
                SmokeCase(
                    request=ScoreRequest.model_validate(summary["control_request"]),
                    expected_score=item["control_score"],
                ),
                model_id="dummy_prior" if slot == "blue" else selected["model_id"],
                source={
                    "registered_model": "fraud-detector",
                    "round_id": "http-update",
                    "registry_version": version,
                },
                parity_rows=item["parity_rows"],
                evidence_kind="verified_reference",
            )
            monkeypatch.setenv("FRAUD_SERVING_RELEASE", str(folder))
            monkeypatch.setenv("FRAUD_SERVING_MANIFEST_SHA256", result["manifest_sha256"])
            apps[slot] = stack.enter_context(TestClient(create_app()))

        class BoundHTTP:
            def __init__(self):
                self.slot = "blue"

            def alias(self):
                return client.get_model_version_by_alias("fraud-detector", "champion").version

            def route(self):
                return self.slot

            def set_route(self, slot):
                self.slot = slot

            def set_alias(self, version, expected):
                assert self.alias() == expected
                client.set_registered_model_alias("fraud-detector", "champion", version)

            def check(self, identity):
                assert self.slot == identity["slot"]
                api = apps[self.slot]
                score = api.post("/score", json=summary["control_request"])
                assert (
                    score.status_code == 200
                    and score.json()["model_sha256"] == identity["model_sha256"]
                )
                assert (
                    api.get("/ready").status_code == 200
                    and api.post("/score", json={}).status_code == 422
                )

            def prepare_recovery(self, identity):
                pass

            def finalize(self, identity):
                self.check(identity)

        backend = BoundHTTP()
        events = []
        intent = {
            "approval_sha256": "a" * 64,
            "before": {**baseline, "slot": "blue"},
            "after": {**selected, "slot": "green"},
        }
        save = lambda v: events.append(copy.deepcopy(v))
        active = transaction.activate(
            intent, backend, save, approval="a" * 64, no_other_writers=True
        )
        assert active["phase"] == "active" and backend.alias() == selected["version"]
        transaction.restore(active, backend, save)
        assert backend.slot == "blue" and backend.alias() == baseline["version"]
        assert events[-1]["phase"] == "rolled_back"
