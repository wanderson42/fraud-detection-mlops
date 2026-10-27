"""Consistent, read-only live views; usable in the isolated portal runtime."""

from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from urllib.parse import quote


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def checked(path):
    path = Path(path).absolute()
    if path.resolve() != path or path.is_symlink():
        raise ValueError("Session paths cannot be redirected")
    return path


def read(folder, expected, *, page=1, page_size=100, only_alerts=False):
    if (
        type(page) is not int
        or page < 1
        or type(page_size) is not int
        or not 1 <= page_size <= 100
        or type(only_alerts) is not bool
    ):
        raise ValueError("Use a positive page and 1..100 rows per page")
    folder = checked(folder)
    receipt = checked(folder / "session.json")
    if sha(receipt) != expected:
        raise ValueError("Session receipt changed")
    config = json.loads(receipt.read_bytes())
    source = checked(folder / "input.json")
    if (
        config["schema"] not in {"fraud_product_session_v1", "fraud_product_session_v2"}
        or sha(source) != config["input_sha256"]
    ):
        raise ValueError("Session source identity changed")
    value = json.loads(source.read_bytes())
    model = value["release_manifest"]
    result = {
        "session_id": config["session_id"],
        "model_sha256": model["model_sha256"],
        "serving_release_id": model["serving_release_id"],
        "model_id": model["model_id"],
        "alert_policy": value["alert_policy"],
        "cases": [],
        "counts": dict.fromkeys(("events", "transactions", "feedback", "decisions", "alerts"), 0),
        "last_event_order": [None, None, None],
        "expected_decisions": value["capture"]["counts"]["decisions"],
        "expected_events": value["capture"]["counts"]["events"],
    }
    result.update(
        totals={"decisions": 0, "alerts": 0, "customers": 0, "feedback": 0},
        outcomes={},
        score_histogram=[0] * 20,
        pagination={"page": 1, "page_size": page_size, "pages": 1, "matching_decisions": 0},
    )
    checkpoint = checked(folder / "checkpoint.sqlite")
    if checkpoint.exists():
        uri = "file:" + quote(str(checkpoint), safe="/") + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=2)) as conn:
            conn.execute("BEGIN")
            identity = json.loads(
                conn.execute("SELECT payload FROM identity WHERE singleton=1").fetchone()[0]
            )
            # Every view belongs to the exact prepared session, not another checkpoint.
            from_input = hashlib.sha256(
                json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
            ).hexdigest()
            if identity["binding"]["input_receipt_sha256"] != from_input:
                raise ValueError("Checkpoint belongs to another session input")
            result["counts"] = {
                name: conn.execute("SELECT count(*) FROM " + name).fetchone()[0]
                for name in result["counts"]
            }
            result["last_event_order"] = list(
                conn.execute(
                    "SELECT clock_ns,event_phase,transaction_id FROM checkpoint WHERE singleton=1"
                ).fetchone()
            )
            if (
                conn.execute("SELECT count(*) FROM http_exchanges").fetchone()[0]
                != result["counts"]["decisions"]
            ):
                raise ValueError("HTTP receipts and decisions differ")
            global_row = conn.execute(
                "SELECT count(*),count(DISTINCT json_extract(d.request,'$.customer_id')),count(f.transaction_id) FROM decisions d LEFT JOIN feedback f USING(transaction_id)"
            ).fetchone()
            result["totals"] = {
                "decisions": global_row[0],
                "customers": global_row[1],
                "feedback": global_row[2],
                "alerts": result["counts"]["alerts"],
            }
            result["outcomes"] = dict(
                conn.execute(
                    "SELECT json_extract(payload,'$.status'),count(*) FROM alert_decisions GROUP BY json_extract(payload,'$.status')"
                )
            )
            for bucket, count in conn.execute(
                "SELECT min(CAST(json_extract(response,'$.score')*20 AS INTEGER),19),count(*) FROM decisions GROUP BY 1"
            ):
                if bucket is None or not 0 <= bucket < 20:
                    raise ValueError("Invalid score histogram")
                result["score_histogram"][bucket] = count
            matching = result["counts"]["alerts"] if only_alerts else result["counts"]["decisions"]
            pages = max(1, (matching + page_size - 1) // page_size)
            actual_page = min(page, pages)
            result["pagination"] = {
                "page": actual_page,
                "page_size": page_size,
                "pages": pages,
                "matching_decisions": matching,
            }
            rows = conn.execute(
                "SELECT d.request,d.response,a.payload,f.available_ns,f.fraud_label,h.request_sha256,h.response_sha256 "
                "FROM decisions d JOIN alert_decisions a USING(transaction_id) "
                "JOIN http_exchanges h USING(transaction_id) LEFT JOIN feedback f USING(transaction_id) "
                "WHERE (?=0 OR json_extract(a.payload,'$.status')='alert') "
                "ORDER BY CASE WHEN json_extract(a.payload,'$.status')='alert' THEN 0 ELSE 1 END, "
                "json_extract(d.response,'$.score') DESC,d.transaction_id LIMIT ? OFFSET ?",
                (int(only_alerts), page_size, (actual_page - 1) * page_size),
            ).fetchall()
            if len(rows) != min(page_size, max(0, matching - (actual_page - 1) * page_size)):
                raise ValueError("Incomplete session page")
            for req, res, triage, available, label, req_sha, res_sha in rows:
                request, response = json.loads(req), json.loads(res)

                def digest(payload):
                    return hashlib.sha256(
                        json.dumps(
                            payload, sort_keys=True, separators=(",", ":"), allow_nan=False
                        ).encode()
                    ).hexdigest()

                if digest(request) != req_sha or digest(response) != res_sha:
                    raise ValueError("HTTP decision bytes differ")
                if response["transaction_id"] != request["transaction_id"] or any(
                    response[name] != model[name]
                    for name in ("model_sha256", "serving_release_id", "model_id")
                ):
                    raise ValueError("Decision model or transaction differs")
                if available is not None and available > result["last_event_order"][0]:
                    raise ValueError("Future feedback is visible")
                result["cases"].append(
                    {
                        "request": request,
                        "response": response,
                        "triage": json.loads(triage),
                        "feedback_available_ns": available,
                        "transaction_feedback": label,
                        "SHAP_explanation": None,
                    }
                )
            conn.commit()
    # Heartbeats describe the producer; ledger counts above remain authoritative.
    heartbeat = checked(folder / "heartbeat.json")
    result["producer"] = {"status": "prepared", "age_seconds": None}
    if heartbeat.exists():
        live = json.loads(heartbeat.read_bytes())
        if live["session_sha256"] != expected:
            raise ValueError("Heartbeat belongs to another session")
        age = max(0, time.time() - live["timestamp"])
        result["producer"] = {**live, "age_seconds": age}
        if live["status"] in {"running", "warming_up", "draining_feedback"} and age > 15:
            result["producer"]["status"] = "stale"
    return result
