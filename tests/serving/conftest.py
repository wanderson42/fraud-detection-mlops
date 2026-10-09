"""Generated, portable releases; no Handbook data or production model."""

import json

from examples.serving_smoke import synthetic_release
import pytest


@pytest.fixture(scope="module")
def serving_release(tmp_path_factory):
    directory = tmp_path_factory.mktemp("serving") / "release"
    result = synthetic_release(directory)
    return directory, result["manifest_sha256"]


@pytest.fixture
def score_payload(serving_release):
    directory, _ = serving_release
    return json.loads((directory / "smoke.json").read_text())["request"]
