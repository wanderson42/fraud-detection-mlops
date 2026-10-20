"""Synthetic checkpoint interruption gate; runs only in a newly owned rehearsal PVC."""

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time

from examples.serving_smoke import synthetic_release
from threadpoolctl import threadpool_limits

from fraud_detection_mlops.serving.model_release import load_release
from fraud_detection_mlops.streaming.contracts.customer_day_alerts import CustomerDayAlertConfig
from fraud_detection_mlops.streaming.contracts.transaction_events import canonical_event
from fraud_detection_mlops.streaming.replay.durable_triage_replay import DurableTriageReplay
from fraud_detection_mlops.streaming.replay.synthetic_event_stream import synthetic_events


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    """Publish complete markers; never replace an earlier phase."""
    data = (json.dumps(value, sort_keys=True, allow_nan=False) + "\n").encode()
    temporary = path.with_suffix(".pending")
    with temporary.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    require(not path.exists(), "Phase already exists; preserve the PVC")
    os.replace(temporary, path)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def logical(path):
    """Compare every row and schema statement; physical SQLite bytes may differ."""
    with closing(sqlite3.connect(path)) as connection:
        require(
            connection.execute("PRAGMA integrity_check").fetchone() == ("ok",), "SQLite integrity"
        )
        require(not connection.execute("PRAGMA foreign_key_check").fetchall(), "Foreign keys")
        schema = connection.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name"
        ).fetchall()
        tables = [r[1] for r in schema if r[0] == "table"]
        rows = {
            name: sorted(connection.execute('SELECT * FROM "' + name + '"').fetchall(), key=repr)
            for name in tables
        }
        raw = json.dumps({"schema": schema, "rows": rows}, sort_keys=True, allow_nan=False)
        return hashlib.sha256(raw.encode()).hexdigest()


def run(root, run_id):
    require(re.fullmatch(r"[a-f0-9]{32}", run_id), "Invalid rehearsal identity")
    require(root.resolve() == root.absolute(), "Use an unredirected PVC root")
    root.mkdir(mode=0o700, exist_ok=True)
    require(root.is_dir(), "PVC run directory is missing")
    events = synthetic_events()
    digest = hashlib.sha256("\n".join(canonical_event(e) for e in events).encode()).hexdigest()
    setup_path = root / "setup.json"
    if not setup_path.exists():
        require(not any(root.iterdir()), "Unknown or incomplete PVC; preserve it")
        receipt = synthetic_release(root / "release")
        write(
            setup_path,
            {
                "run_id": run_id,
                "input_sha256": digest,
                "manifest_sha256": receipt["manifest_sha256"],
                "release_hashes": {p.name: sha(p) for p in sorted((root / "release").iterdir())},
            },
        )
    setup = json.loads(setup_path.read_text())
    require(setup["run_id"] == run_id and setup["input_sha256"] == digest, "PVC identity changed")
    require(
        setup["release_hashes"] == {p.name: sha(p) for p in sorted((root / "release").iterdir())},
        "Synthetic release bytes changed",
    )
    runtime = load_release(root / "release", setup["manifest_sha256"])
    policy = CustomerDayAlertConfig(
        schema_version=1,
        version="first_crossing_customer_day_v1",
        model_sha256=runtime.manifest.model_sha256,
        serving_release_id=runtime.manifest.serving_release_id,
        score_threshold=0.0,
        daily_customer_budget=2,
    )

    def replay(name):
        return DurableTriageReplay(
            root / name,
            run_id="kind-replay-" + run_id,
            input_sha256=digest,
            runtime=runtime,
            policy=policy,
            data_scope="synthetic_fixture",
        )

    pause_path = root / "interrupted.json"
    if not pause_path.exists():
        with replay("reference.sqlite") as reference:
            for event in events:
                reference.handle(event)
        with replay("checkpoint.sqlite") as partial:
            for event in events[:8]:
                partial.handle(event)
            before = partial.summary()

            def interrupt():
                write(
                    pause_path,
                    {
                        "run_id": run_id,
                        "phase": "transaction_open",
                        "before": before,
                        "inflight_event_id": events[8].event_id,
                        "reference_logical_sha256": logical(root / "reference.sqlite"),
                    },
                )
                print(json.dumps({"phase": "transaction_open", "run_id": run_id}), flush=True)
                # SQLite invokes this function after decision/alert writes and before checkpoint/COMMIT.
                # The controller deletes this exact pod; the next process must recover the journal.
                while True:
                    time.sleep(1)

            partial.connection.create_function("interrupt_before_commit", 0, interrupt)
            partial.connection.execute(
                "CREATE TEMP TRIGGER controlled_interruption BEFORE UPDATE ON checkpoint "
                "BEGIN SELECT interrupt_before_commit(); END"
            )
            partial.handle(events[8])
            raise AssertionError("Interruption unexpectedly returned")
    pause = json.loads(pause_path.read_text())
    require(pause["run_id"] == run_id, "Foreign interruption marker")
    result_path = root / "result.json"
    if result_path.exists():
        saved = json.loads(result_path.read_text())
        require(
            saved["run_id"] == run_id and saved["worker_sha256"] == sha(Path(__file__)),
            "Completed worker identity changed",
        )
        require(
            saved["logical_sha256"]
            == logical(root / "checkpoint.sqlite")
            == logical(root / "reference.sqlite"),
            "Completed ledger changed",
        )
        return saved
    with replay("checkpoint.sqlite") as resumed:
        require(
            resumed.summary() == pause["before"], "In-flight transaction did not fully roll back"
        )
        accepted = reused = score_calls = 0
        original = runtime.score

        def score(request):
            nonlocal score_calls
            score_calls += 1
            return original(request)

        runtime.score = score
        for event in events:
            item = resumed.handle(event)
            reused += int(item["reused"])
            accepted += int(not item["reused"])
        final = resumed.summary()
        before_duplicate = logical(root / "checkpoint.sqlite")

        def forbidden(_):
            raise AssertionError("Duplicate delivery attempted to rescore")

        runtime.score = forbidden
        require(all(resumed.handle(e)["reused"] for e in events), "Duplicate was not reused")
        require(logical(root / "checkpoint.sqlite") == before_duplicate, "Duplicates changed rows")
        require(
            score_calls == final["counts"]["decisions"] - pause["before"]["counts"]["decisions"],
            "Committed scores were recomputed",
        )
    expected = logical(root / "reference.sqlite")
    require(expected == pause["reference_logical_sha256"], "Reference changed")
    require(
        logical(root / "checkpoint.sqlite") == expected, "Resumed ledger differs from reference"
    )
    require(
        final["counts"]["events"] == 20 and accepted == 12 and reused == 10, "Event counts differ"
    )
    result = {
        "schema": "fraud_kind_replay_worker_v1",
        "status": "success",
        "run_id": run_id,
        "scope": "synthetic_durable_replay_inflight_SQLite_rollback_and_same_PVC_resume",
        "input_deliveries": len(events),
        "counts": final["counts"],
        "accepted_after_restart": accepted,
        "reused_after_restart": reused,
        "score_calls_after_restart": score_calls,
        "inflight_transaction_rollback_validated": True,
        "all_schema_and_table_rows_equal": True,
        "logical_sha256": expected,
        "duplicate_redelivery_without_rescoring_validated": True,
        "release_manifest_sha256": setup["manifest_sha256"],
        "model_sha256": runtime.manifest.model_sha256,
        "input_sha256": digest,
        "worker_sha256": sha(Path(__file__)),
        "synthetic_fixture_fitted": True,
        "historical_model_refit": False,
        "historical_sources_mounted": False,
        "primary_storage_migrated": False,
        "production_promotion": False,
        "external_alerts_published": False,
        "backup_restore_validated": False,
        "cluster_recreation_validated": False,
    }
    write(result_path, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=Path("/state/run"))
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    with threadpool_limits(limits=2):
        print(json.dumps(run(args.state, args.run_id)), flush=True)
    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()
