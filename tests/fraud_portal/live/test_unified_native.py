"""Opt-in real accepted files through AppTest; independent of a scoring/model runtime."""

import json
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("FRAUD_PORTAL_UNIFIED_LIVE") != "1",
    reason="explicit native unified portal opt-in",
)


def test_current_session_and_verified_evidence_use_one_app_without_source_writes(monkeypatch):
    from scripts.fraud_portal import controller as c
    from scripts.fraud_portal import data, session_link
    from streamlit.testing.v1 import AppTest

    key = c.identifier(os.environ.get("FRAUD_PORTAL_ID", "investigation-v1"))
    receipt, snapshot, _ = c.config(key)
    performance = c.performance_config(key)
    assert performance is not None, "Prepare existing performance evidence first"
    link = c.session_config(key)
    assert link is not None, "Run make portal-session-link first"
    path = session_link.path_for(c.ROOT, key)
    value = session_link.page(path, data.sha(path), page_size=25)
    # This gate operates on the completed accepted source; never race a writer.
    assert value["producer"]["status"] == "complete", (
        "Pause/complete source before this acceptance gate"
    )
    files = [
        path,
        Path(link["directory"]) / "session.json",
        Path(link["directory"]) / "input.json",
        Path(link["directory"]) / "checkpoint.sqlite",
        Path(receipt["directory"]) / "manifest.json",
        Path(receipt["directory"]) / "snapshot.json",
        Path(performance[0]["directory"]) / "manifest.json",
        Path(performance[0]["directory"]) / "performance.json",
        c.ROOT / "data/storage/kind-platform/portal" / (key + ".json"),
        c.ROOT / "data/storage/kind-platform/portal" / (key + "-performance.json"),
    ]
    before = {str(p.relative_to(c.ROOT)): data.sha(p) for p in files}
    monkeypatch.setenv("FRAUD_PORTAL_BUNDLE", receipt["directory"])
    monkeypatch.setenv("FRAUD_PORTAL_MANIFEST_SHA256", receipt["manifest_sha256"])
    monkeypatch.setenv("FRAUD_PORTAL_PERFORMANCE", performance[0]["directory"])
    monkeypatch.setenv("FRAUD_PORTAL_PERFORMANCE_SHA256", performance[0]["manifest_sha256"])
    monkeypatch.setenv("FRAUD_PORTAL_SESSION_LINK", str(path))
    monkeypatch.setenv("FRAUD_PORTAL_SESSION_LINK_SHA256", data.sha(path))
    app = AppTest.from_file(c.ROOT / "scripts/fraud_portal/app.py", default_timeout=45).run()
    assert not app.exception and not app.error
    assert app.sidebar.radio[0].value == "Sessão atual"
    assert app.metric[0].value == f"{value['totals']['decisions']}/{value['expected_decisions']}"
    assert app.metric[1].value == str(value["totals"]["alerts"])
    assert len(app.dataframe[0].value) <= 25
    if value["pagination"]["pages"] > 1:
        app.number_input[0].set_value(2).run()
        assert not app.exception and not app.error
    app.sidebar.radio[0].set_value("Visão geral").run()
    assert (
        not app.exception and not app.error and app.metric[0].value == str(len(snapshot["cases"]))
    )
    app.sidebar.radio[0].set_value("Desempenho do modelo").run()
    assert not app.exception and not app.error
    assert any(m.label == "Average Precision" for m in app.metric)
    app.sidebar.radio[0].set_value("Sessão atual").run()
    assert not app.exception and not app.error
    assert before == {str(p.relative_to(c.ROOT)): data.sha(p) for p in files}
    result = {
        "schema": "fraud_unified_portal_native_v1",
        "status": "success",
        "session_id": link["session_id"],
        "session_sha256": link["session_sha256"],
        "session_model_sha256": link["model_sha256"],
        "session_release_id": link["serving_release_id"],
        "session_totals": value["totals"],
        "evidence_decisions": len(snapshot["cases"]),
        "portal_manifest_sha256": receipt["manifest_sha256"],
        "performance_manifest_sha256": performance[0]["manifest_sha256"],
        "validation_rows": performance[1]["dataset"]["rows"],
        "isolated_Streamlit_AppTest_navigation_validated": True,
        "source_files_unchanged": before,
        "browser_visual_review_validated": False,
        "live_arrivals_validated": False,
        "model_loaded": False,
        "champion_modified": False,
    }
    print(json.dumps(result, indent=2))
