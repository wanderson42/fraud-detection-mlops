"""Generated monitoring fixtures; never reads native data."""

import hashlib

from examples.serving_smoke import synthetic_release
import pytest
from scripts.replay.paced_triage_replay import make_metadata

from fraud_detection_mlops.serving.model_release import load_release
from fraud_detection_mlops.streaming.contracts.customer_day_alerts import CustomerDayAlertConfig
from fraud_detection_mlops.streaming.replay.durable_triage_replay import event_order
from fraud_detection_mlops.streaming.replay.synthetic_event_stream import synthetic_events


@pytest.fixture(scope="module")
def runtime(tmp_path_factory):
    path = tmp_path_factory.mktemp("generated-monitor") / "release"
    result = synthetic_release(path)
    return load_release(path, result["manifest_sha256"])


@pytest.fixture
def setup_job(runtime, tmp_path):
    events = sorted({e.event_id: e for e in synthetic_events()}.values(), key=event_order)
    policy = CustomerDayAlertConfig(
        schema_version=1,
        version="first_crossing_customer_day_v1",
        model_sha256=runtime.manifest.model_sha256,
        serving_release_id=runtime.manifest.serving_release_id,
        score_threshold=0.5,
        daily_customer_budget=1,
    )
    metadata = make_metadata(
        input_sha256=hashlib.sha256(
            "\n".join(e.model_dump_json() for e in events).encode()
        ).hexdigest(),
        runtime=runtime,
        policy=policy,
        speed=1e9,
        capacity=1,
        window=None,
        expected_counts={"events": 20, "transactions": 10, "feedback": 10},
        baseline=[1] * 10,
        source_scope="generated_fixture",
        code={},
    )
    return {
        "events": events,
        "factory": lambda after: (e for e in events if after is None or event_order(e) > after),
        "output": tmp_path / "state",
        "metadata": metadata,
        "runtime": runtime,
        "policy": policy,
    }
