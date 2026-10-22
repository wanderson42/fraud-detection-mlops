"""The same view renders HGB, linear or a future model identity from the report contract."""

import json
from pathlib import Path

import pytest
from scripts.fraud_portal import data
from scripts.fraud_portal.performance import reader

pytest.importorskip("streamlit")
pytest.importorskip("plotly")
from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parents[2] / "scripts/fraud_portal/app.py"


def quality(bundle, monkeypatch, model_id="hist_gradient_boosting"):
    directory, _, snapshot = bundle
    snapshot["model_id"] = model_id
    for case in snapshot["cases"]:
        case["response"]["model_id"] = model_id
    (directory / "snapshot.json").write_bytes(data.encoded(snapshot))
    manifest = json.loads((directory / "manifest.json").read_bytes())
    manifest["snapshot_sha256"] = data.sha(directory / "snapshot.json")
    (directory / "manifest.json").write_bytes(data.encoded(manifest))
    portal_sha = data.sha(directory / "manifest.json")
    monkeypatch.setenv("FRAUD_PORTAL_MANIFEST_SHA256", portal_sha)
    evaluation = directory.parent / "quality"
    evaluation.mkdir()
    report = {
        "schema": "fraud_portal_performance_v1",
        "portal_manifest_sha256": portal_sha,
        "model_sha256": snapshot["model_sha256"],
        "serving_release_id": snapshot["serving_release_id"],
        "registry": {"version": "7", "run_id": "run-identified"},
        "source_commit": "d" * 40,
        "dataset": {
            "rows": 1000,
            "first_transaction": "2018-09-01",
            "last_transaction": "2018-09-07",
            "identity_sha256": "a" * 64,
        },
        "training_dataset": {"first_transaction": "2018-08-01", "last_transaction": "2018-08-28"},
        "labels_mature_as_of": "2018-09-14",
        "candidates": [
            {
                "model_id": model_id,
                "model_sha256": snapshot["model_sha256"],
                "is_snapshot_model": True,
                "metrics": {
                    "average_precision": 0.6,
                    "roc_auc": 0.85,
                    "daily_customer_precision_at_100": 0.5,
                    "fraud_rate": 0.01,
                    "fraud_count": 10,
                    "rows": 1000,
                },
                "curves": {
                    "precision_recall": {"x": [1, 0.5, 0], "y": [0.01, 0.5, 1]},
                    "roc": {"x": [0, 0.1, 1], "y": [0, 0.8, 1]},
                },
                "threshold_transactions": {
                    "true_positive": 5,
                    "false_positive": 5,
                    "false_negative": 5,
                    "true_negative": 985,
                    "precision": 0.5,
                    "recall": 0.5,
                },
            }
        ],
        "queue_policy": {
            "policy": {"score_threshold": 0.5, "daily_customer_budget": 100},
            "alerts": 10,
            "precision": 0.5,
            "recall": 0.5,
            "true_positive": 5,
            "false_positive": 5,
            "false_negative": 5,
        },
    }
    (evaluation / "performance.json").write_bytes(data.encoded(report))
    (evaluation / "manifest.json").write_bytes(
        data.encoded(
            {
                "schema": "fraud_portal_performance_manifest_v1",
                "report_sha256": data.sha(evaluation / "performance.json"),
            }
        )
    )
    pin = data.sha(evaluation / "manifest.json")
    monkeypatch.setenv("FRAUD_PORTAL_PERFORMANCE", str(evaluation))
    monkeypatch.setenv("FRAUD_PORTAL_PERFORMANCE_SHA256", pin)
    return evaluation, pin, portal_sha, snapshot


@pytest.mark.parametrize(
    "model_id", ["hist_gradient_boosting", "logistic_regression", "new_algorithm_v2"]
)
def test_page_is_driven_by_snapshot_model_not_algorithm_name(bundle, monkeypatch, model_id):
    quality(bundle, monkeypatch, model_id)
    app = AppTest.from_file(APP, default_timeout=20).run()
    app.sidebar.radio[0].set_value("Desempenho do modelo").run()
    assert not app.exception
    assert [m.value for m in app.metric[:4]] == ["0.600", "50.00%", "0.850", "1.00%"]
    assert model_id in app.dataframe[0].value["Modelo"].tolist()
    assert len(app.tabs) == 4
    assert any("99.00%" in m.value and "recall de fraude zero" in m.value for m in app.info)
    assert any("não é uma nova confirmação" in m.value for m in app.caption)


def test_missing_evidence_keeps_cases_available_without_inventing_metrics(bundle):
    app = AppTest.from_file(APP, default_timeout=20).run()
    app.sidebar.radio[0].set_value("Desempenho do modelo").run()
    assert not app.exception and any("ainda não foi preparada" in m.value for m in app.info)
    assert not app.metric
    app.sidebar.radio[0].set_value("Fila de investigação").run()
    assert not app.exception and app.dataframe


@pytest.mark.parametrize("change", ["bytes", "model", "release", "portal"])
def test_evidence_from_another_model_release_or_snapshot_is_refused(bundle, monkeypatch, change):
    directory, pin, portal_sha, snapshot = quality(bundle, monkeypatch)
    if change == "bytes":
        (directory / "performance.json").write_text("modified")
    elif change == "model":
        snapshot["model_sha256"] = "f" * 64
    elif change == "release":
        snapshot["serving_release_id"] = "f" * 32
    else:
        portal_sha = "f" * 64
    with pytest.raises(ValueError):
        reader.load(directory, pin, portal_sha, snapshot)
