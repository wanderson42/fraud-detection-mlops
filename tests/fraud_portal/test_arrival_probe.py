"""Exercise the isolated live-probe navigation with explicit synthetic evidence."""

import pytest
from scripts.fraud_portal import controller as portal
from scripts.fraud_portal import data, session_link

pytest.importorskip("streamlit")
pytest.importorskip("plotly")
from tests.fraud_portal.live.probe_arrivals import probe
from tests.fraud_portal.test_performance_ui import quality
from tests.fraud_portal.test_unified_portal import current as session_fixture


@pytest.fixture
def live_fixture(bundle, monkeypatch):
    return session_fixture.__wrapped__(bundle, monkeypatch)


def test_live_probe_uses_one_portal_and_preserves_static_sources(
    live_fixture, bundle, monkeypatch
):
    directory, state, _ = live_fixture
    evaluation, performance_pin, portal_pin, _ = quality(bundle, monkeypatch)
    source = directory.parent / "session"
    source.mkdir()
    for name in ("session.json", "input.json"):
        (source / name).write_text(name)
    link_path = directory.parent / "session-link.json"
    link_path.write_text("linked")
    link = {"session_sha256": "f" * 64, "directory": str(source)}
    monkeypatch.setattr(
        portal,
        "config",
        lambda _: (
            {"directory": str(directory), "manifest_sha256": portal_pin},
            data.load_bundle(directory, portal_pin)[0],
            {},
        ),
    )
    monkeypatch.setattr(
        portal,
        "performance_config",
        lambda _: ({"directory": str(evaluation), "manifest_sha256": performance_pin}, {}),
    )
    monkeypatch.setattr(portal, "session_config", lambda _: link)
    monkeypatch.setattr(session_link, "path_for", lambda *_: link_path)
    before = {p: data.sha(p) for p in source.iterdir()}
    state["producer"]["status"] = "running"
    result = probe("synthetic", "f" * 64)
    assert result["status"] == "live_portal_AppTest_verified"
    assert result["displayed_decisions"] == state["totals"]["decisions"]
    assert result["pagination_and_evidence_navigation_verified"]
    assert before == {p: data.sha(p) for p in source.iterdir()}


def test_live_probe_rejects_other_session_before_UI_start(monkeypatch):
    monkeypatch.setattr(portal, "config", lambda _: ({}, {}, {}))
    monkeypatch.setattr(portal, "performance_config", lambda _: ({}, {}))
    monkeypatch.setattr(portal, "session_config", lambda _: {"session_sha256": "a" * 64})
    with pytest.raises(AssertionError):
        probe("synthetic", "b" * 64)
