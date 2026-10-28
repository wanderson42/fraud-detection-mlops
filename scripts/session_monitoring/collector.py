"""Read one committed SQLite snapshot; no scoring, writes, case IDs or feature export."""

from contextlib import closing
import hashlib
import json
import sqlite3
import time
from urllib.parse import quote

from scripts.product_session.reader import checked, retry_busy, sha
from scripts.session_monitoring import contract as c


def identity(directory):
    receipt = json.loads(checked(directory / "session.json").read_bytes())
    source = checked(directory / "input.json")
    c.require(sha(source) == receipt["input_sha256"], "Session source changed")
    value = json.loads(source.read_bytes())
    model = value["release_manifest"]
    return c.binding(
        {
            "session_id": receipt["session_id"],
            "session_sha256": sha(directory / "session.json"),
            "model_sha256": model["model_sha256"],
            "serving_release_id": model["serving_release_id"],
            "expected_events": value["capture"]["counts"]["events"],
            "expected_decisions": value["capture"]["counts"]["decisions"],
        }
    ), value


@retry_busy
def collect(directory, expected, now=None):
    now = time.time() if now is None else now
    actual, source = identity(directory)
    c.require(actual == expected, "Prepared session identity changed")
    counts = dict.fromkeys(c.COUNTS, 0)
    mark, elapsed = [None] * 3, []
    checkpoint = checked(directory / "checkpoint.sqlite")
    if checkpoint.exists():
        uri = "file:" + quote(str(checkpoint), safe="/") + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=2)) as db:
            db.execute("BEGIN")
            saved = json.loads(
                db.execute("SELECT payload FROM identity WHERE singleton=1").fetchone()[0]
            )
            fingerprint = hashlib.sha256(c.dumps(source)).hexdigest()
            c.require(
                saved["binding"]["input_receipt_sha256"] == fingerprint, "Foreign checkpoint"
            )
            for name in ("events", "transactions", "feedback", "decisions", "alerts"):
                counts[name] = db.execute("SELECT count(*) FROM " + name).fetchone()[0]
            counts["customers"], counts["decision_feedback"] = db.execute(
                "SELECT count(DISTINCT json_extract(d.request,'$.customer_id')),count(f.transaction_id) FROM decisions d LEFT JOIN feedback f USING(transaction_id)"
            ).fetchone()
            rows = db.execute(
                "SELECT elapsed_ms,attempts FROM http_exchanges ORDER BY elapsed_ms"
            ).fetchall()
            c.require(len(rows) == counts["decisions"], "HTTP receipt count differs")
            c.require(
                all(c.number(v) and type(a) is int and 1 <= a <= 3 for v, a in rows),
                "Invalid HTTP receipt",
            )
            elapsed = [v for v, _ in rows]
            counts["http_attempts"] = sum(a for _, a in rows)
            mark = list(
                db.execute(
                    "SELECT clock_ns,event_phase,transaction_id FROM checkpoint WHERE singleton=1"
                ).fetchone()
            )
    heartbeat = checked(directory / "heartbeat.json")
    c.require(
        heartbeat.exists() or counts["events"] == 0, "Missing heartbeat for initialized checkpoint"
    )
    producer = (
        json.loads(heartbeat.read_bytes())
        if heartbeat.exists()
        else {"status": "prepared", "timestamp": 0, "session_sha256": expected["session_sha256"]}
    )
    c.require(
        producer["session_sha256"] == expected["session_sha256"], "Foreign producer heartbeat"
    )
    result = {
        "binding": expected,
        "captured_at": now,
        "producer_at": producer["timestamp"],
        "producer_status": producer["status"],
        "counts": counts,
        "watermark": mark,
        "latency_ms": {
            "p50": c.quantile(elapsed, 0.5),
            "p95": c.quantile(elapsed, 0.95),
            "maximum": max(elapsed, default=0),
        },
    }
    return c.validate(result, expected, now)
