"""Real SHAP, saved HTTP scores, immutable sources and negative identity/parity cases."""

import copy
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from scripts.case_explanations import controller as c
from scripts.case_explanations import hgb_shap

from fraud_detection_mlops.evaluation.validation_diagnostics import explain_sample
from fraud_detection_mlops.features.feature_schema import FEATURE_COLUMNS
from fraud_detection_mlops.serving.model_release import load_release


def test_real_HTTP_cases_SHAP_report_offline_verification_and_unchanged_source(
    accepted, monkeypatch
):
    folder, _, server = accepted
    before = c.source_files(folder)
    http_before = server[1]["scores"]
    result = c.run("fixture", "test", 2, True)
    assert result["case_count"] == 2
    assert c.source_files(folder) == before and server[1]["scores"] == http_before
    report = c.work("test")
    cases = json.loads((report / "cases.json").read_bytes())
    for case in cases:
        shap = case["SHAP_explanation"]
        assert shap["feature_order"] == FEATURE_COLUMNS
        assert len(shap["contributions"]) == 19 and shap["max_absolute_score_error"] == 0
        assert (
            abs(sum(shap["contributions"].values()) + shap["base_log_odds"] - shap["raw_log_odds"])
            < 1e-7
        )
        assert case["request"]["features"].keys() == shap["contributions"].keys()
        assert shap["model_sha256"] == result["model_sha256"]
        assert (report / f"case-{case['request']['transaction_id']}.svg").is_file()
    assert "log-odds" in (report / "index.html").read_text()
    monkeypatch.setattr(c, "load_release", lambda *_: pytest.fail("verify must not load a model"))
    checked = c.verify("test", result["manifest_sha256"])
    assert checked["status"] == "case_explanations_verified" and checked["model_loaded"] is False
    assert c.source_files(folder) == before
    with pytest.raises(ValueError, match="already exists"):
        c.run("fixture", "test", 2, True)


def test_case_adapter_matches_existing_validation_method_and_does_not_use_feedback(accepted):
    _, value, _ = accepted
    _, cases, _ = c.snapshot("fixture", 2)
    active = value["active"]
    runtime = load_release(
        Path(active["paths"]["blue"]), active["plan"]["releases"]["blue"]["manifest_sha256"]
    )
    actual = hgb_shap.explain(runtime, cases)
    flipped = copy.deepcopy(cases)
    for case in flipped:
        case["transaction_feedback"] = None
    assert hgb_shap.explain(runtime, flipped) == actual
    from fraud_detection_mlops.serving.request_schema import ScoreRequest

    frame = pd.concat(
        [ScoreRequest.model_validate(v["request"]).feature_frame() for v in cases],
        ignore_index=True,
    )
    metadata = pd.DataFrame({"TX_FRAUD": [0, 1]})
    scores = np.array([v["response"]["score"] for v in cases])
    _, local, _ = explain_sample(runtime.pipeline, frame, metadata, scores, sample_size=2)
    for i in range(2):
        np.testing.assert_allclose(
            [actual[i]["contributions"][n] for n in FEATURE_COLUMNS],
            [local.iloc[i]["SHAP_" + n] for n in FEATURE_COLUMNS],
            rtol=0,
            atol=0,
        )


@pytest.mark.parametrize(
    "change", ["score", "model", "request", "identity", "ledger", "report", "manifest", "symlink"]
)
def test_divergent_sources_or_report_are_refused(accepted, change):
    folder, value, _ = accepted
    if change in {"report", "manifest", "symlink"}:
        result = c.run("fixture", "test", 2, True)
        directory = c.work("test")
        file = directory / ("manifest.json" if change == "manifest" else "cases.json")
        if change == "symlink":
            file.unlink()
            file.symlink_to(folder / "input.json")
        else:
            file.write_text(file.read_text() + " ")
        with pytest.raises(ValueError):
            c.verify("test", result["manifest_sha256"])
        return
    if change == "ledger":
        with (folder / "checkpoint.sqlite").open("ab") as stream:
            stream.write(b"corruption")
        with pytest.raises(ValueError, match="checkpoint"):
            c.run("fixture", "test", 2, True)
        assert not c.work("test").exists()
        return
    _, cases, _ = c.snapshot("fixture", 2)
    active = value["active"]
    runtime = load_release(
        Path(active["paths"]["blue"]), active["plan"]["releases"]["blue"]["manifest_sha256"]
    )
    if change == "identity":
        cases[0]["response"]["model_sha256"] = "f" * 64
        with pytest.raises(ValueError, match="identifies"):
            hgb_shap.explain(runtime, cases)
    elif change == "score":
        cases[0]["response"]["score"] = 0.99999
        with pytest.raises(ValueError, match="scores"):
            hgb_shap.explain(runtime, cases)
    elif change == "model":
        from sklearn.dummy import DummyClassifier

        runtime.pipeline.steps[-1] = ("dummy", DummyClassifier())
        with pytest.raises(ValueError, match="HGB"):
            hgb_shap.explain(runtime, cases)
    else:
        cases[0]["request"]["features"]["TX_HOUR"] = 25
        with pytest.raises(ValueError):
            hgb_shap.explain(runtime, cases)


def test_API_change_after_compute_does_not_publish_report(accepted, monkeypatch):
    from contextlib import contextmanager

    folder, _, _ = accepted
    before = c.source_files(folder)

    @contextmanager
    def changed(_):
        yield None
        raise ValueError("Active deployment changed")

    monkeypatch.setattr(c.flow, "target", changed)
    with pytest.raises(ValueError, match="Active deployment"):
        c.run("fixture", "test", 2, True)
    assert not c.work("test").exists() and c.source_files(folder) == before


def test_make_requires_writer_declaration_and_external_verification_digest(dispatch):
    result, calls = dispatch("product-explain")
    assert result.returncode != 0 and not calls
    result, calls = dispatch("product-explanations-verify")
    assert result.returncode != 0 and not calls
    result, calls = dispatch("product-explain", PRODUCT_NO_OTHER_WRITERS="1")
    assert result.returncode == 0 and calls[-1][3:5] == [
        "scripts.case_explanations.controller",
        "run",
    ]


def test_nonadditive_SHAP_is_refused_before_publication(accepted, monkeypatch):
    original = hgb_shap.shap.TreeExplainer

    class Broken:
        def __init__(self, *args, **kwargs):
            self.explainer = original(*args, **kwargs)

        def __call__(self, *args, **kwargs):
            result = self.explainer(*args, **kwargs)
            result.values = result.values + 1
            return result

    monkeypatch.setattr(hgb_shap.shap, "TreeExplainer", Broken)
    with pytest.raises(ValueError, match="additivity"):
        c.run("fixture", "invalid", 2, True)
    assert not c.work("invalid").exists()


def test_uncommitted_implementation_is_refused(tmp_path, monkeypatch):
    import subprocess

    monkeypatch.setattr(c.f, "ROOT", tmp_path)
    monkeypatch.setattr(c, "pins", lambda: {"uncommitted.py": "a" * 64})
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    with pytest.raises(ValueError, match="Commit reviewed"):
        c.committed()
