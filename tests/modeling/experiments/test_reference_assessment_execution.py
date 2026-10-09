from copy import deepcopy
import fcntl
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import mlflow
import numpy as np
import pandas as pd
import pytest
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.pipeline import Pipeline
from typer.testing import CliRunner

from fraud_detection_mlops.artifacts import sha256, write_json
from fraud_detection_mlops.config import PROJECT_ROOT
from fraud_detection_mlops.data.contracts.transaction_schema import DTYPES as SILVER_DTYPES
from fraud_detection_mlops.data.datasets import reference_assessment_dataset as dataset
from fraud_detection_mlops.features.feature_schema import FEATURE_COLUMNS
from fraud_detection_mlops.integrations import mlflow_tracking as tracking
from fraud_detection_mlops.modeling.experiments import reference_assessment_execution as assessment
from fraud_detection_mlops.modeling.experiments.final_holdout_evaluation import load_frozen_model


@pytest.fixture(scope="module")
def synthetic_reference():
    features = pd.DataFrame(
        np.random.default_rng(42).uniform(0, 100, (120, len(FEATURE_COLUMNS))),
        columns=FEATURE_COLUMNS,
    )
    return Pipeline(
        [
            (
                "classifier",
                HistGradientBoostingClassifier(random_state=42, max_iter=10, early_stopping=False),
            )
        ]
    ).fit(features, np.arange(120) % 2)


@pytest.fixture
def assessment_source(tmp_path, monkeypatch, synthetic_reference):
    root = tmp_path / "checkout"
    root.mkdir()
    shutil.copytree(
        PROJECT_ROOT / "fraud_detection_mlops",
        root / "fraud_detection_mlops",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    policy = json.loads((PROJECT_ROOT / assessment.POLICY_PATH).read_text())
    paths = [
        str(assessment.POLICY_PATH),
        str(assessment.EXECUTION_PATH),
        "poetry.lock",
        "pyproject.toml",
        "references/handbook_source.json",
        "references/silver_contract_v1.json",
        "references/gold_contract_v1.json",
        *(r["path"] for r in policy["bindings"].values()),
    ]
    for name in paths:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(PROJECT_ROOT / name, target)

    def git(*args):
        return subprocess.check_output(["git", *args], cwd=root, stderr=subprocess.PIPE, text=True)

    git("init")
    git("config", "user.name", "Synthetic Test")
    git("config", "user.email", "synthetic@example.invalid")
    git("add", ".")
    git("commit", "-m", "Prespecified synthetic assessment checkout")
    silver_root = tmp_path / "silver"
    source = silver_root / policy["source_commit"] / "silver_v1"
    inventory = json.loads((root / "references/handbook_source.json").read_text())
    by_day = {r["date"]: r for r in inventory["files"]}
    records, frames = [], []
    days = [
        "2018-05-20",
        *dataset.window_days({"start": "2018-08-19", "end_exclusive": "2018-09-16"}),
        "2018-09-16",
    ]
    for number, day in enumerate(days):
        path = source / f"transactions/tx_date={day}/part-00000.parquet"
        path.parent.mkdir(parents=True)
        if day in ("2018-05-20", "2018-09-16"):
            path.write_bytes(b"reserved/outside scope: invalid parquet")
            rows = 1
        else:
            stamps = pd.Timestamp(day) + pd.to_timedelta(np.arange(1, 7), unit="h")
            frauds = (
                [0] * 6
                if day == "2018-09-02"
                else [1] * 6
                if day == "2018-09-03"
                else [0, 1, 0, 1, 0, 0]
            )
            seconds = (stamps - pd.Timestamp("2018-04-01")).total_seconds().astype("int64")
            frame = pd.DataFrame(
                {
                    "TRANSACTION_ID": np.arange(6) + number * 6,
                    "TX_DATETIME": stamps,
                    "CUSTOMER_ID": [1, 1, 2, 3, 4, 5],
                    "TERMINAL_ID": 1,
                    "TX_AMOUNT": [10, 200, 10, 200, 10, 10],
                    "TX_TIME_SECONDS": seconds,
                    "TX_TIME_DAYS": seconds // 86400,
                    "TX_FRAUD": frauds,
                    "TX_FRAUD_SCENARIO": frauds,
                }
            ).astype(SILVER_DTYPES)
            frame.to_parquet(path, index=False)
            frames.append(frame)
            rows = len(frame)
        item = by_day[day]
        records.append(
            {
                "path": path.relative_to(source).as_posix(),
                "input_filename": item["filename"],
                "input_size_bytes": item["size_bytes"],
                "input_git_blob_sha1": item["git_blob_sha1"],
                "rows": rows,
                "size_bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
        )
    manifest = {
        "source": inventory["source"],
        "layer": "silver",
        "schema_version": 1,
        "contract_version": "silver_v1",
        "contract_sha256": sha256(root / "references/silver_contract_v1.json"),
        "inventory_sha256": sha256(root / "references/handbook_source.json"),
        "files": records,
    }
    write_json(source / "manifest.json", manifest)
    calls = {"model": 0, "predict": 0}
    original_predict = synthetic_reference.predict_proba

    def counted_predict(features):
        calls["predict"] += 1
        return original_predict(features)

    monkeypatch.setattr(synthetic_reference, "predict_proba", counted_predict)
    monkeypatch.setattr(
        HistGradientBoostingClassifier,
        "fit",
        lambda *a, **k: pytest.fail("Unexpected assessment fit"),
    )

    def loaded_reference(*args, **kwargs):
        calls["model"] += 1
        return synthetic_reference

    monkeypatch.setattr(assessment, "load_frozen_model", loaded_reference)
    output = tmp_path / "output"
    directory = output / policy["source_commit"] / assessment.EXECUTION_VERSION
    return SimpleNamespace(
        root=root,
        silver_root=silver_root,
        source=source,
        manifest=manifest,
        frames=pd.concat(frames, ignore_index=True),
        output=output,
        directory=directory,
        calls=calls,
        git=git,
        policy=policy,
        run=lambda: assessment.execute_assessment(silver_root, output, project_root=root),
    )


def rehash_results(directory):
    results = directory / "results"
    manifest = json.loads((results / "manifest.json").read_text())
    manifest["files"] = assessment.file_records(results, [r["path"] for r in manifest["files"]])
    write_json(results / "manifest.json", manifest)
    state = json.loads((directory / "access.json").read_text())
    state["results_manifest_sha256"] = sha256(results / "manifest.json")
    write_json(directory / "access.json", state)


def test_full_window_preserves_causal_context_single_class_days_and_reuses_without_sources(
    assessment_source, monkeypatch
):
    s = assessment_source
    first = s.run()
    assert first["reused"] is False and first["metrics"]["rows"] == 84
    assert first["assessment_evaluated"] is True and first["operational_replay_evaluated"] is False
    assert (
        first["formal_superiority_claim"] is False
        and first["generalization_confidence_interval"] is None
    )
    original_report = (s.directory / "results/report.json").read_bytes()
    first_features = pd.read_parquet(s.directory / "results/features/2018-09-02.parquet")
    stamp = first_features.TX_DATETIME.iloc[0]
    known = s.frames[
        (s.frames.TX_DATETIME >= stamp - pd.Timedelta(days=14))
        & (s.frames.TX_DATETIME < stamp - pd.Timedelta(days=7))
    ]
    assert first_features.TERMINAL_KNOWN_FRAUD_COUNT_7D.iloc[0] == known.TX_FRAUD.sum()
    daily = json.loads((s.directory / "results/daily.json").read_text())
    assert len(daily) == 14
    assert daily[0]["average_precision"] is None and daily[0]["customer_recall_at_100"] is None
    assert daily[1]["average_precision"] is None and daily[1]["roc_auc"] is None
    assert sum(d["rows"] for d in daily) == 84
    shutil.rmtree(s.silver_root)
    monkeypatch.setattr(
        assessment, "load_frozen_model", lambda *a, **k: pytest.fail("Reloaded model on reuse")
    )
    assert s.run()["reused"] is True
    assert assessment.verify_assessment(s.directory)["metrics"] == first["metrics"]
    assert s.calls == {"model": 1, "predict": 1}
    assert (s.directory / "results/report.json").read_bytes() == original_report


def test_model_failure_does_not_read_selected_silver_bytes(assessment_source, monkeypatch):
    s = assessment_source

    def reject(*args, **kwargs):
        raise ValueError("Frozen model bytes changed")

    monkeypatch.setattr(assessment, "load_frozen_model", reject)
    monkeypatch.setattr(
        dataset,
        "check_silver_bytes",
        lambda *a: pytest.fail("Read native Silver before model verification"),
    )
    with pytest.raises(ValueError, match="model bytes"):
        s.run()
    state = json.loads((s.directory / "access.json").read_text())
    assert state["assessment_window_access_started"] is False and state["status"] == "failed"
    assert s.calls["predict"] == 0


def test_uncommitted_execution_blocks_before_model_and_access_record(assessment_source):
    s = assessment_source
    with (s.root / "fraud_detection_mlops/config.py").open("a") as handle:
        handle.write("\nCHANGED = True\n")
    with pytest.raises(ValueError, match="Commit protocol"):
        s.run()
    assert not s.directory.exists() and s.calls == {"model": 0, "predict": 0}


def test_retry_is_audited_and_new_committed_implementation_cannot_reopen_window(
    assessment_source, monkeypatch
):
    s = assessment_source
    original = assessment.build_assessment_features

    def interrupted(*args, **kwargs):
        raise OSError("interrupted feature calculation")

    monkeypatch.setattr(assessment, "build_assessment_features", interrupted)
    with pytest.raises(OSError, match="interrupted"):
        s.run()
    assert (
        json.loads((s.directory / "access.json").read_text())["assessment_window_access_started"]
        is True
    )
    monkeypatch.setattr(assessment, "build_assessment_features", original)
    source = s.root / "fraud_detection_mlops/config.py"
    initial = source.read_bytes()
    source.write_bytes(initial + b"\nCHANGED = True\n")
    s.git("add", ".")
    s.git("commit", "-m", "Different committed implementation")
    with pytest.raises(ValueError, match="Retry policy"):
        s.run()
    source.write_bytes(initial)
    s.git("add", ".")
    s.git("commit", "-m", "Restore original implementation")
    assert s.run()["reused"] is False
    state = json.loads((s.directory / "access.json").read_text())
    assert state["attempts"] == 2 and s.calls["predict"] == 1


@pytest.mark.parametrize(
    "defect", ["hash", "label", "day", "duplicate", "schema", "scenario", "symlink"]
)
def test_invalid_selected_silver_cannot_reach_prediction_and_attempt_is_retained(
    assessment_source, defect
):
    s = assessment_source
    record = next(r for r in s.manifest["files"] if r["input_filename"] == "2018-09-02.pkl")
    path = s.source / record["path"]
    if defect == "hash":
        path.write_bytes(b"corrupt selected partition")
    elif defect == "symlink":
        path.unlink()
        path.symlink_to(s.source / "transactions/tx_date=2018-09-16/part-00000.parquet")
    else:
        frame = pd.read_parquet(path)
        if defect == "label":
            frame.loc[0, "TX_FRAUD"] = 2
        elif defect == "day":
            frame.loc[0, "TX_DATETIME"] += pd.Timedelta(days=1)
        elif defect == "duplicate":
            frame.loc[0, "TRANSACTION_ID"] = frame.loc[1, "TRANSACTION_ID"]
        elif defect == "schema":
            frame["TX_AMOUNT"] = frame.TX_AMOUNT.astype("int64")
        else:
            frame.loc[0, "TX_FRAUD_SCENARIO"] = 1
        frame.to_parquet(path, index=False)
        record.update(sha256=sha256(path), size_bytes=path.stat().st_size)
        write_json(s.source / "manifest.json", s.manifest)
    with pytest.raises(ValueError):
        s.run()
    assert s.calls["predict"] == 0
    assert json.loads((s.directory / "access.json").read_text())["status"] == "failed"


def test_missing_assessment_day_blocks_before_model_loading(assessment_source):
    s = assessment_source
    s.manifest["files"] = [
        r for r in s.manifest["files"] if r["input_filename"] != "2018-09-02.pkl"
    ]
    write_json(s.source / "manifest.json", s.manifest)
    with pytest.raises(ValueError, match="complete ordered"):
        s.run()
    assert s.calls["model"] == 0 and not (s.directory / "access.json").exists()


@pytest.mark.parametrize("defect", ["superiority", "population", "daily", "extra_file"])
def test_offline_verification_rejects_rehashed_semantic_changes(assessment_source, defect):
    s = assessment_source
    s.run()
    results = s.directory / "results"
    if defect == "superiority":
        report = json.loads((results / "report.json").read_text())
        report["formal_superiority_claim"] = True
        write_json(results / "report.json", report)
    elif defect == "population":
        predictions = pd.read_parquet(results / "predictions.parquet")
        predictions.loc[0, "TRANSACTION_ID"] += 100000
        predictions.to_parquet(results / "predictions.parquet", index=False)
    elif defect == "daily":
        daily = json.loads((results / "daily.json").read_text())
        daily.pop()
        write_json(results / "daily.json", daily)
    else:
        (results / "features/2018-09-16.parquet").write_bytes(b"outside assessment window")
    rehash_results(s.directory)
    shutil.rmtree(s.silver_root)
    with pytest.raises(ValueError):
        assessment.verify_assessment(s.directory)


def test_publication_retry_reuses_scores_after_atomic_result_rename(
    assessment_source, monkeypatch
):
    s = assessment_source
    original = assessment.record_phase

    def interrupted(directory, state, phase, **updates):
        if phase == "complete":
            raise OSError("interrupted after result rename")
        return original(directory, state, phase, **updates)

    monkeypatch.setattr(assessment, "record_phase", interrupted)
    with pytest.raises(OSError, match="after result rename"):
        s.run()
    assert (s.directory / "results/manifest.json").is_file()
    monkeypatch.setattr(assessment, "record_phase", original)
    assert s.run()["reused"] is True and s.calls["predict"] == 1


def test_concurrent_writer_blocks_without_scoring(assessment_source):
    s = assessment_source
    s.directory.mkdir(parents=True)
    with (s.directory / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match="already running"):
            s.run()
    assert s.calls == {"model": 0, "predict": 0}


def test_deleted_access_record_cannot_turn_saved_results_into_new_evaluation(assessment_source):
    s = assessment_source
    s.run()
    (s.directory / "access.json").unlink()
    with pytest.raises(ValueError, match="original assessment access"):
        s.run()
    assert s.calls["predict"] == 1


def test_execution_cli_initializes_without_native_data():
    runner = CliRunner()
    assert runner.invoke(assessment.app, ["--help"]).exit_code == 0
    assert runner.invoke(assessment.app, ["run", "--help"]).exit_code == 0
    assert runner.invoke(assessment.app, ["verify", "--help"]).exit_code == 0


@pytest.mark.parametrize("target", ["statistical_protocol.json", "manifest.json", "access.json"])
def test_offline_verifier_refuses_redirected_records_before_reading_native_replay(
    assessment_source, monkeypatch, target
):
    s = assessment_source
    s.run()
    native = s.source / "transactions/tx_date=2018-09-16/part-00000.parquet"
    parent = s.directory if target == "access.json" else s.directory / "results"
    path = parent / target
    path.unlink()
    path.symlink_to(native)
    original = Path.open

    def checked_open(path, *args, **kwargs):
        if path.resolve() == native:
            pytest.fail("Verifier followed a record into reserved replay bytes")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", checked_open)
    with pytest.raises(ValueError, match="regular files"):
        assessment.verify_assessment(s.directory)


def test_explicit_tracking_root_loads_real_skops_bytes_without_new_runs_or_fit(
    tmp_path, synthetic_reference, monkeypatch
):
    model = deepcopy(synthetic_reference)
    model.__dict__.pop("predict_proba", None)
    features = pd.DataFrame(
        np.random.default_rng(43).uniform(0, 100, (12, len(FEATURE_COLUMNS))),
        columns=FEATURE_COLUMNS,
    )
    expected = model.predict_proba(features)[:, 1]
    root = tmp_path / "checkout"
    store = root / "data/tracking"
    logged = tracking.log_candidate(
        model,
        features,
        expected,
        name="hist_gradient_boosting",
        parameters={},
        metrics={},
        tags={"baseline_run_id": "synthetic-assessment-loader"},
        root=store,
        experiment_name="synthetic-assessment-loader",
    )
    with tracking.local_tracking(store):
        artifact = Path(
            mlflow.artifacts.download_artifacts(
                artifact_uri=logged["model_uri"], dst_path=str(tmp_path / "snapshot")
            )
        )
        metadata = mlflow.models.Model.load(artifact / "MLmodel")
    receipt = {
        "tracking_root": "data/tracking",
        "model_uri": logged["model_uri"],
        "model": {
            "mlflow_run_id": logged["run_id"],
            "mlmodel_sha256": sha256(artifact / "MLmodel"),
            "model_sha256": sha256(artifact / metadata.flavors["sklearn"]["pickled_model"]),
        },
        "model_parameters": {"parameters": model[-1].get_params()},
    }
    monkeypatch.setattr(
        HistGradientBoostingClassifier, "fit", lambda *a, **k: pytest.fail("Unexpected loader fit")
    )
    restored = load_frozen_model(receipt, tmp_path / "restored", project_root=root)
    np.testing.assert_allclose(
        restored.predict_proba(features)[:, 1], expected, rtol=1e-12, atol=1e-12
    )
    with tracking.local_tracking(store):
        client = mlflow.MlflowClient()
        experiment = client.get_experiment_by_name("synthetic-assessment-loader")
        assert len(client.search_runs([experiment.experiment_id])) == 1
        assert client.get_run(logged["run_id"]).data.tags["test_evaluated"] == "false"
    receipt["model"]["model_sha256"] = "0" * 64
    monkeypatch.setattr(
        tracking, "download_pipeline", lambda *a, **k: pytest.fail("Deserialized unverified bytes")
    )
    with pytest.raises(ValueError, match="model bytes changed"):
        load_frozen_model(receipt, tmp_path / "invalid", project_root=root)
