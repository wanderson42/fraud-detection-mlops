"""Real SQLite/fit/artifact paths with small synthetic data; no Handbook execution."""

import json
import shutil

import mlflow
import numpy as np
import optuna
import pandas as pd
import pytest

from fraud_detection_mlops.artifacts import write_json
from fraud_detection_mlops.modeling import development, search, tracking, train


@pytest.fixture
def study_environment(prepared_data, tmp_path, monkeypatch):
    directory, policy_path = prepared_data
    monkeypatch.setattr(
        search, "committed_inputs", lambda p: {"git_revision": "test", "files": {"test": "d" * 64}}
    )
    calls = []

    def log(model, features, scores, **kwargs):
        calls.append(kwargs)
        np.testing.assert_allclose(model.predict_proba(features)[:, 1], scores)
        return {"run_id": "synthetic-" + str(len(calls)), "model_uri": "synthetic-only"}

    monkeypatch.setattr(tracking, "log_candidate", log)
    return directory, policy_path, tmp_path / "tracking", calls


def run(environment, count=1):
    directory, policy_path, root, _ = environment
    return search.run_search(
        directory, policy_path=policy_path, tracking_root=root, new_trials=count
    )


def test_global_budget_resume_reuses_completed_fits_and_offline_verification(
    study_environment, monkeypatch
):
    first = run(study_environment)
    assert first["trials_used"] == 1 and first["fit_attempts"] == 6
    assert first["decision"] == "study_incomplete"
    second = run(study_environment, 20)
    assert second["trials_used"] == 2 and second["fit_attempts"] == 9
    assert second["budget_finished"] is True
    assert second["confirmation_evaluated"] is False
    assert second["formal_superiority_claim"] is False
    assert len(study_environment[3]) == 9  # One main run per fitted fold model.
    assert all(c["parameters"]["early_stopping"] is False for c in study_environment[3])
    assert all(c["parameters"]["random_state"] == 42 for c in study_environment[3])
    monkeypatch.setattr(
        train, "fit_candidate", lambda *a, **k: pytest.fail("Finished model refitted")
    )
    monkeypatch.setattr(
        tracking, "log_candidate", lambda *a, **k: pytest.fail("Finished model republished")
    )
    assert run(study_environment, 20) == second
    directory, policy_path, _, _ = study_environment
    assert search.verify_search(directory / "study", policy_path=policy_path) == second
    predictions = directory / "study/models/trial-001/fold_3/validation_predictions.parquet"
    predictions.write_bytes(b"corrupted")
    with pytest.raises(ValueError, match="integrity"):
        search.verify_search(directory / "study", policy_path=policy_path)


def test_failed_trial_consumes_budget_and_completed_reference_is_not_repeated(
    study_environment, monkeypatch
):
    original = train.fit_candidate
    fits = []

    def fail_once(*args, **kwargs):
        fits.append(kwargs)
        if len(fits) == 5:
            raise OSError("simulated failed fit")
        return original(*args, **kwargs)

    monkeypatch.setattr(train, "fit_candidate", fail_once)
    with pytest.raises(OSError, match="failed fit"):
        run(study_environment)
    final = run(study_environment, 20)
    assert final["trials_used"] == 2 and final["failed_trials"] == 1
    assert final["completed_trials"] == 1 and final["fit_attempts"] == 8
    assert len(fits) == 8 and len(study_environment[3]) == 7
    assert final["budget_finished"] is True
    assert not final["production_promotion"]


def test_abandoned_trial_does_not_grant_new_budget(study_environment):
    first = run(study_environment)
    directory = study_environment[0] / "study"
    study = search.open_study(directory, 43)
    study.ask(search.distributions(development.load_policy(study_environment[1])))
    final = run(study_environment, 20)
    assert final["failed_trials"] == 1 and final["trials_used"] == 2
    assert final["fit_attempts"] == first["fit_attempts"]


def test_changed_store_or_implementation_is_rejected_before_new_fit(
    study_environment, monkeypatch
):
    run(study_environment)
    directory, policy_path, root, _ = study_environment
    monkeypatch.setattr(train, "fit_candidate", lambda *a, **k: pytest.fail("Unauthorized fit"))
    with pytest.raises(search.SearchError, match="identity changed"):
        search.run_search(directory, policy_path=policy_path, tracking_root=root / "other")
    monkeypatch.setattr(
        search,
        "committed_inputs",
        lambda p: {"git_revision": "different", "files": {"changed": "e" * 64}},
    )
    with pytest.raises(search.SearchError, match="code or lock differ"):
        run(study_environment)


def test_single_writer_lock_is_released_after_exception(tmp_path):
    with (
        search.study_lock(tmp_path),
        pytest.raises(search.SearchError, match="Another process"),
        search.study_lock(tmp_path),
    ):
        pytest.fail("Concurrent writer acquired lock")
    with search.study_lock(tmp_path):
        pass


def test_prior_corruption_blocks_new_fits(study_environment, monkeypatch):
    run(study_environment)
    predictions = (
        study_environment[0] / "study/models/trial-000/fold_1/validation_predictions.parquet"
    )
    predictions.write_bytes(b"changed")
    monkeypatch.setattr(
        train, "fit_candidate", lambda *a, **k: pytest.fail("Fit after prior corruption")
    )
    with pytest.raises(ValueError, match="integrity"):
        run(study_environment)


def test_interrupted_reference_is_not_silently_retrained(study_environment, monkeypatch):
    monkeypatch.setattr(
        train, "fit_candidate", lambda *a, **k: (_ for _ in ()).throw(OSError("fit stopped"))
    )
    with pytest.raises(OSError, match="fit stopped"):
        run(study_environment)
    monkeypatch.setattr(
        train, "fit_candidate", lambda *a, **k: pytest.fail("Interrupted reference refitted")
    )
    with pytest.raises(search.SearchError, match="Incomplete fit requires review"):
        run(study_environment)


def test_saved_receipt_tampering_is_rejected(study_environment):
    run(study_environment)
    directory, policy_path, _, _ = study_environment
    receipt = directory / "study/models/trial-000/fold_1/result.json"
    result = json.loads(receipt.read_text())
    result["training_rows"] += 1
    write_json(receipt, result)
    with pytest.raises(search.SearchError, match="receipt"):
        search.verify_search(directory / "study", policy_path=policy_path)
    with pytest.raises(search.SearchError, match="receipt"):
        run(study_environment)


def test_tpe_trial_sequence_is_equal_across_restarted_and_continuous_invocations(
    silver_fixture, tmp_path, monkeypatch
):
    policy = development.load_policy()
    policy["budget"].update(max_trials=7, max_fit_attempts=24)
    policy_path = tmp_path / "policy.json"
    write_json(policy_path, policy)
    provenance = {"git_revision": "test", "files": {"test": "d" * 64}}
    monkeypatch.setattr(development, "committed_inputs", lambda p: provenance)
    monkeypatch.setattr(search, "committed_inputs", lambda p: provenance)
    prepared = development.prepare_data(
        silver_fixture / "silver",
        tmp_path / "data",
        policy_path=policy_path,
        inventory_path=silver_fixture / "inventory.json",
    )
    continuous = tmp_path / "continuous"
    resumed = tmp_path / "resumed"
    shutil.copytree(prepared["development_path"], continuous)
    shutil.copytree(prepared["development_path"], resumed)

    # Keep scheduling and SQLite real; isolate model cost with a deterministic objective.
    def fit(directory, name, fold, parameters, *args):
        return {
            "fold_id": fold["id"],
            "metrics": {
                "average_precision": 0.5 + parameters.get("classifier__learning_rate", 0.1),
                "daily_customer_precision_at_100": 0.5,
            },
            "timings": {
                "fit_seconds": 1.0,
                "predict_seconds": 1.0,
                "fit_save_tracking_seconds": 2.0,
                "peak_process_rss_mib": 100.0,
            },
            "path": str((directory / "models" / name / fold["id"]).relative_to(directory)),
            "identity": args[2],
            "files": [],
        }

    monkeypatch.setattr(search, "check_records", lambda *a: None)

    # The artifact contract is covered elsewhere. Record synthetic receipts for preflight.
    def recorded_fit(directory, name, fold, parameters, *args):
        result = fit(directory, name, fold, parameters, *args)
        write_json(directory / result["path"] / "result.json", result)
        write_json(directory / "attempts" / name / (fold["id"] + ".json"), {})
        return result

    monkeypatch.setattr(search, "fit_fold", recorded_fit)
    monkeypatch.setattr(search, "verify_search", lambda directory, **kwargs: {})
    search.run_search(continuous, policy_path=policy_path, new_trials=7)
    for count in (1, 2, 4):
        search.run_search(resumed, policy_path=policy_path, new_trials=count)
    histories = []
    for directory in (continuous, resumed):
        study = optuna.load_study(
            study_name=search.EXPERIMENT, storage="sqlite:///" + str(directory / "study/study.db")
        )
        histories.append([(t.number, t.params, t.value, t.state) for t in study.trials])
    assert len(histories[0]) == 7
    assert histories[0] == histories[1]


def test_development_gate_does_not_promote_an_incomplete_or_unstable_winner(tmp_path):
    policy = development.load_policy()
    policy["budget"]["max_trials"] = 1
    reference = [
        {
            "fold_id": f"fold_{i}",
            "metrics": {"average_precision": 0.5, "daily_customer_precision_at_100": 0.4},
            "timings": {
                "fit_seconds": 1.0,
                "predict_seconds": 1.0,
                "fit_save_tracking_seconds": 2.0,
                "peak_process_rss_mib": 100.0,
            },
        }
        for i in range(1, 4)
    ]
    results = [
        {**r, "metrics": {"average_precision": ap, "daily_customer_precision_at_100": 0.5}}
        for r, ap in zip(reference, [0.45, 0.6, 0.65], strict=True)
    ]
    study = optuna.create_study(direction="maximize")
    study.add_trial(
        optuna.trial.create_trial(value=0.5666666666666667, user_attrs={"fold_results": results})
    )
    report = search.study_report(study, reference, policy, tmp_path)
    assert report["comparison"][0]["delta_mean_ap"] > 0.01
    assert report["candidate_for_confirmation_review"] is None  # One fold loses .05.
    assert report["decision"] == "retain_reference"
    results[0]["metrics"]["average_precision"] = 0.6
    second = optuna.create_study(direction="maximize")
    second.add_trial(
        optuna.trial.create_trial(value=0.6166666666666667, user_attrs={"fold_results": results})
    )
    eligible = search.study_report(second, reference, policy, tmp_path)
    assert eligible["candidate_for_confirmation_review"] == 0
    assert not eligible["production_promotion"] and not eligible["confirmation_evaluated"]


def test_one_temporal_fold_uses_real_native_mlflow_and_skops(prepared_data, tmp_path):
    directory, policy_path = prepared_data
    policy = development.load_policy(policy_path)
    manifest = json.loads((directory / "manifest.json").read_text())
    destination = directory / "study"
    write_json(destination / "protocol.json", policy)
    result = search.fit_fold(
        destination,
        "reference",
        policy["folds"][0],
        {},
        directory,
        manifest,
        {"policy_sha256": "test", "data_sha256": "test", "max_fit_attempts": 9},
        tmp_path / "tracking",
    )
    with tracking.local_tracking(tmp_path / "tracking"):
        client = mlflow.MlflowClient()
        recorded = client.get_run(result["tracking"]["run_id"])
        assert recorded.info.status == "FINISHED"
        assert recorded.data.params["early_stopping"] == "False"
        assert recorded.data.tags["fold_id"] == "fold_1"
        assert recorded.data.tags["model_interface_version"] == "model_interface_v1"
        assert recorded.data.tags["scope"] == "temporal_development_only"
        model, _ = tracking.download_pipeline(
            result["tracking"]["model_uri"], tmp_path / "download"
        )
    predictions = pd.read_parquet(
        destination / "models/reference/fold_1/validation_predictions.parquet"
    )
    _, (features, _, _) = development.load_fold(directory, manifest, policy["folds"][0])
    np.testing.assert_allclose(
        model.predict_proba(features)[:, 1], predictions.SCORE, rtol=1e-12, atol=1e-12
    )
