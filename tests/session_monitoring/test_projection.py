"""Real HTTP boundary, TTL and identity failures without Kubernetes or model loading."""

import copy
from http.server import ThreadingHTTPServer
import json
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from scripts.session_monitoring import contract as c
from scripts.session_monitoring.server import Projection, handler


@pytest.fixture
def payload():
    return {
        "binding": {
            "session_id": "arrival-2k-v1",
            "session_sha256": "a" * 64,
            "model_sha256": "b" * 64,
            "serving_release_id": "c" * 32,
            "expected_events": 100,
            "expected_decisions": 20,
        },
        "captured_at": 100.0,
        "producer_at": 100.0,
        "producer_status": "running",
        "counts": dict(zip(c.COUNTS, [10, 5, 5, 2, 1, 2, 1, 3], strict=True)),
        "watermark": [1000, 1, 4],
        "latency_ms": {"p50": 10.0, "p95": 19.0, "maximum": 20.0},
    }


def test_receiver_expiry_recovery_producer_status_and_no_case_data(payload):
    now = [100.0]
    p = Projection(payload["binding"], clock=lambda: now[0])
    assert not p.summary()["source_valid"]
    assert "fraud_session_decisions{" not in p.metrics()
    p.publish(payload)
    assert p.summary()["source_valid"]
    text = p.metrics()
    assert (
        "fraud_session_http_retries{" in text
        and "customer_id" not in text
        and "transaction_id" not in text
    )
    now[0] = 116
    assert p.summary()["producer_stale"] and not p.summary()["source_valid"]
    payload.update(producer_status="paused", captured_at=116)
    p.publish(payload)
    assert p.summary()["source_valid"]
    now[0] = 147
    assert (
        not p.summary()["source_valid"] and p.summary()["projection"]["counts"]["decisions"] == 2
    )
    payload.update(producer_status="complete", captured_at=147)
    p.publish(payload)
    assert p.summary()["source_valid"]  # idle completed producer is legitimate
    assert "fraud_session_complete{" in p.metrics() and " 0.0" in p.metrics()
    now[0] += 1
    payload.update(captured_at=148, producer_status="failed")
    p.publish(payload)
    assert not p.summary()["source_valid"]


@pytest.mark.parametrize(
    "change",
    [
        "foreign",
        "extra",
        "nan",
        "latency",
        "future",
        "expired",
        "regress",
        "mark",
        "replay",
        "attempts",
        "receipt_size",
    ],
)
def test_bad_publications_leave_last_accepted_projection(payload, change):
    p = Projection(payload["binding"], clock=lambda: 101)
    p.publish(payload)
    bad = copy.deepcopy(payload)
    bad["captured_at"] = 101
    if change == "foreign":
        bad["binding"]["model_sha256"] = "d" * 64
    if change == "extra":
        bad["transaction_id"] = 123
    if change == "nan":
        bad["latency_ms"]["p95"] = float("nan")
    if change == "latency":
        bad["latency_ms"]["p95"] = 100
    if change == "future":
        bad["captured_at"] = 110
    if change == "expired":
        bad["captured_at"] = 0
    if change == "regress":
        bad["counts"]["feedback"] = 4
    if change == "mark":
        bad["watermark"] = [1, 1, 1]
    if change == "replay":
        bad["captured_at"] = 100
    if change == "attempts":
        bad["counts"]["http_attempts"] = 7
    if change == "receipt_size":
        bad["counts"]["events"] = 101
    with pytest.raises(ValueError):
        p.publish(bad)
    assert p.summary()["projection"] == payload and p.summary()["rejected_publications"] == 1


def test_real_http_auth_body_limit_wrong_binding_and_invalidation(payload):
    p = Projection(payload["binding"], clock=lambda: 100)
    token = "secret" * 8
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(p, token))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    uri = "http://127.0.0.1:" + str(server.server_port)

    def put(path, value, auth=token):
        request = Request(
            uri + path,
            data=json.dumps(value).encode(),
            method="PUT",
            headers={"Authorization": "Bearer " + auth},
        )
        with urlopen(request) as response:
            return json.load(response)

    try:
        with pytest.raises(HTTPError) as error:
            put("/projection", payload, "wrong")
        assert error.value.code == 401
        assert put("/projection", payload) == {"accepted": True}
        with pytest.raises(HTTPError) as error:
            put("/projection", "x" * 33000)
        assert error.value.code == 400 and p.summary()["source_valid"]
        with pytest.raises(HTTPError):
            put("/invalidate", {"binding": {}})
        assert p.summary()["source_valid"]
        put("/invalidate", {"binding": payload["binding"]})
        assert not p.summary()["source_valid"]
        with urlopen(uri + "/healthz") as response:
            assert response.status == 200
        with urlopen(uri + "/summary") as response:
            assert json.load(response)["binding"] == payload["binding"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
