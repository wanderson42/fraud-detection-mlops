"""Small hash-pinned release bytes; never deserialize a production model."""

import hashlib
import json

import pytest
from tests.operational_round.test_make import dispatch  # noqa: F401


@pytest.fixture
def seed_release(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "pipeline.skops").write_bytes(b"pinned-model-bytes")
    (source / "smoke.json").write_text("{}\n")
    h = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    (source / "manifest.json").write_text(
        json.dumps(
            {
                "evidence_kind": "verified_reference",
                "model_sha256": h(source / "pipeline.skops"),
                "smoke_sha256": h(source / "smoke.json"),
                "source": {"registered_model": "fraud-detector", "round_id": "portfolio-v1"},
            }
        )
    )
    return source, h(source / "manifest.json")


from tests.product_flow.conftest import prepared, release, server  # noqa: F401
