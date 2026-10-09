import json
from pathlib import Path

import pytest

from fraud_detection_mlops.artifacts import source_fingerprint, write_json


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


def test_source_identity_is_stable_across_checkout_location_and_input_order(tmp_path):
    files = []
    for checkout in ("first", "second"):
        root = tmp_path / checkout
        root.mkdir()
        (root / "contracts").mkdir()
        paths = [root / "builder.py", root / "contracts/schema.py"]
        paths[0].write_text("builder\n")
        paths[1].write_text("schema\n")
        files.append((root, paths))
    assert source_fingerprint(*files[0]) == source_fingerprint(files[1][0], reversed(files[1][1]))


def test_source_identity_changes_when_a_shared_rule_or_its_path_changes(tmp_path):
    builder = tmp_path / "builder.py"
    schema = tmp_path / "schema.py"
    builder.write_text("builder\n")
    schema.write_text("rule = 1\n")
    original = source_fingerprint(tmp_path, [builder, schema])
    schema.write_text("rule = 2\n")
    assert source_fingerprint(tmp_path, [builder, schema]) != original
    schema.write_text("rule = 1\n")
    moved = schema.rename(tmp_path / "renamed.py")
    assert source_fingerprint(tmp_path, [builder, moved]) != original


def test_incomplete_or_external_source_inventory_cannot_have_a_valid_identity(tmp_path):
    root = tmp_path / "checkout"
    root.mkdir()
    external = tmp_path / "external.py"
    external.write_text("external\n")
    with pytest.raises(ValueError, match="at least one file"):
        source_fingerprint(root, [])
    with pytest.raises(FileNotFoundError):
        source_fingerprint(root, [root / "missing.py"])
    with pytest.raises(ValueError):
        source_fingerprint(root, [external])


def test_json_create_only_never_replaces_existing_receipt(tmp_path):
    path = tmp_path / "receipt.json"
    write_json(path, {"original": True}, overwrite=False)
    original = path.read_bytes()
    with pytest.raises(FileExistsError):
        write_json(path, {"replacement": True}, overwrite=False)
    assert path.read_bytes() == original
    assert sorted(p.name for p in tmp_path.iterdir()) == ["receipt.json"]
