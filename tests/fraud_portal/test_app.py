"""Real Streamlit AppTest navigation, queue interaction and missing-explanation states."""

from pathlib import Path

import pytest

pytest.importorskip("streamlit")
pytest.importorskip("plotly")
from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parents[2] / "scripts/fraud_portal/app.py"


def test_overview_shows_full_scope_and_opens_selected_case(bundle):
    app = AppTest.from_file(APP, default_timeout=20).run()
    assert not app.exception
    assert [metric.value for metric in app.metric] == ["3", "2", "2/3", "1/3"]
    app.button[0].click().run()
    assert not app.exception and app.title[0].value == "Investigar caso"
    assert app.selectbox[0].value == 101
    assert any("Reconstrução" in m.value for m in app.markdown)


def test_filters_empty_and_invalid_input_have_clear_states(bundle):
    app = AppTest.from_file(APP, default_timeout=20).run()
    app.sidebar.radio[0].set_value("Fila de investigação").run()
    assert not app.exception and len(app.dataframe[0].value) == 2
    app.text_input[0].set_value("999").run()
    assert not app.exception and any("Nenhuma decisão" in m.value for m in app.info)
    app.text_input[0].set_value("invalid").run()
    assert not app.exception and any("numérico" in m.value for m in app.info)
    app.text_input[0].set_value("").run()
    app.multiselect[0].set_value([]).run()
    assert not app.exception and any("Nenhuma decisão" in m.value for m in app.info)


def test_case_without_SHAP_shows_context_and_feedback_without_fabricating_explanation(bundle):
    app = AppTest.from_file(APP, default_timeout=20).run()
    app.sidebar.radio[0].set_value("Investigar caso").run()
    app.selectbox[0].set_value(102).run()
    assert not app.exception
    assert any("ainda não foi preparada" in m.value for m in app.info)
    assert any("sem fraude" in m.value for m in app.info)
    assert not any("Reconstrução" in m.value for m in app.markdown)
    app.selectbox[0].set_value(103).run()
    assert not app.exception and any("ainda não disponível" in m.value for m in app.info)


def test_corruption_is_not_presented_as_valid_data(bundle):
    directory, _, _ = bundle
    (directory / "snapshot.json").write_text("changed")
    app = AppTest.from_file(APP, default_timeout=20).run()
    assert not app.exception and app.error
    assert not app.metric


def test_real_Streamlit_HTTP_health_with_verified_synthetic_bundle(bundle):
    import os
    import socket
    import subprocess
    import sys
    import time
    from urllib.error import URLError
    from urllib.request import ProxyHandler, build_opener

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    environment = dict(
        os.environ, STREAMLIT_BROWSER_GATHER_USAGE_STATS="false", DO_NOT_TRACK="true"
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            str(APP),
            "--server.address=127.0.0.1",
            f"--server.port={port}",
            "--server.headless=true",
            "--browser.gatherUsageStats=false",
        ],
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                with build_opener(ProxyHandler({})).open(
                    f"http://127.0.0.1:{port}/_stcore/health", timeout=1
                ) as response:
                    assert response.status == 200 and response.read(20).strip() == b"ok"
                    return
            except URLError, TimeoutError:
                time.sleep(0.1)
        pytest.fail("Streamlit health did not become ready")
    finally:
        process.terminate()
        process.wait(timeout=10)


def test_queue_pagination_and_filter_change_preserve_selected_case(bundle, monkeypatch):
    import copy

    from scripts.fraud_portal import data

    directory, _, value = bundle
    template = value["cases"][1]
    value["cases"] = []
    for index in range(60):
        case = copy.deepcopy(template)
        case["request"]["transaction_id"] = case["response"]["transaction_id"] = 200 + index
        case["request"]["customer_id"] = 100 + index
        value["cases"].append(case)
    (directory / "snapshot.json").write_bytes(data.encoded(value))
    manifest = {
        "schema": "fraud_investigation_portal_v1",
        "snapshot_sha256": data.sha(directory / "snapshot.json"),
        "model_sha256": value["model_sha256"],
        "serving_release_id": value["serving_release_id"],
        "case_count": 60,
        "created_at": "2026-10-22T15:00:00+00:00",
    }
    (directory / "manifest.json").write_bytes(data.encoded(manifest))
    monkeypatch.setenv("FRAUD_PORTAL_MANIFEST_SHA256", data.sha(directory / "manifest.json"))
    app = AppTest.from_file(APP, default_timeout=20).run()
    app.sidebar.radio[0].set_value("Fila de investigação").run()
    assert not app.exception and len(app.dataframe[0].value) == 25
    app.number_input[0].set_value(3).run()
    assert not app.exception and len(app.dataframe[0].value) == 10
    app.selectbox[0].set_value(100).run()
    assert not app.exception and len(app.dataframe[0].value) == 1
    app.button[0].click().run()
    assert not app.exception and app.selectbox[0].value == 200
