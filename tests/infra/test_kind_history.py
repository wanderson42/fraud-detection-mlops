"""Real MLflow SDK transfer with preserved originals, all baselines and a pinned champion."""

from contextlib import closing
import copy
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen

from examples.serving_smoke import synthetic_release
import mlflow
from mlflow import MlflowClient
from mlflow.entities import Dataset, DatasetInput, InputTag
from mlflow.models import infer_signature
import mlflow.sklearn
import numpy as np
import pandas as pd
import pytest
from scripts.platform.history import registry, source, transfer, worker
from scripts.storage import archive
from scripts.storage import mlflow_backup as backup
from scripts.storage import mlflow_recovery as recovery
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from fraud_detection_mlops.integrations import mlflow_tracking as tracking
from fraud_detection_mlops.integrations.skops_persistence import TRUSTED_TYPES
from fraud_detection_mlops.serving.model_release import SmokeCase, write_release


@pytest.fixture(scope="module")
def history_template(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("source-template")
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").write_text("# synthetic transfer fixture\n")
    refs = root / "references/evidence"
    refs.mkdir(parents=True)
    generated = tmp_path / "generated"
    synthetic_release(generated)
    smoke = SmokeCase.model_validate_json((generated / "smoke.json").read_bytes())
    features = list(smoke.request.feature_frame().columns)
    frame = pd.DataFrame(
        np.random.default_rng(7).normal(size=(60, len(features))), columns=features
    )
    labels = np.arange(60) % 2
    control_frame = smoke.request.feature_frame().astype("float64")
    model_specs = {
        "dummy_prior": Pipeline([("classifier", DummyClassifier(strategy="prior"))]),
        "logistic_regression": Pipeline(
            [("scale", StandardScaler()), ("classifier", LogisticRegression())]
        ),
        "hist_gradient_boosting": Pipeline(
            [
                (
                    "classifier",
                    HistGradientBoostingClassifier(max_iter=3, min_samples_leaf=3, random_state=3),
                )
            ]
        ),
    }
    baselines, hgb_bytes, hgb_uri, score = [], None, None, None
    for store in ("data/tracking", "data/tracking/interface-smoke"):
        directory = root / store
        directory.mkdir(parents=True, exist_ok=True)
        with tracking.local_tracking(directory):
            client = MlflowClient()
            eid = client.create_experiment(
                "fraud-temporal-baseline-v1" if store == "data/tracking" else "interface-check",
                artifact_location=(directory / "artifacts").as_uri(),
            )
            if store == "data/tracking":
                for name, model in model_specs.items():
                    model.fit(frame, labels)
                    run = client.create_run(
                        eid,
                        start_time=123000,
                        run_name=name,
                        tags={"model_id": name, "source": "synthetic-test-fixture"},
                    )
                    client.log_param(run.info.run_id, "seed", "3")
                    client.log_metric(
                        run.info.run_id, "average_precision", 0.6, timestamp=124000, step=0
                    )
                    client.log_metric(
                        run.info.run_id, "average_precision", 0.7, timestamp=125000, step=1
                    )
                    local = tmp_path / name
                    mlflow.sklearn.save_model(
                        model,
                        str(local),
                        serialization_format="skops",
                        pip_requirements=[],
                        skops_trusted_types=sorted(TRUSTED_TYPES),
                        pyfunc_predict_fn="predict_proba",
                        signature=infer_signature(
                            frame.head(5), model.predict_proba(frame.head(5))
                        ),
                    )
                    client.log_artifacts(run.info.run_id, str(local), "model")
                    client.log_inputs(
                        run.info.run_id,
                        datasets=[
                            DatasetInput(
                                Dataset(
                                    "training", "abcd", "code", "{}", schema="{}", profile="{}"
                                ),
                                [InputTag("context", "training")],
                            )
                        ],
                    )
                    client.set_terminated(run.info.run_id, end_time=126000)
                    uri = "runs:/" + run.info.run_id + "/model"
                    baselines.append(
                        {"model_id": name, "mlflow_run_id": run.info.run_id, "model_uri": uri}
                    )
                    if name == "hist_gradient_boosting":
                        hgb_bytes, hgb_uri = (local / "model.skops").read_bytes(), uri
                        score = float(model.predict_proba(control_frame)[0, 1])
                failed = client.create_run(
                    eid, run_name="failed-history", tags={"attempt": "failed"}
                )
                Path(failed.info.artifact_uri.removeprefix("file://")).mkdir(
                    parents=True, exist_ok=True
                )
                client.set_terminated(failed.info.run_id, status="FAILED", end_time=127000)
                client.delete_run(failed.info.run_id)
                eid = client.create_experiment(
                    "fraud-temporal-optuna-v1",
                    artifact_location=(directory / "artifacts").as_uri(),
                    tags={"purpose": "historical-search"},
                )
            # MLflow 3 logged models, metrics and run/model outputs in both stores.
            with mlflow.start_run(experiment_id=eid) as run:
                mlflow.log_param("sample", "true")
                mlflow.log_metric("history", 0.25, step=0)
                mlflow.log_metric("history", 0.75, step=1)
                model = model_specs["hist_gradient_boosting"]
                logged = mlflow.sklearn.log_model(
                    model,
                    name="model",
                    serialization_format="skops",
                    skops_trusted_types=sorted(TRUSTED_TYPES),
                    pyfunc_predict_fn="predict_proba",
                    signature=infer_signature(frame.head(5), model.predict_proba(frame.head(5))),
                    pip_requirements=[],
                )
                mlflow.set_tag("logged_model_uri", logged.model_uri)
            with closing(sqlite3.connect(directory / "mlflow.db")) as connection:
                for (location,) in connection.execute("SELECT artifact_uri FROM runs"):
                    Path(location.removeprefix("file://")).mkdir(parents=True, exist_ok=True)
                connection.execute(
                    "UPDATE experiments SET artifact_location=? WHERE experiment_id=0",
                    (str(root / "mlruns/0"),),
                )
                connection.commit()
    release = root / recovery.RELEASE_PATH
    release_result = write_release(
        release,
        hgb_bytes,
        SmokeCase(request=smoke.request, expected_score=score),
        model_id="hist_gradient_boosting",
        source={"model_uri": hgb_uri},
        parity_rows=1,
        evidence_kind="synthetic_smoke",
    )
    (root / "data/serving-export.json").write_text(json.dumps(release_result))
    (refs / recovery.REFERENCE.name).write_text(
        json.dumps(
            {
                "release": release_result,
                "wheel_check": {
                    "http_score": {
                        "model_sha256": release_result["model_sha256"],
                        "serving_release_id": release_result["serving_release_id"],
                        "transaction_id": smoke.request.transaction_id,
                        "score": score,
                    }
                },
            }
        )
    )
    frozen = root / "references/frozen_candidate_v1.json"
    frozen.write_text(
        json.dumps(
            {
                "model_uri": hgb_uri,
                "policy": {
                    "acceptance": {
                        "minimum_average_precision": 0.5,
                        "minimum_daily_customer_precision_at_100": 0.45,
                    }
                },
                "model": {
                    "mlflow_run_id": baselines[-1]["mlflow_run_id"],
                    "model_sha256": release_result["model_sha256"],
                },
            }
        )
    )
    (refs / "mlflow_execution_2026-10-07.json").write_text(json.dumps({"models": baselines}))
    gate = {
        "passed": True,
        "decision": "eligible_for_laboratory_serving_review",
        "scope": "offline_candidate_for_laboratory_serving",
        "criteria": {
            "average_precision": {"minimum": 0.5, "value": 0.7, "passed": True},
            "daily_customer_precision_at_100": {"minimum": 0.45, "value": 0.55, "passed": True},
        },
    }
    report = tmp_path / "report.json"
    report.write_text(
        json.dumps(
            {
                "gate": gate,
                "freeze_sha256": archive.digest(frozen),
                "model_uri": hgb_uri,
                "refit": False,
            }
        )
    )
    with tracking.local_tracking(root / "data/tracking"):
        client = MlflowClient()
        eid = client.create_experiment(
            "fraud-final-evaluation-v1",
            artifact_location=(root / "data/tracking/artifacts").as_uri(),
        )
        evaluation = client.create_run(
            eid, run_name="frozen-hgb-test", tags={"report_sha256": archive.digest(report)}
        )
        client.log_artifact(evaluation.info.run_id, str(report))
        client.set_terminated(evaluation.info.run_id)
    (refs / "final_evaluation_execution_2026-10-08.json").write_text(
        json.dumps(
            {
                "gate": gate,
                "freeze_sha256": archive.digest(frozen),
                "model_uri": hgb_uri,
                "tracking": {
                    "run_id": evaluation.info.run_id,
                    "report_sha256": archive.digest(report),
                },
            }
        )
    )
    snapshot = backup.prepare(root, writers_stopped=True)
    release_id = archive.prepare(
        root, [recovery.RELEASE_PATH.as_posix(), "data/serving-export.json"]
    )
    work = archive.workspace(root)
    for identity in (snapshot["bundle_id"], release_id):
        target = work / "restored" / identity
        target.parent.mkdir(exist_ok=True)
        shutil.copytree(work / "bundles" / identity, target)
    # Only restored bundles remain available; no legacy source fallback can pass.
    shutil.rmtree(root / "data/tracking")
    shutil.rmtree(root / "data/serving")
    shutil.rmtree(work / "bundles")
    return root, snapshot["bundle_id"], release_id


@pytest.fixture
def history(history_template, tmp_path):
    template, identity, release_id = history_template
    root = tmp_path / "project"
    shutil.copytree(template, root)
    plan = source.prepare(root, identity, release_id)
    destination = tmp_path / "target.db"
    uri = "sqlite:///" + str(destination)
    target = MlflowClient(tracking_uri=uri, registry_uri=uri)
    foreign = target.create_experiment(
        "existing-smoke", artifact_location=(tmp_path / "target-artifacts").as_uri()
    )
    untouched = target.create_run(foreign, tags={"preserve": "true"})
    target.log_metric(untouched.info.run_id, "metric", 1.0, timestamp=1, step=0)
    target.set_terminated(untouched.info.run_id)
    return root, plan, target, uri


def test_all_baselines_full_history_logged_models_and_readback_without_originals(history):
    root, plan, client, uri = history
    before = worker.protected(client, plan)
    result = transfer.execute(client, plan, uri, create=True)
    assert result["verification"]["runs"] == 7
    assert result["verification"]["logged_models"] == 2
    assert result["verification"]["metric_history_rows"] == 10
    assert worker.protected(client, plan) == before
    assert registry.references(root, plan)["models"]
    repeated = transfer.execute(client, plan, uri, create=True)
    assert repeated["mapping"] == result["mapping"]
    assert source.prepare(root, plan["snapshot_bundle_id"], plan["release_bundle_id"]) == plan


def test_registry_champion_requires_three_reloads_and_historical_hash_gate(history):
    root, plan, client, uri = history
    result = transfer.execute(client, plan, uri, create=True)
    refs = registry.references(root, plan)
    checked = registry.champion(client, plan, result["mapping"], uri, refs)
    assert set(checked["baseline_models"]) == {
        "dummy_prior",
        "logistic_regression",
        "hist_gradient_boosting",
    }
    assert checked["alias"] == "champion" and checked["historical_release_deployed"] is False
    assert (
        checked["baseline_models"]["hist_gradient_boosting"]["control_score"]
        == plan["control"]["expected_score"]
    )
    again = registry.champion(client, plan, result["mapping"], uri, refs)
    assert checked["version"] == again["version"]


def test_foreign_experiment_is_not_adopted_or_modified(history):
    _, plan, client, uri = history
    client.create_experiment("fraud-temporal-baseline-v1", tags={"foreign": "true"})
    before = worker.protected(client, plan)
    with pytest.raises(ValueError, match="foreign"):
        transfer.execute(client, plan, uri, create=True)
    assert worker.protected(client, plan) == before


def test_changed_remote_artifact_prevents_verified_result(history):
    _, plan, client, uri = history
    result = transfer.execute(client, plan, uri, create=True)
    old = plan["stores"][0]["runs"][0]["id"]
    run = client.get_run(result["mapping"]["runs"][old])
    path = Path(run.info.artifact_uri.removeprefix("file://"))
    candidate = next(path.rglob("*.skops"))
    candidate.write_bytes(b"wrong")
    with pytest.raises(ValueError, match="size differs|SHA-256 differs"):
        transfer.execute(client, plan, uri, create=False)


def test_false_quality_gate_blocks_registry_before_destination_changes(history):
    root, plan, client, _ = history
    gate = root / "references/evidence/final_evaluation_execution_2026-10-08.json"
    value = json.loads(gate.read_text())
    value["gate"]["passed"] = False
    gate.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="quality gate"):
        registry.references(root, plan)
    assert list(client.search_registered_models()) == []


def test_unknown_populated_source_table_is_rejected_before_target_access(history):
    _, plan, _, _ = history
    path = Path(plan["snapshot_directory"]) / "data/tracking/mlflow.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE unknown_history(x TEXT)")
        connection.execute("INSERT INTO unknown_history VALUES ('do not omit')")
    with pytest.raises(ValueError, match="explicit API mapping"):
        source.rows(path)


def test_missing_baseline_or_changed_control_cannot_register_champion(history):
    root, plan, _, _ = history
    changed = copy.deepcopy(plan)
    changed["stores"][0]["runs"] = [
        r for r in changed["stores"][0]["runs"] if r["tags"].get("model_id") != "dummy_prior"
    ]
    with pytest.raises(ValueError, match="baseline run missing"):
        registry.references(root, changed)
    changed = copy.deepcopy(plan)
    changed["control"]["model_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="identities differ"):
        registry.references(root, changed)


def test_partial_failure_resumes_without_duplicate_entities_or_registry(history, monkeypatch):
    _, plan, client, uri = history
    original = client.log_artifact
    calls = 0

    def interrupted(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ConnectionError("injected transport interruption")
        return original(*args, **kwargs)

    with monkeypatch.context() as context:
        context.setattr(client, "log_artifact", interrupted)
        with pytest.raises(ConnectionError, match="interruption"):
            transfer.execute(client, plan, uri, create=True)
    before = transfer.resolve(client, plan, create=False)
    assert list(client.search_registered_models()) == []
    after = transfer.execute(client, plan, uri, create=True)
    assert before == after["mapping"]


def test_http_proxy_import_independent_readback_and_champion(history, tmp_path, monkeypatch):
    root, plan, _, _ = history
    # A real MLflow HTTP server with a separate backend and artifact directory.
    # Native PostgreSQL/S3 and KinD ownership are exercised by the author's CLI gate.
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    uri = f"http://127.0.0.1:{port}"
    env = {k: v for k, v in os.environ.items() if not k.startswith(("AWS_", "PG", "MLFLOW_"))}
    env.update(MLFLOW_DISABLE_TELEMETRY="true", MLFLOW_DISABLE_AGENT_HINT="1")
    args = [
        sys.executable,
        "-m",
        "mlflow",
        "server",
        "--backend-store-uri",
        "sqlite:///" + str(tmp_path / "http.db"),
        "--artifacts-destination",
        str(tmp_path / "remote-artifacts"),
        "--serve-artifacts",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--workers",
        "1",
    ]
    with (
        (tmp_path / "http.log").open("w") as log,
        subprocess.Popen(
            args, env=env, cwd=tmp_path, stdout=log, stderr=subprocess.STDOUT
        ) as process,
    ):
        try:
            deadline = time.monotonic() + 45
            while True:
                assert process.poll() is None, (tmp_path / "http.log").read_text()[-3000:]
                try:
                    with urlopen(uri + "/health", timeout=1) as response:
                        if response.status == 200:
                            break
                except OSError, URLError:
                    pass
                assert time.monotonic() < deadline, "HTTP server did not start"
                time.sleep(0.2)
            for name in list(os.environ):
                if name.startswith(("AWS_", "PG")) or name == "MLFLOW_BACKEND_STORE_URI":
                    monkeypatch.delenv(name)
            request = {
                "action": "import",
                "uri": uri,
                "payload": plan,
                "reference": registry.references(root, plan),
            }
            imported = worker.execute(request)
            client = MlflowClient(tracking_uri=uri, registry_uri=uri)
            assert all(
                client.get_run(identity).info.artifact_uri.startswith("mlflow-artifacts:/")
                for identity in imported["mapping"]["runs"].values()
            )
            request["action"] = "champion"
            result = worker.execute(request)
            assert result["registry"]["alias"] == "champion"
            assert result["verification"]["runs"] == plan["source_counts"]["runs"]
            assert result["unrelated_destination_metadata_unchanged"]
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


def test_mutating_actions_require_writer_declaration_before_reading_plan(monkeypatch):
    from scripts.platform import kind_history

    monkeypatch.setattr(kind_history, "read_plan", lambda _: pytest.fail("must not read or write"))
    for action in ("import", "champion"):
        with pytest.raises(ValueError, match="no-other-writers"):
            kind_history.run(action, Path("absent.json"))


def test_archived_gate_bytes_must_match_the_pinned_report(history):
    root, plan, _, _ = history
    gate = root / "references/evidence/final_evaluation_execution_2026-10-08.json"
    value = json.loads(gate.read_bytes())
    value["tracking"]["report_sha256"] = "0" * 64
    gate.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="report absent"):
        registry.references(root, plan)


def test_foreign_registered_name_is_preserved_without_assigning_alias(history):
    root, plan, client, uri = history
    result = transfer.execute(client, plan, uri, create=True)
    client.create_registered_model("fraud-hgb-reference", tags={"foreign": "preserve"})
    with pytest.raises(ValueError, match="another provenance"):
        registry.champion(client, plan, result["mapping"], uri, registry.references(root, plan))
    registered = client.get_registered_model("fraud-hgb-reference")
    assert registered.tags == {"foreign": "preserve"}
    assert registered.aliases == {}


def test_failed_baseline_reload_cannot_create_registry_version(history, monkeypatch):
    root, plan, client, uri = history
    result = transfer.execute(client, plan, uri, create=True)
    changed = copy.deepcopy(plan)
    changed["control"]["expected_score"] += 0.1
    with pytest.raises(ValueError, match="control score differs"):
        registry.champion(client, changed, result["mapping"], uri, registry.references(root, plan))
    assert list(client.search_registered_models()) == []


def test_interrupted_run_keeps_its_open_status_and_absent_end_time(history):
    _, plan, client, uri = history
    changed = copy.deepcopy(plan)
    run = next(
        r for s in changed["stores"] for r in s["runs"] if r["tags"].get("attempt") == "failed"
    )
    run["metadata"].update(status="RUNNING", end_time=None, lifecycle_stage="active")
    result = transfer.execute(client, changed, uri, create=True)
    actual = client.get_run(result["mapping"]["runs"][run["id"]])
    assert actual.info.status == "RUNNING" and actual.info.end_time is None


@pytest.mark.parametrize(
    ("content", "expected_size", "listing_size", "bad_hash", "error"),
    [
        (b"", 0, None, False, None),
        (b"tampered", 0, None, False, "Downloaded artifact sizes differ"),
        (b"x", 1, None, False, "Remote artifact set or size differs"),
        (b"", 0, 1, False, "Remote artifact set or size differs"),
        (b"", 0, None, True, "SHA-256 differs"),
    ],
)
def test_absent_listing_size_only_accepts_proven_empty_content(
    tmp_path, monkeypatch, content, expected_size, listing_size, bad_hash, error
):
    from hashlib import sha256
    from types import SimpleNamespace

    (tmp_path / "publish.lock").write_bytes(content)
    calls = []

    def download(*args):
        calls.append(args)
        return str(tmp_path)

    client = SimpleNamespace(download_artifacts=download)
    monkeypatch.setattr(transfer, "remote_files", lambda *a: {"publish.lock": listing_size})
    items = {
        "publish.lock": {
            "bytes": expected_size,
            "sha256": "0" * 64 if bad_hash else sha256(b"").hexdigest(),
        }
    }
    if error:
        with pytest.raises(ValueError, match=error):
            transfer.check_files(client, "owned-control", items, "unused")
    else:
        transfer.check_files(client, "owned-control", items, "unused")
        assert len(calls) == 1  # Missing listing metadata must never skip the download.
