import json
from pathlib import Path

import pytest

from fraud_detection_mlops.artifacts import write_json


def test_atomic_metadata_failure_preserves_previous_document(tmp_path, monkeypatch):
    path = tmp_path / "manifest.json"
    write_json(path, {"previous": True})

    def fail_replace(*args):
        raise OSError("replace failed")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        write_json(path, {"new": True})
    assert json.loads(path.read_text()) == {"previous": True}
    assert not list(tmp_path.glob("*.tmp"))
