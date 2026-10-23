"""The live page follows committed arrivals without analyst ingestion controls."""

from pathlib import Path

import pytest
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
    monkeypatch.setattr(reader, "read", lambda *_: value)
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

    def broken(*_):
        raise ValueError("Checkpoint belongs to another session input")

    monkeypatch.setattr(reader, "read", broken)
    app = AppTest.from_file(APP).run()
    assert not app.exception and app.error and not app.metric
