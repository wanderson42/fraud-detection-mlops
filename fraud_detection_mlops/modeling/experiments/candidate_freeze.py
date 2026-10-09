"""Freeze the existing validation reference without opening the test or fitting."""

from datetime import UTC, datetime
from importlib.metadata import version
import json
from pathlib import Path
from typing import Annotated

import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits
import typer

from fraud_detection_mlops.artifacts import sha256, write_json
from fraud_detection_mlops.config import PROJECT_ROOT
from fraud_detection_mlops.data.datasets.gold_dataset import DEFAULT_GOLD
from fraud_detection_mlops.features.feature_schema import FEATURE_COLUMNS, METADATA_DTYPES
from fraud_detection_mlops.modeling.experiments import (
    baseline_policy as baseline,
)
from fraud_detection_mlops.modeling.experiments.experiment_artifacts import check_records

DEFAULT_POLICY = PROJECT_ROOT / "references/final_evaluation_protocol_v1.json"
DEFAULT_FREEZE = PROJECT_ROOT / "references/frozen_candidate_v1.json"
DEFAULT_TRACKING = PROJECT_ROOT / "data/tracking"
CHOICES = {
    "features": "all_19_gold_v1_in_contract_order",
    "model_parameters": "baseline_protocol_v1.hist_gradient_boosting",
    "preprocessor": None,
    "refit": False,
    "probability_calibration": False,
    "score_semantics": "uncalibrated_ranking",
    "population": "all_transactions_without_customer_blocking",
    "customer_score": "maximum_transaction_score_per_customer_day",
    "customer_label": "any_fraud_per_customer_day",
    "alert_budget": 100,
    "tie_break": "CUSTOMER_ID_ascending",
    "evaluation_clock": "retrospective_complete_day",
    "daily_aggregation": "unweighted_mean",
    "label_delay_days": 7,
}
app = typer.Typer(no_args_is_help=True)


class FreezeError(ValueError):
    """The declared candidate cannot be frozen with the current evidence."""


def load_policy(path: Path) -> dict:
    policy = json.loads(path.read_text())
    choices = policy["choices"]
    acceptance = policy["acceptance"]
    if (
        policy["schema_version"] != 1
        or policy["version"] != "final_evaluation_v1"
        or policy["reference"]["model_id"] != "hist_gradient_boosting"
        or choices != CHOICES
        or policy["holdout"]["split"] != "test"
        or policy["holdout"]["evaluate_after_freeze_commit"] is not True
        or policy["holdout"]["select_or_tune_on_test"] is not False
        or acceptance["scope"] != "offline_candidate_for_laboratory_serving"
        or acceptance["production_promotion"] is not False
        or policy["reporting"]["hypothesis_test"] is not False
        or policy["reporting"]["confidence_intervals"] is not False
        or policy["reporting"]["formal_superiority_claim"] is not False
    ):
        raise FreezeError("Unsupported final evaluation choices")
    for key in ("minimum_average_precision", "minimum_daily_customer_precision_at_100"):
        value = acceptance[key]
        if type(value) not in (int, float) or not np.isfinite(value) or not 0 < value <= 1:
            raise FreezeError("Expected finite acceptance targets in (0, 1]")
    return policy


def committed_inputs(path: Path) -> dict:
    # Reuse the existing Git/lockfile guard; no second provenance framework.
    from fraud_detection_mlops.modeling.experiments import (
        experiment_provenance as experiments,
    )

    try:
        return experiments.committed_inputs(path)
    except experiments.ExperimentError as exc:
        raise FreezeError("Commit the freeze policy and implementation before freezing") from exc


def reference_identity(policy, manifest, features, scores, tracking_root) -> dict:
    from fraud_detection_mlops.modeling.experiments.terminal_feature_ablation import (
        reference_model,
    )

    return reference_model(policy, manifest, features, scores, tracking_root)


def relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT.resolve()))
    except ValueError as exc:
        raise FreezeError("Freeze artifacts must be inside the project checkout") from exc


def verify_freeze(path: Path = DEFAULT_FREEZE, *, require_committed: bool = False) -> dict:
    """Verify receipt and bound files without reading Parquet or loading a model."""
    receipt = json.loads(path.read_text())
    if (
        receipt["schema_version"] != 1
        or receipt["version"] != "freeze_v1"
        or receipt["test_evaluated"] is not False
        or receipt["refit"] is not False
        or receipt["feature_columns"] != FEATURE_COLUMNS
    ):
        raise FreezeError("Unexpected frozen candidate scope")
    for name, expected in receipt["bound_files"].items():
        target = PROJECT_ROOT / name
        if relative(target) != name or not target.is_file() or sha256(target) != expected:
            raise FreezeError(f"Frozen input changed or missing: {name}")
    policy = load_policy(PROJECT_ROOT / receipt["policy_path"])
    if policy != receipt["policy"]:
        raise FreezeError("Frozen policy differs from committed policy")
    if require_committed:
        committed_inputs(path)
    return {
        "freeze_path": str(path),
        "freeze_sha256": sha256(path),
        "model_id": policy["reference"]["model_id"],
        "feature_count": len(FEATURE_COLUMNS),
        "test_evaluated": False,
        "require_committed": require_committed,
        "status": "success",
    }


def freeze_reference(
    directory: Path,
    experiment_path: Path,
    *,
    policy_path: Path = DEFAULT_POLICY,
    freeze_path: Path = DEFAULT_FREEZE,
    gold_root: Path = DEFAULT_GOLD,
    tracking_root: Path = DEFAULT_TRACKING,
    **baseline_options,
) -> dict:
    """Check validation parity and create an immutable receipt for the next stage."""
    from fraud_detection_mlops.modeling.experiments import (
        candidate_training,
    )
    from fraud_detection_mlops.modeling.experiments import (
        terminal_feature_ablation as experiments,
    )

    policy = load_policy(policy_path)
    provenance = committed_inputs(policy_path)
    if freeze_path.exists():
        if json.loads(freeze_path.read_text())["policy"] != policy:
            raise FreezeError("Existing freeze belongs to a different policy")
        return {**verify_freeze(freeze_path), "reused": True}
    checked = baseline.verify_baseline(directory, **baseline_options)
    manifest = json.loads((directory / "manifest.json").read_text())
    ref = policy["reference"]
    if (
        checked["selected_model"] != ref["model_id"]
        or manifest["run_id"] != ref["baseline_run_id"]
        or sha256(directory / "manifest.json") != ref["baseline_manifest_sha256"]
        or manifest["gold_manifest_sha256"] != ref["gold_manifest_sha256"]
    ):
        raise FreezeError("Reference baseline or Gold identity mismatch")
    experiment_files = {
        relative(experiment_path / name): sha256(experiment_path / name)
        for name in ("manifest.json", "report.json")
    }
    experiment = experiments.verify_experiments(experiment_path)
    report = json.loads((experiment_path / "report.json").read_text())
    if (
        experiment["run_id"] != ref["experiment_run_id"]
        or experiment["eligible_ranking"] != []
        or experiment["fallback"] != "retain_reference"
        or report["identity"]["baseline_manifest_sha256"] != ref["baseline_manifest_sha256"]
        or report["identity"]["gold_manifest_sha256"] != ref["gold_manifest_sha256"]
    ):
        raise FreezeError("Ablation evidence does not retain this reference")
    for key, package in (("sklearn", "scikit-learn"), ("numpy", "numpy"), ("pandas", "pandas")):
        if manifest["environment"][key] != version(package):
            raise FreezeError("Restore reference package versions; do not refit")
    gold_dir = gold_root / manifest["source"]["commit"] / "gold_v1"
    gold_manifest = json.loads((gold_dir / "manifest.json").read_text())
    holdout = policy["holdout"]
    if (
        sha256(gold_dir / "manifest.json") != ref["gold_manifest_sha256"]
        or gold_manifest["protocol"] != manifest["protocol"]
        or gold_manifest["feature_columns"] != FEATURE_COLUMNS
        or gold_manifest["split_rows"]["test"] != holdout["expected_rows"]
        or gold_manifest["protocol"]["windows"]["test"]
        != {k: holdout[k] for k in ("start", "end_exclusive")}
        or [f for f in gold_manifest["files"] if f["split"] in ("train", "validation")]
        != manifest["input_partitions"]
    ):
        raise FreezeError("Gold lineage, feature order or reserved window mismatch")
    # Check only validation bytes. No verify_gold call: it queries every split.
    partitions = [f for f in gold_manifest["files"] if f["split"] == "validation"]
    check_records(gold_dir, partitions)
    features, _, metadata = candidate_training.load_split(gold_dir, gold_manifest, "validation")
    predictions_path = directory / "models/hist_gradient_boosting/validation_predictions.parquet"
    predictions = pd.read_parquet(predictions_path)
    if (
        not metadata.equals(predictions[list(METADATA_DTYPES)])
        or not np.isfinite(features.to_numpy()).all()
    ):
        raise FreezeError("Validation population or feature values mismatch")
    with threadpool_limits(limits=4):
        model = reference_identity(policy, manifest, features, predictions.SCORE, tracking_root)
    files = {
        **provenance["files"],
        relative(policy_path): sha256(policy_path),
        relative(directory / "manifest.json"): ref["baseline_manifest_sha256"],
        **experiment_files,
        relative(gold_dir / "manifest.json"): ref["gold_manifest_sha256"],
        relative(predictions_path): next(
            f["sha256"]
            for f in manifest["files"]
            if f["path"] == "models/hist_gradient_boosting/validation_predictions.parquet"
        ),
    }
    files.update({relative(gold_dir / f["path"]): f["sha256"] for f in partitions})
    receipt = {
        "schema_version": 1,
        "version": "freeze_v1",
        "recorded_at_utc": datetime.now(UTC).isoformat(),
        "implementation_revision": provenance["git_revision"],
        "policy_path": relative(policy_path),
        "policy": policy,
        "feature_columns": FEATURE_COLUMNS,
        "model_parameters": manifest["config"]["models"][ref["model_id"]],
        "model": model,
        "model_uri": ref["model_uri"],
        "tracking_root": relative(tracking_root),
        "gold_path": relative(gold_dir),
        "source": manifest["source"],
        "environment": {
            p: version(p) for p in ("scikit-learn", "numpy", "pandas", "mlflow", "skops")
        },
        "validation_metrics": checked["comparison"][ref["model_id"]],
        "bound_files": files,
        "test_evaluated": False,
        "refit": False,
    }
    relative(freeze_path)
    for name, expected in files.items():
        if sha256(PROJECT_ROOT / name) != expected:
            raise FreezeError(f"Input changed during freeze: {name}")
    write_json(freeze_path, receipt, overwrite=False)
    return {**verify_freeze(freeze_path), "reused": False}


@app.command("build")
def build(
    directory: Annotated[Path, typer.Argument()],
    experiment_path: Annotated[Path, typer.Option()],
    policy_path: Annotated[Path, typer.Option()] = DEFAULT_POLICY,
    freeze_path: Annotated[Path, typer.Option()] = DEFAULT_FREEZE,
    gold_root: Annotated[Path, typer.Option()] = DEFAULT_GOLD,
    tracking_root: Annotated[Path, typer.Option()] = DEFAULT_TRACKING,
):
    try:
        result = freeze_reference(
            directory,
            experiment_path,
            policy_path=policy_path,
            freeze_path=freeze_path,
            gold_root=gold_root,
            tracking_root=tracking_root,
        )
        print(json.dumps(result, indent=2))
    except (ValueError, OSError, KeyError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc


@app.command("verify")
def verify(
    path: Annotated[Path, typer.Argument()] = DEFAULT_FREEZE,
    require_committed: Annotated[bool, typer.Option()] = False,
):
    try:
        print(json.dumps(verify_freeze(path, require_committed=require_committed), indent=2))
    except (ValueError, OSError, KeyError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc


if __name__ == "__main__":
    app()
