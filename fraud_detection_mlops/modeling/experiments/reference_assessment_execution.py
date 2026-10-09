"""Audit the prespecified September reference assessment; never fit or open replay."""

from contextlib import contextmanager
from datetime import UTC, datetime
import fcntl
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import platform
from tempfile import TemporaryDirectory
from typing import Annotated

import pandas as pd
import pyarrow.parquet as pq
from threadpoolctl import threadpool_limits
import typer

from fraud_detection_mlops.artifacts import sha256, write_json
from fraud_detection_mlops.config import PROJECT_ROOT
from fraud_detection_mlops.data.datasets.reference_assessment_dataset import (
    build_assessment_features,
    load_assessment_features,
    silver_snapshot,
    window_days,
)
from fraud_detection_mlops.data.datasets.silver_dataset import DEFAULT_OUTPUT
from fraud_detection_mlops.evaluation.temporal_reference_metrics import assessment_measurements
from fraud_detection_mlops.features.feature_schema import FEATURE_COLUMNS, METADATA_DTYPES
from fraud_detection_mlops.modeling.contracts.model_interface import predict_scores
from fraud_detection_mlops.modeling.contracts.prediction_schema import PREDICTION_SCHEMA
from fraud_detection_mlops.modeling.contracts.temporal_assessment_protocol import (
    POLICY_PATH,
    POLICY_SHA256,
    load_assessment_protocol,
)
from fraud_detection_mlops.modeling.experiments.experiment_artifacts import (
    check_records,
    file_records,
)
from fraud_detection_mlops.modeling.experiments.final_holdout_evaluation import load_frozen_model
from fraud_detection_mlops.modeling.experiments.reference_temporal_assessment import (
    _committed_revision,
)

EXECUTION_PATH = Path("references/reference_assessment_execution_v1.json")
EXECUTION_SHA256 = "46b2d6e94c3e2fc39cf7f6f19ee0578fa03db87a2df371ffcdc8ccfdb4fa6919"
EXECUTION_VERSION = "reference_assessment_execution_v1"
DEFAULT_ASSESSMENT = PROJECT_ROOT / "data/processed/handbook"
app = typer.Typer(no_args_is_help=True)


def identity_hash(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def json_record(directory: Path, name: str):
    path = directory.resolve() / name
    if path.resolve() != path or not path.is_file():
        raise ValueError("Assessment records must be regular files inside the saved bundle")
    return json.loads(path.read_text())


def execution_context(project_root: Path) -> dict:
    """Require committed policy, implementation and environment before native access."""
    root = project_root.resolve()
    policy, records = load_assessment_protocol(root)
    if sha256(root / EXECUTION_PATH) != EXECUTION_SHA256:
        raise ValueError("Assessment execution policy changed; review a new version")
    execution = json.loads((root / EXECUTION_PATH).read_text())
    if execution["assessment_protocol_sha256"] != POLICY_SHA256:
        raise ValueError("Assessment execution is bound to a different statistical protocol")
    extra = [str(EXECUTION_PATH), *execution["required_committed_inputs"]]
    bound = {
        **policy,
        "bindings": {**policy["bindings"], **{name: {"path": name} for name in extra}},
    }
    revision = _committed_revision(root, bound)
    receipt = records["frozen_reference"]
    environment = {
        p: version(p)
        for p in (
            "mlflow",
            "numpy",
            "pandas",
            "scikit-learn",
            "skops",
            "duckdb",
            "pyarrow",
            "scipy",
        )
    }
    if any(environment.get(p) != v for p, v in receipt["environment"].items()):
        raise ValueError("Restore the frozen model's locked environment before assessment")
    names = {
        str(POLICY_PATH),
        "poetry.lock",
        *extra,
        *(r["path"] for r in policy["bindings"].values()),
        *(str(p.relative_to(root)) for p in (root / "fraud_detection_mlops").rglob("*.py")),
    }
    return {
        "policy": policy,
        "execution": execution,
        "receipt": receipt,
        "git_revision": revision,
        "identity": {
            "statistical_protocol_sha256": POLICY_SHA256,
            "execution_protocol_sha256": EXECUTION_SHA256,
            "frozen_reference_sha256": policy["bindings"]["frozen_reference"]["sha256"],
            "source_commit": policy["source_commit"],
            "implementation": {name: sha256(root / name) for name in sorted(names)},
            "environment": {"python": platform.python_version(), **environment},
            "tracking_root": str((root / receipt["tracking_root"]).resolve()),
        },
    }


@contextmanager
def assessment_lock(directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("Assessment is already running") from exc
        yield


def record_phase(directory: Path, state: dict, phase: str, **updates) -> None:
    state.update(updates, status=phase)
    state["events"].append(
        {
            "phase": phase,
            "attempt": state["attempts"],
            "at": datetime.now(UTC).isoformat(),
            **updates,
        }
    )
    write_json(directory / "access.json", state)


def report_for(policy: dict, receipt: dict, identity: dict, revision: str, metrics: dict) -> dict:
    return {
        "schema_version": 1,
        "version": EXECUTION_VERSION,
        "assessment_id": identity_hash(identity),
        "git_revision": revision,
        "source_commit": policy["source_commit"],
        "model_uri": receipt["model_uri"],
        "model_sha256": receipt["model"]["model_sha256"],
        "assessment_window": policy["windows"]["assessment"],
        "assessment_clock": policy["time_policy"]["assessment_labels_complete_before"],
        "metrics": metrics,
        "assessment_evaluated": True,
        "refit": False,
        "new_tracking_runs": 0,
        "candidate_confirmation_evaluated": False,
        "operational_replay_evaluated": False,
        "original_may_test_reopened": False,
        "generalization_confidence_interval": None,
        "formal_temporal_hypothesis_test": False,
        "formal_superiority_claim": False,
        "production_promotion": False,
        "status": "success",
    }


def verify_results(results: Path, state: dict) -> dict:
    """Recompute saved evidence; never consult source paths or load a model."""
    manifest = json_record(results, "manifest.json")
    if sha256(results / "manifest.json") != state.get("results_manifest_sha256"):
        raise ValueError("Saved assessment manifest differs from the access record")
    policy = json_record(results, "statistical_protocol.json")
    json_record(results, "execution_policy.json")
    if (
        sha256(results / "statistical_protocol.json") != POLICY_SHA256
        or sha256(results / "execution_policy.json") != EXECUTION_SHA256
    ):
        raise ValueError("Saved assessment policy differs from the fixed snapshots")
    days = window_days(policy["windows"]["assessment"])
    features = manifest["feature_files"]
    names = [f"features/{d}.parquet" for d in days] + [
        "statistical_protocol.json",
        "execution_policy.json",
        "frozen_reference.json",
        "predictions.parquet",
        "daily.json",
        "report.json",
        "source_snapshot.json",
    ]
    if (
        manifest.get("version") != EXECUTION_VERSION
        or manifest.get("identity", {}).get("execution") != state["execution_identity"]
        or manifest["identity"].get("silver") != state["silver_snapshot"]
        or [r["path"] for r in manifest["files"]] != names
        or {p.relative_to(results).as_posix() for p in results.rglob("*") if p.is_file()}
        != set(names + ["manifest.json"])
    ):
        raise ValueError("Saved assessment identity, outputs or access record differ")
    check_records(results, manifest["files"])
    if (
        sha256(results / "frozen_reference.json")
        != policy["bindings"]["frozen_reference"]["sha256"]
    ):
        raise ValueError("Saved frozen reference changed")
    receipt = json_record(results, "frozen_reference.json")
    snapshot = json_record(results, "source_snapshot.json")
    execution_identity = manifest["identity"]["execution"]
    if (
        execution_identity["statistical_protocol_sha256"] != POLICY_SHA256
        or execution_identity["execution_protocol_sha256"] != EXECUTION_SHA256
        or execution_identity["frozen_reference_sha256"]
        != policy["bindings"]["frozen_reference"]["sha256"]
        or execution_identity["source_commit"] != policy["source_commit"]
        or snapshot["source_commit"] != policy["source_commit"]
        or snapshot["inventory_sha256"]
        != execution_identity["implementation"]["references/handbook_source.json"]
        or snapshot["contract_sha256"]
        != execution_identity["implementation"]["references/silver_contract_v1.json"]
    ):
        raise ValueError("Saved assessment source or protocol bindings do not reconcile")
    expected_source_days = window_days(
        {
            "start": policy["windows"]["assessment_context"]["start"],
            "end_exclusive": policy["windows"]["assessment"]["end_exclusive"],
        }
    )
    if (
        snapshot != manifest["identity"]["silver"]
        or [r["input_filename"][:10] for r in snapshot["files"]] != expected_source_days
    ):
        raise ValueError("Saved assessment source coverage differs")
    if [r["path"] for r in snapshot["files"]] != [
        f"transactions/tx_date={day}/part-00000.parquet" for day in expected_source_days
    ]:
        raise ValueError("Saved Silver paths extend outside the declared assessment context")
    original_rows = {r["input_filename"][:10]: r["rows"] for r in snapshot["files"]}
    if any(r["rows"] != original_rows[r["date"]] for r in features):
        raise ValueError("Saved assessment Gold lost source rows")
    frame = load_assessment_features(results, features, policy)
    if not pq.ParquetFile(results / "predictions.parquet").schema_arrow.equals(
        PREDICTION_SCHEMA, check_metadata=False
    ):
        raise ValueError("Saved prediction schema differs")
    predictions = pd.read_parquet(results / "predictions.parquet")
    if not predictions[list(METADATA_DTYPES)].equals(frame[list(METADATA_DTYPES)]):
        raise ValueError("Saved predictions differ from the exact assessment population")
    metrics, daily = assessment_measurements(predictions, policy)
    expected = report_for(policy, receipt, manifest["identity"], manifest["git_revision"], metrics)
    if (
        json_record(results, "report.json") != expected
        or json_record(results, "daily.json") != daily
    ):
        raise ValueError("Saved assessment metrics or interpretation differ")
    return expected


def verify_assessment(directory: Path) -> dict:
    directory = Path(directory)
    try:
        state = json_record(directory, "access.json")
        if state.get("assessment_window_access_started") is not True:
            raise ValueError("Missing original assessment access record")
        results = directory.resolve() / "results"
        if results.resolve() != results:
            raise ValueError("Assessment results must stay inside the saved bundle")
        report = verify_results(results, state)
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Malformed saved assessment records") from exc
    return {**report, "assessment_path": str(directory.resolve())}


def execute_assessment(
    silver_root: Path = DEFAULT_OUTPUT,
    output_root: Path = DEFAULT_ASSESSMENT,
    *,
    project_root: Path | None = None,
) -> dict:
    root = PROJECT_ROOT if project_root is None else Path(project_root)
    context = execution_context(root)
    policy, receipt, execution = (context[k] for k in ("policy", "receipt", "execution"))
    directory = Path(output_root) / policy["source_commit"] / EXECUTION_VERSION
    with assessment_lock(directory):
        state_path = directory / "access.json"
        state = json_record(directory, "access.json") if state_path.exists() else None
        if state is not None and state["execution_identity"] != context["identity"]:
            raise ValueError(
                "Retry policy, implementation or environment differs; restore the recorded inputs"
            )
        if (directory / "results").exists():
            if state is None:
                raise ValueError("Missing original assessment access record")
            report = verify_assessment(directory)
            record_phase(directory, state, "complete")
            return {**report, "reused": True}
        snapshot = silver_snapshot(Path(silver_root), policy, root)
        if state is not None and state["silver_snapshot"] != snapshot:
            raise ValueError("Retry Silver inputs differ from the original assessment access")
        if state is None:
            state = {
                "version": EXECUTION_VERSION,
                "execution_identity": context["identity"],
                "silver_snapshot": snapshot,
                "attempts": 0,
                "events": [],
                "assessment_window_access_started": False,
            }
        state["attempts"] += 1
        record_phase(directory, state, "verifying_model", error_type=None)
        try:
            with TemporaryDirectory(prefix=".attempt-", dir=directory) as temporary:
                temporary = Path(temporary)
                model = load_frozen_model(receipt, temporary / "model", project_root=root)
                record_phase(
                    directory, state, "building_features", assessment_window_access_started=True
                )
                results = temporary / "results"
                features = build_assessment_features(
                    snapshot, policy, results / "features", execution["resources"]
                )
                frame = load_assessment_features(results, features, policy)
                record_phase(directory, state, "scoring")
                with threadpool_limits(limits=execution["resources"]["threads"]):
                    scores = predict_scores(model, frame[FEATURE_COLUMNS].astype("float64"))
                predictions = frame[list(METADATA_DTYPES)].copy()
                predictions["SCORE"] = scores
                predictions.to_parquet(
                    results / "predictions.parquet", index=False, compression="zstd", version="2.6"
                )
                metrics, daily = assessment_measurements(predictions, policy)
                identity = {"execution": context["identity"], "silver": snapshot}
                report = report_for(policy, receipt, identity, context["git_revision"], metrics)
                for target, original in (
                    ("statistical_protocol.json", POLICY_PATH),
                    ("execution_policy.json", EXECUTION_PATH),
                    (
                        "frozen_reference.json",
                        Path(policy["bindings"]["frozen_reference"]["path"]),
                    ),
                ):
                    (results / target).write_bytes((root / original).read_bytes())
                for target, value in (
                    ("report.json", report),
                    ("daily.json", daily),
                    ("source_snapshot.json", snapshot),
                ):
                    json.dumps(value, allow_nan=False)
                    write_json(results / target, value)
                if execution_context(root)["identity"] != context["identity"]:
                    raise ValueError("Committed assessment inputs changed during execution")
                names = [r["path"] for r in features] + [
                    "statistical_protocol.json",
                    "execution_policy.json",
                    "frozen_reference.json",
                    "predictions.parquet",
                    "daily.json",
                    "report.json",
                    "source_snapshot.json",
                ]
                write_json(
                    results / "manifest.json",
                    {
                        "version": EXECUTION_VERSION,
                        "identity": identity,
                        "git_revision": context["git_revision"],
                        "feature_files": features,
                        "files": file_records(results, names),
                    },
                )
                record_phase(
                    directory,
                    state,
                    "publishing",
                    results_manifest_sha256=sha256(results / "manifest.json"),
                )
                verify_results(results, state)
                results.rename(directory / "results")
            record_phase(directory, state, "complete")
            return {**report, "assessment_path": str(directory.resolve()), "reused": False}
        except Exception as exc:
            record_phase(directory, state, "failed", error_type=type(exc).__name__)
            raise


@app.callback()
def assessment_execution_commands():
    """Run consumes 2-15 September; verify uses only saved results."""


@app.command("run")
def run_command(
    silver_root: Annotated[Path, typer.Option()] = DEFAULT_OUTPUT,
    output_root: Annotated[Path, typer.Option()] = DEFAULT_ASSESSMENT,
):
    try:
        report = execute_assessment(silver_root, output_root)
    except (ValueError, OSError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(json.dumps(report, indent=2, allow_nan=False))


@app.command("verify")
def verify_command(directory: Annotated[Path, typer.Argument()]):
    try:
        report = verify_assessment(directory)
    except (ValueError, OSError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(json.dumps(report, indent=2, allow_nan=False))


if __name__ == "__main__":
    app()
