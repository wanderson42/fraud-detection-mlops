"""Validation-only inspection of an existing model; no fit or feature selection."""

from datetime import UTC, datetime
from importlib.metadata import version
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import Annotated
from uuid import uuid4

import mlflow
import numpy as np
import pandas as pd
from scipy.special import expit
import shap
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.inspection import permutation_importance
from threadpoolctl import threadpool_limits
import typer

from fraud_detection_mlops.artifacts import sha256, write_json
from fraud_detection_mlops.config import PROJECT_ROOT
from fraud_detection_mlops.features import FEATURE_COLUMNS, METADATA_DTYPES
from fraud_detection_mlops.gold import DEFAULT_GOLD
from fraud_detection_mlops.modeling import baseline, persistence, tracking
from fraud_detection_mlops.modeling.metrics import evaluate_ranking
from fraud_detection_mlops.modeling.train import load_split

VERSION = "diagnostics_v1"
app = typer.Typer(no_args_is_help=True)


class DiagnosticError(ValueError):
    """Inspection inputs or outputs do not reconcile with the frozen experiment."""


def daily_diagnostics(predictions: pd.DataFrame) -> pd.DataFrame:
    """Measure daily ranking and missed customers under the existing top-100 policy."""
    rows = []
    for day, group in predictions.groupby(predictions.TX_DATETIME.dt.strftime("%Y-%m-%d")):
        measured = evaluate_ranking(group.TX_FRAUD, group.SCORE, group)
        operational = measured.pop("daily_customer_metrics")[0]
        positive_customers = int(group.groupby("CUSTOMER_ID").TX_FRAUD.max().sum())
        found = operational["fraudulent_customers_in_alerts"]
        rows.append(
            {
                "date": day,
                **measured,
                **{k: v for k, v in operational.items() if k != "date"},
                "fraudulent_customers": positive_customers,
                "genuine_customers_in_alerts": operational["alerts"] - found,
                "missed_fraudulent_customers": positive_customers - found,
                "customer_recall_at_100": found / positive_customers
                if positive_customers
                else None,
            }
        )
    return pd.DataFrame(rows)


def explain_sample(model, features, metadata, scores, *, sample_size=1000, seed=42):
    """Uniform SHAP sample plus explicit illustrative cases, in HGB log-odds units."""
    if len(model.steps) != 1 or type(model[-1]) is not HistGradientBoostingClassifier:
        raise DiagnosticError("diagnostics_v1 SHAP supports the untransformed HGB pipeline only")
    uniform = np.sort(
        np.random.default_rng(seed).choice(len(features), sample_size, replace=False)
    )
    labels = metadata.TX_FRAUD.to_numpy()
    genuine, fraud = np.flatnonzero(labels == 0), np.flatnonzero(labels == 1)
    cases = {
        "highest_scored_genuine": int(genuine[np.argmax(scores[genuine])]),
        "lowest_scored_fraud": int(fraud[np.argmin(scores[fraud])]),
        "highest_scored_fraud": int(fraud[np.argmax(scores[fraud])]),
    }
    indices = np.unique(np.concatenate([uniform, list(cases.values())]))
    sample = features.iloc[indices]
    explainer = shap.TreeExplainer(
        model[-1], model_output="raw", feature_perturbation="tree_path_dependent"
    )
    explanation = explainer(sample, check_additivity=True)
    values = np.asarray(explanation.values, dtype="float64")
    base = np.broadcast_to(explanation.base_values, (len(indices),)).astype("float64")
    margins = model.decision_function(sample)
    if (
        values.shape != sample.shape
        or not np.isfinite(values).all()
        or not np.isfinite(base).all()
        or not np.allclose(base + values.sum(axis=1), margins, rtol=1e-7, atol=1e-7)
        or not np.allclose(expit(margins), scores[indices], rtol=1e-12, atol=1e-12)
    ):
        raise DiagnosticError("SHAP additivity or score reconstruction failed")
    is_uniform = np.isin(indices, uniform)
    summary = pd.DataFrame(
        {"feature": FEATURE_COLUMNS, "mean_abs_shap_log_odds": np.abs(values[is_uniform]).mean(0)}
    ).sort_values(["mean_abs_shap_log_odds", "feature"], ascending=[False, True])
    local = (
        metadata.iloc[indices]
        .reset_index(drop=True)
        .assign(
            SCORE=scores[indices],
            UNIFORM_SAMPLE=is_uniform,
            CASE=[";".join(k for k, v in cases.items() if v == i) for i in indices],
            BASE_LOG_ODDS=base,
            RAW_LOG_ODDS=margins,
        )
    )
    for column in FEATURE_COLUMNS:
        local[column] = sample[column].to_numpy()
    for i, column in enumerate(FEATURE_COLUMNS):
        local["SHAP_" + column] = values[:, i]
    case_position = int(np.flatnonzero(indices == cases["lowest_scored_fraud"])[0])
    return summary, local, explanation[case_position]


def load_validation(directory, manifest, gold_root):
    """Read only validation partitions and reconcile their identities and metadata."""
    gold_dir = gold_root / manifest["source"]["commit"] / "gold_v1"
    if sha256(gold_dir / "manifest.json") != manifest["gold_manifest_sha256"]:
        raise DiagnosticError("Gold manifest differs from the baseline input")
    gold_manifest = json.loads((gold_dir / "manifest.json").read_text())
    records = [f for f in gold_manifest["files"] if f["split"] == "validation"]
    if records != [f for f in manifest["input_partitions"] if f["split"] == "validation"]:
        raise DiagnosticError("Validation partitions differ from the baseline input")
    for item in records:
        path = gold_dir / item["path"]
        if (
            not path.resolve().is_relative_to(gold_dir.resolve())
            or path.stat().st_size != item["size_bytes"]
            or sha256(path) != item["sha256"]
        ):
            raise DiagnosticError("Gold validation partition integrity mismatch")
    features, _, metadata = load_split(gold_dir, {"files": records}, "validation")
    predictions = pd.read_parquet(
        directory / "models" / "hist_gradient_boosting" / "validation_predictions.parquet"
    )
    if (
        not metadata.equals(predictions[list(METADATA_DTYPES)])
        or not np.isfinite(features.to_numpy()).all()
    ):
        raise DiagnosticError("Gold validation rows do not match the saved predictions")
    return features, metadata, predictions.SCORE.to_numpy(), gold_dir, records


def save_plots(directory, permutations, shap_summary, case):
    """Two static figures; raw tables remain the source of numerical interpretation."""
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 2, figsize=(15, 7), layout="constrained")
    p = permutations.sort_values("ap_drop_mean")
    axes[0].barh(p.feature, p.ap_drop_mean, xerr=p.ap_drop_std, color="#27648c")
    axes[0].axvline(0, color="black", linewidth=0.6)
    axes[0].set(title="Permutation: validation AP drop", xlabel="AP drop (SD across shuffles)")
    s = shap_summary.sort_values("mean_abs_shap_log_odds")
    axes[1].barh(s.feature, s.mean_abs_shap_log_odds, color="#33816e")
    axes[1].set(title="SHAP: uniform validation sample", xlabel="Mean absolute SHAP (log-odds)")
    fig.savefig(directory / "importance.png", dpi=150)
    plt.close(fig)
    shap.plots.waterfall(case, max_display=12, show=False)
    plt.gcf().savefig(directory / "case_lowest_scored_fraud.png", dpi=150, bbox_inches="tight")
    plt.close(plt.gcf())


def run_diagnostics(
    directory: Path,
    model_uri: str,
    *,
    gold_root: Path = DEFAULT_GOLD,
    tracking_root: Path = tracking.DEFAULT_TRACKING,
    output_root: Path = PROJECT_ROOT / "data/processed/handbook",
    repeats: int = 5,
    sample_size: int = 1000,
    **baseline_options,
) -> dict:
    if not 2 <= repeats <= 10 or not 1 <= sample_size <= 2000:
        raise DiagnosticError("Use 2..10 permutation repeats and 1..2000 uniform SHAP rows")
    checked = baseline.verify_baseline(directory, **baseline_options)
    manifest = json.loads((directory / "manifest.json").read_text())
    for field, package in (("sklearn", "scikit-learn"), ("numpy", "numpy"), ("pandas", "pandas")):
        if manifest["environment"][field] != version(package):
            raise DiagnosticError(
                f"Use the baseline's {package} version; diagnostics do not refit"
            )
    digest = sha256(directory / "manifest.json")
    features, metadata, scores, gold_dir, records = load_validation(directory, manifest, gold_root)
    if sample_size > len(features):
        raise DiagnosticError("SHAP sample cannot exceed the validation population")
    if not (tracking_root / "mlflow.db").is_file() or not model_uri.startswith(
        ("runs:/", "models:/")
    ):
        raise DiagnosticError("Use an existing local MLflow database and runs:/ or models:/ URI")
    run_id = uuid4().hex
    parent = output_root / manifest["source"]["commit"]
    destination = parent / VERSION / run_id
    audit_path = parent / "runs" / f"diagnostics_{run_id}.json"
    audit = {
        "version": VERSION,
        "run_id": run_id,
        "baseline_run_id": manifest["run_id"],
        "baseline_manifest_sha256": digest,
        "model_uri": model_uri,
        "status": "running",
        "started_at_utc": datetime.now(UTC).isoformat(),
        "test_evaluated": False,
        "refit": False,
        "feature_selection": False,
        "hypothesis_test": False,
    }
    write_json(audit_path, audit)
    start = perf_counter()
    try:
        with TemporaryDirectory(prefix=".diagnostics-", dir=parent) as temporary:
            stage = Path(temporary) / "outputs"
            stage.mkdir()
            with tracking.local_tracking(tracking_root):
                model, model_dir = tracking.download_pipeline(model_uri, Path(temporary) / "model")
                model_meta = mlflow.models.Model.load(model_dir / "MLmodel")
                # save_model exports predating log_model have no run_id in MLmodel.
                owner_id = (
                    model_uri.split("/")[1]
                    if model_uri.startswith("runs:/")
                    else model_meta.run_id
                )
                if not owner_id or model_meta.run_id not in (None, owner_id):
                    raise DiagnosticError("Model URI and MLflow ownership do not reconcile")
                run = mlflow.MlflowClient().get_run(owner_id)
                expected_tags = {
                    "baseline_run_id": manifest["run_id"],
                    "model_id": "hist_gradient_boosting",
                    "source_commit": manifest["source"]["commit"],
                    "gold_manifest_sha256": manifest["gold_manifest_sha256"],
                }
                if run.info.status != "FINISHED" or any(
                    run.data.tags.get(k) != v for k, v in expected_tags.items()
                ):
                    raise DiagnosticError("MLflow run is not the completed baseline candidate")
                if (
                    manifest["version"] == "baseline_v1"
                    and run.data.tags.get("baseline_manifest_sha256") != digest
                ):
                    raise DiagnosticError(
                        "Historical MLflow export has a different baseline digest"
                    )
                model_sha = sha256(model_dir / model_meta.flavors["sklearn"]["pickled_model"])
            with threadpool_limits(limits=4):
                persistence.check_scores(model.predict_proba(features), scores)
                typer.echo("Validation scores reconciled; computing permutation AP and SHAP...")
                perm = permutation_importance(
                    model,
                    features,
                    metadata.TX_FRAUD,
                    scoring="average_precision",
                    n_repeats=repeats,
                    n_jobs=1,
                    random_state=42,
                    max_samples=1.0,
                )
                permutation = pd.DataFrame(
                    {
                        "feature": FEATURE_COLUMNS,
                        "ap_drop_mean": perm.importances_mean,
                        "ap_drop_std": perm.importances_std,
                        **{f"repeat_{i + 1}": perm.importances[:, i] for i in range(repeats)},
                    }
                ).sort_values(["ap_drop_mean", "feature"], ascending=[False, True])
                shap_summary, local, case = explain_sample(
                    model, features, metadata, scores, sample_size=sample_size
                )
            daily = []
            for name in baseline.CLASSIFIERS:
                predicted = pd.read_parquet(
                    directory / "models" / name / "validation_predictions.parquet"
                )
                daily.append(daily_diagnostics(predicted).assign(model_id=name))
            pd.concat(daily, ignore_index=True).to_csv(stage / "daily.csv", index=False)
            permutation.to_csv(stage / "permutation.csv", index=False)
            shap_summary.to_csv(stage / "shap_summary.csv", index=False)
            local.to_parquet(stage / "shap_values.parquet", index=False, compression="zstd")
            save_plots(stage, permutation, shap_summary, case)
            report = {
                **audit,
                "status": "success",
                "model_id": "hist_gradient_boosting",
                "baseline_selected_model": checked["selected_model"],
                "mlflow_run_id": run.info.run_id,
                "model_sha256": model_sha,
                "gold_manifest_sha256": manifest["gold_manifest_sha256"],
                "validation_rows": len(features),
                "validation_frauds": int(metadata.TX_FRAUD.sum()),
                "validation_average_precision": checked["comparison"]["hist_gradient_boosting"][
                    "average_precision"
                ],
                "seed": 42,
                "permutation_repeats": repeats,
                "permutation_rows": len(features),
                "uniform_shap_rows": sample_size,
                "total_explained_rows": len(local),
                "shap_output": "raw_log_odds",
                "shap_feature_perturbation": "tree_path_dependent",
                "environment": {
                    p: version(p) for p in ("shap", "mlflow", "scikit-learn", "numpy", "pandas")
                },
                "implementation_sha256": sha256(Path(__file__)),
                "poetry_lock_sha256": sha256(PROJECT_ROOT / "poetry.lock"),
                "elapsed_seconds": perf_counter() - start,
            }
            # Detect source changes before publishing any completed diagnostics.
            baseline.verify_baseline(directory, **baseline_options)
            if (
                sha256(directory / "manifest.json") != digest
                or any(sha256(gold_dir / f["path"]) != f["sha256"] for f in records)
                or sha256(gold_dir / "manifest.json") != manifest["gold_manifest_sha256"]
            ):
                raise DiagnosticError("Source artifacts changed during diagnostics")
            write_json(stage / "report.json", report)
            write_json(
                stage / "manifest.json",
                {
                    "version": VERSION,
                    "run_id": run_id,
                    "files": [
                        {"path": p.name, "size_bytes": p.stat().st_size, "sha256": sha256(p)}
                        for p in sorted(stage.iterdir())
                    ],
                },
            )
            verify_diagnostics(stage)
            destination.parent.mkdir(parents=True, exist_ok=True)
            stage.rename(destination)
        result = {
            "diagnostics_path": str(destination),
            "run_id": run_id,
            "verified_outputs": 7,
            "test_evaluated": False,
            "status": "success",
        }
        audit.update(status="success", result=result)
    except BaseException as exc:
        audit.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        audit["finished_at_utc"] = datetime.now(UTC).isoformat()
        write_json(audit_path, audit)
    return {**result, "audit_path": str(audit_path)}


def verify_diagnostics(directory: Path) -> dict:
    """Check diagnostic output bytes and scope; does not reload the source model."""
    manifest = json.loads((directory / "manifest.json").read_text())
    names = [
        "case_lowest_scored_fraud.png",
        "daily.csv",
        "importance.png",
        "permutation.csv",
        "report.json",
        "shap_summary.csv",
        "shap_values.parquet",
    ]
    files = manifest.get("files", [])
    if (
        manifest.get("version") != VERSION
        or [f.get("path") for f in files] != names
        or {p.name for p in directory.iterdir()} != set(names + ["manifest.json"])
    ):
        raise DiagnosticError("Incomplete or untracked diagnostic outputs")
    for item in files:
        p = directory / item["path"]
        if p.stat().st_size != item["size_bytes"] or sha256(p) != item["sha256"]:
            raise DiagnosticError(f"Diagnostic integrity mismatch: {p.name}")
    report = json.loads((directory / "report.json").read_text())
    if (
        report.get("run_id") != manifest.get("run_id")
        or report.get("status") != "success"
        or any(
            report.get(k) is not False
            for k in ("test_evaluated", "refit", "feature_selection", "hypothesis_test")
        )
    ):
        raise DiagnosticError("Diagnostic scope or report mismatch")
    return {
        "diagnostics_path": str(directory),
        "verified_outputs": len(files),
        "status": "success",
    }


@app.command("run")
def run(
    directory: Annotated[Path, typer.Argument()],
    model_uri: Annotated[str, typer.Option()],
    gold_root: Annotated[Path, typer.Option()] = DEFAULT_GOLD,
    tracking_root: Annotated[Path, typer.Option()] = tracking.DEFAULT_TRACKING,
    output_root: Annotated[Path, typer.Option()] = PROJECT_ROOT / "data/processed/handbook",
    repeats: Annotated[int, typer.Option(min=2, max=10)] = 5,
    sample_size: Annotated[int, typer.Option(min=1, max=2000)] = 1000,
):
    """Inspect the frozen HGB candidate using validation only."""
    try:
        result = run_diagnostics(
            directory,
            model_uri,
            gold_root=gold_root,
            tracking_root=tracking_root,
            output_root=output_root,
            repeats=repeats,
            sample_size=sample_size,
        )
    except Exception as exc:
        typer.echo(f"Diagnostics failed: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(json.dumps(result, indent=2))


@app.command("verify")
def verify(directory: Annotated[Path, typer.Argument()]):
    """Verify a saved diagnostic run offline."""
    try:
        result = verify_diagnostics(directory)
    except Exception as exc:
        typer.echo(f"Diagnostic verification failed: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(json.dumps(result, indent=2))


if __name__ == "__main__":
    app()
