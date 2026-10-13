"""Freeze a label-free capacity threshold using only identified development Gold."""

import argparse
from contextlib import contextmanager
import fcntl
import json
import logging
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

import pandas as pd
from threadpoolctl import threadpool_limits

from fraud_detection_mlops.artifacts import sha256, write_json
from fraud_detection_mlops.config import PROJECT_ROOT
from fraud_detection_mlops.data.datasets.development_scoring_dataset import (
    DEVELOPMENT_RUN_ID,
    check_selected_bytes,
    development_snapshot,
    load_scoring_partition,
    regular_file,
)
from fraud_detection_mlops.features.feature_schema import FEATURE_COLUMNS
from fraud_detection_mlops.modeling.contracts.model_interface import predict_scores
from fraud_detection_mlops.serving.model_release import load_release
from fraud_detection_mlops.streaming.contracts.customer_day_alerts import CustomerDayAlertConfig
from fraud_detection_mlops.streaming.decisions.development_threshold_selection import (
    SCORING_COLUMNS,
    select_development_threshold,
    selection_days,
)

VERSION = "development_alert_threshold_v1"
LOGGER = logging.getLogger(__name__)
PROTOCOL_SHA256 = "a932d674509444665cbafb093225c6255ce6f0b0ef109cc6a3b2dcac3af43cc3"
SOURCE_FILES = (
    "fraud_detection_mlops/artifacts.py",
    "fraud_detection_mlops/streaming/preparation/development_alert_threshold.py",
    "fraud_detection_mlops/streaming/decisions/development_threshold_selection.py",
    "fraud_detection_mlops/streaming/contracts/customer_day_alerts.py",
    "fraud_detection_mlops/data/datasets/development_scoring_dataset.py",
    "fraud_detection_mlops/modeling/experiments/temporal_development_data.py",
    "fraud_detection_mlops/serving/model_release.py",
    "fraud_detection_mlops/serving/request_schema.py",
    "fraud_detection_mlops/integrations/skops_persistence.py",
    "fraud_detection_mlops/modeling/contracts/model_interface.py",
    "fraud_detection_mlops/features/feature_schema.py",
    "fraud_detection_mlops/features/feature_validation.py",
    "references/causal_replay_protocol_v1.json",
    "references/hgb_optuna_protocol_v1.json",
    "poetry.lock",
)


def protocol(project_root: Path) -> dict:
    path = regular_file(project_root / "references/causal_replay_protocol_v1.json")
    if sha256(path) != PROTOCOL_SHA256:
        raise ValueError(
            "Commit/review a separate protocol version before changing selection rules"
        )
    return json.loads(path.read_text())


def committed_selection_inputs(project_root: Path) -> dict:
    subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", *SOURCE_FILES],
        cwd=project_root,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "diff", "--quiet", "HEAD", "--", *SOURCE_FILES],
        cwd=project_root,
        check=True,
        capture_output=True,
    )
    return {
        "git_revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=project_root, text=True
        ).strip(),
        "files": {name: sha256(regular_file(project_root / name)) for name in SOURCE_FILES},
    }


@contextmanager
def publication_lock(output: Path):
    output.parent.mkdir(parents=True, exist_ok=True)
    lock = output.parent / ".development-threshold.lock"
    if lock.is_symlink():
        raise ValueError("Threshold publication lock cannot be redirected")
    with lock.open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("Another threshold preparation is using this output parent") from exc
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def prepare(
    development: Path, release: Path, output: Path, project_root: Path = PROJECT_ROOT
) -> dict:
    policy = protocol(project_root)
    provenance = committed_selection_inputs(project_root)
    output = output.absolute()
    if output.exists() or output.is_symlink():
        raise ValueError(
            "Threshold output already exists; preserve it and run verify with its digest"
        )
    if output.resolve() != output or any(
        output.is_relative_to(path.absolute()) for path in (development, release)
    ):
        raise ValueError("Keep threshold output separate from input data and release")
    with publication_lock(output):
        if output.exists():
            raise ValueError("Threshold output already exists; no overwrite")
        snapshot = development_snapshot(development, project_root, policy["source_commit"])
        check_selected_bytes(snapshot)
        reference = policy["reference"]
        with threadpool_limits(limits=4):
            runtime = load_release(release, reference["manifest_sha256"])
            if (
                runtime.manifest.model_sha256 != reference["model_sha256"]
                or runtime.manifest.serving_release_id != reference["serving_release_id"]
                or runtime.manifest.evidence_kind != "verified_reference"
            ):
                raise ValueError(
                    "Use the identified frozen April reference, never an Optuna fold refit"
                )
            frames = []
            for record in snapshot["files"]:
                LOGGER.info("Scoring authorized development day %s", record["date"])
                frame = load_scoring_partition(snapshot, record)
                result = frame[["TRANSACTION_ID", "CUSTOMER_ID", "TX_DATETIME"]].copy()
                result["score"] = predict_scores(runtime.pipeline, frame[FEATURE_COLUMNS])
                frames.append(result)
        scores = pd.concat(frames, ignore_index=True)
        selection = select_development_threshold(scores)
        config = CustomerDayAlertConfig(
            schema_version=1,
            version="first_crossing_customer_day_v1",
            model_sha256=reference["model_sha256"],
            serving_release_id=reference["serving_release_id"],
            score_threshold=selection["score_threshold"],
            daily_customer_budget=100,
        )
        check_selected_bytes(snapshot)
        if (
            committed_selection_inputs(project_root)["files"] != provenance["files"]
            or sha256(regular_file(release / "manifest.json")) != reference["manifest_sha256"]
            or sha256(regular_file(release / "pipeline.skops")) != reference["model_sha256"]
        ):
            raise ValueError("Code, development or release changed during selection")
        report = {
            "version": VERSION,
            "status": "success",
            **selection,
            "policy_sha256": config.policy_sha256,
            "refit": False,
            "current_labels_used": False,
            "native_replay_evaluated": False,
            "production_promotion": False,
        }
        with TemporaryDirectory(prefix=".threshold-preparing-", dir=output.parent) as temporary:
            stage = Path(temporary) / "result"
            stage.mkdir()
            scores.to_parquet(
                stage / "scores.parquet", index=False, compression="zstd", version="2.6"
            )
            write_json(stage / "policy.json", config.model_dump())
            write_json(stage / "report.json", report)
            files = [
                {"path": name, "sha256": sha256(stage / name)}
                for name in ("scores.parquet", "policy.json", "report.json")
            ]
            write_json(
                stage / "manifest.json",
                {
                    "version": VERSION,
                    "schema_version": 1,
                    "protocol_sha256": PROTOCOL_SHA256,
                    "source_commit": policy["source_commit"],
                    "inputs": snapshot,
                    "reference": reference,
                    "provenance": provenance,
                    "runtime_environment": runtime.manifest.runtime_environment,
                    "files": files,
                },
            )
            digest = sha256(stage / "manifest.json")
            verify(stage, digest, project_root)
            if output.exists():
                raise ValueError("Output appeared during selection; no overwrite")
            stage.rename(output)
        return {**report, "result_path": str(output), "manifest_sha256": digest}


def verify(directory: Path, manifest_sha256: str, project_root: Path = PROJECT_ROOT) -> dict:
    policy = protocol(project_root)
    path = regular_file(directory / "manifest.json")
    if sha256(path) != manifest_sha256:
        raise ValueError("Threshold manifest integrity mismatch")
    manifest = json.loads(path.read_text())
    inputs = manifest["inputs"]
    if (
        manifest.get("version") != VERSION
        or manifest.get("schema_version") != 1
        or manifest.get("protocol_sha256") != PROTOCOL_SHA256
        or manifest.get("source_commit") != policy["source_commit"]
        or manifest.get("reference") != policy["reference"]
        or inputs.get("run_id") != DEVELOPMENT_RUN_ID
        or [r["date"] for r in inputs["files"]] != selection_days()
        or [r["path"] for r in inputs["files"]]
        != [f"transactions/{day}.parquet" for day in selection_days()]
        or [r["path"] for r in manifest["files"]]
        != ["scores.parquet", "policy.json", "report.json"]
    ):
        raise ValueError("Threshold receipt identity, scope or coverage differs")
    for record in manifest["files"]:
        if sha256(regular_file(directory / record["path"])) != record["sha256"]:
            raise ValueError("Threshold result artifact integrity mismatch")
    scores = pd.read_parquet(directory / "scores.parquet")
    if list(scores.columns) != SCORING_COLUMNS:
        raise ValueError("Saved selection scores must contain no extra columns or labels")
    selection = select_development_threshold(scores)
    config = CustomerDayAlertConfig.model_validate_json((directory / "policy.json").read_bytes())
    expected = CustomerDayAlertConfig(
        schema_version=1,
        version="first_crossing_customer_day_v1",
        model_sha256=policy["reference"]["model_sha256"],
        serving_release_id=policy["reference"]["serving_release_id"],
        score_threshold=selection["score_threshold"],
        daily_customer_budget=100,
    )
    report = json.loads((directory / "report.json").read_text())
    if (
        config != expected
        or report
        != {
            "version": VERSION,
            "status": "success",
            **selection,
            "policy_sha256": config.policy_sha256,
            "refit": False,
            "current_labels_used": False,
            "native_replay_evaluated": False,
            "production_promotion": False,
        }
        or scores.TX_DATETIME.dt.strftime("%Y-%m-%d").value_counts().to_dict()
        != {r["date"]: r["rows"] for r in inputs["files"]}
    ):
        raise ValueError("Saved threshold does not reproduce its scores and capacity rule")
    return {**report, "result_path": str(directory.absolute()), "manifest_sha256": manifest_sha256}


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare_command = commands.add_parser("prepare")
    prepare_command.add_argument("development", type=Path)
    prepare_command.add_argument("--release", required=True, type=Path)
    prepare_command.add_argument("--output", required=True, type=Path)
    prepare_command.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    verify_command = commands.add_parser("verify")
    verify_command.add_argument("result", type=Path)
    verify_command.add_argument("--manifest-sha256", required=True)
    verify_command.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    args = parser.parse_args()
    try:
        result = (
            prepare(args.development, args.release, args.output, args.project_root)
            if args.command == "prepare"
            else verify(args.result, args.manifest_sha256, args.project_root)
        )
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Threshold preparation failed: {exc}\n")
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
