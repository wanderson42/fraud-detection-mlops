"""Historical receipts remain verifiable without keeping their migration runner."""

import json
from pathlib import Path

import pytest

from fraud_detection_mlops.artifacts import sha256
from fraud_detection_mlops.modeling import baseline, persistence, train


def test_v1_receipt_verification_never_deserializes_historical_bytes(baseline_source, monkeypatch):
    result = train.run_baseline(
        baseline_source / "gold",
        baseline_source / "experiments",
        inventory_path=baseline_source / "inventory.json",
        protocol_path=baseline_source / "protocol.json",
        tracking_root=None,
    )
    directory = Path(result["baseline_path"])
    manifest = json.loads((directory / "manifest.json").read_text())
    manifest["version"] = "baseline_v1"
    manifest.pop("tracking")
    manifest.pop("model_format")
    # Synthetic opaque artifacts exercise the historical byte/metadata verifier.
    # They are not presented as models migrated from the author's real experiment.
    for record in manifest["files"]:
        if record["path"].endswith("model.skops"):
            previous = directory / record["path"]
            record["path"] = record["path"].replace("model.skops", "model.joblib")
            path = directory / record["path"]
            path.write_bytes(b"synthetic historical opaque model")
            previous.unlink()
            record.update(size_bytes=path.stat().st_size, sha256=sha256(path))
    (directory / "manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(
        persistence.sio, "load", lambda *a, **kw: pytest.fail("historical bytes loaded")
    )
    assert (
        baseline.verify_baseline(
            directory,
            inventory_path=baseline_source / "inventory.json",
            protocol_path=baseline_source / "protocol.json",
        )["status"]
        == "success"
    )
