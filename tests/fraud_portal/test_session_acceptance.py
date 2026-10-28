"""Real HTTP session ledgers can feed the portal without a fictional rehearsal."""
# Shared fixture names deliberately match the existing generated HTTP fixtures.
# ruff: noqa: F811

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3

import pytest
from scripts.fraud_portal import data, export, session_acceptance
from scripts.product_flow import controller as flow
from tests.product_flow.conftest import prepared, release, scorer, server  # noqa: F401


@pytest.fixture
def reconciled(prepared, release, server, monkeypatch):
    folder, value, _ = prepared
    path, manifest_sha = release
    value["flow_id"] = "session-fixture"
    value["active"] = {
        "version": "1",
        "slot": "blue",
        "paths": {"blue": str(path)},
        "plan": {
            "releases": {
                "blue": {
                    "manifest_sha256": manifest_sha,
                    "manifest": value["release_manifest"],
                }
            }
        },
    }
    (folder / "input.json").write_bytes(data.encoded(value))
    (folder / "session.json").write_bytes(
        data.encoded(
            {
                "schema": "fraud_product_session_v1",
                "session_id": "fixture",
                "input_sha256": data.sha(folder / "input.json"),
            }
        )
    )
    monkeypatch.setattr(flow, "work", lambda _: folder)
    monkeypatch.setattr(flow, "read", lambda _: value)

    @contextmanager
    def target(_):
        client = scorer(server[0], value)
        client.ready()
        yield client
        client.ready()

    monkeypatch.setattr(flow, "target", target)
    client = scorer(server[0], value)
    flow.run_events(folder, value, client)
    verification = flow.verify_ledger(folder, value)
    receipt = folder / "verification-timing-fixture.json"
    receipt.write_bytes(data.encoded({**verification, "verification_elapsed_seconds": 0.1}))
    monkeypatch.setattr(export.f, "managed_destination", lambda p, area: data.checked_path(p))
    return folder, value, receipt, server


def test_full_export_keeps_session_bytes_and_issues_no_new_scoring(reconciled, tmp_path):
    folder, value, receipt, server = reconciled
    before = session_acceptance.source_files(folder)
    calls = server[1]["scores"]
    result = export.prepare(tmp_path / "portal", "vm", value["flow_id"], None, None, True)
    snapshot, _ = data.load_bundle(Path(result["directory"]), result["manifest_sha256"])
    assert result["summary"]["decisions"] == 2
    assert result["summary"]["explanations"] == 0
    assert snapshot["source_acceptance"] == "reconciled_session_not_product_pause_resume_rehearsal"
    assert receipt.name in snapshot["source_files"]
    assert not (folder / "rehearsal.json").exists()
    assert before == session_acceptance.source_files(folder)
    assert calls == server[1]["scores"]
    assert export.prepare(tmp_path / "portal", "vm", value["flow_id"], None, None, True) == result


@pytest.mark.parametrize(
    "change",
    ["checkpoint", "release", "counts", "status", "session", "input", "missing"],
)
def test_changed_or_missing_acceptance_rejected_before_export(reconciled, tmp_path, change):
    folder, value, receipt, _ = reconciled
    if change == "missing":
        receipt.unlink()
    elif change in {"session", "input"}:
        config = json.loads((folder / "session.json").read_bytes())
        config["session_id" if change == "session" else "input_sha256"] = "foreign"
        (folder / "session.json").write_bytes(data.encoded(config))
    else:
        report = json.loads(receipt.read_bytes())
        if change == "checkpoint":
            report["checkpoint_sha256"] = "0" * 64
        elif change == "release":
            report["serving_release_id"] = "foreign"
        elif change == "counts":
            report["counts"]["decisions"] += 1
        else:
            report["status"] = "paused"
        receipt.write_bytes(data.encoded(report))
    with pytest.raises(ValueError):
        export.prepare(tmp_path / "portal", "vm", value["flow_id"], None, None, True)
    assert not (tmp_path / "portal/data/reports/fraud-portal/vm").exists()


def test_matching_receipt_is_insufficient_if_ledger_was_tampered(reconciled, tmp_path):
    folder, value, receipt, _ = reconciled
    with sqlite3.connect(folder / "checkpoint.sqlite") as connection:
        connection.execute("UPDATE feedback SET fraud_label=1-fraud_label WHERE transaction_id=0")
    report = json.loads(receipt.read_bytes())
    report["checkpoint_sha256"] = data.sha(folder / "checkpoint.sqlite")
    receipt.write_bytes(data.encoded(report))
    with pytest.raises(ValueError, match="reconcile"):
        export.prepare(tmp_path / "portal", "vm", value["flow_id"], None, None, True)


def test_invalid_legacy_receipt_is_not_bypassed(reconciled, tmp_path):
    folder, value, _, _ = reconciled
    (folder / "rehearsal.json").write_text("{}")
    with pytest.raises((ValueError, KeyError)):
        export.prepare(tmp_path / "portal", "vm", value["flow_id"], None, None, True)
