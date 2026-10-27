"""Opt-in daily arrivals with concurrent isolated UI and native session telemetry."""

from contextlib import ExitStack
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time

import pandas as pd
import pytest
from tests.product_session.operations import Observer, Worker, progressing, require, sample

pytestmark = pytest.mark.skipif(
    os.environ.get("FRAUD_LIVE_OPERATIONS") != "1",
    reason="Explicit daily live-operation rehearsal",
)


def probe_ui(root, portal_id, pin):
    python = root / ".tox/portal-ui/bin/python"
    require(python.is_file(), "Run tox -e portal-ui before the native rehearsal")
    env = {
        k: v
        for k, v in os.environ.items()
        if k in {"PATH", "HOME", "LANG", "LC_ALL", "TZ", "TMPDIR", "XDG_CACHE_HOME"}
    }
    env.update(
        PYTHONPATH=str(root), STREAMLIT_BROWSER_GATHER_USAGE_STATS="false", DO_NOT_TRACK="true"
    )
    result = subprocess.run(
        [
            str(python),
            str(root / "tests/fraud_portal/live/probe_arrivals.py"),
            "--portal-id",
            portal_id,
            "--session-sha256",
            pin,
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    require(result.returncode == 0, "Isolated UI probe failed: " + result.stderr[-3000:])
    lines = [
        v.split("=", 1)[1]
        for v in result.stdout.splitlines()
        if v.startswith("FRAUD_LIVE_PORTAL_PROBE=")
    ]
    require(len(lines) == 1, "Missing isolated UI receipt")
    return json.loads(lines[0])


def test_daily_arrivals_live_portal_metrics_pause_resume_and_delayed_feedback():
    from scripts.fraud_portal import controller as portal
    from scripts.fraud_portal import data
    from scripts.platform import kind_foundation as f
    from scripts.platform import kind_observability as k
    from scripts.platform import kind_operations as ops
    from scripts.platform import local_access
    from scripts.product_session import controller as session
    from scripts.product_session import reader
    from scripts.session_monitoring import collector
    from scripts.session_monitoring import controller as monitor

    require(
        os.environ.get("FRAUD_SESSION_NO_OTHER_WRITERS") == "1",
        "Declare exclusive producer, route and Registry operation",
    )
    key = os.environ.get("FRAUD_LIVE_SESSION_ID", "arrivals-day-v1")
    portal_id = os.environ.get("FRAUD_PORTAL_ID", "investigation-v1")
    require(key not in {"arrival-v1", "arrival-2k-v1"}, "Use a new session ID")
    baseline = Path(
        os.environ.get(
            "FRAUD_LIVE_BASELINE",
            "data/storage/kind-platform/operations/baseline-arrivals-day-v1.json",
        )
    )
    for name in (
        "tests/product_session/live/test_live_operations.py",
        "tests/product_session/operations.py",
        "tests/fraud_portal/live/probe_arrivals.py",
        "tox.toml",
        "infra/make/session.mk",
    ):
        committed = subprocess.run(
            ["git", "show", "HEAD:" + name], cwd=f.ROOT, capture_output=True, check=False
        )
        import hashlib

        require(
            committed.returncode == 0
            and hashlib.sha256(committed.stdout).hexdigest() == reader.sha(f.ROOT / name),
            "Commit reviewed rehearsal code: " + name,
        )
    require((f.ROOT / ".tox/portal-ui/bin/python").is_file(), "Run tox -e portal-ui first")
    directory, value = session.load(key)
    window = value["expanded_window"]
    expected = value["capture"]["counts"]["decisions"]
    require(
        pd.Timestamp(window["end_exclusive"]) - pd.Timestamp(window["start"])
        == pd.Timedelta(days=1),
        "Use exactly one daily window",
    )
    require(
        expected == window["eligible_transactions"] and 1000 <= expected <= 50000,
        "Capture the full available daily window, 1000..50000 decisions",
    )
    binding, _ = collector.identity(directory)
    require(
        monitor.settings()["binding"] == binding, "Explicitly bind the same session in monitoring"
    )
    require(
        portal.session_config(portal_id)["session_sha256"] == binding["session_sha256"],
        "Explicitly bind the same session in the unified portal",
    )
    portal.smoke(portal_id, 18501)
    initial_active = session.flow.active()
    ops.check(baseline)
    static = [directory / name for name in ("session.json", "input.json", "events.sqlite")]
    for old_key, report in (
        ("arrival-v1", "native-rehearsal.json"),
        ("arrival-2k-v1", "native-scale-rehearsal.json"),
    ):
        old, _ = session.load(old_key)
        static += [
            old / name for name in ("session.json", "input.json", "checkpoint.sqlite", report)
        ]
    config, _, _ = portal.config(portal_id)
    evaluation = portal.performance_config(portal_id)
    require(evaluation is not None, "Preserve the accepted performance evaluation")
    static += [Path(config["directory"]) / name for name in ("manifest.json", "snapshot.json")]
    static += [
        Path(evaluation[0]["directory"]) / name for name in ("manifest.json", "performance.json")
    ]
    frozen = {str(p): data.sha(p) for p in static}
    final_path, progress_path = (
        directory / "native-live-rehearsal.json",
        directory / "live-progress.json",
    )
    if final_path.exists():
        report = json.loads(final_path.read_bytes())
        require(
            report["session_sha256"] == binding["session_sha256"]
            and report["accepted_sources_sha256"] == frozen,
            "Completed rehearsal identity changed",
        )
        checked = session.verify(directory, value)
        require(checked["counts"]["decisions"] == expected, "Completed session differs")
        print(
            json.dumps(
                {
                    "status": "completed_live_rehearsal_readback_verified",
                    "report": str(final_path),
                    "new_arrivals_executed": False,
                    "champion_modified": False,
                },
                indent=2,
            )
        )
        return
    if progress_path.exists():
        progress = json.loads(progress_path.read_bytes())
        require(
            progress["session_sha256"] == binding["session_sha256"]
            and progress["accepted_sources_sha256"] == frozen,
            "Interrupted rehearsal identity changed",
        )
    else:
        require(
            not (directory / "checkpoint.sqlite").exists(),
            "Use a new prepared checkpoint; preserve manually executed sessions",
        )
        progress = {
            "session_sha256": binding["session_sha256"],
            "accepted_sources_sha256": frozen,
            "stages": {},
            "observations": [],
        }
        session.atomic(progress_path, progress)

    def save():
        session.atomic(progress_path, progress)

    observer, producer = None, None
    with ExitStack() as stack:
        lock = stack.enter_context((monitor.work() / "watch.lock").open("a"))
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("Stop session-monitor-watch before this rehearsal") from exc
        uri = stack.enter_context(monitor.forward())
        prom = stack.enter_context(k.forward("prometheus"))
        graf = stack.enter_context(k.forward("grafana"))
        api = stack.enter_context(local_access.connection("api"))

        def start_observer():
            return Observer(
                lambda: collector.collect(directory, binding),
                lambda payload: monitor.put(uri, "/projection", payload),
                interval=2,
            ).start()

        def numeric(name):
            result = list(
                k.query(
                    prom,
                    "fraud_session_"
                    + name
                    + '{session_sha256="'
                    + binding["session_sha256"]
                    + '"}',
                ).values()
            )
            return result[0] if len(result) == 1 else None

        def read_view():
            deadline = time.monotonic() + 10
            while True:
                try:
                    return reader.read(directory, binding["session_sha256"], page_size=25)
                except sqlite3.OperationalError as exc:
                    if (
                        "no such table" not in str(exc)
                        or (directory / "heartbeat.json").exists()
                        or time.monotonic() >= deadline
                    ):
                        raise
                    time.sleep(0.05)

        def observe():
            latest = observer.latest()
            if latest is None:
                return
            metric, valid = numeric("decisions"), numeric("source_valid")
            if metric is None or valid != 1:
                return
            view = read_view()
            row = sample(binding, latest, view["totals"]["decisions"], metric, valid)
            records = progress["observations"]
            require(
                not records or metric >= records[-1]["prometheus_decisions"],
                "Session metric regressed",
            )
            if not records or metric != records[-1]["prometheus_decisions"]:
                records.append(row)
                save()

        def run_phase(name, *, feedback=False, stop_after=None, live_ui=False):
            nonlocal producer
            producer = Worker(lambda: session.execute(key, True, 0.05, feedback, stop_after))
            deadline, announced = time.monotonic() + 5400, 0.0
            while not producer.done.wait(1):
                observer.latest()
                observe()
                view = read_view()
                count = view["totals"]["decisions"]
                if (
                    live_ui
                    and "ui_running" not in progress["stages"]
                    and 600 <= count < expected - 100
                    and view["producer"]["status"] == "running"
                ):
                    receipt = probe_ui(f.ROOT, portal_id, binding["session_sha256"])
                    require(
                        not producer.done.is_set(),
                        "Producer completed before live UI observation; preserve this attempt",
                    )
                    progress["stages"]["ui_running"] = receipt
                    save()
                if time.monotonic() - announced >= 45:
                    print(
                        f"Live phase {name}: {view['counts']['events']} events; {count}/{expected} decisions",
                        flush=True,
                    )
                    announced = time.monotonic()
                require(
                    time.monotonic() < deadline,
                    "Native phase exceeded 90 minutes; preserve checkpoint",
                )
            result = producer.join()
            producer = None
            observe()
            progress["stages"][name] = result
            save()
            return result

        try:
            observer = start_observer()
            paused_at = min(500, expected // 4)
            if "first" not in progress["stages"]:
                now = reader.read(directory, binding["session_sha256"])["counts"]["decisions"]
                require(now <= paused_at, "Unexpected progress before the planned pause")
                if now < paused_at:
                    result = run_phase("first", stop_after=paused_at - now)
                    require(result["status"] == "paused", "First phase must pause")
                else:
                    require(
                        reader.read(directory, binding["session_sha256"])["producer"]["status"]
                        == "paused",
                        "Missing pause acknowledgement",
                    )
                    progress["stages"]["first"] = {
                        "status": "paused",
                        "resumed_after_receipt_interruption": True,
                    }
                    save()
            if "collection_recovery" not in progress["stages"]:
                observer.stop()
                observer = None
                paused = collector.collect(directory, binding)
                require(
                    paused["counts"]["decisions"] == paused_at
                    and paused["producer_status"] == "paused",
                    "Paused checkpoint differs",
                )
                monitor.put(uri, "/projection", paused)
                monitor.check_projection(uri, prom, graf, paused, True)
                progress["stages"]["ui_paused"] = probe_ui(
                    f.ROOT, portal_id, binding["session_sha256"]
                )
                print(
                    "Paused producer; waiting for collection expiry while API remains available...",
                    flush=True,
                )
                deadline = time.monotonic() + 35
                while time.monotonic() < deadline:
                    time.sleep(min(1, max(0, deadline - time.monotonic())))
                ready = k.http(api["url"], "/ready")
                require(
                    ready["serving_release_id"] == binding["serving_release_id"],
                    "API identity changed during collection expiry",
                )
                monitor.check_projection(uri, prom, graf, paused, False)
                require(
                    collector.collect(directory, binding)["counts"] == paused["counts"],
                    "Decisions changed while producer paused",
                )
                refreshed = collector.collect(directory, binding)
                monitor.put(uri, "/projection", refreshed)
                monitor.check_projection(uri, prom, graf, refreshed, True)
                observer = start_observer()
                progress["stages"]["collection_recovery"] = {
                    "source_expiry_and_recovery_verified": True,
                    "paused_counts_unchanged": True,
                    "API_ready_during_expiry": True,
                }
                save()
            if "arrivals" not in progress["stages"]:
                require(
                    run_phase("arrivals", live_ui=True)["status"] == "awaiting_feedback",
                    "Arrivals must finish before delayed feedback",
                )
            require(
                "ui_running" in progress["stages"],
                "No live UI observation; preserve the session and investigate",
            )
            require(
                progressing(progress["observations"], expected),
                "No concurrent Prometheus progress was observed",
            )
            if "feedback" not in progress["stages"]:
                before_feedback = read_view()
                if not progress["stages"].get("feedback_hidden_before_drain"):
                    require(
                        before_feedback["totals"]["decisions"] == expected
                        and before_feedback["totals"]["feedback"] == 0,
                        "Daily scored feedback was exposed before its seven-day availability",
                    )
                    progress["stages"]["feedback_hidden_before_drain"] = True
                    save()
                require(
                    run_phase("feedback", feedback=True)["status"] == "complete",
                    "Feedback drain incomplete",
                )
            observer.stop()
            observer = None
            completed = collector.collect(directory, binding)
            monitor.put(uri, "/projection", completed)
            monitor.check_projection(uri, prom, graf, completed, True)
            require(
                completed["counts"]["decision_feedback"] == expected,
                "Mature decision feedback is incomplete",
            )
            verified = session.verify(directory, value)
            reused = session.execute(key, True, 0.05, True, None)
            require(reused["scoring_HTTP_attempts"] == 0, "Completed reuse scored again")
            require(
                frozen == {str(p): data.sha(p) for p in static}, "Accepted source bytes changed"
            )
            require(session.flow.active() == initial_active, "Serving or champion changed")
            portal.smoke(portal_id, 18501)
            ops.check(baseline)
            report = {
                "schema": "fraud_live_operations_native_v1",
                "status": "success",
                "session_id": key,
                "session_sha256": binding["session_sha256"],
                "binding": binding,
                "window": window,
                "stages": progress["stages"],
                "concurrent_observations": progress["observations"],
                "verification": verified,
                "completed_reuse": reused,
                "accepted_sources_sha256": frozen,
                "live_arrivals_native_validated": True,
                "same_session_Grafana_validated": True,
                "isolated_live_portal_navigation_validated": True,
                "browser_visual_review_validated": False,
                "champion_modified": False,
                "fit_executed": False,
                "new_independent_quality_confirmation": False,
            }
            f.private_json(final_path, report)
            print(json.dumps(report, indent=2))
        finally:
            try:
                if producer is not None and not producer.done.is_set():
                    session.atomic(directory / "pause.request", {"requested_at": time.time()})
                    producer.join(120)
            finally:
                if observer is not None:
                    observer.stop()
