"""Native-shaped export reuses the accepted SHAP and HTTP readers without changing sources."""
# Imported pytest fixtures intentionally share parameter names.
# ruff: noqa: F811

from pathlib import Path

import pytest
from scripts.case_explanations import controller as explanations
from scripts.fraud_portal import controller as portal
from scripts.fraud_portal import data, export
from tests.case_explanations.conftest import accepted  # noqa: F401
from tests.operational_round.test_make import dispatch  # noqa: F401
from tests.product_flow.conftest import prepared, release, server  # noqa: F401


def test_export_contains_the_full_queue_not_only_explained_cases_and_is_idempotent(
    accepted,
    tmp_path,
    monkeypatch,
):
    folder, _, _ = accepted
    report = explanations.run("fixture", "test", 1, True)
    original = explanations.source_files(folder)
    root = tmp_path / "presentation"
    monkeypatch.setattr(
        export.f, "managed_destination", lambda path, area: data.checked_path(path)
    )
    result = export.prepare(
        root, "investigation-v1", "fixture", "test", report["manifest_sha256"], True
    )
    assert result["summary"]["decisions"] == 2
    assert result["summary"]["explanations"] == 1
    value, manifest = data.load_bundle(Path(result["directory"]), result["manifest_sha256"])
    assert len(value["cases"]) == 2 and manifest["champion_modified"] is False
    first = (Path(result["directory"]) / "manifest.json").read_bytes()
    again = export.prepare(
        root, "investigation-v1", "fixture", "test", report["manifest_sha256"], True
    )
    assert result == again and (Path(result["directory"]) / "manifest.json").read_bytes() == first
    assert explanations.source_files(folder) == original
    monkeypatch.setattr(portal, "ROOT", root)
    assert portal.config("investigation-v1")[1] == value


def test_export_refuses_wrong_source_digest_and_requires_no_writers(accepted, tmp_path):
    with pytest.raises(ValueError, match="NO_OTHER_WRITERS"):
        export.prepare(tmp_path, "test", "fixture", "test", "f" * 64, False)
    with pytest.raises((ValueError, OSError)):
        export.prepare(tmp_path, "test", "fixture", "test", "f" * 64, True)
    assert not (tmp_path / "data/reports/fraud-portal/test").exists()


def test_make_prepare_requires_verified_digest_and_writer_declaration(dispatch):
    result, calls = dispatch("portal-prepare")
    assert result.returncode != 0 and not calls
    result, calls = dispatch("portal-prepare", PRODUCT_NO_OTHER_WRITERS="1")
    assert result.returncode != 0 and not calls
    result, calls = dispatch(
        "portal-prepare", PRODUCT_NO_OTHER_WRITERS="1", PRODUCT_EXPLANATION_SHA256="a" * 64
    )
    assert result.returncode == 0 and calls[-1][3:5] == [
        "scripts.fraud_portal.controller",
        "prepare",
    ]


def test_accepted_queue_can_be_exported_without_algorithm_specific_explanation(
    accepted, tmp_path, monkeypatch
):
    monkeypatch.setattr(
        export.f, "managed_destination", lambda path, area: data.checked_path(path)
    )
    result = export.prepare(tmp_path / "no-explainer", "queue", "fixture", None, None, True)
    value, _ = data.load_bundle(Path(result["directory"]), result["manifest_sha256"])
    assert len(value["cases"]) == 2 and result["summary"]["explanations"] == 0
    assert value["source_explanation_manifest_sha256"] is None
