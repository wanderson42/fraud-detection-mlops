"""Only the access pointer is writable; original receipts and checkpoints stay intact."""

import json
from pathlib import Path

import pytest
from scripts.fraud_portal import controller as c
from scripts.fraud_portal import data
from tests.fraud_portal import test_session_link

binding = test_session_link.binding


def test_link_idempotence_and_explicit_rebind_preserve_sources(binding, tmp_path, monkeypatch):
    from scripts.platform import kind_foundation as f
    from scripts.product_session import controller as sessions
    from scripts.product_session import reader

    path, _pin, old = binding
    source = Path(old["directory"])
    checkpoint = source / "checkpoint.sqlite"
    checkpoint.write_bytes(b"preserved source checkpoint")
    before = {p.name: data.sha(p) for p in source.iterdir()}
    monkeypatch.setattr(c, "ROOT", tmp_path)
    monkeypatch.setattr(c, "committed", lambda: None)
    monkeypatch.setattr(c, "config", lambda _: ({}, {}, {}))
    monkeypatch.setattr(f, "managed_destination", lambda path, _: path)
    monkeypatch.setattr(sessions, "load", lambda key: (source, {}))
    monkeypatch.setattr(
        reader,
        "read",
        lambda *_a, **_kw: {
            "model_sha256": old["model_sha256"],
            "serving_release_id": old["serving_release_id"],
        },
    )
    path.unlink()
    assert c.link_session("investigation-v1", "arrival-v1")["status"] == "portal_session_linked"
    first = path.read_bytes()
    assert (
        c.link_session("investigation-v1", "arrival-v1")["status"]
        == "portal_session_already_linked"
    )
    assert path.read_bytes() == first
    next_source = source.with_name("session-arrival-v2")
    next_source.mkdir()
    next_input = json.loads((source / "input.json").read_bytes())
    (next_source / "input.json").write_bytes(data.encoded(next_input))
    (next_source / "session.json").write_bytes(
        data.encoded(
            {"session_id": "arrival-v2", "input_sha256": data.sha(next_source / "input.json")}
        )
    )
    monkeypatch.setattr(sessions, "load", lambda key: (next_source, {}))
    with pytest.raises(ValueError, match="SHA da sessão anterior"):
        c.link_session("investigation-v1", "arrival-v2")
    assert path.read_bytes() == first
    assert (
        c.link_session("investigation-v1", "arrival-v2", old["session_sha256"])["status"]
        == "portal_session_linked"
    )
    archive = path.with_name("investigation-v1-session-" + old["session_sha256"] + ".json")
    assert json.loads(archive.read_bytes()) == old
    assert before == {p.name: data.sha(p) for p in source.iterdir()}
