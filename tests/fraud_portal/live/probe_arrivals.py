"""Read-only AppTest probe in the isolated UI environment, while arrivals may continue."""

import argparse
import json
import os
from pathlib import Path

from scripts.fraud_portal import controller as portal
from scripts.fraud_portal import data, session_link
from streamlit.testing.v1 import AppTest


def probe(portal_id, expected_session):
    receipt, snapshot, _ = portal.config(portal_id)
    performance = portal.performance_config(portal_id)
    link = portal.session_config(portal_id)
    assert link and link["session_sha256"] == expected_session
    assert performance is not None
    path = session_link.path_for(portal.ROOT, portal_id)
    os.environ.update(
        FRAUD_PORTAL_BUNDLE=receipt["directory"],
        FRAUD_PORTAL_MANIFEST_SHA256=receipt["manifest_sha256"],
        FRAUD_PORTAL_PERFORMANCE=performance[0]["directory"],
        FRAUD_PORTAL_PERFORMANCE_SHA256=performance[0]["manifest_sha256"],
        FRAUD_PORTAL_SESSION_LINK=str(path),
        FRAUD_PORTAL_SESSION_LINK_SHA256=data.sha(path),
    )
    static = [
        path,
        Path(link["directory"]) / "session.json",
        Path(link["directory"]) / "input.json",
        Path(receipt["directory"]) / "manifest.json",
        Path(receipt["directory"]) / "snapshot.json",
        Path(performance[0]["directory"]) / "manifest.json",
        Path(performance[0]["directory"]) / "performance.json",
    ]
    before_bytes = {str(p): data.sha(p) for p in static}
    before = session_link.page(path, data.sha(path), page_size=25)
    app = AppTest.from_file(portal.ROOT / "scripts/fraud_portal/app.py", default_timeout=45).run()
    assert not app.exception and not app.error
    assert app.sidebar.radio[0].value == "Sessão atual"
    displayed = int(app.metric[0].value.split("/")[0])
    after = session_link.page(path, data.sha(path), page_size=25)
    assert before["totals"]["decisions"] <= displayed <= after["totals"]["decisions"]
    app.checkbox[0].set_value(False).run()
    assert not app.exception and not app.error
    assert len(app.dataframe[0].value) <= 25
    if displayed > 25:
        app.number_input[0].set_value(2).run()
        assert not app.exception and not app.error
    app.sidebar.radio[0].set_value("Visão geral").run()
    assert not app.exception and not app.error
    assert app.metric[0].value == str(len(snapshot["cases"]))
    app.sidebar.radio[0].set_value("Desempenho do modelo").run()
    assert not app.exception and not app.error
    assert any(metric.label == "Average Precision" for metric in app.metric)
    assert before_bytes == {str(p): data.sha(p) for p in static}
    return {
        "status": "live_portal_AppTest_verified",
        "session_sha256": expected_session,
        "displayed_decisions": displayed,
        "committed_before": before["totals"]["decisions"],
        "committed_after": after["totals"]["decisions"],
        "pagination_and_evidence_navigation_verified": True,
        "static_sources_unchanged": True,
        "browser_visual_review_validated": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--portal-id", required=True)
    parser.add_argument("--session-sha256", required=True)
    args = parser.parse_args()
    print(
        "FRAUD_LIVE_PORTAL_PROBE="
        + json.dumps(probe(args.portal_id, args.session_sha256), allow_nan=False)
    )


if __name__ == "__main__":
    main()
