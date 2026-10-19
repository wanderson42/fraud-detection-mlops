"""Restricted native preparation, accelerated durable replay and read-only assessment."""

import argparse
from contextlib import closing, contextmanager
import fcntl
import hashlib
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
    assessment_projection,
    readonly_connection,
    replay_snapshot,
    stream_events,
    validate_event_stream,
    write_event_stream,
)
from fraud_detection_mlops.data.datasets.reference_assessment_dataset import (
    build_assessment_features,
    check_silver_bytes,
    load_assessment_features,
    window_days,
)
from fraud_detection_mlops.features.feature_schema import FEATURE_COLUMNS
from fraud_detection_mlops.modeling.contracts.model_interface import predict_scores
from fraud_detection_mlops.serving.model_release import runtime_environment
from fraud_detection_mlops.streaming.contracts.customer_day_alerts import CustomerDayAlertConfig
from fraud_detection_mlops.streaming.contracts.native_replay_execution import (
    VERSION,
    committed_inputs,
    execution_protocol,
    identified_runtime,
)
from fraud_detection_mlops.streaming.evaluation.native_triage_assessment import (
    ledger_frame,
    triage_quality,
)
from fraud_detection_mlops.streaming.replay.durable_triage_replay import (
    DurableTriageReplay,
    inspect_checkpoint,
)

LOGGER = logging.getLogger(__name__)


def digest_record(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


@contextmanager
def job_lock(directory: Path):
    """One adapter writer; do not let parallel resumptions publish inconsistent reports."""
    directory = directory.absolute()
    if directory.resolve() != directory:
        raise ValueError("Native job directory cannot be redirected")
    directory.mkdir(parents=True, exist_ok=True)
    lock = directory / ".native-replay.lock"
    if lock.is_symlink():
        raise ValueError("Native job lock cannot be redirected")
    with lock.open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("Another adapter invocation owns this output") from exc
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def plan(silver_root: Path, root: Path = PROJECT_ROOT) -> dict:
    protocol = execution_protocol(root)
    provenance = committed_inputs(root)
    snapshot = replay_snapshot(silver_root, protocol, root)
    return {
        "version": VERSION,
        "status": "success",
        "mode": "metadata_only",
        "partition_bytes_opened": False,
        "windows": protocol["windows"],
        "partitions": len(snapshot["files"]),
        "transactions_including_context": sum(r["rows"] for r in snapshot["files"]),
        "source_manifest_sha256": snapshot["manifest_sha256"],
        "git_revision": provenance["git_revision"],
        "native_replay_evaluated": False,
        "production_promotion": False,
    }


def prepare(
    silver_root: Path, release: Path, threshold: Path, output: Path, root: Path = PROJECT_ROOT
) -> dict:
    protocol = execution_protocol(root)
    provenance = committed_inputs(root)
    output = output.absolute()
    if output.exists() or output.is_symlink() or output.resolve() != output:
        raise ValueError("Native input output exists or is redirected; preserve it")
    if any(output.is_relative_to(p.resolve()) for p in (silver_root, release, threshold)):
        raise ValueError("Native input must be separate from source, release and threshold")
    with threadpool_limits(limits=4):
        runtime, config = identified_runtime(root, release, threshold, protocol)
        snapshot = replay_snapshot(silver_root, protocol, root)
        projection = assessment_projection(protocol)
        output.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix=".native-preparing-", dir=output.parent) as temporary:
            stage = Path(temporary) / "input"
            LOGGER.info("Checking only 02–30/09 and building independent Gold oracle")
            records = build_assessment_features(
                snapshot,
                projection,
                stage / "features",
                {"threads": 4, "duckdb_memory_limit": "2GB"},
            )
            frame = load_assessment_features(stage, records, projection)
            frame["offline_score"] = predict_scores(runtime.pipeline, frame[FEATURE_COLUMNS])
            frame.to_parquet(
                stage / "oracle.parquet", index=False, compression="zstd", version="2.6"
            )
            counts = write_event_stream(snapshot, protocol, stage / "events.sqlite")
            validate_event_stream(stage / "events.sqlite", protocol, counts)
            check_silver_bytes(snapshot)
            if committed_inputs(root)["files"] != provenance["files"]:
                raise ValueError("Code or contracts changed during native input preparation")
            # Recheck immutable threshold/release after native reads, before publication.
            identified_runtime(root, release, threshold, protocol)
            names = ["events.sqlite", "oracle.parquet", *[r["path"] for r in records]]
            manifest = {
                "schema_version": 1,
                "version": VERSION,
                "protocol": protocol,
                "provenance": provenance,
                "environment": {**runtime_environment(), "duckdb": version("duckdb")},
                "snapshot": snapshot,
                "policy": config.model_dump(),
                "counts": counts,
                "scored_rows": len(frame),
                "feature_records": records,
                "files": [{"path": name, "sha256": sha256(stage / name)} for name in names],
            }
            write_json(stage / "manifest.json", manifest)
            digest = sha256(stage / "manifest.json")
            verify_input(stage, digest, root)
            if output.exists():
                raise ValueError("Native input output appeared; no overwrite")
            stage.rename(output)
    return {
        "version": VERSION,
        "status": "success",
        "input_path": str(output),
        "manifest_sha256": digest,
        "counts": counts,
        "scored_rows": len(frame),
        "native_replay_evaluated": False,
        "reserved_window_materialized": True,
        "refit": False,
        "production_promotion": False,
    }


def verify_input(directory: Path, digest: str, root: Path) -> dict:
    path = regular_file(directory / "manifest.json")
    if sha256(path) != digest:
        raise ValueError("Native input manifest changed")
    manifest = json.loads(path.read_text())
    protocol = execution_protocol(root)
    expected_days = window_days(protocol["windows"]["scored_replay"])
    names = ["events.sqlite", "oracle.parquet", *[f"features/{d}.parquet" for d in expected_days]]
    if (
        manifest["version"] != VERSION
        or manifest["schema_version"] != 1
        or manifest["protocol"] != protocol
        or manifest["environment"] != {**runtime_environment(), "duckdb": version("duckdb")}
        or [r["path"] for r in manifest["files"]] != names
        or manifest["policy"]
        != json.loads((root / "references/customer_day_alert_policy_v1.json").read_text())
        or manifest["provenance"]["files"] != committed_inputs(root)["files"]
    ):
        raise ValueError("Native input scope, policy or committed implementation differs")
    for record in manifest["files"]:
        if sha256(regular_file(directory / record["path"])) != record["sha256"]:
            raise ValueError("Native input artifact changed")
    validate_event_stream(directory / "events.sqlite", protocol, manifest["counts"])
    return manifest


def execution_identity(manifest: dict, digest: str) -> str:
    return digest_record(
        {
            "input_manifest_sha256": digest,
            "protocol": manifest["protocol"],
            "code": manifest["provenance"]["files"],
        }
    )


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
    output = output.absolute()
    if any(output.is_relative_to(p.resolve()) for p in (directory, release, threshold)):
        raise ValueError("Keep checkpoint output separate from immutable inputs")
    with job_lock(output), threadpool_limits(limits=4):
        manifest = verify_input(directory, digest, root)
        runtime, config = identified_runtime(root, release, threshold, manifest["protocol"])
        state = output / "checkpoint.sqlite"
        if (output / "assessment").exists():
            return {**verify(directory, digest, output, root), "reused": True}
        start = time.perf_counter()
        before = resource.getrusage(resource.RUSAGE_SELF)
        elapsed_ms, accepted = [], 0
        window = manifest["protocol"]["windows"]["scored_replay"]
        with DurableTriageReplay(
            state,
            run_id="native-september-v1",
            input_sha256=execution_identity(manifest, digest),
            runtime=runtime,
            policy=config,
            scoring_window=(window["start"] + "T00:00:00", window["end_exclusive"] + "T00:00:00"),
        ) as executor:
            summary = executor.summary()
            previous = summary["last_event_order"]
            after = tuple(previous) if previous[0] is not None else None
            for event in stream_events(directory / "events.sqlite", after=after):
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


def assessment_report(directory: Path, manifest: dict, state: Path) -> dict:
    summary = inspect_checkpoint(state)
    protocol = manifest["protocol"]
    expected_counts = manifest["counts"]
    if (
        any(summary["counts"][k] != v for k, v in expected_counts.items())
        or summary["counts"]["decisions"] != manifest["scored_rows"]
        or summary["counts"]["alert_decisions"] != manifest["scored_rows"]
        or summary["input_sha256"]
        != execution_identity(manifest, sha256(directory / "manifest.json"))
        or summary["policy"] != manifest["policy"]
    ):
        raise ValueError("Native checkpoint is incomplete or belongs to another input")
    # Compare every persisted source event; no Silver partition is reopened here.
    with closing(readonly_connection(state)) as connection:
        for event in stream_events(directory / "events.sqlite"):
            record = connection.execute(
                "SELECT payload FROM events WHERE event_id=?", (event.event_id,)
            ).fetchone()
            if record is None or json.loads(record[0]) != event.model_dump():
                raise ValueError("Checkpoint event differs from the immutable input")
    oracle = pd.read_parquet(directory / "oracle.parquet")
    config = CustomerDayAlertConfig.model_validate(manifest["policy"])
    frame = ledger_frame(state, oracle, config, protocol)
    return {
        "version": VERSION,
        "status": "success",
        "complete": True,
        "counts": summary["counts"],
        "policy_sha256": config.policy_sha256,
        "model_sha256": config.model_sha256,
        "serving_release_id": config.serving_release_id,
        "windows": protocol["windows"],
        "feature_parity_evaluated": True,
        "score_parity_evaluated": True,
        "causal_decision_parity_evaluated": True,
        "quality": triage_quality(frame, config, protocol["windows"]["scored_replay"]),
        "native_replay_evaluated": True,
        "refit": False,
        "external_alerts_published": False,
        "production_promotion": False,
        "paced_1x_evaluated": False,
        "end_to_end_latency_evaluated": False,
        "sla_validated": False,
    }


def verify(
    directory: Path,
    digest: str,
    output: Path,
    root: Path = PROJECT_ROOT,
    receipt_digest: str | None = None,
) -> dict:
    manifest = verify_input(directory, digest, root)
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
    for name in ("plan", "prepare", "run", "verify"):
        command = commands.add_parser(name)
        command.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
        if name in ("plan", "prepare"):
            command.add_argument(
                "--silver-root", type=Path, default=PROJECT_ROOT / "data/interim/handbook"
            )
        if name in ("prepare", "run"):
            command.add_argument("--release", type=Path, required=True)
            command.add_argument("--threshold", type=Path, required=True)
        if name in ("run", "verify"):
            command.add_argument("--input", type=Path, required=True)
            command.add_argument("--manifest-sha256", required=True)
        if name != "plan":
            command.add_argument("--output", type=Path, required=True)
        if name == "verify":
            command.add_argument("--receipt-digest", required=True)
        if name == "run":
            command.add_argument("--stop-after", type=int)
    args = vars(parser.parse_args())
    name = args.pop("command")
    args["root"] = args.pop("project_root")
    if "input" in args:
        args["directory"] = args.pop("input")
        args["digest"] = args.pop("manifest_sha256")
    try:
        result = {"plan": plan, "prepare": prepare, "run": run, "verify": verify}[name](**args)
    except (ValueError, OSError, KeyError, sqlite3.Error, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Native causal replay failed: {exc}\n")
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
