"""Bounded local producer/consumer replay with durable timing and unchanged decision code."""

import argparse
from dataclasses import dataclass, field
import hashlib
import json
import logging
import math
from pathlib import Path
import queue
import subprocess
import threading
import time

from scripts.monitoring.statistics import score_counts
from scripts.monitoring.telemetry import VERSION, Journal
from threadpoolctl import threadpool_limits

from fraud_detection_mlops.artifacts import sha256, write_json
from fraud_detection_mlops.config import PROJECT_ROOT
from fraud_detection_mlops.data.datasets.native_replay_dataset import stream_events
from fraud_detection_mlops.streaming.features.deterministic_feature_state import (
    DeterministicTriageReplay,
)
from fraud_detection_mlops.streaming.replay import numerical_corrective_replay as correction
from fraud_detection_mlops.streaming.replay.durable_triage_replay import event_order
from fraud_detection_mlops.streaming.replay.native_causal_replay import job_lock

LOGGER = logging.getLogger(__name__)


@dataclass
class Envelope:
    event: object
    scheduled: float
    scheduled_unix: float
    ready: threading.Event = field(default_factory=threading.Event)
    emitted: float = 0.0
    error: BaseException | None = None


def operational_files(root=PROJECT_ROOT):
    names = sorted(
        [
            p.relative_to(root).as_posix()
            for folder in ("scripts/monitoring", "scripts/replay")
            for p in (root / folder).glob("*.py")
        ]
        + ["references/paced_monitoring_protocol_v1.json"]
    )
    return names


def operations_identity(root=PROJECT_ROOT):
    names = operational_files(root)
    subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", *names],
        cwd=root,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "diff", "--quiet", "HEAD", "--", *names], cwd=root, check=True, capture_output=True
    )
    running = Path(__file__).resolve().parents[2]
    for name in names:
        if name.endswith(".py") and sha256(root / name) != sha256(running / name):
            raise ValueError("Running operations code differs")
    return {name: sha256(root / name) for name in names}


def make_metadata(
    *,
    input_sha256,
    runtime,
    policy,
    speed,
    capacity,
    window,
    expected_counts,
    baseline,
    source_scope,
    code,
):
    if (
        not math.isfinite(speed)
        or speed < 1
        or type(capacity) is not int
        or not 1 <= capacity <= 10000
    ):
        raise ValueError("Use finite speed >= 1 and bounded capacity 1..10000")
    return {
        "version": VERSION,
        "input_sha256": input_sha256,
        "scoring": runtime.information(),
        "policy": policy.model_dump(),
        "speed": float(speed),
        "queue_capacity": capacity,
        "scoring_window": list(window) if window else None,
        "expected_counts": expected_counts,
        "score_reference_counts": baseline,
        "source_scope": source_scope,
        "code": code,
        "measurement_scope": "local_scheduled_emission_to_decision_commit_no_external_broker",
        "histogram_scope": "paced_transactions_only_context_and_unmeasured_recovery_excluded",
        "sla_validated": False,
        "production_promotion": False,
    }


def execute(events, output, metadata, runtime, policy, *, max_paced_events=None):
    if max_paced_events is not None and (
        type(max_paced_events) is not int or max_paced_events <= 0
    ):
        raise ValueError("Positive max-paced-events required")
    output = output.absolute()
    with job_lock(output), threadpool_limits(limits=4):
        path = output / "metadata.json"
        if path.exists():
            if path.is_symlink() or json.loads(path.read_text()) != metadata:
                raise ValueError("Paced identity changed; preserve output")
        elif any(p.name != ".native-replay.lock" for p in output.iterdir()):
            raise ValueError("Foreign partial output; preserve it")
        else:
            write_json(path, metadata, overwrite=False)
        identity = hashlib.sha256(
            json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        checkpoint = output / "checkpoint.sqlite"
        window = tuple(metadata["scoring_window"]) if metadata["scoring_window"] else None
        journal = Journal(output / "telemetry.sqlite", metadata)
        stopped = threading.Event()
        worker = None
        session = None
        try:
            with DeterministicTriageReplay(
                checkpoint,
                run_id="paced-observability-v1",
                input_sha256=identity,
                runtime=runtime,
                policy=policy,
                scoring_window=window,
            ) as executor:
                journal.reconcile(checkpoint)
                summary = executor.summary()
                if all(summary["counts"][k] == v for k, v in metadata["expected_counts"].items()):
                    return {
                        "version": VERSION,
                        "status": "reused",
                        "complete": True,
                        "counts": summary["counts"],
                        "new_warmup_events": 0,
                        "new_paced_events": 0,
                        "speed": metadata["speed"],
                        "measurement_scope": metadata["measurement_scope"],
                        "sla_validated": False,
                        "production_promotion": False,
                    }
                session = journal.start()
                previous = executor.summary()["last_event_order"]
                after = tuple(previous) if previous[0] is not None else None
                iterable = iter(events(after))
                from fraud_detection_mlops.streaming.contracts.transaction_events import clock_ns

                cutoff = clock_ns(window[0]) if window else None
                start = time.perf_counter()
                warmup = 0
                first = None
                try:
                    for event in iterable:
                        if cutoff is None or event.event_clock_ns >= cutoff:
                            first = event
                            break
                        executor.handle(event)
                        warmup += 1
                        after = event_order(event)
                        if warmup % 5000 == 0:
                            journal.heartbeat(session)
                            LOGGER.info(
                                "Warmup committed %d new events (excluded from pacing)", warmup
                            )
                finally:
                    if hasattr(iterable, "close"):
                        iterable.close()
                journal.warmup(session, warmup, time.perf_counter() - start)
                channel = queue.Queue(maxsize=metadata["queue_capacity"])
                errors = []

                def put(item):
                    while not stopped.is_set():
                        try:
                            channel.put(item, timeout=0.1)
                            return True
                        except queue.Full:
                            pass
                    return False

                def produce():
                    try:
                        origin = time.perf_counter()
                        unix_origin = time.time()
                        last_heartbeat = origin
                        source_origin = first.event_clock_ns if first else 0
                        # Open the source cursor inside its owning thread. The
                        # warmup cursor is closed before handing off production.
                        for index, event in enumerate(events(after) if first else ()):
                            if max_paced_events is not None and index >= max_paced_events:
                                break
                            offset = (
                                (event.event_clock_ns - source_origin) / 1e9 / metadata["speed"]
                            )
                            if offset < 0:
                                raise ValueError("Source order moved backwards")
                            due = origin + offset
                            while not stopped.is_set() and time.perf_counter() < due:
                                stopped.wait(min(0.1, max(0, due - time.perf_counter())))
                                if time.perf_counter() - last_heartbeat >= 5:
                                    journal.heartbeat(session)
                                    last_heartbeat = time.perf_counter()
                            if stopped.is_set():
                                break
                            item = Envelope(event, due, unix_origin + offset)
                            if not put(item):
                                break
                            try:
                                item.emitted = time.perf_counter()
                                journal.emitted(
                                    session,
                                    event,
                                    item.scheduled_unix,
                                    unix_origin + (item.emitted - origin),
                                    channel.qsize(),
                                )
                            except BaseException as exc:
                                item.error = exc
                                raise
                            finally:
                                item.ready.set()
                    except BaseException as exc:  # noqa: BLE001 - propagate producer failures to the consumer
                        errors.append(exc)

                worker = threading.Thread(target=produce, name="paced-source", daemon=False)
                worker.start()
                paced = 0
                while worker.is_alive() or not channel.empty():
                    try:
                        item = channel.get(timeout=0.1)
                    except queue.Empty:
                        continue
                    item.ready.wait()
                    if item.error:
                        raise item.error
                    dequeued = time.perf_counter()
                    journal.dequeued(
                        session, item.event, item.scheduled_unix + (dequeued - item.scheduled)
                    )
                    started = time.perf_counter()
                    executor.handle(item.event)
                    finished = time.perf_counter()
                    journal.committed(
                        session,
                        item.event,
                        item.scheduled_unix + (finished - item.scheduled),
                        finished - started,
                    )
                    paced += 1
                    if paced % 1000 == 0:
                        LOGGER.info("Paced committed %d new events", paced)
                worker.join()
                if errors:
                    raise errors[0]
                summary = executor.summary()
                complete = all(
                    summary["counts"][k] == v for k, v in metadata["expected_counts"].items()
                )
                journal.finish(session, "complete" if complete else "paused")
                return {
                    "version": VERSION,
                    "status": "success",
                    "complete": complete,
                    "counts": summary["counts"],
                    "new_warmup_events": warmup,
                    "new_paced_events": paced,
                    "speed": metadata["speed"],
                    "measurement_scope": metadata["measurement_scope"],
                    "sla_validated": False,
                    "production_promotion": False,
                }
        except BaseException:
            stopped.set()
            if worker:
                worker.join()
            if session:
                journal.finish(session, "interrupted")
            raise
        finally:
            journal.close()


def native_run(args):
    code = operations_identity(args.project_root)
    receipt_path = args.project_root / "data/replay/native-numerical-input-receipt.json"
    receipt = json.loads(correction.regular_file(receipt_path).read_text())
    directory = Path(receipt["input_path"])
    manifest = correction.verify_prepared(directory, receipt["manifest_sha256"], args.project_root)
    runtime, policy = correction.identified_runtime(
        args.project_root, args.release, args.threshold, manifest["protocol"]
    )
    correction.separate_output(
        args.output,
        directory,
        Path(manifest["original_input"]),
        Path(manifest["original_checkpoint"]).parent,
        args.release,
        args.threshold,
        args.project_root / "data/replay/native-september-numerical-v1",
    )
    window = manifest["protocol"]["windows"]["scored_replay"]
    # The verified development threshold artefact contains scores, not current labels.
    import pandas as pd

    baseline = score_counts(pd.read_parquet(args.threshold / "scores.parquet")["score"].tolist())
    metadata = make_metadata(
        input_sha256=receipt["manifest_sha256"],
        runtime=runtime,
        policy=policy,
        speed=args.speed,
        capacity=args.capacity,
        window=(window["start"] + "T00:00:00", window["end_exclusive"] + "T00:00:00"),
        expected_counts=manifest["counts"],
        baseline=baseline,
        source_scope="materialized_native_events_operational_reuse_not_new_quality_confirmation",
        code=code,
    )
    return execute(
        lambda after: stream_events(
            Path(manifest["original_input"]) / "events.sqlite", after=after
        ),
        args.output,
        metadata,
        runtime,
        policy,
        max_paced_events=args.max_paced_events,
    )


def demo(args):
    from examples.serving_smoke import synthetic_release

    from fraud_detection_mlops.serving.model_release import load_release
    from fraud_detection_mlops.streaming.contracts.customer_day_alerts import (
        CustomerDayAlertConfig,
    )
    from fraud_detection_mlops.streaming.replay.durable_triage_replay import event_order
    from fraud_detection_mlops.streaming.replay.synthetic_event_stream import synthetic_events

    release = args.output.parent / "generated-monitoring-release"
    if not release.exists():
        synthetic_release(release)
    runtime = load_release(release, sha256(release / "manifest.json"))
    if runtime.manifest.evidence_kind != "synthetic_smoke":
        raise ValueError("Demo requires a generated model")
    policy = CustomerDayAlertConfig(
        schema_version=1,
        version="first_crossing_customer_day_v1",
        model_sha256=runtime.manifest.model_sha256,
        serving_release_id=runtime.manifest.serving_release_id,
        score_threshold=0.5,
        daily_customer_budget=100,
    )
    events = list({e.event_id: e for e in synthetic_events()}.values())
    events.sort(key=event_order)
    checksum = hashlib.sha256("\n".join(e.model_dump_json() for e in events).encode()).hexdigest()
    metadata = make_metadata(
        input_sha256=checksum,
        runtime=runtime,
        policy=policy,
        speed=args.speed,
        capacity=args.capacity,
        window=None,
        expected_counts={"events": 20, "transactions": 10, "feedback": 10},
        baseline=[1] * 10,
        source_scope="generated_fixture",
        code={name: sha256(PROJECT_ROOT / name) for name in operational_files()},
    )
    return execute(
        lambda after: (e for e in events if after is None or event_order(e) > after),
        args.output,
        metadata,
        runtime,
        policy,
        max_paced_events=args.max_paced_events,
    )


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("demo", "native"):
        p = commands.add_parser(name)
        p.add_argument("--output", type=Path, required=True)
        p.add_argument("--speed", type=float, default=86400)
        p.add_argument("--capacity", type=int, default=128)
        p.add_argument("--max-paced-events", type=int)
        if name == "native":
            p.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
            p.add_argument(
                "--release", type=Path, default=PROJECT_ROOT / "data/serving/reference-v1"
            )
            p.add_argument(
                "--threshold",
                type=Path,
                default=PROJECT_ROOT / "data/replay/policies/development-threshold-v1",
            )
    args = parser.parse_args()
    try:
        result = demo(args) if args.command == "demo" else native_run(args)
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Paced replay failed: {exc}\n")
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
