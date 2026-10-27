"""Digest-pinned access pointer, path boundaries and launch credential isolation."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts.fraud_portal import controller as c
from scripts.fraud_portal import data
from scripts.fraud_portal import session_link as link


@pytest.fixture
def binding(tmp_path):
    folder = tmp_path / "data/storage/kind-platform/product-flow/session-arrival-v1"
    folder.mkdir(parents=True)
    source = {"release_manifest": {"model_sha256": "a" * 64, "serving_release_id": "b" * 32}}
    (folder / "input.json").write_bytes(data.encoded(source))
    receipt = {"session_id": "arrival-v1", "input_sha256": data.sha(folder / "input.json")}
    (folder / "session.json").write_bytes(data.encoded(receipt))
    path = link.path_for(tmp_path, "investigation-v1")
    path.parent.mkdir(parents=True)
    value = {
        "schema": "fraud_portal_session_access_v1",
        "portal_id": "investigation-v1",
        "session_id": "arrival-v1",
        "directory": str(folder),
        "session_sha256": data.sha(folder / "session.json"),
        "input_sha256": receipt["input_sha256"],
        **source["release_manifest"],
    }
    path.write_bytes(data.encoded(value))
    return path, data.sha(path), value


def test_pointer_is_bound_to_exact_session_input_model_and_managed_path(binding, tmp_path):
    path, pin, value = binding
    assert link.load(path, pin, root=tmp_path) == value
    (Path(value["directory"]) / "input.json").write_text("changed")
    with pytest.raises(ValueError, match="Fonte da sessão alterada"):
        link.load(path, pin, root=tmp_path)


@pytest.mark.parametrize("change", ["digest", "path", "model", "session", "symlink"])
def test_wrong_binding_cannot_be_used_as_live_source(binding, tmp_path, change):
    path, pin, value = binding
    if change == "digest":
        pin = "f" * 64
    if change == "path":
        value["directory"] = str(tmp_path / "outside")
    if change == "model":
        value["model_sha256"] = "f" * 64
    if change == "session":
        value["session_id"] = "../../escape"
    if change == "symlink":
        original = path.with_suffix(".saved")
        path.rename(original)
        path.symlink_to(original)
    elif change != "digest":
        path.write_bytes(data.encoded(value))
        pin = data.sha(path)
    with pytest.raises((ValueError, OSError)):
        link.load(path, pin, root=tmp_path)


def test_existing_launcher_passes_only_pinned_link_and_no_service_secrets(
    binding, tmp_path, monkeypatch
):
    path, pin, _ = binding
    runtime = tmp_path / "runtime"
    (runtime / "bin").mkdir(parents=True)
    (runtime / "bin/python").touch()
    monkeypatch.setattr(c, "ROOT", tmp_path)
    monkeypatch.setattr(c, "ENVIRONMENT", runtime)
    monkeypatch.setattr(
        c,
        "config",
        lambda _key: ({"directory": "verified-bundle", "manifest_sha256": "a" * 64}, {}, {}),
    )
    monkeypatch.setattr(c, "performance_config", lambda _: None)
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "private")
    monkeypatch.setenv("POSTGRES_PASSWORD", "private")
    calls = []
    monkeypatch.setattr(
        c.subprocess, "run", lambda *a, **kw: calls.append(kw) or SimpleNamespace(returncode=0)
    )
    assert c.open_portal("investigation-v1", 18501) == 0
    environment = calls[0]["env"]
    assert environment["FRAUD_PORTAL_SESSION_LINK"] == str(path)
    assert environment["FRAUD_PORTAL_SESSION_LINK_SHA256"] == pin
    assert "AWS_SECRET_ACCESS_KEY" not in environment and "POSTGRES_PASSWORD" not in environment
