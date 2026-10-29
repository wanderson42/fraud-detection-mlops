"""Real SHAP on reconciled sessions, with immutable old snapshots and no new HTTP scores."""

# ruff: noqa: F811
import copy
import json
from pathlib import Path
import sqlite3

import pytest
from scripts.case_explanations import controller as explanations
from scripts.case_explanations import hgb_shap
from scripts.fraud_portal import data, export, session_acceptance
from tests.fraud_portal.test_session_acceptance import reconciled  # noqa: F401
from tests.product_flow.conftest import prepared, release, scorer, server  # noqa: F401


@pytest.fixture
def session_report(reconciled, tmp_path, monkeypatch):
    folder, value, _, server = reconciled
    active = value["active"]
    path = Path(active["paths"]["blue"])
    monkeypatch.setattr(explanations, "resolve_active", lambda _: active)
    monkeypatch.setattr(explanations, "work", lambda key: tmp_path / "reports/shap" / key)
    monkeypatch.setattr(explanations, "committed", explanations.pins)
    managed = explanations.managed_destination
    monkeypatch.setattr(
        explanations, "managed_destination", lambda p, area: p if p == path else managed(p, area)
    )
    return folder, value, server


def test_real_session_shap_and_sibling_portal_preserve_original(session_report, tmp_path):
    folder, value, server = session_report
    flow_id = value["flow_id"]
    before = session_acceptance.source_files(folder)
    calls = server[1]["scores"]
    root = tmp_path / "portal"
    original = export.prepare(root, "original", flow_id, None, None, True)
    original_dir = Path(original["directory"])
    saved = {p.name: p.read_bytes() for p in original_dir.iterdir()}
    result = explanations.run(flow_id, "session-shap", 2, True)
    assert result["case_count"] == 2
    assert explanations.verify("session-shap", result["manifest_sha256"])["model_loaded"] is False
    candidate = export.prepare(
        root, "candidate", flow_id, "session-shap", result["manifest_sha256"], True
    )
    snapshot, _ = data.load_bundle(Path(candidate["directory"]), candidate["manifest_sha256"])
    assert candidate["summary"]["explanations"] == 2
    assert snapshot["source_acceptance"] == "reconciled_session_not_product_pause_resume_rehearsal"
    for case in snapshot["cases"]:
        shap = case["SHAP_explanation"]
        assert len(shap["contributions"]) == 19
        assert shap["max_absolute_score_error"] == 0
    assert {p.name: p.read_bytes() for p in original_dir.iterdir()} == saved
    assert session_acceptance.source_files(folder) == before
    assert server[1]["scores"] == calls
    assert not (folder / "rehearsal.json").exists()
    with pytest.raises(ValueError, match="Snapshot existente difere"):
        export.prepare(root, "original", flow_id, "session-shap", result["manifest_sha256"], True)
    assert {p.name: p.read_bytes() for p in original_dir.iterdir()} == saved


@pytest.mark.parametrize("change", ["missing", "receipt", "session", "legacy", "ledger"])
def test_session_shap_rejects_invalid_acceptance_before_publication(session_report, change):
    folder, value, _ = session_report
    receipt = folder / "verification-timing-fixture.json"
    if change == "missing":
        receipt.unlink()
    elif change == "receipt":
        item = json.loads(receipt.read_bytes())
        item["counts"]["decisions"] += 1
        receipt.write_bytes(data.encoded(item))
    elif change == "session":
        item = json.loads((folder / "session.json").read_bytes())
        item["session_id"] = "foreign"
        (folder / "session.json").write_bytes(data.encoded(item))
    elif change == "legacy":
        (folder / "rehearsal.json").write_text("{}")
    else:
        with sqlite3.connect(folder / "checkpoint.sqlite") as connection:
            connection.execute(
                "UPDATE feedback SET fraud_label=1-fraud_label WHERE transaction_id=0"
            )
        item = json.loads(receipt.read_bytes())
        item["checkpoint_sha256"] = data.sha(folder / "checkpoint.sqlite")
        receipt.write_bytes(data.encoded(item))
    with pytest.raises((ValueError, KeyError)):
        explanations.run(value["flow_id"], "invalid", 2, True)
    assert not explanations.work("invalid").exists()


def test_session_shap_is_label_free(session_report):
    _, value, _ = session_report
    _, cases, _ = explanations.snapshot(value["flow_id"], 2)
    active = value["active"]
    runtime = explanations.load_release(
        Path(active["paths"]["blue"]), active["plan"]["releases"]["blue"]["manifest_sha256"]
    )
    changed = copy.deepcopy(cases)
    for case in changed:
        case["transaction_feedback"] = None
    assert hgb_shap.explain(runtime, cases) == hgb_shap.explain(runtime, changed)
