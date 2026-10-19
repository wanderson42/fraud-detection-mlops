"""Read-only coherent snapshots; no rescoring, label oracle or source data access."""

from contextlib import closing
from datetime import UTC
import hashlib
import json
from pathlib import Path
import time

from scripts.monitoring.statistics import (
    LATENCY_BOUNDS,
    jensen_shannon,
    mature_quality,
    score_counts,
)
from scripts.monitoring.telemetry import VERSION, readonly


def snapshot(directory: Path):
    directory = directory.absolute()
    if directory.resolve() != directory:
        raise ValueError("Monitoring directory cannot be redirected")
    metadata_path = directory / "metadata.json"
    if metadata_path.is_symlink():
        raise ValueError("Monitoring metadata redirected")
    metadata = json.loads(metadata_path.read_text())
    if metadata["version"] != VERSION:
        raise ValueError("Foreign operational identity")
    identity = hashlib.sha256(
        json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    with closing(readonly(directory / "checkpoint.sqlite")) as con:
        con.execute("BEGIN")
        saved = json.loads(
            con.execute("SELECT payload FROM identity WHERE singleton=1").fetchone()[0]
        )
        if (
            saved["run_id"] != "paced-observability-v1"
            or saved["input_sha256"] != identity
            or saved["policy"] != metadata["policy"]
        ):
            raise ValueError("Checkpoint and monitoring metadata differ")
        counts = {
            table: con.execute("SELECT count(*) FROM " + table).fetchone()[0]
            for table in ("events", "transactions", "feedback", "decisions", "alerts")
        }
        watermark = con.execute("SELECT clock_ns FROM checkpoint WHERE singleton=1").fetchone()[0]
        rows = con.execute(
            "SELECT t.tx_ns,t.customer_id,json_extract(d.response,'$.score'),"
            "CASE WHEN json_extract(a.payload,'$.status')='alert' THEN 1 ELSE 0 END,f.fraud_label "
            "FROM decisions d JOIN transactions t USING(transaction_id) "
            "JOIN alert_decisions a USING(transaction_id) LEFT JOIN feedback f USING(transaction_id) "
            "ORDER BY t.tx_ns,t.transaction_id"
        ).fetchall()
        statuses = dict(
            con.execute(
                "SELECT json_extract(payload,'$.status'),count(*) FROM alert_decisions GROUP BY 1"
            ).fetchall()
        )
        pending_core_ids = {r[0] for r in con.execute("SELECT event_id FROM events")}
        if metadata["scoring_window"]:
            from datetime import datetime

            cut = int(
                datetime.fromisoformat(metadata["scoring_window"][0])
                .replace(tzinfo=UTC)
                .timestamp()
                * 1e9
            )
            context = con.execute(
                "SELECT count(*) FROM transactions WHERE tx_ns<?", (cut,)
            ).fetchone()[0]
            context += con.execute(
                "SELECT count(*) FROM feedback WHERE available_ns<?", (cut,)
            ).fetchone()[0]
        else:
            context = 0
        con.execute("COMMIT")
    with closing(readonly(directory / "telemetry.sqlite")) as con:
        con.execute("BEGIN")
        if (
            json.loads(con.execute("SELECT payload FROM identity WHERE id=1").fetchone()[0])
            != metadata
        ):
            raise ValueError("Telemetry identity differs")
        session = con.execute(
            "SELECT id,state,heartbeat FROM sessions ORDER BY id DESC LIMIT 1"
        ).fetchone()
        timings = con.execute(
            "SELECT event_id,event_type,scheduled,emitted,dequeued,committed,processing_seconds,status FROM timings"
        ).fetchall()
        con.execute("COMMIT")
    complete = all(counts[key] == value for key, value in metadata["expected_counts"].items())
    quality = mature_quality(rows, watermark or 0, complete)
    current = score_counts([r[2] for r in rows[-1000:]])
    measured = [r for r in timings if r[7] == "measured" and r[1] == "transaction"]
    durations = {
        name: []
        for name in ("scheduled_to_commit", "emission_lateness", "queue_wait", "processing")
    }
    for _, _, scheduled, emitted, dequeued, committed, processing, _ in measured:
        durations["scheduled_to_commit"].append(max(0, committed - scheduled))
        durations["emission_lateness"].append(max(0, emitted - scheduled))
        durations["queue_wait"].append(max(0, dequeued - emitted))
        durations["processing"].append(max(0, processing))
    committed_ids = {r[0] for r in timings if r[7] in ("measured", "recovered_unmeasured")}
    pending = [r for r in timings if r[7] == "emitted"]
    pending_uncommitted = sum(r[0] not in pending_core_ids for r in pending)
    measured_ids = {r[0] for r in timings if r[7] == "measured"}
    recovered = len({r[0] for r in timings if r[7] == "recovered_unmeasured"} - measured_ids)
    return {
        "snapshot_timestamp": time.time(),
        "complete": complete,
        "counts": counts,
        "context_events": context,
        "source_watermark_seconds": watermark / 1e9 if watermark is not None else 0,
        "speed": metadata["speed"],
        "session_running": int(bool(session and session[1] == "running")),
        "heartbeat": session[2] if session else 0,
        "pending_uncommitted_events": pending_uncommitted,
        "unmeasured_committed_events": max(0, counts["events"] - context - len(committed_ids))
        + recovered,
        "durations": durations,
        "quality": quality,
        "decision_statuses": statuses,
        "pending_feedback_transactions": sum(r[4] is None for r in rows),
        "observed_scoring_dates": len({r[0] // (86400 * 10**9) for r in rows}),
        "daily_customer_budget": metadata["policy"]["daily_customer_budget"],
        "score_window_observations": sum(current),
        "score_jensen_shannon": jensen_shannon(metadata["score_reference_counts"], current),
        "source_scope": metadata["source_scope"],
        "measurement_scope": metadata["measurement_scope"],
    }


def exposition(data):
    lines = []

    def metric(name, value, kind="gauge", labels=None, help_text=None):
        if value is None:
            return
        suffix = ""
        if labels:
            escaped = {
                k: str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
                for k, v in labels.items()
            }
            suffix = "{" + ",".join(f'{k}="{v}"' for k, v in escaped.items()) + "}"
        lines.append(f"# HELP {name} {help_text or name.replace('_', ' ')}.")
        lines.append(f"# TYPE {name} {kind}")
        lines.append(f"{name}{suffix} {float(value):.17g}")

    metric("fraud_monitor_collector_up", 1)
    metric("fraud_monitor_snapshot_timestamp_seconds", data["snapshot_timestamp"])
    for table, value in data["counts"].items():
        metric("fraud_replay_" + table + "_total", value, "counter")
    for key in (
        "context_events",
        "source_watermark_seconds",
        "speed",
        "session_running",
        "heartbeat",
        "pending_uncommitted_events",
        "unmeasured_committed_events",
        "score_window_observations",
        "score_jensen_shannon",
    ):
        metric(
            "fraud_replay_" + ("heartbeat_timestamp_seconds" if key == "heartbeat" else key),
            data[key],
        )
    for key in (
        "pending_feedback_transactions",
        "observed_scoring_dates",
        "daily_customer_budget",
    ):
        metric("fraud_replay_" + key, data[key])
    for status in ("alert", "already_alerted", "below_threshold", "budget_exhausted"):
        metric(
            "fraud_triage_" + status + "_total",
            data["decision_statuses"].get(status, 0),
            "counter",
        )
    metric("fraud_replay_complete", int(data["complete"]))
    metric("fraud_replay_source_info", 1, labels={"scope": data["source_scope"]})
    for key, value in data["quality"].items():
        # Customer-days count distinct customer/date pairs; they are not elapsed time.
        name = key.replace("customer_days", "customer_date_pairs").replace(
            "mature_days", "mature_dates"
        )
        metric("fraud_quality_" + name, value)
    for name, values in data["durations"].items():
        full = "fraud_replay_" + name + "_seconds"
        lines.append(
            "# HELP "
            + full
            + " Paced transaction latency in seconds; context and timing gaps excluded."
        )
        lines.append("# TYPE " + full + " histogram")
        for bound in [*LATENCY_BOUNDS, float("inf")]:
            label = "+Inf" if bound == float("inf") else str(bound)
            lines.append(f'{full}_bucket{{le="{label}"}} {sum(v <= bound for v in values)}')
        lines.extend([f"{full}_sum {sum(values):.17g}", f"{full}_count {len(values)}"])
    return "\n".join(lines) + "\n"
