import numpy as np
import pytest

from fraud_detection_mlops.integrations import skops_persistence as persistence


def test_unknown_types_are_rejected_before_deserialization(monkeypatch, tmp_path):
    monkeypatch.setattr(persistence.sio, "get_untrusted_types", lambda **kwargs: ["foreign.Code"])
    monkeypatch.setattr(
        persistence.sio, "loads", lambda *a, **kw: pytest.fail("unreviewed model loaded")
    )
    path = tmp_path / "model.skops"
    path.write_bytes(b"model bytes to inspect")
    with pytest.raises(persistence.PersistenceError, match="Unreviewed"):
        persistence.load_pipeline(path)


def test_round_trip_rejects_score_change(toy_pipeline, tmp_path):
    model, features = toy_pipeline
    with pytest.raises(persistence.PersistenceError, match="changed validation scores"):
        persistence.save_pipeline(
            model, tmp_path / "model.skops", features, np.zeros(len(features))
        )
