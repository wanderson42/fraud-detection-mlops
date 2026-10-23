"""The live page follows committed arrivals without analyst ingestion controls."""

from pathlib import Path

import pytest
from scripts.fraud_portal import data
from scripts.product_session import reader

pytest.importorskip("streamlit")
pytest.importorskip("plotly")
from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parents[2] / "scripts/product_session/app.py"


def test_empty_then_new_arrivals_and_readonly_investigation(bundle, monkeypatch):
    directory, pin, snapshot = bundle
    value = {
        **snapshot,
        "session_id": "fixture",
        "producer": {"status": "prepared", "age_seconds": None},
        "last_event_order": [None, None, None],
        "expected_decisions": 3,
        "cases": [],
    }
    monkeypatch.setenv("FRAUD_SESSION_DIRECTORY", str(directory))
    monkeypatch.setenv("FRAUD_SESSION_SHA256", pin)

    def page_view(*_, page=1, page_size=100, only_alerts=False):
        cases = data.select(value["cases"], statuses=("alert",) if only_alerts else ())
        return {
            **value,
            "cases": cases[(page - 1) * page_size : page * page_size],
            "totals": data.summary(value["cases"]),
            "outcomes": {
                name: sum(c["triage"]["status"] == name for c in value["cases"])
                for name in {c["triage"]["status"] for c in value["cases"]}
            },
            "score_histogram": [0] * 20,
            "pagination": {
                "page": page,
                "page_size": page_size,
                "pages": 1,
                "matching_decisions": len(cases),
            },
        }

    monkeypatch.setattr(reader, "read", page_view)
    app = AppTest.from_file(APP, default_timeout=20).run()
    assert not app.exception and app.metric[0].value == "0/3"
    assert any("Aguardando a primeira" in i.value for i in app.info)
    value.update(
        cases=snapshot["cases"],
        producer={"status": "running", "age_seconds": 1.2},
        last_event_order=[1537660800000000000, 1, 103],
    )
    app.run()
    assert not app.exception and app.metric[0].value == "3/3"
    assert len(app.dataframe[0].value) == 2
    assert not app.button
    app.checkbox[0].uncheck().run()
    assert len(app.dataframe[0].value) == 3
    assert any("SHAP ainda não preparado" in c.value for c in app.caption)
    value["producer"]["status"] = "stale"
    app.run()
    assert any("sem atualização recente" in i.value for i in app.info)


def test_invalid_session_fails_closed_in_UI(monkeypatch):
    monkeypatch.setenv("FRAUD_SESSION_DIRECTORY", "/tmp/session")
    monkeypatch.setenv("FRAUD_SESSION_SHA256", "a" * 64)

    def broken(*_, **_kwargs):
        raise ValueError("Checkpoint belongs to another session input")

    monkeypatch.setattr(reader, "read", broken)
    app = AppTest.from_file(APP).run()
    assert not app.exception and app.error and not app.metric


def test_page_navigation_keeps_global_totals_and_bounded_rows(bundle, monkeypatch):
    import copy
    import math

    directory, pin, snapshot = bundle
    template = snapshot["cases"][0]
    calls = []

    def page_view(*_, page=1, page_size=25, only_alerts=False):
        calls.append((page, page_size, only_alerts))
        cases = []
        for index in range((page - 1) * page_size, min(page * page_size, 501)):
            case = copy.deepcopy(template)
            case["request"]["transaction_id"] = index
            case["response"]["transaction_id"] = index
            cases.append(case)
        return {
            **snapshot,
            "cases": cases,
            "session_id": "large-fixture",
            "producer": {"status": "complete", "age_seconds": 1},
            "last_event_order": [1537660800000000000, 0, 500],
            "expected_decisions": 501,
            "totals": {"decisions": 501, "alerts": 501, "customers": 501, "feedback": 501},
            "outcomes": {"alert": 501},
            "score_histogram": [501] + [0] * 19,
            "pagination": {
                "page": page,
                "page_size": page_size,
                "pages": math.ceil(501 / page_size),
                "matching_decisions": 501,
            },
        }

    monkeypatch.setenv("FRAUD_SESSION_DIRECTORY", str(directory))
    monkeypatch.setenv("FRAUD_SESSION_SHA256", pin)
    monkeypatch.setattr(reader, "read", page_view)
    app = AppTest.from_file(APP, default_timeout=20).run()
    assert not app.exception and app.metric[0].value == "501/501"
    assert len(app.dataframe[0].value) == 25
    first = set(app.dataframe[0].value["Transação"])
    app.number_input[0].set_value(2).run()
    assert not app.exception and app.metric[0].value == "501/501"
    assert not first & set(app.dataframe[0].value["Transação"])
    assert all(size <= 100 for _, size, _ in calls)
