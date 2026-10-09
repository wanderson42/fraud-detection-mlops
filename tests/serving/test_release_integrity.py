"""Fail closed before deserialization, then check runtime and score identity."""

import hashlib
import json
import shutil

from fastapi.testclient import TestClient
import pytest
import skops.io as sio

from fraud_detection_mlops.artifacts import sha256, write_json
from fraud_detection_mlops.serving.http_service import create_app
from fraud_detection_mlops.serving.model_release import ServingReleaseError, load_release


@pytest.mark.parametrize("filename", ["manifest.json", "pipeline.skops", "smoke.json"])
def test_changed_bytes_block_startup_before_deserialization(
    serving_release, tmp_path, monkeypatch, filename
):
    source, digest = serving_release
    directory = tmp_path / "release"
    shutil.copytree(source, directory)
    (directory / filename).write_bytes(b"changed")
    monkeypatch.setattr(sio, "loads", lambda *a, **k: pytest.fail("Changed bytes were loaded"))
    with (
        pytest.raises(ServingReleaseError, match="bytes changed"),
        TestClient(create_app(directory, digest)),
    ):
        pytest.fail("Tampered release became ready")


@pytest.mark.parametrize("change", ["environment", "columns", "types", "scores"])
def test_incompatible_release_or_canary_cannot_become_ready(
    serving_release, tmp_path, monkeypatch, change
):
    source, _ = serving_release
    directory = tmp_path / "release"
    shutil.copytree(source, directory)
    manifest = json.loads((directory / "manifest.json").read_text())
    if change == "environment":
        manifest["runtime_environment"]["scikit-learn"] = "different"
    elif change == "columns":
        manifest["feature_columns"].reverse()
    elif change == "types":
        monkeypatch.setattr(sio, "get_untrusted_types", lambda **k: ["unapproved.Type"])
    else:
        smoke = json.loads((directory / "smoke.json").read_text())
        smoke["expected_score"] = 0.99
        write_json(directory / "smoke.json", smoke)
        manifest["smoke_sha256"] = sha256(directory / "smoke.json")
    write_json(directory / "manifest.json", manifest)
    if change != "scores":
        monkeypatch.setattr(
            sio, "loads", lambda *a, **k: pytest.fail("Incompatible release was deserialized")
        )
    with pytest.raises(ServingReleaseError):
        load_release(directory, sha256(directory / "manifest.json"))


def test_missing_release_and_operator_pin_are_required(serving_release, tmp_path, monkeypatch):
    directory, _ = serving_release
    for digest in ("", "a" * 64):
        with pytest.raises(ServingReleaseError):
            load_release(directory, digest)
    with pytest.raises(ServingReleaseError):
        load_release(tmp_path / "missing", hashlib.sha256(b"anything").hexdigest())
    monkeypatch.delenv("FRAUD_SERVING_RELEASE", raising=False)
    monkeypatch.delenv("FRAUD_SERVING_MANIFEST_SHA256", raising=False)
    with pytest.raises(ServingReleaseError, match="Set FRAUD"):
        create_app()
