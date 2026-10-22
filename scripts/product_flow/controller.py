"""Prepare, resume and reconcile causal events through the active KinD champion."""

import argparse
from contextlib import closing, contextmanager
import json
from pathlib import Path
import sqlite3
import subprocess
import time
from uuid import uuid4

from scripts.model_updates import controller as updates
from scripts.model_updates import deployment
from scripts.operational_round import policy as p
from scripts.operational_round import serving
from scripts.platform import kind_foundation as f
from scripts.platform import source_compatibility
from scripts.product_flow import inputs
from scripts.product_flow.http_scoring import HTTPScorer, checked_response, digest
from scripts.product_flow.ledger import HTTPReplay

from fraud_detection_mlops.data.datasets.native_replay_dataset import (
    readonly_connection,
    stream_events,
)
from fraud_detection_mlops.serving.model_release import ReleaseManifest
from fraud_detection_mlops.streaming.contracts.customer_day_alerts import CustomerDayAlertConfig
from fraud_detection_mlops.streaming.contracts.transaction_events import LABEL_DELAY_NS, clock_ns
from fraud_detection_mlops.streaming.replay.native_causal_replay import job_lock

POLICY = "references/product_flow_policy_v1.json"


def policy():
    value = json.loads((f.ROOT / POLICY).read_bytes())
    p.require(
        value["schema"] == "fraud_product_flow_policy_v1"
        and type(value["scored_transactions"]) is int
        and 2 <= value["scored_transactions"] <= 200
        and type(value["maximum_scored_with_timestamp_peers"]) is int
        and value["scored_transactions"] <= value["maximum_scored_with_timestamp_peers"] <= 256
        and type(value["score_threshold"]) is float
        and 0 <= value["score_threshold"] <= 1
        and type(value["daily_customer_budget"]) is int
        and 1 <= value["daily_customer_budget"] <= 100
        and value["feedback_delay_days"] == 7
        and clock_ns(value["scoring_start"]) - clock_ns(value["context_start"])
        >= 2 * LABEL_DELAY_NS,
        "Invalid bounded product flow policy",
    )
    return value


def pins():
    # Additive modules outside the frozen scientific package; accepted updates stay valid.
    values = updates.pins()
    for path in (f.ROOT / "scripts/product_flow").glob("*.py"):
        values[path.relative_to(f.ROOT).as_posix()] = inputs.sha(path)
    values[POLICY] = inputs.sha(f.ROOT / POLICY)
    return values


def work(key):
    return f.managed_destination(f.workspace() / "product-flow" / p.round_id(key), "storage")


def read(key):
    folder = work(key)
    value = json.loads(f.managed_destination(folder / "input.json", "storage").read_bytes())
    source_compatibility.accept(value["source_pins"], pins(), f.ROOT)
    p.require(
        value["policy"] == policy(), "Product policy changed; use an explicit reviewed revision"
    )
    p.require(
        value["capture"]["input_sha256"]
        == inputs.sha(f.managed_destination(folder / "events.sqlite", "storage")),
        "Prepared event bytes changed",
    )
    manifest = ReleaseManifest.model_validate(value["release_manifest"])
    alert = CustomerDayAlertConfig.model_validate(value["alert_policy"])
    p.require(
        value["flow_id"] == key and value["schema"] == "fraud_product_flow_input_v1",
        "Product input identity differs",
    )
    p.require(
        alert.model_sha256 == manifest.model_sha256
        and alert.serving_release_id == manifest.serving_release_id
        and alert.score_threshold == value["policy"]["score_threshold"]
        and alert.daily_customer_budget == value["policy"]["daily_customer_budget"]
        and value["capture"]["scoring_window_ns"][0] == clock_ns(value["policy"]["scoring_start"])
        and manifest.model_dump()
        == value["active"]["plan"]["releases"][value["active"]["slot"]]["manifest"],
        "Prepared scoring, triage or active release binding differs",
    )
    return value


def active():
    # Shared active identity, rather than the initial round or a retired update baseline.
    path = updates.workspace() / "current.json"
    value = json.loads(path.read_bytes())
    p.require(
        value["model_sha256"]
        == value["plan"]["releases"][value["slot"]]["manifest"]["model_sha256"],
        "Active model binding differs",
    )
    return value


def save(folder, name, value):
    f.private_json(folder / name, value)
    return value


def prepare(key, source, no_other_writers):
    p.require(no_other_writers, "Declare the source and deployment free of other writers")
    config = policy()
    source = f.managed_destination(Path(source) / "checkpoint.sqlite", "replay")
    folder = work(key)
    if (folder / "input.json").exists():
        value = read(key)
        p.require(
            value["source_state"] == str(source.parent), "Existing input uses another source"
        )
        return {"status": "product_input_already_prepared", "directory": str(folder)}
    values = pins()
    for name, expected in values.items():
        result = subprocess.run(
            ["git", "show", "HEAD:" + name], cwd=f.ROOT, capture_output=True, check=False
        )
        import hashlib

        p.require(
            result.returncode == 0 and hashlib.sha256(result.stdout).hexdigest() == expected,
            "Commit reviewed product flow implementation before prepare: " + name,
        )
    current = active()
    deployment.check_active(
        current, json.loads((f.ROOT / "references/model_update_policy_v1.json").read_bytes())
    )
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    p.require(folder.stat().st_mode & 0o077 == 0, "Product workspace must be private")
    p.require(
        not any(folder.iterdir()), "Incomplete product preparation; preserve and inspect directory"
    )
    capture = inputs.capture(source, folder / "events.sqlite", config)
    p.require(pins() == values, "Implementation changed during preparation")
    release = current["plan"]["releases"][current["slot"]]["manifest"]
    manifest = ReleaseManifest.model_validate(release)
    value = {
        "schema": "fraud_product_flow_input_v1",
        "flow_id": key,
        "implementation_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=f.ROOT, text=True
        ).strip(),
        "source_pins": values,
        "source_state": str(source.parent),
        "policy": config,
        "capture": capture,
        "active": current,
        "release_manifest": manifest.model_dump(),
        "alert_policy": {
            "schema_version": 1,
            "version": "first_crossing_customer_day_v1",
            "model_sha256": manifest.model_sha256,
            "serving_release_id": manifest.serving_release_id,
            "score_threshold": float(config["score_threshold"]),
            "daily_customer_budget": config["daily_customer_budget"],
        },
        "fit_executed": False,
        "historical_model_loaded": False,
        "source_models_or_decisions_reused": False,
    }
    save(folder, "input.json", value)
    return {
        "status": "product_input_prepared",
        "directory": str(folder),
        "counts": capture["counts"],
        "model_sha256": manifest.model_sha256,
        "threshold_scope": config["threshold_scope"],
    }


def binding(value):
    return {
        "flow_id": value["flow_id"],
        "input_sha256": value["capture"]["input_sha256"],
        "input_receipt_sha256": digest(value),
    }


def replay(path, value, scorer):
    return HTTPReplay(
        path,
        scorer=scorer,
        policy=value["alert_policy"],
        binding=binding(value),
        scoring_window_ns=value["capture"]["scoring_window_ns"],
    )


def run_events(folder, value, scorer, *, stop_after_scored=None):
    """Each HTTP exchange is committed by inherited handle with its event and decision."""
    if stop_after_scored is not None and (
        type(stop_after_scored) is not int or stop_after_scored <= 0
    ):
        raise ValueError("stop-after-scored must be a positive count")
    started, calls_before = time.monotonic(), scorer.requests
    with job_lock(folder), replay(folder / "checkpoint.sqlite", value, scorer) as executor:
        summary = executor.summary()
        before = summary["counts"]["decisions"]
        last = summary["last_event_order"]
        after = tuple(last) if last[0] is not None else None
        for count, event in enumerate(stream_events(folder / "events.sqlite", after=after), 1):
            executor.handle(event)
            if count % 10000 == 0:
                print(
                    f"Committed {count} new product events; transaction {event.transaction_id}",
                    flush=True,
                )
            if (
                stop_after_scored is not None
                and event.event_type == "transaction"
                and event.event_clock_ns >= value["capture"]["scoring_window_ns"][0]
                and executor.connection.execute("SELECT count(*) FROM decisions").fetchone()[0]
                - before
                >= stop_after_scored
            ):
                break
        summary = executor.summary()
        http_rows = executor.connection.execute("SELECT count(*) FROM http_exchanges").fetchone()[
            0
        ]
        p.require(
            http_rows == summary["counts"]["decisions"], "A scored decision lacks its HTTP receipt"
        )
    result = {
        "schema": "fraud_product_flow_invocation_v1",
        "status": "complete"
        if summary["counts"]["events"] == value["capture"]["counts"]["events"]
        else "paused",
        "counts": summary["counts"],
        "http_receipts": http_rows,
        "scoring_HTTP_attempts_this_invocation": scorer.requests - calls_before,
        "new_decisions": summary["counts"]["decisions"] - before,
        "elapsed_seconds": time.monotonic() - started,
        "model_sha256": value["release_manifest"]["model_sha256"],
        "fit_executed": False,
        "champion_modified": False,
    }
    save(folder, "invocation-" + uuid4().hex + ".json", result)
    return result


class StoredScorer:
    """Verification reproduces features/triage from events and already-recorded HTTP responses."""

    def __init__(self, connection, manifest):
        self.connection, self.manifest = connection, manifest
        self.record = None

    def information(self):
        return {
            "transport": "HTTP_no_local_fallback_v1",
            "model_sha256": self.manifest.model_sha256,
            "serving_release_id": self.manifest.serving_release_id,
            "model_id": self.manifest.model_id,
        }

    def score(self, request):
        row = self.connection.execute(
            "SELECT d.request,d.response,h.request_sha256,h.response_sha256,h.attempts,h.elapsed_ms "
            "FROM decisions d JOIN http_exchanges h USING(transaction_id) WHERE transaction_id=?",
            (request.transaction_id,),
        ).fetchone()
        if row is None or json.loads(row[0]) != request.model_dump():
            raise ValueError("Persisted HTTP request differs from causal event reconstruction")
        response = checked_response(json.loads(row[1]), request, self.manifest)
        self.record(
            request,
            response,
            {
                "request_sha256": row[2],
                "response_sha256": row[3],
                "attempts": row[4],
                "elapsed_ms": row[5],
            },
        )
        return response


def verify_ledger(folder, value):
    """No model loading or network: reconstruct every committed event and compare ledgers."""
    with job_lock(folder), closing(readonly_connection(folder / "checkpoint.sqlite")) as source:
        source.execute("BEGIN")
        if source.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("HTTP checkpoint integrity failed")
        saved = json.loads(
            source.execute("SELECT payload FROM identity WHERE singleton=1").fetchone()[0]
        )
        scorer = StoredScorer(source, ReleaseManifest.model_validate(value["release_manifest"]))
        with replay(":memory:", value, scorer) as rebuilt:
            p.require(saved == rebuilt.identity, "Saved HTTP checkpoint binding changed")
            for event in stream_events(folder / "events.sqlite"):
                rebuilt.handle(event)
            for table in [
                "identity",
                "checkpoint",
                "transactions",
                "feedback",
                "events",
                "decisions",
                "alerts",
                "alert_decisions",
                "http_exchanges",
            ]:
                actual = source.execute("SELECT * FROM " + table + " ORDER BY 1").fetchall()
                expected = rebuilt.connection.execute(
                    "SELECT * FROM " + table + " ORDER BY 1"
                ).fetchall()
                p.require(actual == expected, "HTTP ledger does not reconcile: " + table)
            summary = rebuilt.summary()
        counts = summary["counts"]
        p.require(
            all(counts[key] == number for key, number in value["capture"]["counts"].items()),
            "Input event/decision counts differ",
        )
        p.require(counts["decisions"] == counts["alert_decisions"], "Missing alert decisions")
        confirmed = source.execute(
            "SELECT count(*) FROM alerts a JOIN feedback f USING(transaction_id) WHERE f.fraud_label=1"
        ).fetchone()[0]
        source.execute("COMMIT")
    return {
        "schema": "fraud_product_flow_verification_v1",
        "status": "product_flow_reconciled",
        "flow_id": value["flow_id"],
        "counts": counts,
        "HTTP_receipts": counts["decisions"],
        "causal_requests_and_triage_rebuilt": True,
        "source_model_scores_reused": False,
        "feedback_transactions": counts["feedback"],
        "alerts_with_positive_transaction_feedback": confirmed,
        "model_sha256": value["release_manifest"]["model_sha256"],
        "serving_release_id": value["release_manifest"]["serving_release_id"],
        "threshold_scope": value["policy"]["threshold_scope"],
        "checkpoint_sha256": inputs.sha(folder / "checkpoint.sqlite"),
        "fit_executed": False,
        "new_independent_quality_confirmation": False,
        "champion_modified": False,
        "production_promotion": False,
    }


@contextmanager
def target(value):
    from scripts.platform.serving_recovery import resolve_active

    expected = resolve_active(value["active"])
    p.require(active() == expected, "Active deployment changed; preserve this product flow")
    config = json.loads((f.ROOT / "references/model_update_policy_v1.json").read_bytes())
    before = deployment.check_active(expected, config)
    with serving.forward(serving.gateway(expected["plan"])) as url:
        scorer = HTTPScorer(url, ReleaseManifest.model_validate(value["release_manifest"]))
        scorer.ready()
        yield scorer
        scorer.ready()
    p.require(active() == expected, "Active state changed during product execution")
    deployment.check_active(expected, config)
    p.require(
        before["score"]["model_sha256"] == expected["model_sha256"], "Active identity mismatch"
    )


def execute(key, action, no_other_writers, stop_after_scored=None):
    p.require(no_other_writers, "Declare no other product, Registry or route writers")
    folder, value = work(key), read(key)
    try:
        with target(value) as scorer:
            if action == "run":
                return run_events(folder, value, scorer, stop_after_scored=stop_after_scored)
            if action == "rehearse":
                p.require(
                    not (folder / "rehearsal.json").exists(),
                    "Rehearsal already recorded; use verify",
                )
                p.require(
                    not (folder / "checkpoint.sqlite").exists(),
                    "Use run/verify to resume an existing checkpoint; do not restart a rehearsal",
                )
                paused = run_events(
                    folder,
                    value,
                    scorer,
                    stop_after_scored=max(1, value["capture"]["counts"]["decisions"] // 2),
                )
                complete = run_events(folder, value, scorer)
                repeated = run_events(folder, value, scorer)
                checked = verify_ledger(folder, value)
                p.require(
                    paused["status"] == "paused"
                    and complete["status"] == "complete"
                    and repeated["new_decisions"]
                    == repeated["scoring_HTTP_attempts_this_invocation"]
                    == 0,
                    "Pause/resume/idempotence rehearsal did not reconcile",
                )
                result = {
                    **checked,
                    "status": "product_rehearsal_passed",
                    "pause_resume_verified": True,
                    "completed_reinvocation_without_new_score_calls": True,
                    "invocations": [paused, complete, repeated],
                    "native_distinct_weight_successor_activation_validated": False,
                }
            else:
                result = verify_ledger(folder, value)
        save(
            folder,
            "rehearsal.json" if action == "rehearse" else "verification-" + uuid4().hex + ".json",
            result,
        )
        return result
    except Exception as exc:
        save(
            folder,
            "failure-" + uuid4().hex + ".json",
            {
                "status": "failed",
                "action": action,
                "error_type": type(exc).__name__,
                "reason": str(exc)[-1000:],
                "resume": "Preserve input/checkpoint; rerun product-run after resolving the cause",
            },
        )
        raise


def cases(key, limit):
    value, folder = read(key), work(key)
    p.require(1 <= limit <= 20, "Choose 1..20 cases")
    with (
        job_lock(folder),
        closing(readonly_connection(folder / "checkpoint.sqlite")) as connection,
    ):
        result = []
        for request, response, alert, available, label in connection.execute(
            "SELECT d.request,d.response,a.payload,f.available_ns,f.fraud_label "
            "FROM decisions d JOIN alert_decisions a USING(transaction_id) "
            "LEFT JOIN feedback f USING(transaction_id) "
            "ORDER BY CASE WHEN json_extract(a.payload,'$.status')='alert' THEN 0 ELSE 1 END, "
            "json_extract(d.response,'$.score') DESC,d.transaction_id LIMIT ?",
            (limit,),
        ):
            result.append(
                {
                    "request": json.loads(request),
                    "response": json.loads(response),
                    "triage": json.loads(alert),
                    "feedback_available_ns": available,
                    "transaction_feedback": label,
                    "SHAP_explanation": "pending_current_champion_integration",
                }
            )
    return {"flow_id": value["flow_id"], "cases": result}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=["validate", "prepare", "run", "verify", "rehearse", "cases"]
    )
    parser.add_argument("--flow-id", default="champion-http-v1")
    parser.add_argument("--source-state", type=Path)
    parser.add_argument("--no-other-writers", action="store_true")
    parser.add_argument("--stop-after-scored", type=int)
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()
    try:
        if args.action == "validate":
            policy()
            result = {
                "status": "product_flow_policy_validated",
                "native_HTTP_flow_validated": False,
            }
        elif args.action == "cases":
            result = cases(args.flow_id, args.limit)
        else:
            with f.exclusive():
                if args.action == "prepare":
                    p.require(args.source_state is not None, "Set --source-state explicitly")
                    result = prepare(args.flow_id, args.source_state, args.no_other_writers)
                else:
                    result = execute(
                        args.flow_id, args.action, args.no_other_writers, args.stop_after_scored
                    )
    except (ValueError, OSError, sqlite3.Error, KeyError) as exc:
        parser.exit(1, f"Product flow stopped: {exc}\n")
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
