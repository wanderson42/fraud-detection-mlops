"""Single-portal navigation, bounded live pages and model-bound explanation reuse."""

import copy
from pathlib import Path

import pytest
from scripts.fraud_portal import session_link

pytest.importorskip("streamlit")
pytest.importorskip("plotly")
from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parents[2] / "scripts/fraud_portal/app.py"


@pytest.fixture
def current(bundle, monkeypatch):
    directory, pin, snapshot = bundle
    state = {
        "session_id": "arrival-fixture",
        "model_sha256": snapshot["model_sha256"],
        "serving_release_id": snapshot["serving_release_id"],
        "model_id": "any_estimator",
        "alert_policy": snapshot["alert_policy"],
        "producer": {"status": "complete", "age_seconds": 1},
        "last_event_order": [1537660800000000000, 0, 5],
        "expected_decisions": 501,
        "totals": {"decisions": 501, "alerts": 81, "customers": 401, "feedback": 501},
        "outcomes": {"alert": 81, "below_threshold": 420},
        "score_histogram": [501] + [0] * 19,
    }
    calls = []

    def page(*_, page=1, page_size=25, only_alerts=False):
        calls.append((page, page_size, only_alerts))
        rows = []
        for i in range(page_size):
            case = copy.deepcopy(snapshot["cases"][1])
            case["request"]["transaction_id"] = 1000 + (page - 1) * page_size + i
            case["response"]["transaction_id"] = case["request"]["transaction_id"]
            rows.append(case)
        return {
            **state,
            "cases": rows,
            "pagination": {
                "page": page,
                "page_size": page_size,
                "pages": 4,
                "matching_decisions": 81,
            },
        }

    monkeypatch.setenv("FRAUD_PORTAL_SESSION_LINK", str(directory / "link.json"))
    monkeypatch.setenv("FRAUD_PORTAL_SESSION_LINK_SHA256", pin)
    monkeypatch.setattr(session_link, "page", page)
    return directory, state, calls


def test_one_portal_defaults_to_session_and_keeps_global_totals_when_paging(current):
    _, _state, calls = current
    app = AppTest.from_file(APP, default_timeout=20).run()
    assert not app.exception
    assert app.sidebar.radio[0].value == "Sessão atual"
    assert [m.value for m in app.metric[:4]] == ["501/501", "81", "401", "501"]
    assert len(app.dataframe[0].value) == 25
    first = set(app.dataframe[0].value["Transação"])
    app.number_input[0].set_value(2).run()
    assert not app.exception and app.metric[0].value == "501/501"
    assert not first & set(app.dataframe[0].value["Transação"])
    assert any("somente os casos desta página" in c.value for c in app.caption)
    assert all(size <= 100 for _, size, _ in calls)
    assert not any(
        "reconstrução" in str(b.label).lower() or "treinar" in str(b.label).lower()
        for b in app.button
    )


def test_navigation_preserves_evidence_scope_instead_of_relabelling_it_as_current(current):
    app = AppTest.from_file(APP, default_timeout=20).run()
    app.sidebar.radio[0].set_value("Visão geral").run()
    assert not app.exception and app.metric[0].value == "3"
    assert any("Fonte: evidências verificadas · 3 decisões" in c.value for c in app.caption)
    app.sidebar.radio[0].set_value("Desempenho do modelo").run()
    assert not app.exception and any("ainda não foi preparada" in x.value for x in app.info)
    app.sidebar.radio[0].set_value("Sessão atual").run()
    assert not app.exception and app.metric[0].value == "501/501"


def test_broken_session_never_falls_back_to_old_snapshot_as_live(current, monkeypatch):
    def broken(*_, **_kw):
        raise ValueError("Fonte da sessão alterada")

    monkeypatch.setattr(session_link, "page", broken)
    app = AppTest.from_file(APP, default_timeout=20).run()
    assert not app.exception and app.error and not app.metric
    app.sidebar.radio[0].set_value("Visão geral").run()
    assert not app.exception and app.metric[0].value == "3"


def test_corrupt_evidence_does_not_hide_valid_current_session(current):
    directory, _, _ = current
    (directory / "snapshot.json").write_text("corrupt")
    app = AppTest.from_file(APP, default_timeout=20).run()
    assert not app.exception and app.metric[0].value == "501/501"
    app.sidebar.radio[0].set_value("Visão geral").run()
    assert not app.exception and app.error and not app.metric


def test_explanations_reused_only_for_exact_input_and_model_response(bundle):
    _, pin, snapshot = bundle
    case = copy.deepcopy(snapshot["cases"][0])
    case["SHAP_explanation"] = None
    result = session_link.explainable([case], snapshot, pin)[0]
    assert (
        result["SHAP_explanation"]
        and result["explanation_origin"]["portal_manifest_sha256"] == pin
    )
    assert case["SHAP_explanation"] is None
    for change in ("input", "model", "score", "release"):
        different = copy.deepcopy(case)
        if change == "input":
            different["request"]["features"]["TX_AMOUNT"] += 1
        if change == "model":
            different["response"]["model_sha256"] = "f" * 64
        if change == "score":
            different["response"]["score"] += 0.0001
        if change == "release":
            different["response"]["serving_release_id"] = "f" * 32
        assert session_link.explainable([different], snapshot, pin)[0]["SHAP_explanation"] is None
    assert session_link.explainable([case], None, pin)[0]["SHAP_explanation"] is None
