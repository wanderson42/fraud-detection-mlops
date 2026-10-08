"""Critical ablation contracts: pairing, frozen inputs, resume and offline verification."""

import json
from pathlib import Path
import subprocess

import mlflow
import numpy as np
import pandas as pd
import pytest
from typer.testing import CliRunner

from fraud_detection_mlops.artifacts import sha256
from fraud_detection_mlops.modeling import experiments, persistence, tracking, train
from fraud_detection_mlops.modeling.comparison import compare_predictions


def prediction_frame(scores):
    stamp = pd.to_datetime(
        [
            "2018-05-06 01:00",
            "2018-05-06 02:00",
            "2018-05-06 03:00",
            "2018-05-07 01:00",
            "2018-05-07 02:00",
            "2018-05-07 03:00",
        ]
    )
    return pd.DataFrame(
        {
            "TRANSACTION_ID": np.arange(6, dtype="int64"),
            "TX_DATETIME": stamp,
            "CUSTOMER_ID": np.array([1, 1, 2, 1, 2, 3], dtype="int64"),
            "TERMINAL_ID": np.ones(6, dtype="int64"),
            "LABEL_AVAILABLE_AT": stamp + pd.Timedelta(days=7),
            "TX_FRAUD": np.array([1, 0, 0, 1, 0, 0], dtype="int8"),
            "SCORE": scores,
        }
    )


def tiny_policy():
    policy = json.loads(experiments.DEFAULT_POLICY.read_text())
    policy["data_policy"]["validation_days"] = 2
    return policy


def test_paired_effects_ignore_row_order_and_leave_days_out_without_refitting():
    reference = prediction_frame([0.2, 0.4, 0.3, 0.8, 0.1, 0.05])
    candidate = prediction_frame([0.9, 0.4, 0.3, 0.8, 0.1, 0.05]).iloc[::-1]
    policy = tiny_policy()
    summary, daily, influence, decision = compare_predictions(
        reference, {c["id"]: candidate for c in policy["candidates"]}, policy
    )
    assert summary.delta_ap.tolist() == pytest.approx([0.25] * 3)
    assert summary.delta_precision_at_100.tolist() == pytest.approx([0] * 3)
    assert len(daily) == len(influence) == 6
    for _, group in influence.groupby("candidate_id"):
        assert group.omitted_day.tolist() == ["2018-05-06", "2018-05-07"]
        assert group.delta_ap.tolist() == pytest.approx([0, 2 / 3])
    assert decision["candidate_for_review"] is None
    assert decision["formal_superiority_claim"] is False


@pytest.mark.parametrize("change", ["label", "customer", "missing", "duplicate"])
def test_population_changes_are_rejected(change):
    reference = prediction_frame([0.2, 0.4, 0.3, 0.8, 0.1, 0.05])
    candidate = reference.copy()
    if change == "label":
        candidate.loc[0, "TX_FRAUD"] = 0
    elif change == "customer":
        candidate.loc[0, "CUSTOMER_ID"] = 4
    elif change == "missing":
        candidate = candidate.iloc[:-1]
    else:
        candidate = pd.concat([candidate, candidate.iloc[:1]])
    policy = tiny_policy()
    with pytest.raises(ValueError):
        compare_predictions(reference, {c["id"]: candidate for c in policy["candidates"]}, policy)


def test_single_class_day_effect_is_undefined_not_evidence_of_perfection():
    reference = prediction_frame([0.8, 0.4, 0.3, 0.7, 0.1, 0.05])
    reference.loc[3, "TX_FRAUD"] = 0
    policy = tiny_policy()
    summary, _, influence, _ = compare_predictions(
        reference, {c["id"]: reference.copy() for c in policy["candidates"]}, policy
    )
    assert influence[influence.omitted_day == "2018-05-06"].delta_ap.isna().all()
    assert summary.average_precision.notna().all()


def test_commit_guard_rejects_dirty_policy_and_untracked_implementation(tmp_path, monkeypatch):
    monkeypatch.setattr(experiments, "PROJECT_ROOT", tmp_path)
    policy = tmp_path / "references/experiment_protocol_v1.json"
    policy.parent.mkdir()
    policy.write_text("{}")
    (tmp_path / "poetry.lock").write_text("lock")
    source = tmp_path / "fraud_detection_mlops"
    source.mkdir()
    (source / "module.py").write_text("x = 1\n")
    for args in (
        ["init", "-q"],
        ["add", "."],
        [
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
    ):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)
    frozen = experiments.committed_inputs(policy)
    assert frozen["files"]["poetry.lock"] == sha256(tmp_path / "poetry.lock")
    policy.write_text('{"dirty": true}')
    with pytest.raises(experiments.ExperimentError, match="Commit"):
        experiments.committed_inputs(policy)
    subprocess.run(["git", "restore", str(policy)], cwd=tmp_path, check=True)
    (source / "untracked.py").write_text("x = 2\n")
    with pytest.raises(experiments.ExperimentError, match="Commit"):
        experiments.committed_inputs(policy)


def test_partial_tracking_failure_resumes_without_refit_or_duplicate_finished_runs(
    baseline_source, monkeypatch
):
    root = baseline_source
    options = {"inventory_path": root / "inventory.json", "protocol_path": root / "protocol.json"}
    result = train.run_baseline(
        root / "gold", root / "baseline", tracking_root=root / "tracking", **options
    )
    directory = Path(result["baseline_path"])
    policy = tiny_policy()
    policy["reference"].update(
        baseline_run_id=result["run_id"],
        baseline_manifest_sha256=sha256(directory / "manifest.json"),
        model_uri=result["tracking"]["hist_gradient_boosting"]["model_uri"],
        **{
            k: result["comparison"]["hist_gradient_boosting"][k]
            for k in ("average_precision", "daily_customer_precision_at_100")
        },
    )
    policy["data_policy"].update(train_rows=160, validation_rows=160)
    policy_path = root / "experiment_policy.json"
    policy_path.write_text(json.dumps(policy))
    provenance = {
        "git_revision": "a" * 40,
        "files": {"poetry.lock": sha256(experiments.PROJECT_ROOT / "poetry.lock")},
    }
    monkeypatch.setattr(experiments, "committed_inputs", lambda path: provenance)
    fitted, loaded = [], []
    original_fit, original_load, original_log = (
        train.fit_candidate,
        train.load_split,
        tracking.log_candidate,
    )

    def fit(name, X_train, *args):
        fitted.append(list(X_train.columns))
        return original_fit(name, X_train, *args)

    def load(directory, manifest, split):
        loaded.append(split)
        return original_load(directory, manifest, split)

    failures = []

    def log(*args, **kwargs):
        if kwargs["name"] == "without_terminal_fraud_counts" and not failures:
            failures.append(True)
            raise OSError("MLflow unavailable")
        return original_log(*args, **kwargs)

    monkeypatch.setattr(train, "fit_candidate", fit)
    monkeypatch.setattr(train, "load_split", load)
    monkeypatch.setattr(tracking, "log_candidate", log)
    arguments = dict(
        policy_path=policy_path,
        gold_root=root / "gold",
        tracking_root=root / "tracking",
        output_root=root / "experiments",
        **options,
    )
    with pytest.raises(OSError, match="unavailable"):
        experiments.run_experiments(directory, **arguments)
    destination = next((root / "experiments" / ("b" * 40) / experiments.VERSION).iterdir())
    assert json.loads((destination / "state.json").read_text())["status"] == "failed"
    assert not (destination / "report.json").exists()
    assert len(fitted) == 2
    completed = experiments.run_experiments(directory, resume=destination, **arguments)
    assert [len(columns) for columns in fitted] == [17, 17, 15]
    assert loaded == ["train", "validation"] * 2
    assert completed["test_evaluated"] is False
    assert completed["candidate_for_review"] is None
    assert completed["promotion_status"] == "not_promoted"
    with tracking.local_tracking(root / "tracking"):
        client = mlflow.MlflowClient()
        experiment = client.get_experiment_by_name(experiments.EXPERIMENT)
        runs = client.search_runs([experiment.experiment_id])
        assert len(runs) == 3
        for run in runs:
            assert run.info.status == "FINISHED"
            assert "mlflow.parentRunId" not in run.data.tags
            assert run.data.tags["protocol_sha256"] == sha256(policy_path)
            assert run.data.tags["promotion_status"] == "not_promoted"
            assert {"protocol.json", "metrics.json", "validation_predictions.parquet"} <= {
                item.path for item in client.list_artifacts(run.info.run_id)
            }
            verified = tracking.verify_model(run.data.tags["logged_model_uri"], root / "tracking")
            assert verified["feature_count"] == int(run.data.params["feature_count"])
    # Crash gap: MLflow finished but the local tracking receipt was not committed.
    checkpoint_path = destination / "models/without_terminal_volume/checkpoint.json"
    checkpoint = json.loads(checkpoint_path.read_text())
    checkpoint["tracking"] = None
    checkpoint_path.write_text(json.dumps(checkpoint))
    state = json.loads((destination / "state.json").read_text())
    state["status"] = "failed"
    (destination / "state.json").write_text(json.dumps(state))
    for name in (
        "manifest.json",
        "report.json",
        "summary.csv",
        "daily.csv",
        "leave_one_day_out.csv",
    ):
        (destination / name).unlink()
    experiments.run_experiments(directory, resume=destination, **arguments)
    assert len(fitted) == 3
    state = json.loads((destination / "state.json").read_text())
    assert sum(len(a["fits"]) for a in state["attempts"]) == 3
    with tracking.local_tracking(root / "tracking"):
        assert len(client.search_runs([experiment.experiment_id])) == 3
    monkeypatch.setattr(
        persistence, "load_pipeline", lambda *a, **k: pytest.fail("Offline verify loaded a model")
    )
    assert experiments.verify_experiments(destination)["verified_outputs"] == 18
    assert CliRunner().invoke(experiments.app, ["verify", str(destination)]).exit_code == 0
    path = (
        destination
        / "models/without_terminal_volume_and_fraud_counts/validation_predictions.parquet"
    )
    path.write_bytes(path.read_bytes() + b"corruption")
    with pytest.raises(experiments.ExperimentError, match="integrity"):
        experiments.verify_experiments(destination)


def test_reference_mismatch_fails_before_new_fits(baseline_source, monkeypatch):
    root = baseline_source
    options = {"inventory_path": root / "inventory.json", "protocol_path": root / "protocol.json"}
    result = train.run_baseline(root / "gold", root / "baseline", tracking_root=None, **options)
    monkeypatch.setattr(experiments, "committed_inputs", lambda path: {})
    monkeypatch.setattr(train, "fit_candidate", lambda *a, **k: pytest.fail("Unexpected new fit"))
    with pytest.raises(experiments.ExperimentError, match="declared reference"):
        experiments.run_experiments(
            Path(result["baseline_path"]), gold_root=root / "gold", **options
        )
