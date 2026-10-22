"""Disposable real HTTP API and already-observed synthetic source events."""

import socket
from threading import Thread
import time

from examples.serving_smoke import synthetic_release
from fastapi.responses import JSONResponse
import pytest
from scripts.product_flow import inputs
from scripts.product_flow.controller import policy
from scripts.product_flow.http_scoring import HTTPScorer
from tests.operational_round.test_make import dispatch  # noqa: F401
import uvicorn

from fraud_detection_mlops.serving.http_service import create_app
from fraud_detection_mlops.serving.model_release import ReleaseManifest, load_release
from fraud_detection_mlops.streaming.contracts.customer_day_alerts import CustomerDayAlertConfig
from fraud_detection_mlops.streaming.replay.durable_triage_replay import DurableTriageReplay
from fraud_detection_mlops.streaming.replay.synthetic_event_stream import synthetic_events


@pytest.fixture(scope="module")
def release(tmp_path_factory):
    path = tmp_path_factory.mktemp("product-release") / "release"
    result = synthetic_release(path)
    return path, result["manifest_sha256"]


@pytest.fixture
def prepared(tmp_path, release, monkeypatch):
    from fraud_detection_mlops.storage import managed_paths

    monkeypatch.setattr(managed_paths, "DATA_DIR", tmp_path)
    path, digest = release
    runtime = load_release(path, digest)
    source = tmp_path / "source.sqlite"
    alert = CustomerDayAlertConfig(
        schema_version=1,
        version="first_crossing_customer_day_v1",
        model_sha256=runtime.manifest.model_sha256,
        serving_release_id=runtime.manifest.serving_release_id,
        score_threshold=0.0,
        daily_customer_budget=100,
    )
    with DurableTriageReplay(
        source,
        run_id="source",
        input_sha256="a" * 64,
        runtime=runtime,
        policy=alert,
        data_scope="synthetic_fixture",
    ) as engine:
        for event in synthetic_events():
            engine.handle(event)
    config = policy()
    config.update(
        context_start="2018-04-01T00:00:00",
        scoring_start="2018-04-15T00:00:00",
        scored_transactions=2,
        maximum_scored_with_timestamp_peers=4,
        score_threshold=0.0,
    )
    folder = tmp_path / "storage/flow"
    folder.mkdir(parents=True)
    capture = inputs.capture(source, folder / "events.sqlite", config)
    value = {
        "flow_id": "fixture",
        "capture": capture,
        "policy": config,
        "release_manifest": runtime.manifest.model_dump(),
        "alert_policy": alert.model_dump(),
    }
    return folder, value, source


@pytest.fixture
def server(release):
    path, digest = release
    faults = {"failures": 0, "wrong_identity": False, "scores": 0, "invalid": False}
    app = create_app(path, digest)

    @app.middleware("http")
    async def fault(request, call_next):
        if request.url.path == "/score":
            faults["scores"] += 1
            if faults["failures"]:
                faults["failures"] -= 1
                return JSONResponse({"error": "temporary"}, status_code=503)
            if faults["invalid"]:
                return JSONResponse({"error": "invalid"}, status_code=422)
            if faults["wrong_identity"]:
                data = await request.json()
                return JSONResponse(
                    {
                        "schema_version": 1,
                        "transaction_id": data["transaction_id"],
                        "score": 0.1,
                        "score_semantics": "uncalibrated_ranking",
                        "model_id": "hist_gradient_boosting",
                        "model_sha256": "f" * 64,
                        "feature_contract_version": "gold_v1",
                        "serving_release_id": "b" * 32,
                    }
                )
        return await call_next(request)

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    instance = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = Thread(target=instance.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not instance.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert instance.started
    try:
        yield "http://127.0.0.1:" + str(port), faults
    finally:
        instance.should_exit = True
        thread.join(10)
        assert not thread.is_alive()


def scorer(url, value):
    return HTTPScorer(url, ReleaseManifest.model_validate(value["release_manifest"]))
