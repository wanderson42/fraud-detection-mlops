"""Generated event streams and release artifacts; no native fraud data."""

import hashlib

from examples.serving_smoke import synthetic_release
import pytest

from fraud_detection_mlops.serving.model_release import load_release
from fraud_detection_mlops.streaming.contracts.transaction_events import canonical_event
from fraud_detection_mlops.streaming.replay.synthetic_event_stream import synthetic_events


@pytest.fixture
def replay_events():
    return synthetic_events()


@pytest.fixture
def replay_checksum(replay_events):
    return hashlib.sha256(
        "\n".join(canonical_event(e) for e in replay_events).encode()
    ).hexdigest()


@pytest.fixture(scope="module")
def replay_release(tmp_path_factory):
    directory = tmp_path_factory.mktemp("replay-generated-release") / "release"
    receipt = synthetic_release(directory)
    return directory, receipt["manifest_sha256"]


@pytest.fixture
def replay_runtime(replay_release):
    return load_release(*replay_release)
