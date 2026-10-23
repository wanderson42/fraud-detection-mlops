"""Pace bounded observed transactions through the champion; preserve causal checkpoints."""

import argparse
from contextlib import closing
import json
import math
import os
from pathlib import Path
import sqlite3
import subprocess
import time
from uuid import uuid4

from scripts.fraud_portal import controller as portal
from scripts.fraud_portal import theme
from scripts.platform import kind_foundation as f
from scripts.product_flow import controller as flow
from scripts.product_session import reader

from fraud_detection_mlops.data.datasets.native_replay_dataset import (
    readonly_connection,
    stream_events,
)
from fraud_detection_mlops.streaming.replay.native_causal_replay import job_lock


def folder(key):
    return flow.work("session-" + flow.p.round_id(key))


def atomic(path, value):
    f.managed_destination(path, "storage")
    stage = path.with_name("." + path.name + "." + uuid4().hex)
    try:
        f.private_json(stage, value)
        os.replace(stage, path)
    finally:
        stage.unlink(missing_ok=True)


def prepare(key, source, declared):
    flow.p.require(declared, "Declare SESSION_NO_OTHER_WRITERS=1")
    # The session is additive: accepted historical product flows stay untouched.
    for path in (f.ROOT / "scripts/product_session").glob("*.py"):
        name = path.relative_to(f.ROOT).as_posix()
        saved = subprocess.run(
            ["git", "show", "HEAD:" + name], cwd=f.ROOT, capture_output=True, check=False
        )
        import hashlib

        flow.p.require(
            saved.returncode == 0 and hashlib.sha256(saved.stdout).hexdigest() == reader.sha(path),
            "Commit reviewed session implementation before prepare: " + name,
        )
    directory = folder(key)
    flow.prepare("session-" + key, source, declared)
    config = {
        "schema": "fraud_product_session_v1",
        "session_id": key,
        "input_sha256": reader.sha(directory / "input.json"),
        "code_pins": {
            v.name: reader.sha(v) for v in (f.ROOT / "scripts/product_session").glob("*.py")
        },
    }
    receipt = reader.checked(directory / "session.json")
    if receipt.exists():
        flow.p.require(
            json.loads(receipt.read_bytes()) == config,
            "Session implementation or input changed; preserve session",
        )
    else:
        f.private_json(receipt, config)
    return {
        "status": "session_prepared",
        "session_id": key,
        "session_sha256": reader.sha(receipt),
        "directory": str(directory),
        "champion_modified": False,
    }


def load(key):
    directory = folder(key)
    config = json.loads(reader.checked(directory / "session.json").read_bytes())
    flow.p.require(
        config["schema"] == "fraud_product_session_v1"
        and config["session_id"] == key
        and config["code_pins"]
        == {v.name: reader.sha(v) for v in (f.ROOT / "scripts/product_session").glob("*.py")},
        "Session code identity changed",
    )
    value = flow.read("session-" + key)
    flow.p.require(
        reader.sha(directory / "input.json") == config["input_sha256"], "Session input changed"
    )
    return directory, value


def heartbeat(directory, status):
    atomic(
        directory / "heartbeat.json",
        {
            "status": status,
            "timestamp": time.time(),
            "session_sha256": reader.sha(directory / "session.json"),
        },
    )


def pump(
    directory, value, scorer, *, interval=1.0, feedback=False, stop_after=None, wait=time.sleep
):
    flow.p.require(
        isinstance(interval, (float, int)) and math.isfinite(interval) and 0.05 <= interval <= 60,
        "Interval must be 0.05..60 seconds",
    )
    flow.p.require(
        stop_after is None or type(stop_after) is int and stop_after > 0, "Invalid stop count"
    )
    status = "paused"
    with job_lock(directory):
        (directory / "pause.request").unlink(missing_ok=True)
        try:
            with flow.replay(directory / "checkpoint.sqlite", value, scorer) as engine:
                before = engine.summary()["counts"]["decisions"]
                last = engine.summary()["last_event_order"]
                heartbeat(directory, "draining_feedback" if feedback else "warming_up")
                start, end = value["capture"]["scoring_window_ns"]
                announced = time.monotonic()
                for event in stream_events(
                    directory / "events.sqlite", after=tuple(last) if last[0] is not None else None
                ):
                    if (directory / "pause.request").exists():
                        break
                    if not feedback and event.event_clock_ns > end:
                        status = "awaiting_feedback"
                        break
                    scored = event.event_type == "transaction" and event.event_clock_ns >= start
                    if scored:
                        heartbeat(directory, "running")
                        # Interruptible pacing; never wait inside a SQLite transaction.
                        remaining = float(interval)
                        while remaining > 0 and not (directory / "pause.request").exists():
                            step = min(remaining, 0.25)
                            wait(step)
                            remaining -= step
                            if time.monotonic() - announced >= 2:
                                heartbeat(directory, "running")
                                announced = time.monotonic()
                        if (directory / "pause.request").exists():
                            break
                    engine.handle(event)
                    if scored or time.monotonic() - announced >= 2:
                        heartbeat(
                            directory,
                            "draining_feedback"
                            if feedback
                            else "running"
                            if event.event_clock_ns >= start
                            else "warming_up",
                        )
                        announced = time.monotonic()
                    if (
                        stop_after is not None
                        and scored
                        and engine.connection.execute("SELECT count(*) FROM decisions").fetchone()[
                            0
                        ]
                        - before
                        >= stop_after
                    ):
                        break
                else:
                    status = "complete"
                summary = engine.summary()
                if summary["counts"]["events"] == value["capture"]["counts"]["events"]:
                    status = "complete"
        except KeyboardInterrupt:
            status = "paused"
        except BaseException:
            heartbeat(directory, "failed")
            raise
        heartbeat(directory, status)
    return {"status": status, "champion_modified": False, "fit_executed": False}


def execute(key, declared, interval, feedback, stop_after):
    flow.p.require(
        declared, "Declare SESSION_NO_OTHER_WRITERS=1; preserve Registry and route identity"
    )
    directory, value = load(key)
    if feedback:
        with closing(readonly_connection(directory / "checkpoint.sqlite")) as conn:
            flow.p.require(
                conn.execute("SELECT count(*) FROM decisions").fetchone()[0]
                == value["capture"]["counts"]["decisions"],
                "Finish transaction arrivals before advancing delayed feedback",
            )
    with flow.target(value) as scorer:
        return pump(
            directory, value, scorer, interval=interval, feedback=feedback, stop_after=stop_after
        )


def open_portal(key, port):
    directory, _ = load(key)
    python = portal.ENVIRONMENT / "bin/python"
    flow.p.require(python.is_file(), "Execute make portal-install")
    flow.p.require(1024 <= port <= 65535, "Invalid port")
    environment = {
        k: v
        for k, v in os.environ.items()
        if k in {"PATH", "HOME", "LANG", "LC_ALL", "TZ", "TMPDIR", "XDG_CACHE_HOME"}
    }
    environment.update(
        PYTHONPATH=str(f.ROOT),
        FRAUD_SESSION_DIRECTORY=str(directory),
        FRAUD_SESSION_SHA256=reader.sha(directory / "session.json"),
        STREAMLIT_BROWSER_GATHER_USAGE_STATS="false",
        DO_NOT_TRACK="true",
    )
    return subprocess.run(
        [
            str(python),
            "-m",
            "streamlit",
            "run",
            str(f.ROOT / "scripts/product_session/app.py"),
            "--server.address=127.0.0.1",
            f"--server.port={port}",
            "--server.headless=true",
            *theme.launch_flags(),
        ],
        cwd=f.ROOT,
        env=environment,
        check=False,
    ).returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=["prepare", "run", "pause", "feedback", "status", "verify", "open"]
    )
    parser.add_argument("--session-id", default="arrival-v1")
    parser.add_argument("--source-state", type=Path, default=Path("data/replay/paced-native-v1"))
    parser.add_argument("--no-other-writers", action="store_true")
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--stop-after", type=int)
    parser.add_argument("--port", type=int, default=18502)
    args = parser.parse_args()
    try:
        if args.action == "prepare":
            with f.exclusive():
                result = prepare(args.session_id, args.source_state, args.no_other_writers)
        elif args.action == "pause":
            directory, _ = load(args.session_id)
            atomic(directory / "pause.request", {"requested_at": time.time()})
            result = {
                "status": "pause_requested",
                "note": "Wait for producer acknowledgement in session-status",
            }
        elif args.action == "open":
            raise SystemExit(open_portal(args.session_id, args.port))
        elif args.action in {"run", "feedback"}:
            result = execute(
                args.session_id,
                args.no_other_writers,
                args.interval,
                args.action == "feedback",
                args.stop_after,
            )
        else:
            directory, value = load(args.session_id)
            result = (
                reader.read(directory, reader.sha(directory / "session.json"))
                if args.action == "status"
                else flow.verify_ledger(directory, value)
            )
            if args.action == "status":
                result.pop("cases")
        print(json.dumps(result, indent=2, allow_nan=False))
    except (ValueError, OSError, KeyError, sqlite3.Error) as exc:
        parser.exit(1, f"Session stopped: {exc}\n")


if __name__ == "__main__":
    main()
