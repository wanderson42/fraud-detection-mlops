"""Bounded, resumable ablations against an existing validation reference."""

from datetime import UTC, datetime
import fcntl
from importlib.metadata import version
import json
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory
from typing import Annotated
from uuid import UUID, uuid4

import mlflow
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from threadpoolctl import threadpool_limits
import typer

from fraud_detection_mlops.artifacts import sha256, write_json
from fraud_detection_mlops.config import PROJECT_ROOT
from fraud_detection_mlops.data.datasets.gold_dataset import DEFAULT_GOLD, verify_gold
from fraud_detection_mlops.evaluation.paired_comparison import compare_predictions, measured
from fraud_detection_mlops.evaluation.ranking_metrics import evaluate_ranking
from fraud_detection_mlops.features.feature_schema import FEATURE_COLUMNS, METADATA_DTYPES
from fraud_detection_mlops.integrations import mlflow_tracking as tracking
from fraud_detection_mlops.integrations import skops_persistence as persistence
from fraud_detection_mlops.modeling.contracts.experiment_errors import ExperimentError
from fraud_detection_mlops.modeling.experiments import (
    baseline_policy as baseline,
)
from fraud_detection_mlops.modeling.experiments import (
    candidate_training,
)
from fraud_detection_mlops.modeling.experiments.experiment_artifacts import (
    check_records,
    file_records,
)
from fraud_detection_mlops.modeling.experiments.experiment_provenance import committed_inputs

VERSION = "experiment_v1"
EXPERIMENT = "fraud-controlled-ablation-v1"
DEFAULT_POLICY = PROJECT_ROOT / "references/experiment_protocol_v1.json"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "data/processed/handbook"
app = typer.Typer(no_args_is_help=True)


def load_policy(path):
    policy = json.loads(path.read_text())
    volume = ["TERMINAL_TX_COUNT_1D", "TERMINAL_TX_COUNT_7D"]
    counts = ["TERMINAL_KNOWN_FRAUD_COUNT_1D", "TERMINAL_KNOWN_FRAUD_COUNT_7D"]
    expected = [
        ("without_terminal_volume", volume),
        ("without_terminal_fraud_counts", counts),
        ("without_terminal_volume_and_fraud_counts", volume + counts),
    ]
    actual = [(c["id"], c["drop_features"]) for c in policy["candidates"]]
    budget = {
        "max_new_fits": 3,
        "max_threads": 4,
        "random_state": 42,
        "execution_order": "catalog_order_sequential",
        "hyperparameter_search": False,
        "automatic_catalog_expansion": False,
    }
    data, ref, gate, stats = (
        policy[k] for k in ("data_policy", "reference", "development_gate", "statistical_analysis")
    )
    if (
        policy.get("version") != VERSION
        or policy["model_id"] != "hist_gradient_boosting"
        or actual != expected
        or policy["budget"] != budget
        or any(
            c["feature_count"] != len(FEATURE_COLUMNS) - len(c["drop_features"])
            for c in policy["candidates"]
        )
        or any(
            data[k] is not False for k in ("change_gold", "test_evaluated", "refit_on_validation")
        )
        or data["fit_split"] != "train"
        or data["evaluation_split"] != "validation"
        or data["preserve_all_rows"] is not True
        or data["preserve_feature_order"] is not True
        or ref["refit"] is not False
        or ref["require_validation_score_parity"] is not True
        or stats["leave_one_day_out"]["refit"] is not False
        or any(
            stats[k] is not False
            for k in ("p_values", "confidence_intervals", "formal_superiority_claim")
        )
        or gate["required_complete_catalog"] is not True
        or gate["ranking"]
        != [
            "average_precision_desc",
            "daily_customer_precision_at_100_desc",
            "feature_count_asc",
            "candidate_id_asc",
        ]
    ):
        raise ExperimentError("Experiment policy changed the bounded catalog or evaluation scope")
    for key in (
        "minimum_absolute_ap_gain",
        "minimum_absolute_daily_customer_precision_at_100_gain",
    ):
        if (
            not isinstance(gate[key], (int, float))
            or not np.isfinite(gate[key])
            or not 0 < gate[key] <= 1
        ):
            raise ExperimentError("Expected finite positive development gate gains")
    return policy


def reference_model(policy, manifest, features, scores, root):
    uri = policy["reference"]["model_uri"]
    if not (root / "mlflow.db").is_file() or not uri.startswith(("runs:/", "models:/")):
        raise ExperimentError("Expected the existing local reference model store")
    with tracking.local_tracking(root), TemporaryDirectory(prefix="fraud-reference-") as temporary:
        model, path = tracking.download_pipeline(uri, Path(temporary))
        metadata = mlflow.models.Model.load(path / "MLmodel")
        owner = uri.split("/")[1] if uri.startswith("runs:/") else metadata.run_id
        run = mlflow.MlflowClient().get_run(owner)
        expected = {
            "baseline_run_id": manifest["run_id"],
            "model_id": "hist_gradient_boosting",
            "source_commit": manifest["source"]["commit"],
            "gold_manifest_sha256": manifest["gold_manifest_sha256"],
        }
        if manifest["version"] == "baseline_v1":
            expected["baseline_manifest_sha256"] = policy["reference"]["baseline_manifest_sha256"]
        if (
            run.info.status != "FINISHED"
            or metadata.run_id not in (None, owner)
            or any(run.data.tags.get(k) != v for k, v in expected.items())
            or len(model.steps) != 1
            or type(model[-1]) is not HistGradientBoostingClassifier
            or any(
                model[-1].get_params().get(k) != v
                for k, v in baseline.MODEL_PARAMETERS["hist_gradient_boosting"].items()
            )
        ):
            raise ExperimentError("Reference model ownership or fixed parameters mismatch")
        persistence.check_scores(model.predict_proba(features), scores)
        return {
            "mlflow_run_id": owner,
            "model_sha256": sha256(path / metadata.flavors["sklearn"]["pickled_model"]),
            "mlmodel_sha256": sha256(path / "MLmodel"),
            "serialization_format": "skops",
        }


def verified_checkpoint(part, definition, features, reference, policy, identity):
    checkpoint = json.loads((part / "checkpoint.json").read_text())
    if checkpoint["identity"] != identity or checkpoint["candidate_id"] != definition["id"]:
        raise ExperimentError("Resume checkpoint identity mismatch")
    names = ["model.skops", "metrics.json", "validation_predictions.parquet"]
    if [f["path"] for f in checkpoint["files"]] != names:
        raise ExperimentError("Incomplete candidate checkpoint")
    check_records(part, checkpoint["files"])
    predictions = pd.read_parquet(part / names[2])
    measurement = json.loads((part / "metrics.json").read_text())
    if (
        not predictions[list(METADATA_DTYPES)].equals(reference[list(METADATA_DTYPES)])
        or measurement["feature_columns"] != list(features.columns)
        or measurement["configuration"]
        != baseline.load_configuration(baseline.DEFAULT_CONFIG)["models"]["hist_gradient_boosting"]
        or measurement["fit_rows"] != policy["data_policy"]["train_rows"]
        or measurement["metrics"]
        != evaluate_ranking(predictions.TX_FRAUD, predictions.SCORE, predictions)
    ):
        raise ExperimentError("Candidate checkpoint metadata or metrics mismatch")
    model = persistence.load_pipeline(part / "model.skops", feature_columns=list(features.columns))
    persistence.check_scores(model.predict_proba(features), predictions.SCORE)
    return model, predictions, measurement, checkpoint


def check_tracked_checkpoint(record, definition, features, scores, identity, run_id, root):
    with (
        tracking.local_tracking(root),
        TemporaryDirectory(prefix="fraud-candidate-check-") as temporary,
    ):
        run = mlflow.MlflowClient().get_run(record["run_id"])
        expected = {
            "experiment_run_id": run_id,
            "model_id": definition["id"],
            "protocol_sha256": identity["protocol_sha256"],
            "gold_manifest_sha256": identity["gold_manifest_sha256"],
        }
        model, path = tracking.download_pipeline(
            record["model_uri"], Path(temporary), feature_columns=list(features.columns)
        )
        metadata = mlflow.models.Model.load(path / "MLmodel")
        owner = (
            record["model_uri"].split("/")[1]
            if record["model_uri"].startswith("runs:/")
            else metadata.run_id
        )
        if (
            run.info.status != "FINISHED"
            or owner != record["run_id"]
            or metadata.run_id not in (None, owner)
            or any(run.data.tags.get(k) != v for k, v in expected.items())
        ):
            raise ExperimentError("Tracked checkpoint ownership or identity mismatch")
        persistence.check_scores(model.predict_proba(features), scores)


def recover_finished_run(name, run_id, root):
    with tracking.local_tracking(root):
        client = mlflow.MlflowClient()
        experiment = client.get_experiment_by_name(EXPERIMENT)
        if experiment is None:
            return None
        runs = client.search_runs(
            [experiment.experiment_id],
            filter_string=f"tags.experiment_run_id = '{run_id}' AND tags.model_id = '{name}' AND attributes.status = 'FINISHED'",
            max_results=2,
        )
        if len(runs) > 1:
            raise ExperimentError("Duplicate finished runs require investigation")
        if not runs:
            return None
        uri = runs[0].data.tags.get("logged_model_uri")
        if not uri:
            raise ExperimentError("Finished run lacks its checked model receipt")
        return {"run_id": runs[0].info.run_id, "model_uri": uri}


def output_names(policy):
    names = [
        "protocol.json",
        "reference_predictions.parquet",
        "summary.csv",
        "daily.csv",
        "leave_one_day_out.csv",
        "report.json",
    ]
    return names + [
        f"models/{c['id']}/{name}"
        for c in policy["candidates"]
        for name in (
            "model.skops",
            "metrics.json",
            "validation_predictions.parquet",
            "checkpoint.json",
        )
    ]


def verify_experiments(directory):
    """Verify hashes and recompute effects offline, without deserializing models."""
    manifest = json.loads((directory / "manifest.json").read_text())
    policy = load_policy(directory / "protocol.json")
    names = output_names(policy)
    actual = {str(p.relative_to(directory)) for p in directory.rglob("*") if p.is_file()}
    if (
        manifest["version"] != VERSION
        or [f["path"] for f in manifest["files"]] != names
        or actual != set(names + ["manifest.json", "state.json"])
    ):
        raise ExperimentError("Incomplete or unexpected experiment outputs")
    check_records(directory, manifest["files"])
    reference = pd.read_parquet(directory / "reference_predictions.parquet")
    candidates = {
        c["id"]: pd.read_parquet(directory / "models" / c["id"] / "validation_predictions.parquet")
        for c in policy["candidates"]
    }
    summary, daily, influence, decision = compare_predictions(reference, candidates, policy)
    for filename, expected in zip(
        ("summary.csv", "daily.csv", "leave_one_day_out.csv"),
        (summary, daily, influence),
        strict=True,
    ):
        pd.testing.assert_frame_equal(
            pd.read_csv(directory / filename), expected, check_dtype=False, rtol=1e-12, atol=1e-12
        )
    report = json.loads((directory / "report.json").read_text())
    if (
        report["run_id"] != manifest["run_id"]
        or report["status"] != "success"
        or any(
            report[k] is not False
            for k in (
                "test_evaluated",
                "refit_on_validation",
                "hypothesis_test",
                "confidence_intervals",
            )
        )
        or any(report[k] != v for k, v in decision.items())
        or report["identity"]["protocol_sha256"] != sha256(directory / "protocol.json")
    ):
        raise ExperimentError("Experiment decision or evaluation scope mismatch")
    return {
        "experiment_path": str(directory),
        "run_id": manifest["run_id"],
        "verified_outputs": len(names),
        **decision,
        "test_evaluated": False,
        "status": "success",
    }


def run_experiments(
    directory,
    *,
    policy_path=DEFAULT_POLICY,
    gold_root=DEFAULT_GOLD,
    tracking_root=tracking.DEFAULT_TRACKING,
    output_root=DEFAULT_OUTPUT_ROOT,
    resume=None,
    **baseline_options,
):
    policy = load_policy(policy_path)
    provenance = committed_inputs(policy_path)
    checked = baseline.verify_baseline(directory, **baseline_options)
    manifest = json.loads((directory / "manifest.json").read_text())
    ref = policy["reference"]
    if (
        manifest["run_id"] != ref["baseline_run_id"]
        or sha256(directory / "manifest.json") != ref["baseline_manifest_sha256"]
    ):
        raise ExperimentError("The declared reference baseline does not match")
    for key, package in (("sklearn", "scikit-learn"), ("numpy", "numpy"), ("pandas", "pandas")):
        if manifest["environment"][key] != version(package):
            raise ExperimentError("Restore reference package versions; do not silently refit")
    verified = verify_gold(
        gold_root, **{k: v for k, v in baseline_options.items() if k != "config_path"}
    )
    gold_dir = Path(verified["gold_path"])
    gold_manifest = json.loads((gold_dir / "manifest.json").read_text())
    partitions = [f for f in gold_manifest["files"] if f["split"] in ("train", "validation")]
    if (
        sha256(gold_dir / "manifest.json") != manifest["gold_manifest_sha256"]
        or partitions != manifest["input_partitions"]
    ):
        raise ExperimentError("Gold lineage differs from reference inputs")
    X_train, y_train, meta_train = candidate_training.load_split(gold_dir, gold_manifest, "train")
    X_val, y_val, meta_val = candidate_training.load_split(gold_dir, gold_manifest, "validation")
    reference = pd.read_parquet(
        directory / "models/hist_gradient_boosting/validation_predictions.parquet"
    )
    data = policy["data_policy"]
    if (
        not meta_val.equals(reference[list(METADATA_DTYPES)])
        or len(X_train) != data["train_rows"]
        or len(X_val) != data["validation_rows"]
        or meta_val.TX_DATETIME.dt.normalize().nunique() != data["validation_days"]
        or set(y_train.unique()) != {0, 1}
        or set(y_val.unique()) != {0, 1}
        or set(meta_train.TRANSACTION_ID) & set(meta_val.TRANSACTION_ID)
        or meta_train.LABEL_AVAILABLE_AT.max() >= meta_val.TX_DATETIME.min().normalize()
        or not np.isfinite(X_train.to_numpy()).all()
        or not np.isfinite(X_val.to_numpy()).all()
    ):
        raise ExperimentError("Experiment rows, temporal scope or feature values mismatch")
    for key in ("average_precision", "daily_customer_precision_at_100"):
        if not np.isclose(
            checked["comparison"]["hist_gradient_boosting"][key], ref[key], rtol=1e-12, atol=1e-12
        ):
            raise ExperimentError("Declared reference metrics differ from validation evidence")
    identity = {
        "protocol_sha256": sha256(policy_path),
        "gold_manifest_sha256": sha256(gold_dir / "manifest.json"),
        "baseline_manifest_sha256": sha256(directory / "manifest.json"),
        "tracking_root": str(tracking_root.resolve()),
        "reference_model_uri": ref["model_uri"],
        "provenance": provenance,
        "environment": {
            "python": sys.version.split()[0],
            **{
                p: version(p)
                for p in ("scikit-learn", "numpy", "pandas", "mlflow", "skops", "pyarrow")
            },
        },
    }
    parent = output_root / manifest["source"]["commit"]
    if resume is not None:
        if (
            resume.parent.name != VERSION
            or resume.parent.parent.name != manifest["source"]["commit"]
            or UUID(resume.name).hex != resume.name
        ):
            raise ExperimentError("Resume requires an existing experiment UUID directory")
        parent = resume.parent.parent
    destination = resume if resume is not None else parent / VERSION / uuid4().hex
    destination.mkdir(parents=True, exist_ok=resume is not None)
    run_id = destination.name
    typer.echo(f"Experiment: {destination}")
    audit_path = parent / "runs" / f"experiment_{run_id}.json"
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    with audit_path.with_suffix(".lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ExperimentError("This experiment is already running") from exc
        state_path = destination / "state.json"
        if resume is None:
            shutil.copyfile(policy_path, destination / "protocol.json")
            reference.to_parquet(destination / "reference_predictions.parquet", index=False)
            state = {
                "version": VERSION,
                "run_id": run_id,
                "identity": identity,
                "status": "running",
                "attempts": [],
                "reference_files": file_records(
                    destination, ["protocol.json", "reference_predictions.parquet"]
                ),
            }
            write_json(state_path, state)
        else:
            state = json.loads(state_path.read_text())
            if state["identity"] != identity or state["version"] != VERSION:
                raise ExperimentError("Resume inputs, code, environment or policy changed")
            check_records(destination, state["reference_files"])
            if state["status"] == "success":
                return verify_experiments(destination)
        attempt = {
            "started_at_utc": datetime.now(UTC).isoformat(),
            "status": "running",
            "fits": [],
        }
        state["attempts"].append(attempt)
        state["status"] = "running"
        write_json(state_path, state)
        try:
            with threadpool_limits(limits=policy["budget"]["max_threads"]):
                reference_model(policy, manifest, X_val, reference.SCORE, tracking_root)
                predictions = {}
                for definition in policy["candidates"]:
                    name = definition["id"]
                    columns = [c for c in FEATURE_COLUMNS if c not in definition["drop_features"]]
                    features = X_val[columns]
                    part = destination / "models" / name
                    if not part.exists():
                        if (
                            sum(len(a["fits"]) for a in state["attempts"])
                            >= policy["budget"]["max_new_fits"]
                        ):
                            raise ExperimentError(
                                "Fit budget exhausted; investigate the interrupted fit"
                            )
                        attempt["fits"].append(name)
                        write_json(state_path, state)
                        typer.echo(f"Fitting {name} on train; scoring validation...")
                        model, scores, metrics, timings = candidate_training.fit_candidate(
                            "hist_gradient_boosting",
                            X_train[columns],
                            y_train,
                            features,
                            y_val,
                            meta_val,
                        )
                        measurement = {
                            "model_id": name,
                            "fit_split": "train",
                            "evaluation_split": "validation",
                            "fit_rows": len(y_train),
                            "configuration": manifest["config"]["models"][
                                "hist_gradient_boosting"
                            ],
                            "feature_columns": columns,
                            "metrics": metrics,
                            **timings,
                        }
                        with TemporaryDirectory(
                            prefix=".candidate-", dir=destination
                        ) as temporary:
                            stage = Path(temporary) / name
                            candidate_training.save_candidate(
                                stage, model, features, scores, meta_val, measurement
                            )
                            write_json(
                                stage / "checkpoint.json",
                                {
                                    "candidate_id": name,
                                    "identity": identity,
                                    "tracking": None,
                                    "files": file_records(
                                        stage,
                                        [
                                            "model.skops",
                                            "metrics.json",
                                            "validation_predictions.parquet",
                                        ],
                                    ),
                                },
                            )
                            part.parent.mkdir(exist_ok=True)
                            stage.rename(part)
                    model, predicted, measurement, checkpoint = verified_checkpoint(
                        part, definition, features, reference, policy, identity
                    )
                    record = checkpoint["tracking"]
                    if record is None:
                        record = recover_finished_run(name, run_id, tracking_root)
                        if record is not None:
                            check_tracked_checkpoint(
                                record,
                                definition,
                                features,
                                predicted.SCORE,
                                identity,
                                run_id,
                                tracking_root,
                            )
                    if record is None:
                        record = tracking.log_candidate(
                            model,
                            features,
                            predicted.SCORE,
                            name=name,
                            root=tracking_root,
                            experiment_name=EXPERIMENT,
                            parameters={
                                **measurement["configuration"]["parameters"],
                                "fit_rows": len(y_train),
                                "feature_count": len(columns),
                            },
                            metrics={
                                **{
                                    "validation_" + k: v
                                    for k, v in measurement["metrics"].items()
                                    if k != "daily_customer_metrics" and v is not None
                                },
                                "fit_seconds": measurement["fit_seconds"],
                                "predict_seconds": measurement["predict_seconds"],
                                "model_size_bytes": (part / "model.skops").stat().st_size,
                            },
                            tags={
                                "baseline_run_id": manifest["run_id"],
                                "experiment_run_id": run_id,
                                "source_commit": manifest["source"]["commit"],
                                "protocol_version": VERSION,
                                "protocol_sha256": identity["protocol_sha256"],
                                "gold_manifest_sha256": identity["gold_manifest_sha256"],
                                "git_revision": provenance["git_revision"],
                                "poetry_lock_sha256": provenance["files"]["poetry.lock"],
                                "reference_model_uri": ref["model_uri"],
                                "promotion_status": "not_promoted",
                                "score_semantics": "uncalibrated_ranking",
                            },
                            artifacts=(
                                destination / "protocol.json",
                                part / "metrics.json",
                                part / "validation_predictions.parquet",
                            ),
                        )
                    else:
                        check_tracked_checkpoint(
                            record,
                            definition,
                            features,
                            predicted.SCORE,
                            identity,
                            run_id,
                            tracking_root,
                        )
                        typer.echo(f"Reused {name}; no fit or new tracking run")
                    checkpoint["tracking"] = record
                    write_json(part / "checkpoint.json", checkpoint)
                    predictions[name] = predicted
                summary, daily, influence, decision = compare_predictions(
                    reference, predictions, policy
                )
                for name, frame in zip(
                    ("summary.csv", "daily.csv", "leave_one_day_out.csv"),
                    (summary, daily, influence),
                    strict=True,
                ):
                    frame.to_csv(destination / name, index=False, float_format="%.17g")
                if committed_inputs(policy_path) != provenance:
                    raise ExperimentError("Committed inputs changed during execution")
                check_records(gold_dir, partitions)
                baseline.verify_baseline(directory, **baseline_options)
                if sha256(gold_dir / "manifest.json") != identity["gold_manifest_sha256"]:
                    raise ExperimentError("Gold manifest changed during execution")
                write_json(
                    destination / "report.json",
                    {
                        "version": VERSION,
                        "run_id": run_id,
                        "status": "success",
                        "identity": identity,
                        "reference": {
                            k: v
                            for k, v in measured(reference).items()
                            if k != "daily_customer_metrics"
                        },
                        **decision,
                        "test_evaluated": False,
                        "refit_on_validation": False,
                        "hypothesis_test": False,
                        "confidence_intervals": False,
                        "tracking": {
                            n: json.loads(
                                (destination / "models" / n / "checkpoint.json").read_text()
                            )["tracking"]
                            for n in predictions
                        },
                    },
                )
                write_json(
                    destination / "manifest.json",
                    {
                        "version": VERSION,
                        "run_id": run_id,
                        "files": file_records(destination, output_names(policy)),
                    },
                )
                result = verify_experiments(destination)
            attempt["status"] = state["status"] = "success"
        except BaseException as exc:
            attempt["status"] = state["status"] = (
                "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
            )
            attempt["error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            attempt["finished_at_utc"] = datetime.now(UTC).isoformat()
            write_json(state_path, state)
            write_json(audit_path, {**state, "experiment_path": str(destination)})
    return {**result, "audit_path": str(audit_path)}


@app.command("run")
def run(
    directory: Annotated[Path, typer.Argument()],
    policy: Annotated[Path, typer.Option()] = DEFAULT_POLICY,
    gold_root: Annotated[Path, typer.Option()] = DEFAULT_GOLD,
    tracking_root: Annotated[Path, typer.Option()] = tracking.DEFAULT_TRACKING,
    output_root: Annotated[Path, typer.Option()] = DEFAULT_OUTPUT_ROOT,
    resume: Annotated[Path | None, typer.Option()] = None,
):
    """Fit the three declared ablations; retain the historical reference and sealed test."""
    try:
        result = run_experiments(
            directory,
            policy_path=policy,
            gold_root=gold_root,
            tracking_root=tracking_root,
            output_root=output_root,
            resume=resume,
        )
    except Exception as exc:
        typer.echo(f"Experiment failed: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(json.dumps(result, indent=2))


@app.command("verify")
def verify(directory: Annotated[Path, typer.Argument()]):
    """Recompute paired comparison and check output hashes without loading models."""
    try:
        result = verify_experiments(directory)
    except Exception as exc:
        typer.echo(f"Experiment verification failed: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(json.dumps(result, indent=2))


if __name__ == "__main__":
    app()
