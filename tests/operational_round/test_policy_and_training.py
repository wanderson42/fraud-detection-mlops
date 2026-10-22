"""The new round requires dataset Inputs, all candidates and a quality-gated task champion."""

import copy
from pathlib import Path

from examples.model_contribution import synthetic_batches
from mlflow import MlflowClient
import pytest
from scripts.operational_round import lineage, policy, promotion, training


@pytest.fixture
def batches():
    train, validation = synthetic_batches()
    # Give each validation transaction a distinct customer so P@100 has support.
    validation[2]["CUSTOMER_ID"] = validation[2].TRANSACTION_ID
    return train, validation


def test_policy_selects_best_passing_candidate_and_never_dummy():
    p = policy.load()
    values = {
        "dummy_prior": {"average_precision": 0.1, "daily_customer_precision_at_100": 0.1},
        "logistic_regression": {"average_precision": 0.7, "daily_customer_precision_at_100": 0.6},
        "hist_gradient_boosting": {
            "average_precision": 0.8,
            "daily_customer_precision_at_100": 0.4,
        },
    }
    assert policy.decide(values, p)["selected_model"] == "logistic_regression"
    values["logistic_regression"]["average_precision"] = 0.2
    assert policy.decide(values, p)["passed"] is False
    values.pop("dummy_prior")
    with pytest.raises(ValueError, match="three"):
        policy.decide(values, p)


@pytest.mark.parametrize("change", ["values", "labels", "partition"])
def test_lineage_identity_binds_consumed_content(batches, change):
    batch = batches[0]
    args = {
        "context": "training",
        "provenance": {
            "gold_manifest_uri": "s3://fraud-datasets/gold/v1/" + "a" * 64 + "/manifest.json"
        },
        "partitions": [{"sha256": "b" * 64}],
    }
    before, _ = lineage.description(*batch, **args)
    changed = copy.deepcopy(batch)
    if change == "values":
        changed[0].iloc[0, 0] += 1
    elif change == "labels":
        changed[1].iloc[0] = 1 - changed[1].iloc[0]
        changed[2].loc[changed[1].index[0], "TX_FRAUD"] = changed[1].iloc[0]
    else:
        args["partitions"][0]["sha256"] = "c" * 64
    after, _ = lineage.description(*changed, **args)
    assert before["identity_sha256"] != after["identity_sha256"]


@pytest.fixture(scope="module")
def http_server(tmp_path_factory):
    import os
    import socket
    import subprocess
    import sys
    import time
    from urllib.request import urlopen

    root = tmp_path_factory.mktemp("round-http")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    uri = f"http://127.0.0.1:{port}"
    env = {k: v for k, v in os.environ.items() if not k.startswith(("AWS_", "PG", "MLFLOW_"))}
    env.update(MLFLOW_DISABLE_TELEMETRY="true", MLFLOW_DISABLE_AGENT_HINT="1")
    with (root / "server.log").open("w") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "mlflow",
                "server",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--workers",
                "1",
                "--backend-store-uri",
                "sqlite:///" + str(root / "backend.db"),
                "--artifacts-destination",
                str(root / "artifacts"),
                "--serve-artifacts",
            ],
            cwd=root,
            env=env,
            stdout=log,
            stderr=log,
        )
        try:
            deadline = time.monotonic() + 45
            while True:
                assert process.poll() is None, (root / "server.log").read_text()[-3000:]
                try:
                    with urlopen(uri + "/health", timeout=1) as response:
                        if response.status == 200:
                            break
                except OSError:
                    pass
                assert time.monotonic() < deadline
                time.sleep(0.2)
            yield uri
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


def test_new_round_native_inputs_model_metrics_and_champion(
    batches, http_server, tmp_path, monkeypatch
):
    p = policy.load()
    # This fixture has only 20 rows, so a 100-customer budget caps its P@100 at .2.
    p["minimum_daily_customer_precision_at_100"] = 0.1
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
        round_key="fixture-cycle",
        uri=http_server,
        policy=p,
        work=tmp_path,
    )
    assert summary["status"] == "qualified"
    client = MlflowClient(tracking_uri=http_server, registry_uri=http_server)
    for item in summary["candidates"].values():
        assert len(client.get_run(item["run_id"]).inputs.dataset_inputs) == 2
    result = promotion.promote(
        summary,
        http_server,
        batches[1],
        expected_alias=None,
        owner_id="e" * 32,
        training=batches[0],
        partitions=parts,
    )
    assert result["registered_model"] == "fraud-detector"
    assert result["selected_model"] in p["eligible_candidates"]
    assert result["historical_models_used"] is False
    repeated = promotion.promote(
        summary,
        http_server,
        batches[1],
        expected_alias=None,
        owner_id="e" * 32,
        training=batches[0],
        partitions=parts,
    )
    assert repeated["version"] == result["version"]
    changed_data = copy.deepcopy(batches[1])
    changed_data[0].iloc[0, 0] += 1
    with pytest.raises(ValueError, match="dataset identity differs"):
        promotion.promote(
            summary,
            http_server,
            changed_data,
            expected_alias=None,
            owner_id="e" * 32,
            training=batches[0],
            partitions=parts,
        )
    from types import SimpleNamespace

    from mlflow.entities import RunInputs

    original_get = client.get_run

    def without_inputs(identity):
        run = original_get(identity)
        return SimpleNamespace(
            info=run.info, data=run.data, inputs=RunInputs([]), outputs=run.outputs
        )

    with monkeypatch.context() as context:
        context.setattr(client, "get_run", without_inputs)
        with pytest.raises(ValueError, match="Inputs missing"):
            promotion.verify(client, summary, http_server, batches[1])
    # Exercise the actual API application on the newly selected weights, without Docker.
    from fastapi.testclient import TestClient
    from mlflow.artifacts import download_artifacts
    from scripts.operational_round.seed import seed

    from fraud_detection_mlops.integrations.mlflow_tracking import http_tracking
    from fraud_detection_mlops.serving.http_service import create_app
    from fraud_detection_mlops.serving.model_release import SmokeCase, write_release
    from fraud_detection_mlops.serving.request_schema import ScoreRequest

    selected = summary["candidates"][result["selected_model"]]
    with http_tracking(http_server):
        downloaded = Path(
            download_artifacts(
                artifact_uri=selected["model_artifact_uri"],
                tracking_uri=http_server,
                dst_path=str(tmp_path / "api-bytes"),
            )
        )
    release = tmp_path / "release"
    prepared = write_release(
        release,
        (downloaded / selected["weight_file"]).read_bytes(),
        SmokeCase(
            request=ScoreRequest.model_validate(summary["control_request"]),
            expected_score=selected["control_score"],
        ),
        model_id=result["selected_model"],
        source={"registered_model": "fraud-detector", "round_id": "fixture-cycle"},
        parity_rows=selected["parity_rows"],
        evidence_kind="verified_reference",
    )
    seeded = tmp_path / "seeded"
    seed(release, seeded, prepared["manifest_sha256"])
    monkeypatch.setenv("FRAUD_SERVING_RELEASE", str(seeded))
    monkeypatch.setenv("FRAUD_SERVING_MANIFEST_SHA256", prepared["manifest_sha256"])
    with TestClient(create_app()) as api:
        response = api.post("/score", json=summary["control_request"])
        assert response.status_code == 200
        assert response.json()["model_sha256"] == result["model_sha256"]
        assert abs(response.json()["score"] - selected["control_score"]) <= 1e-12
        assert api.post("/score", json={}).status_code == 422
    (release / "pipeline.skops").write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="identity differs"):
        seed(release, tmp_path / "bad-seed", prepared["manifest_sha256"])
    changed = copy.deepcopy(summary)
    changed["gate"]["selected_model"] = "dummy_prior"
    with pytest.raises(ValueError, match="not qualified"):
        promotion.promote(changed, http_server, batches[1], expected_alias=None, owner_id="e" * 32)
    with pytest.raises(ValueError, match="already has"):
        training.candidates(
            *batches,
            provenance,
            parts,
            round_key="fixture-cycle",
            uri=http_server,
            policy=p,
            work=tmp_path,
        )
