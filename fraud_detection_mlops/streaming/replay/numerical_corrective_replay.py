"""Versioned numerical correction from immutable replay events; preserve the failed ledger."""

import argparse
from contextlib import closing
from importlib.metadata import version
import json
import logging
from pathlib import Path
import resource
import shutil
import sqlite3
import subprocess
from tempfile import TemporaryDirectory
import time
from uuid import uuid4

import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

from fraud_detection_mlops.artifacts import sha256, write_json
from fraud_detection_mlops.config import PROJECT_ROOT
from fraud_detection_mlops.data.datasets.development_scoring_dataset import regular_file
from fraud_detection_mlops.data.datasets.native_replay_dataset import (
    readonly_connection,
    stream_events,
    validate_event_stream,
)
from fraud_detection_mlops.data.datasets.reference_assessment_dataset import window_days
from fraud_detection_mlops.features.deterministic_causal_history import deterministic_features
from fraud_detection_mlops.features.exact_binary_mean import NUMERICAL_VERSION
from fraud_detection_mlops.features.feature_schema import FEATURE_COLUMNS
from fraud_detection_mlops.modeling.contracts.model_interface import predict_scores
from fraud_detection_mlops.serving.model_release import runtime_environment
from fraud_detection_mlops.streaming.contracts.customer_day_alerts import CustomerDayAlertConfig
from fraud_detection_mlops.streaming.contracts.native_replay_execution import (
    VERSION as ORIGINAL_VERSION,
)
from fraud_detection_mlops.streaming.contracts.native_replay_execution import (
    committed_inputs,
    execution_protocol,
    identified_runtime,
)
from fraud_detection_mlops.streaming.evaluation.native_triage_assessment import (
    ledger_frame,
    triage_quality,
)
from fraud_detection_mlops.streaming.features.deterministic_feature_state import (
    DeterministicTriageReplay,
)
from fraud_detection_mlops.streaming.replay.durable_triage_replay import (
    inspect_checkpoint,
    source_identity,
)
from fraud_detection_mlops.streaming.replay.native_causal_replay import digest_record, job_lock

VERSION = "native_causal_replay_numerical_v1"
PROTOCOL_PATH = "references/native_causal_replay_numerical_v1.json"
PROTOCOL_SHA256 = "f5fd8b9f93abb0d8051db365d7b15a96d5f759a94d9f75b3638e050b6f07938c"
LOGGER = logging.getLogger(__name__)


def correction_protocol(root: Path) -> dict:
    path = regular_file(root / PROTOCOL_PATH)
    if sha256(path) != PROTOCOL_SHA256:
        raise ValueError("Numerical correction protocol changed")
    return json.loads(path.read_text())


def correction_inputs(root: Path) -> dict:
    result = committed_inputs(root)
    subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", PROTOCOL_PATH],
        cwd=root,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "diff", "--quiet", "HEAD", "--", PROTOCOL_PATH],
        cwd=root,
        check=True,
        capture_output=True,
    )
    result["files"][PROTOCOL_PATH] = sha256(regular_file(root / PROTOCOL_PATH))
    return result


def separate_output(output: Path, *inputs: Path) -> Path:
    output = output.absolute()
    if output.resolve() != output:
        raise ValueError("Correction output cannot be redirected")
    for source in inputs:
        source = source.resolve()
        if output.is_relative_to(source) or source.is_relative_to(output):
            raise ValueError("Correction output must be separate from original artifacts")
    return output


def original_input(directory: Path, digest: str, checkpoint: Path, root: Path) -> dict:
    rule = correction_protocol(root)
    if digest != rule["original_input_manifest_sha256"]:
        raise ValueError("Use the externally identified original input")
    path = regular_file(directory / "manifest.json")
    if sha256(path) != digest:
        raise ValueError("Original input manifest changed")
    manifest = json.loads(path.read_text())
    protocol = execution_protocol(root)
    days = window_days(protocol["windows"]["scored_replay"])
    names = ["events.sqlite", "oracle.parquet", *[f"features/{day}.parquet" for day in days]]
    if (
        manifest["version"] != ORIGINAL_VERSION
        or manifest["schema_version"] != 1
        or manifest["protocol"] != protocol
        or manifest["environment"] != {**runtime_environment(), "duckdb": version("duckdb")}
        or manifest["provenance"]["files"] != rule["original_provenance"]
        or manifest["provenance"]["git_revision"] != rule["original_git_revision"]
        or [r["path"] for r in manifest["files"]] != names
        or manifest["policy"]
        != json.loads((root / "references/customer_day_alert_policy_v1.json").read_text())
    ):
        raise ValueError("Original scope, environment, policy or implementation differs")
    for record in manifest["files"]:
        if sha256(regular_file(directory / record["path"])) != record["sha256"]:
            raise ValueError("Original input artifact changed")
    if sha256(regular_file(checkpoint)) != rule["original_checkpoint_sha256"]:
        raise ValueError("Original checkpoint changed; preserve the failed execution")
    summary = inspect_checkpoint(checkpoint)
    expected_identity = digest_record(
        {
            "input_manifest_sha256": digest,
            "protocol": protocol,
            "code": manifest["provenance"]["files"],
        }
    )
    if (
        any(summary["counts"][key] != value for key, value in manifest["counts"].items())
        or summary["counts"]["decisions"] != manifest["scored_rows"]
        or summary["counts"]["alert_decisions"] != manifest["scored_rows"]
        or summary["input_sha256"] != expected_identity
        or summary["run_id"] != "native-september-v1"
        or summary["policy"] != manifest["policy"]
        or summary["source_sha256"] != source_identity()
    ):
        raise ValueError("Original checkpoint is incomplete or has another identity")
    validate_event_stream(directory / "events.sqlite", protocol, manifest["counts"])
    return manifest


def transaction_frame(directory: Path) -> pd.DataFrame:
    """Offline labels are joined only to build the independent assessment oracle."""
    with closing(readonly_connection(directory / "events.sqlite")) as connection:
        records = connection.execute(
            "SELECT t.transaction_id,t.clock_ns,"
            "json_extract(t.payload,'$.customer_id'),json_extract(t.payload,'$.terminal_id'),"
            "json_extract(t.payload,'$.amount'),json_extract(f.payload,'$.fraud_label') "
            "FROM stream t JOIN stream f USING(transaction_id) "
            "WHERE t.phase=1 AND f.phase=0 ORDER BY t.clock_ns,t.transaction_id"
        ).fetchall()
    frame = pd.DataFrame(
        records,
        columns=[
            "TRANSACTION_ID",
            "TX_DATETIME",
            "CUSTOMER_ID",
            "TERMINAL_ID",
            "TX_AMOUNT",
            "TX_FRAUD",
        ],
    )
    frame["TX_DATETIME"] = pd.to_datetime(frame.TX_DATETIME, unit="ns")
    return frame


def prepare(
    directory: Path,
    digest: str,
    original_checkpoint: Path,
    release: Path,
    threshold: Path,
    output: Path,
    root: Path = PROJECT_ROOT,
) -> dict:
    output = separate_output(output, directory, original_checkpoint.parent, release, threshold)
    if output.exists() or output.is_symlink():
        raise ValueError("Corrective input exists; preserve it")
    rule = correction_protocol(root)
    provenance = correction_inputs(root)
    original = original_input(directory, digest, original_checkpoint, root)
    with threadpool_limits(limits=4):
        runtime, config = identified_runtime(root, release, threshold, original["protocol"])
        output.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix=".numerical-preparing-", dir=output.parent) as temporary:
            stage = Path(temporary) / "input"
            stage.mkdir()
            LOGGER.info(
                "Building deterministic oracle from existing events only; no Silver access"
            )
            frame = deterministic_features(transaction_frame(directory))
            window = original["protocol"]["windows"]["scored_replay"]
            frame = frame[
                (frame.TX_DATETIME >= pd.Timestamp(window["start"]))
                & (frame.TX_DATETIME < pd.Timestamp(window["end_exclusive"]))
            ].copy()
            if len(frame) != original["scored_rows"]:
                raise ValueError("Corrective oracle coverage differs")
            frame["offline_score"] = predict_scores(runtime.pipeline, frame[FEATURE_COLUMNS])
            frame.to_parquet(
                stage / "oracle.parquet", index=False, compression="zstd", version="2.6"
            )
            manifest = {
                "version": VERSION,
                "schema_version": 1,
                "numerical_version": NUMERICAL_VERSION,
                "correction_protocol_sha256": sha256(root / PROTOCOL_PATH),
                "original_input": str(directory.absolute()),
                "original_input_manifest_sha256": digest,
                "original_checkpoint": str(original_checkpoint.absolute()),
                "original_checkpoint_sha256": rule["original_checkpoint_sha256"],
                "protocol": original["protocol"],
                "provenance": provenance,
                "environment": {**runtime_environment(), "duckdb": version("duckdb")},
                "policy": config.model_dump(),
                "counts": original["counts"],
                "scored_rows": original["scored_rows"],
                "files": [{"path": "oracle.parquet", "sha256": sha256(stage / "oracle.parquet")}],
            }
            write_json(stage / "manifest.json", manifest)
            new_digest = sha256(stage / "manifest.json")
            verify_prepared(stage, new_digest, root)
            identified_runtime(root, release, threshold, original["protocol"])
            if correction_inputs(root)["files"] != provenance["files"]:
                raise ValueError("Corrective implementation changed during preparation")
            if output.exists():
                raise ValueError("Correction output appeared; no overwrite")
            stage.rename(output)
    return {
        "version": VERSION,
        "status": "success",
        "input_path": str(output),
        "manifest_sha256": new_digest,
        "counts": original["counts"],
        "scored_rows": len(frame),
        "source_Silver_reopened": False,
        "original_checkpoint_changed": False,
        "native_replay_evaluated": False,
        "refit": False,
        "production_promotion": False,
    }


def verify_prepared(directory: Path, digest: str, root: Path) -> dict:
    path = regular_file(directory / "manifest.json")
    if sha256(path) != digest:
        raise ValueError("Corrective input manifest changed")
    manifest = json.loads(path.read_text())
    original = original_input(
        Path(manifest["original_input"]),
        manifest["original_input_manifest_sha256"],
        Path(manifest["original_checkpoint"]),
        root,
    )
    if (
        manifest["version"] != VERSION
        or manifest["schema_version"] != 1
        or manifest["numerical_version"] != NUMERICAL_VERSION
        or manifest["correction_protocol_sha256"] != sha256(root / PROTOCOL_PATH)
        or manifest["original_checkpoint_sha256"]
        != correction_protocol(root)["original_checkpoint_sha256"]
        or manifest["environment"] != original["environment"]
        or manifest["provenance"]["files"] != correction_inputs(root)["files"]
        or any(manifest[k] != original[k] for k in ("protocol", "policy", "counts", "scored_rows"))
        or [r["path"] for r in manifest["files"]] != ["oracle.parquet"]
    ):
        raise ValueError("Corrective scope, code, arithmetic or immutable bindings differ")
    for record in manifest["files"]:
        if sha256(regular_file(directory / record["path"])) != record["sha256"]:
            raise ValueError("Corrective input artifact changed")
    return manifest


def execution_identity(manifest: dict, digest: str) -> str:
    return digest_record(
        {
            "version": VERSION,
            "numerical_version": NUMERICAL_VERSION,
            "input_manifest_sha256": digest,
            "code": manifest["provenance"]["files"],
            "original_checkpoint_sha256": manifest["original_checkpoint_sha256"],
        }
    )


def saved_decisions(state: Path):
    with closing(readonly_connection(state)) as connection:
        rows = connection.execute(
            "SELECT transaction_id,request,response FROM decisions ORDER BY transaction_id"
        ).fetchall()
        alerts = {
            r[0]: json.loads(r[1])
            for r in connection.execute("SELECT transaction_id,payload FROM alert_decisions")
        }
    return [
        (tx, json.loads(request)["features"], json.loads(response)["score"], alerts[tx])
        for tx, request, response in rows
    ]


def correction_comparison(state: Path, original: Path, threshold: float) -> dict:
    old, new = saved_decisions(original), saved_decisions(state)
    if [r[0] for r in old] != [r[0] for r in new]:
        raise ValueError("Original and corrective decision coverage differs")
    deltas = [abs(a[2] - b[2]) for a, b in zip(old, new, strict=True)]
    old_alerts = {r[3]["alert_id"]: r[0] for r in old if r[3]["status"] == "alert"}
    new_alerts = {r[3]["alert_id"]: r[0] for r in new if r[3]["status"] == "alert"}
    return {
        "rows": len(new),
        "score_changes": sum(d != 0 for d in deltas),
        "max_absolute_score_change": max(deltas, default=0.0),
        "feature_changes": {
            name: sum(a[1][name] != b[1][name] for a, b in zip(old, new, strict=True))
            for name in FEATURE_COLUMNS
        },
        "threshold_eligibility_changes": sum(
            (a[2] >= threshold) != (b[2] >= threshold) for a, b in zip(old, new, strict=True)
        ),
        "alert_status_changes": sum(
            a[3]["status"] != b[3]["status"] for a, b in zip(old, new, strict=True)
        ),
        "alerts_removed": len(old_alerts.keys() - new_alerts.keys()),
        "alerts_added": len(new_alerts.keys() - old_alerts.keys()),
        "alert_transaction_changes": sum(
            old_alerts[k] != new_alerts[k] for k in old_alerts.keys() & new_alerts.keys()
        ),
        "original_alerts": len(old_alerts),
        "corrective_alerts": len(new_alerts),
    }


def assessment_report(directory: Path, manifest: dict, state: Path) -> dict:
    summary = inspect_checkpoint(state)
    if (
        any(summary["counts"][k] != v for k, v in manifest["counts"].items())
        or summary["counts"]["decisions"] != manifest["scored_rows"]
        or summary["counts"]["alert_decisions"] != manifest["scored_rows"]
        or summary["input_sha256"]
        != execution_identity(manifest, sha256(directory / "manifest.json"))
        or summary["policy"] != manifest["policy"]
        or summary["run_id"] != "native-september-numerical-v1"
    ):
        raise ValueError("Corrective checkpoint is incomplete or belongs to another input")
    original = Path(manifest["original_checkpoint"])
    if sha256(regular_file(original)) != manifest["original_checkpoint_sha256"]:
        raise ValueError("Original checkpoint changed")
    with closing(readonly_connection(state)) as connection:
        for event in stream_events(Path(manifest["original_input"]) / "events.sqlite"):
            row = connection.execute(
                "SELECT payload FROM events WHERE event_id=?", (event.event_id,)
            ).fetchone()
            if row is None or json.loads(row[0]) != event.model_dump():
                raise ValueError("Corrective event differs from immutable original input")
    oracle = pd.read_parquet(directory / "oracle.parquet")
    config = CustomerDayAlertConfig.model_validate(manifest["policy"])
    frame = ledger_frame(state, oracle, config, manifest["protocol"])
    actual = saved_decisions(state)
    ordered = oracle.sort_values("TRANSACTION_ID")
    if [r[0] for r in actual] != ordered.TRANSACTION_ID.tolist():
        raise ValueError("Exact feature comparison coverage differs")
    for name in FEATURE_COLUMNS:
        if not np.array_equal([r[1][name] for r in actual], ordered[name].to_numpy()):
            raise ValueError(f"Exact deterministic feature parity failed: {name}")
    return {
        "version": VERSION,
        "numerical_version": NUMERICAL_VERSION,
        "status": "success",
        "complete": True,
        "counts": summary["counts"],
        "policy_sha256": config.policy_sha256,
        "model_sha256": config.model_sha256,
        "serving_release_id": config.serving_release_id,
        "windows": manifest["protocol"]["windows"],
        "feature_parity_evaluated": True,
        "feature_parity_exact": True,
        "score_parity_evaluated": True,
        "score_parity_atol": manifest["protocol"]["parity"]["scores_atol"],
        "score_parity_rtol": manifest["protocol"]["parity"]["scores_rtol"],
        "causal_decision_parity_evaluated": True,
        "original_checkpoint_sha256": manifest["original_checkpoint_sha256"],
        "comparison_to_original": correction_comparison(state, original, config.score_threshold),
        "quality": triage_quality(frame, config, manifest["protocol"]["windows"]["scored_replay"]),
        "source_Silver_reopened": False,
        "original_checkpoint_changed": False,
        "native_replay_evaluated": True,
        "refit": False,
        "external_alerts_published": False,
        "production_promotion": False,
        "paced_1x_evaluated": False,
        "end_to_end_latency_evaluated": False,
        "sla_validated": False,
    }


def run(
    directory: Path,
    digest: str,
    release: Path,
    threshold: Path,
    output: Path,
    root: Path = PROJECT_ROOT,
    stop_after: int | None = None,
) -> dict:
    if stop_after is not None and (type(stop_after) is not int or stop_after <= 0):
        raise ValueError("stop-after must be a positive count of new events")
    output = separate_output(output, directory, release, threshold)
    input_manifest = regular_file(directory / "manifest.json")
    if sha256(input_manifest) != digest:
        raise ValueError("Corrective input manifest changed")
    bindings = json.loads(input_manifest.read_text())
    separate_output(
        output, Path(bindings["original_input"]), Path(bindings["original_checkpoint"]).parent
    )
    with job_lock(output), threadpool_limits(limits=4):
        manifest = verify_prepared(directory, digest, root)
        runtime, config = identified_runtime(root, release, threshold, manifest["protocol"])
        state = output / "checkpoint.sqlite"
        if (output / "assessment").exists():
            return {**verify(directory, digest, output, root), "reused": True}
        start = time.perf_counter()
        before = resource.getrusage(resource.RUSAGE_SELF)
        elapsed_ms, accepted = [], 0
        window = manifest["protocol"]["windows"]["scored_replay"]
        with DeterministicTriageReplay(
            state,
            run_id="native-september-numerical-v1",
            input_sha256=execution_identity(manifest, digest),
            runtime=runtime,
            policy=config,
            scoring_window=(window["start"] + "T00:00:00", window["end_exclusive"] + "T00:00:00"),
        ) as executor:
            summary = executor.summary()
            previous = summary["last_event_order"]
            after = tuple(previous) if previous[0] is not None else None
            for event in stream_events(
                Path(manifest["original_input"]) / "events.sqlite", after=after
            ):
                started = time.perf_counter()
                executor.handle(event)
                if event.event_type == "transaction" and event.tx_datetime[:10] >= window["start"]:
                    elapsed_ms.append((time.perf_counter() - started) * 1000)
                accepted += 1
                if accepted % 5000 == 0:
                    LOGGER.info(
                        "Committed %d new events; source clock %s",
                        accepted,
                        pd.Timestamp(event.event_clock_ns).isoformat(),
                    )
                if stop_after is not None and accepted >= stop_after:
                    break
            summary = executor.summary()
        elapsed = time.perf_counter() - start
        usage = resource.getrusage(resource.RUSAGE_SELF)
        complete = summary["counts"]["events"] == manifest["counts"]["events"]
        invocation = {
            "accepted_this_invocation": accepted,
            "complete": complete,
            "measurement_mode": "accelerated_serial_local_no_pacing",
            "processing_wall_seconds": elapsed,
            "new_events_per_second": accepted / elapsed if elapsed else None,
            "CPU_seconds": usage.ru_utime + usage.ru_stime - before.ru_utime - before.ru_stime,
            "process_lifetime_peak_rss_mib": usage.ru_maxrss / 1024,
            "checkpoint_bytes": state.stat().st_size,
            "scored_handle_duration_ms": {
                "scope": "local_scorer_and_SQLite_commit_no_scheduled_emission_or_queue",
                "requests": len(elapsed_ms),
                **{
                    f"p{q}": float(np.percentile(elapsed_ms, q)) if elapsed_ms else None
                    for q in (50, 95, 99)
                },
            },
            "paced_1x_evaluated": False,
            "end_to_end_latency_evaluated": False,
            "sla_validated": False,
            "recovery_seconds_measured": False,
        }
        write_json(
            output / f"invocation-{summary['counts']['events']:09d}-{uuid4().hex}.json", invocation
        )
        if not complete:
            return {
                "version": VERSION,
                "status": "success",
                "counts": summary["counts"],
                **invocation,
                "native_replay_evaluated": False,
                "production_promotion": False,
            }
        verify_prepared(directory, digest, root)
        report = assessment_report(directory, manifest, state)
        report.update(
            input_manifest_sha256=digest,
            execution_input_sha256=execution_identity(manifest, digest),
            measurement_invocations=sorted(p.name for p in output.glob("invocation-*.json")),
        )
        with TemporaryDirectory(prefix=".assessment-preparing-", dir=output) as temporary:
            final = Path(temporary) / "assessment"
            final.mkdir()
            write_json(final / "result.json", report, overwrite=False)
            for source in sorted(output.glob("invocation-*.json")):
                shutil.copyfile(source, final / source.name)
            receipt = {
                "version": VERSION,
                "input_manifest_sha256": digest,
                "files": [
                    {"path": p.name, "sha256": sha256(p)}
                    for p in [final / "result.json", *sorted(final.glob("invocation-*.json"))]
                ],
                "checkpoint_sha256": sha256(state),
            }
            write_json(final / "receipt.json", receipt, overwrite=False)
            final.rename(output / "assessment")
        return {
            **report,
            "result_path": str(output / "assessment/result.json"),
            "receipt_sha256": sha256(output / "assessment/receipt.json"),
            "reused": False,
        }


def verify(
    directory: Path,
    digest: str,
    output: Path,
    root: Path = PROJECT_ROOT,
    receipt_digest: str | None = None,
) -> dict:
    manifest = verify_prepared(directory, digest, root)
    final = output / "assessment"
    receipt_path = regular_file(final / "receipt.json")
    if receipt_digest is not None and sha256(receipt_path) != receipt_digest:
        raise ValueError("Native result external receipt digest differs")
    receipt = json.loads(receipt_path.read_text())
    names = ["result.json", *sorted(p.name for p in final.glob("invocation-*.json"))]
    if (
        receipt["version"] != VERSION
        or receipt["input_manifest_sha256"] != digest
        or [r["path"] for r in receipt["files"]] != names
    ):
        raise ValueError("Native result receipt identity or files differ")
    for record in receipt["files"]:
        if sha256(regular_file(final / record["path"])) != record["sha256"]:
            raise ValueError("Native result artifact changed")
    state = regular_file(output / "checkpoint.sqlite")
    if sha256(state) != receipt["checkpoint_sha256"]:
        raise ValueError("Completed native checkpoint changed")
    expected = assessment_report(directory, manifest, state)
    expected.update(
        input_manifest_sha256=digest,
        execution_input_sha256=execution_identity(manifest, digest),
        measurement_invocations=names[1:],
    )
    if json.loads((final / "result.json").read_text()) != expected:
        raise ValueError("Native report no longer reproduces the committed ledger")
    return {
        **expected,
        "result_path": str(final / "result.json"),
        "receipt_sha256": sha256(receipt_path),
    }


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "run", "verify"):
        command = commands.add_parser(name)
        command.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
        command.add_argument("--input", type=Path, required=True)
        command.add_argument("--manifest-sha256", required=True)
        command.add_argument("--output", type=Path, required=True)
        if name in ("prepare", "run"):
            command.add_argument("--release", type=Path, required=True)
            command.add_argument("--threshold", type=Path, required=True)
        if name == "prepare":
            command.add_argument("--original-checkpoint", type=Path, required=True)
        if name == "run":
            command.add_argument("--stop-after", type=int)
        if name == "verify":
            command.add_argument("--receipt-digest", required=True)
    args = vars(parser.parse_args())
    name = args.pop("command")
    args["root"] = args.pop("project_root")
    args["directory"] = args.pop("input")
    args["digest"] = args.pop("manifest_sha256")
    try:
        result = {"prepare": prepare, "run": run, "verify": verify}[name](**args)
    except (ValueError, OSError, KeyError, sqlite3.Error, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Numerical corrective replay failed: {exc}\n")
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
