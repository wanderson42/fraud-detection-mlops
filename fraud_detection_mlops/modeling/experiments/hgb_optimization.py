"""Bounded temporal Optuna optimization for HistGradientBoostingClassifier only."""

from contextlib import contextmanager
import fcntl
from importlib.metadata import version
import json
from pathlib import Path
import resource
from time import perf_counter
from typing import Annotated

import numpy as np
import optuna
from optuna.trial import TrialState
import pandas as pd
from threadpoolctl import threadpool_limits
import typer

from fraud_detection_mlops.artifacts import sha256, write_json
from fraud_detection_mlops.evaluation.ranking_metrics import evaluate_ranking
from fraud_detection_mlops.integrations import mlflow_tracking as tracking
from fraud_detection_mlops.modeling.algorithms.model_catalog import (
    DEFAULT_MODEL_PARAMETERS,
    MODEL_CATALOG,
)
from fraud_detection_mlops.modeling.experiments import (
    candidate_training,
)
from fraud_detection_mlops.modeling.experiments import (
    temporal_development_data as development,
)
from fraud_detection_mlops.modeling.experiments.experiment_artifacts import (
    check_records,
    file_records,
)
from fraud_detection_mlops.modeling.experiments.experiment_provenance import committed_inputs

app = typer.Typer(no_args_is_help=True)
EXPERIMENT = "fraud-temporal-optuna-v1"


class HGBOptimizationError(ValueError):
    """The study budget, fitted artifacts or declared identity differ."""


@contextmanager
def study_lock(directory):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".writer.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise HGBOptimizationError("Another process owns this study") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def hgb_search_distributions(policy):
    return {
        name: optuna.distributions.FloatDistribution(s["low"], s["high"], log=s["log"])
        if s["type"] == "float"
        else optuna.distributions.CategoricalDistribution(s["choices"])
        for name, s in policy["search_space"].items()
    }


def open_hgb_study(directory, seed):
    return optuna.create_study(
        study_name=EXPERIMENT,
        storage="sqlite:///" + str((directory / "study.db").resolve()),
        direction="maximize",
        load_if_exists=True,
        sampler=optuna.samplers.TPESampler(
            seed=seed, n_startup_trials=5, multivariate=False, constant_liar=False
        ),
        pruner=optuna.pruners.NopPruner(),
    )


def fit_hgb_fold(directory, name, fold, parameters, data_dir, manifest, identity, tracking_root):
    """A completed reference fold is reused; interrupted fits are never silently repeated."""
    destination = directory / "models" / name / fold["id"]
    receipt = destination / "result.json"
    if receipt.exists():
        result = json.loads(receipt.read_text())
        if result["parameters"] != parameters or result["identity"] != identity:
            raise HGBOptimizationError("Completed fit belongs to different inputs")
        if not (directory / "attempts" / name / (fold["id"] + ".json")).exists():
            raise HGBOptimizationError("Completed fit has no budget receipt")
        check_records(destination, result["files"])
        return result
    attempt = directory / "attempts" / name / (fold["id"] + ".json")
    if len(list((directory / "attempts").rglob("*.json"))) >= identity["max_fit_attempts"]:
        raise HGBOptimizationError("Global fit attempt budget exhausted")
    if attempt.exists():
        raise HGBOptimizationError(
            "Incomplete fit requires review; automatic refitting is disabled"
        )
    training, validation = development.load_fold(data_dir, manifest, fold)
    X_train, y_train, _ = training
    X_val, y_val, metadata = validation
    write_json(
        attempt,
        {"identity": identity, "parameters": parameters, "fold_id": fold["id"]},
        overwrite=False,
    )
    typer.echo(f"Fitting {name}/{fold['id']} on past training rows...")
    started = perf_counter()
    with threadpool_limits(limits=4):
        model, scores, metrics, timings = candidate_training.fit_candidate(
            "hist_gradient_boosting",
            X_train,
            y_train,
            X_val,
            y_val,
            metadata,
            model_spec=MODEL_CATALOG["hist_gradient_boosting"],
            parameters=parameters,
            random_state=42,
        )
        candidate_training.save_candidate(destination, model, X_val, scores, metadata, metrics)
        full_parameters = {
            **DEFAULT_MODEL_PARAMETERS["hist_gradient_boosting"],
            **{k.removeprefix("classifier__"): v for k, v in parameters.items()},
        }
        logged = tracking.log_candidate(
            model,
            X_val,
            scores,
            name="hist_gradient_boosting",
            parameters=full_parameters,
            metrics={
                **timings,
                **{
                    k: metrics[k] for k in ("average_precision", "daily_customer_precision_at_100")
                },
            },
            tags={
                "experiment_run_id": manifest["run_id"],
                "study_candidate": name,
                "fold_id": fold["id"],
                "protocol_sha256": identity["policy_sha256"],
                "development_manifest_sha256": identity["data_sha256"],
                "exact_features": json.dumps(list(X_val.columns)),
                "scope": "temporal_development_only",
            },
            root=tracking_root,
            experiment_name=EXPERIMENT,
            artifacts=[
                destination / "metrics.json",
                destination / "validation_predictions.parquet",
                data_dir / "manifest.json",
                directory / "protocol.json",
            ],
        )
    timings = {
        **timings,
        "fit_save_tracking_seconds": perf_counter() - started,
        "peak_process_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
    }
    result = {
        "identity": identity,
        "parameters": parameters,
        "fold_id": fold["id"],
        "metrics": metrics,
        "timings": timings,
        "tracking": logged,
        "files": file_records(
            destination, ["model.skops", "validation_predictions.parquet", "metrics.json"]
        ),
        "training_rows": len(X_train),
        "validation_rows": len(X_val),
        "path": str(destination.relative_to(directory)),
    }
    write_json(receipt, result, overwrite=False)
    return result


def aggregate(results):
    return {
        "mean_ap": float(np.mean([r["metrics"]["average_precision"] for r in results])),
        "mean_precision_at_100": float(
            np.mean([r["metrics"]["daily_customer_precision_at_100"] for r in results])
        ),
        "fit_seconds": float(sum(r["timings"]["fit_seconds"] for r in results)),
        "predict_seconds": float(sum(r["timings"]["predict_seconds"] for r in results)),
        "fit_save_tracking_seconds": float(
            sum(r["timings"]["fit_save_tracking_seconds"] for r in results)
        ),
        "peak_process_rss_mib": float(max(r["timings"]["peak_process_rss_mib"] for r in results)),
    }


def summarize_hgb_optimization(study, reference, policy, directory):
    base = aggregate(reference) if len(reference) == 3 else None
    complete = [t for t in study.trials if t.state == TrialState.COMPLETE]
    budget_finished = len(study.trials) == policy["budget"]["max_trials"] and all(
        t.state.is_finished() for t in study.trials
    )
    candidates = []
    gate = policy["development_gate"]
    for trial in complete if base is not None else []:
        results = trial.user_attrs["fold_results"]
        metrics = aggregate(results)
        delta_ap = metrics["mean_ap"] - base["mean_ap"]
        delta_precision = metrics["mean_precision_at_100"] - base["mean_precision_at_100"]
        fold_deltas = [
            r["metrics"]["average_precision"] - b["metrics"]["average_precision"]
            for r, b in zip(results, reference, strict=True)
        ]
        candidates.append(
            {
                "trial_number": trial.number,
                "parameters": trial.params,
                **metrics,
                "delta_mean_ap": delta_ap,
                "delta_mean_precision_at_100": delta_precision,
                "fold_ap_deltas": fold_deltas,
                "fold_metrics": [
                    {
                        "fold_id": r["fold_id"],
                        "average_precision": r["metrics"]["average_precision"],
                        "daily_customer_precision_at_100": r["metrics"][
                            "daily_customer_precision_at_100"
                        ],
                    }
                    for r in results
                ],
                "passes_development_gate": delta_ap >= gate["minimum_absolute_mean_ap_gain"]
                and delta_precision >= gate["minimum_absolute_mean_precision_at_100_gain"]
                and min(fold_deltas) >= -gate["maximum_fold_ap_loss"],
            }
        )
    candidates.sort(key=lambda r: (-r["mean_ap"], -r["mean_precision_at_100"], r["trial_number"]))
    eligible = [r for r in candidates if r["passes_development_gate"]] if budget_finished else []
    return {
        "study_path": str(directory.resolve()),
        "version": "optuna_v1",
        "reference": base,
        "trials_used": len(study.trials),
        "max_trials": policy["budget"]["max_trials"],
        "completed_trials": len(complete),
        "failed_trials": sum(t.state == TrialState.FAIL for t in study.trials),
        "fit_attempts": len(list((directory / "attempts").rglob("*.json"))),
        "budget_finished": budget_finished,
        "comparison": candidates,
        "best_development_trial": candidates[0]["trial_number"] if candidates else None,
        "candidate_for_confirmation_review": eligible[0]["trial_number"] if eligible else None,
        "decision": "candidate_for_confirmation_review"
        if eligible
        else "retain_reference"
        if budget_finished
        else "study_incomplete",
        "production_promotion": False,
        "confirmation_evaluated": False,
        "original_test_reused": False,
        "formal_superiority_claim": False,
    }


def optimize_hgb(
    data_dir,
    *,
    policy_path=development.DEFAULT_POLICY,
    tracking_root=tracking.DEFAULT_TRACKING,
    new_trials=1,
):
    data_dir, tracking_root = Path(data_dir), Path(tracking_root)
    if type(new_trials) is not int or new_trials < 1:
        raise HGBOptimizationError("Expected a positive maximum of new trials for this invocation")
    policy = development.load_policy(policy_path)
    development.verify_data(data_dir, policy_path=policy_path)
    manifest = json.loads((data_dir / "manifest.json").read_text())
    provenance = committed_inputs(policy_path)
    if provenance["files"] != manifest["implementation"]:
        raise HGBOptimizationError(
            "Preparation and search code or lock differ; prepare a new study"
        )
    directory = data_dir / "study"
    identity = {
        "data_sha256": sha256(data_dir / "manifest.json"),
        "policy_sha256": sha256(policy_path),
        "implementation": provenance["files"],
        "tracking_root": str(tracking_root.resolve()),
        "optuna": version("optuna"),
        "max_fit_attempts": policy["budget"]["max_fit_attempts"],
    }
    with study_lock(directory):
        study = open_hgb_study(directory, 42)
        if study.user_attrs and study.user_attrs.get("identity") != identity:
            raise HGBOptimizationError("Persistent study identity changed")
        if not study.user_attrs:
            study.set_user_attr("identity", identity)
            write_json(directory / "protocol.json", policy)
        if len(study.trials) > policy["budget"]["max_trials"]:
            raise HGBOptimizationError("Persistent trial budget was exceeded")
        # Validate completed artifacts before spending more of the study budget.
        for completed in study.trials:
            if completed.state == TrialState.COMPLETE:
                for result in completed.user_attrs["fold_results"]:
                    part = directory / result["path"]
                    if (
                        not part.resolve().is_relative_to(directory.resolve())
                        or result["identity"] != identity
                    ):
                        raise HGBOptimizationError("Completed trial artifact identity differs")
                    check_records(part, result["files"])
                    receipt = part / "result.json"
                    attempt = (
                        directory / "attempts" / part.parent.name / (result["fold_id"] + ".json")
                    )
                    if (
                        not receipt.exists()
                        or json.loads(receipt.read_text()) != result
                        or not attempt.exists()
                    ):
                        raise HGBOptimizationError("Completed trial receipt differs")
        # The OS lock proves no active writer; abandoned trials consume the budget.
        for trial in study.trials:
            if trial.state == TrialState.RUNNING:
                study.tell(trial.number, state=TrialState.FAIL)
        reference = []
        try:
            for fold in policy["folds"]:
                reference.append(
                    fit_hgb_fold(
                        directory,
                        "reference",
                        fold,
                        {},
                        data_dir,
                        manifest,
                        identity,
                        tracking_root,
                    )
                )
            remaining = min(new_trials, policy["budget"]["max_trials"] - len(study.trials))
            for _ in range(remaining):
                # Recreate per trial: SQLite does not persist a sampler's RNG state.
                study = open_hgb_study(directory, 42 + len(study.trials))
                trial = study.ask(hgb_search_distributions(policy))
                results = []
                try:
                    for fold in policy["folds"]:
                        results.append(
                            fit_hgb_fold(
                                directory,
                                f"trial-{trial.number:03d}",
                                fold,
                                trial.params,
                                data_dir,
                                manifest,
                                identity,
                                tracking_root,
                            )
                        )
                    trial.set_user_attr("fold_results", results)
                    study.tell(trial, aggregate(results)["mean_ap"])
                except BaseException as exc:
                    trial.set_user_attr("failure", type(exc).__name__ + ": " + str(exc)[:300])
                    study.tell(trial, state=TrialState.FAIL)
                    raise
        finally:
            write_json(
                directory / "report.json",
                summarize_hgb_optimization(study, reference, policy, directory),
            )
    return verify_hgb_optimization(directory, policy_path=policy_path)


def verify_hgb_optimization(directory, *, policy_path=development.DEFAULT_POLICY):
    """Offline artifact/metric reconciliation; does not fit or access the MLflow server."""
    directory = Path(directory)
    policy = development.load_policy(policy_path)
    development.verify_data(directory.parent, policy_path=policy_path)
    study = optuna.load_study(
        study_name=EXPERIMENT, storage="sqlite:///" + str((directory / "study.db").resolve())
    )
    identity = study.user_attrs["identity"]
    if (
        identity["policy_sha256"] != sha256(policy_path)
        or identity["data_sha256"] != sha256(directory.parent / "manifest.json")
        or identity["implementation"]
        != json.loads((directory.parent / "manifest.json").read_text())["implementation"]
        or json.loads((directory / "protocol.json").read_text()) != policy
    ):
        raise HGBOptimizationError("Study policy or dataset identity differs")
    if len(study.trials) > policy["budget"]["max_trials"] or any(
        t.state == TrialState.WAITING for t in study.trials
    ):
        raise HGBOptimizationError("Study budget or trial state differs")
    reference = []
    receipts = []
    for fold in policy["folds"]:
        receipt = directory / "models" / "reference" / fold["id"] / "result.json"
        if receipt.exists():
            reference.append(json.loads(receipt.read_text()))
    receipts.extend(reference)
    for trial in study.trials:
        if trial.state == TrialState.COMPLETE:
            results = trial.user_attrs["fold_results"]
            if (
                len(results) != 3
                or [r["fold_id"] for r in results] != [f["id"] for f in policy["folds"]]
                or any(r["parameters"] != trial.params for r in results)
                or not np.isclose(trial.value, aggregate(results)["mean_ap"], rtol=0, atol=1e-12)
            ):
                raise HGBOptimizationError("Trial objective or fold population differs")
            receipts.extend(results)
    manifest = json.loads((directory.parent / "manifest.json").read_text())
    for result in receipts:
        part = directory / result["path"]
        if (
            not part.resolve().is_relative_to(directory.resolve())
            or result["identity"] != identity
        ):
            raise HGBOptimizationError("Model artifact identity differs")
        check_records(part, result["files"])
        receipt = part / "result.json"
        name = part.parent.name
        attempt = directory / "attempts" / name / (result["fold_id"] + ".json")
        if (
            not attempt.exists()
            or not receipt.exists()
            or json.loads(receipt.read_text()) != result
        ):
            raise HGBOptimizationError("Saved fit receipt or budget receipt differs")
        predictions = pd.read_parquet(part / "validation_predictions.parquet")
        fold = next(f for f in policy["folds"] if f["id"] == result["fold_id"])
        training, (_, _, metadata) = development.load_fold(directory.parent, manifest, fold)
        if result["training_rows"] != len(training[0]) or result["validation_rows"] != len(
            metadata
        ):
            raise HGBOptimizationError("Saved training or validation population differs")
        pd.testing.assert_frame_equal(
            predictions[list(metadata.columns)], metadata.reset_index(drop=True), check_exact=True
        )
        metrics = evaluate_ranking(predictions.TX_FRAUD, predictions.SCORE, predictions)
        if (
            metrics != result["metrics"]
            or json.loads((part / "metrics.json").read_text()) != metrics
        ):
            raise HGBOptimizationError("Saved metrics differ from paired validation predictions")
    report = summarize_hgb_optimization(study, reference, policy, directory)
    if report["fit_attempts"] > policy["budget"]["max_fit_attempts"] or report != json.loads(
        (directory / "report.json").read_text()
    ):
        raise HGBOptimizationError("Study fit budget or report differs")
    return {**report, "status": "success"}


@app.command("prepare")
def prepare_hgb_data(policy_path: Annotated[Path, typer.Option()] = development.DEFAULT_POLICY):
    """Build new causal features from authorized Silver dates only."""
    typer.echo(json.dumps(development.prepare_data(policy_path=policy_path), indent=2))


@app.command("optimize")
def optimize_hgb_command(
    data_path: Annotated[Path, typer.Argument()],
    new_trials: Annotated[int, typer.Option(min=1, max=20)] = 1,
    policy_path: Annotated[Path, typer.Option()] = development.DEFAULT_POLICY,
):
    """Default: one trial for cost review; resume under the same global budget."""
    typer.echo(
        json.dumps(
            optimize_hgb(data_path, policy_path=policy_path, new_trials=new_trials), indent=2
        )
    )


@app.command("verify")
def verify_hgb_optimization_command(
    study_path: Annotated[Path, typer.Argument()],
    policy_path: Annotated[Path, typer.Option()] = development.DEFAULT_POLICY,
):
    """Reconcile saved scores, metrics, identities and global trial/fit budgets offline."""
    typer.echo(json.dumps(verify_hgb_optimization(study_path, policy_path=policy_path), indent=2))


if __name__ == "__main__":
    app()
