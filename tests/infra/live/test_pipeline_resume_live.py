"""Opt-in controller termination with live owned Jobs, then stage and result reuse."""

import json
import os
import subprocess
import sys
import time
from uuid import uuid4

import pytest


@pytest.mark.skipif(
    os.environ.get("FRAUD_PIPELINE_RESUME_LIVE") != "1",
    reason="Opt-in new dataset Jobs; never runs against CI or old accepted attempts",
)
def test_controller_exit_job_reuse_stage_resume_and_duplicate_invocation():
    from scripts.platform import host_supervision
    from scripts.platform import kind_foundation as f
    from scripts.platform import pipeline_runs as runs

    f.require(
        os.environ.get("FRAUD_PIPELINE_NO_OTHER_WRITERS") == "1",
        "Declare no other platform/dataset writers before this native rehearsal",
    )
    session_id = os.environ.get("FRAUD_PIPELINE_SESSION_ID")
    f.require(bool(session_id), "Set FRAUD_PIPELINE_SESSION_ID to the accepted session")
    before = host_supervision.check(session_id)
    run_id = uuid4().hex
    print("Identified pipeline run: " + run_id, flush=True)
    with f.exclusive():
        plan = runs.prepare(run_id, os.environ.get("FRAUD_PIPELINE_BRONZE_BUNDLE_ID"))
    root = runs.directory(run_id)
    log = root / "native-controller.log"
    fd = os.open(log, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    command = [sys.executable, "-m", "scripts.platform.pipeline_runs", "run", "--run-id", run_id]
    with os.fdopen(fd, "wb") as output:
        process = subprocess.Popen(
            command, cwd=f.ROOT, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT
        )
    receipt = {
        "schema": "fraud_pipeline_run_native_v1",
        "status": "failed",
        "run_id": run_id,
        "plan_sha256": runs.archive.digest(root / "plan.json"),
        "native_controller_log": str(log),
        "fit_executed": False,
        "champion_modified": False,
        "accepted_session_id": session_id,
    }
    try:
        deadline = time.monotonic() + 150
        name = plan["release"] + "-silver"
        while True:
            f.require(
                process.poll() is None, "Controller exited before live interruption; inspect log"
            )
            job = runs.read_job(name)
            if job and job.get("status", {}).get("active", 0) > 0:
                break
            f.require(
                time.monotonic() < deadline, "Silver Job did not become active; inspect attempt"
            )
            time.sleep(1)
        uid = job["metadata"]["uid"]
        duplicate = subprocess.run(
            command, cwd=f.ROOT, capture_output=True, text=True, timeout=15, check=False
        )
        f.require(
            duplicate.returncode != 0 and "Another" in duplicate.stderr,
            "Duplicate CLI was not refused by the platform/run lock",
        )
        # Only terminate this Popen child. Never search by command name or touch its Job.
        process.terminate()
        process.wait(timeout=15)
        f.require(
            process.returncode != 0 and runs.read_job(name)["metadata"]["uid"] == uid,
            "Owned Job did not survive the controller exit",
        )
        print("Controller stopped; resuming the same Silver Job UID...", flush=True)
        with f.exclusive():
            paused = runs.execute(run_id, stop_after="silver")
        f.require(paused["status"] == "pipeline_run_paused", "Silver did not pause before Gold")
        f.require(runs.load(root / "job-silver.json")["uid"] == uid, "Silver Job was replaced")
        silver_sha = runs.archive.digest(root / "stage-silver.json")
        with f.exclusive():
            paused = runs.execute(run_id, stop_after="gold")
        f.require(
            paused["after_stage"] == "gold" and not (root / "result.json").exists(),
            "Expected pause after Gold before cleanup/final result",
        )
        gold_sha = runs.archive.digest(root / "stage-gold.json")
        with f.exclusive():
            result = runs.execute(run_id)
            reused = runs.execute(run_id)
        f.require(result == reused and result["status"] == "success", "Completed run not reused")
        f.require(
            runs.archive.digest(root / "stage-silver.json") == silver_sha
            and runs.archive.digest(root / "stage-gold.json") == gold_sha,
            "Sealed stage receipts changed during reuse",
        )
        after = host_supervision.check(session_id)
        for field in ("binding", "counts", "accepted_sources_unchanged"):
            f.require(before[field] == after[field], "Accepted session changed: " + field)
        f.require(
            after["protected_resources_unchanged"] and not after["new_inferences_executed"],
            "Protected resources or session inference changed",
        )
        receipt.update(
            status="verified",
            controller_exit_job_uid_preserved=True,
            duplicate_invocation_refused=True,
            stages_reused_without_new_jobs=True,
            completed_result_reused=True,
            temporary_jobs_removed=True,
            source_session_unchanged=True,
            new_session_inferences=False,
            silver_job_uid=uid,
            silver_receipt_sha256=silver_sha,
            gold_receipt_sha256=gold_sha,
            result_sha256=runs.archive.digest(root / "result.json"),
        )
        print(json.dumps(receipt, indent=2), flush=True)
    except BaseException as exc:
        receipt.update(error_type=type(exc).__name__, reason=str(exc)[:1000])
        raise
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        runs.seal(root / "native-acceptance.json", receipt)
        print("Native receipt: " + str(root / "native-acceptance.json"), flush=True)
        print(
            "Do not discard a failed attempt or rerun with another ID before inspection.",
            flush=True,
        )
