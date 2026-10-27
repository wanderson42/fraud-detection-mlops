"""Customer-day alerts can exceed distinct customers across a multi-day session."""

import copy
from http.server import ThreadingHTTPServer
import json
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from scripts.session_monitoring import collector
from scripts.session_monitoring import contract as c
from scripts.session_monitoring.server import Projection, handler


def example():
    binding = {
        "session_id": "two-days",
        "session_sha256": "a" * 64,
        "model_sha256": "b" * 64,
        "serving_release_id": "c" * 32,
        "expected_events": 8,
        "expected_decisions": 4,
    }
    return {
        "binding": binding,
        "captured_at": 100.0,
        "producer_at": 100.0,
        "producer_status": "complete",
        "counts": dict(zip(c.COUNTS, [8, 4, 4, 4, 4, 2, 4, 4], strict=True)),
        "watermark": [2000, 1, 4],
        "latency_ms": {"p50": 1.0, "p95": 2.0, "maximum": 3.0},
    }


def test_multiday_counts_pass_contract_receiver_and_metrics():
    value = example()
    assert c.validate(value, value["binding"], 100) == value
    projection = Projection(value["binding"], clock=lambda: 100)
    projection.publish(value)
    assert projection.summary()["source_valid"]
    lines = projection.metrics().splitlines()
    for name, count in (("alerts", 4), ("customers", 2), ("decisions", 4)):
        assert next(row for row in lines if row.startswith("fraud_session_" + name + "{"))[
            -3:
        ] == str(float(count))


@pytest.mark.parametrize("field", ["alerts", "customers"])
def test_each_count_remains_bounded_by_decisions(field):
    value = example()
    value["counts"][field] = 5
    with pytest.raises(ValueError, match="Inconsistent counts"):
        c.validate(value, value["binding"], 100)


def test_multiday_http_acceptance_still_rejects_foreign_binding():
    value = example()
    projection = Projection(value["binding"], clock=lambda: 100)
    token = "private" * 8
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(projection, token))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:

        def put(payload):
            request = Request(
                f"http://127.0.0.1:{server.server_port}/projection",
                data=c.dumps(payload),
                method="PUT",
                headers={"Authorization": "Bearer " + token},
            )
            with urlopen(request, timeout=2) as response:
                return json.load(response)

        assert put(value) == {"accepted": True}
        foreign = copy.deepcopy(value)
        foreign["binding"]["model_sha256"] = "d" * 64
        with pytest.raises(HTTPError) as error:
            put(foreign)
        assert error.value.code == 400
        assert projection.summary()["projection"] == value
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_collector_preserves_distinct_customer_and_customer_day_units(tmp_path):
    import hashlib
    import sqlite3

    from scripts.product_session.reader import sha

    value = example()
    source = {
        "release_manifest": {
            key: value["binding"][key] for key in ("model_sha256", "serving_release_id")
        },
        "capture": {"counts": {"events": 8, "decisions": 4}},
    }
    (tmp_path / "input.json").write_bytes(c.dumps(source))
    receipt = {"session_id": "two-days", "input_sha256": sha(tmp_path / "input.json")}
    (tmp_path / "session.json").write_bytes(c.dumps(receipt))
    binding, _ = collector.identity(tmp_path)
    (tmp_path / "heartbeat.json").write_bytes(
        c.dumps(
            {
                "status": "complete",
                "timestamp": 100,
                "session_sha256": binding["session_sha256"],
            }
        )
    )
    with sqlite3.connect(tmp_path / "checkpoint.sqlite") as db:
        db.executescript("""
            CREATE TABLE identity(singleton INTEGER PRIMARY KEY,payload TEXT);
            CREATE TABLE checkpoint(singleton INTEGER PRIMARY KEY,clock_ns INTEGER,event_phase INTEGER,transaction_id INTEGER);
            CREATE TABLE events(id INTEGER PRIMARY KEY);
            CREATE TABLE transactions(transaction_id INTEGER PRIMARY KEY);
            CREATE TABLE feedback(transaction_id INTEGER PRIMARY KEY);
            CREATE TABLE decisions(transaction_id INTEGER PRIMARY KEY,request TEXT);
            CREATE TABLE http_exchanges(transaction_id INTEGER PRIMARY KEY,elapsed_ms REAL,attempts INTEGER);
            CREATE TABLE alerts(customer_id INTEGER,day TEXT,PRIMARY KEY(customer_id,day));
        """)
        db.execute(
            "INSERT INTO identity VALUES(1,?)",
            (
                json.dumps(
                    {
                        "binding": {
                            "input_receipt_sha256": hashlib.sha256(c.dumps(source)).hexdigest()
                        }
                    }
                ),
            ),
        )
        db.execute("INSERT INTO checkpoint VALUES(1,2000,1,4)")
        db.executemany("INSERT INTO events VALUES(?)", [(n,) for n in range(8)])
        for tid, customer, day in [
            (1, 10, "2018-09-16"),
            (2, 20, "2018-09-16"),
            (3, 10, "2018-09-17"),
            (4, 20, "2018-09-17"),
        ]:
            db.execute("INSERT INTO transactions VALUES(?)", (tid,))
            db.execute("INSERT INTO feedback VALUES(?)", (tid,))
            db.execute(
                "INSERT INTO decisions VALUES(?,?)", (tid, json.dumps({"customer_id": customer}))
            )
            db.execute("INSERT INTO http_exchanges VALUES(?,1.0,1)", (tid,))
            db.execute("INSERT INTO alerts VALUES(?,?)", (customer, day))
    before = {p.name: sha(p) for p in tmp_path.iterdir()}
    result = collector.collect(tmp_path, binding, now=100)
    assert result["counts"]["alerts"] == 4
    assert result["counts"]["customers"] == 2
    assert result["counts"]["decisions"] == 4
    assert before == {p.name: sha(p) for p in tmp_path.iterdir() if p.name in before}
