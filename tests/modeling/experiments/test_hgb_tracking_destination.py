"""Destination identity and unchanged study scheduling, using offline fixtures."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from fraud_detection_mlops.modeling.experiments import hgb_optimization as hgb


@pytest.mark.parametrize("root,uri", [(None, None), (Path("local"), "http://127.0.0.1:15001")])
def test_ambiguous_destination_rejected_before_input_or_writes(tmp_path, root, uri):
    with pytest.raises(hgb.HGBOptimizationError, match="exactly one"):
        hgb.optimize_hgb(tmp_path / "absent", tracking_root=root, http_uri=uri)
    assert not list(tmp_path.iterdir())


def test_invalid_uri_rejected_before_inputs_or_study(tmp_path):
    with pytest.raises(ValueError, match="credentials"):
        hgb.optimize_hgb(
            tmp_path / "absent", tracking_root=None, http_uri="http://user:secret@host"
        )
    assert not list(tmp_path.iterdir())


def test_local_identity_shape_is_unchanged(tmp_path):
    assert hgb.hgb_tracking_target(tmp_path, None) == {"tracking_root": str(tmp_path.resolve())}


def test_fit_cannot_reuse_another_destination_receipt(tmp_path):
    with pytest.raises(hgb.HGBOptimizationError, match="destination differs"):
        hgb.fit_hgb_fold(
            tmp_path,
            "reference",
            {"id": "fold_1"},
            {},
            tmp_path,
            {},
            {"tracking_root": str(tmp_path)},
            None,
            http_uri="http://127.0.0.1:15001",
        )
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("mode", ["default", "local", "http", "conflict"])
def test_cli_routes_explicitly_without_changing_default(monkeypatch, tmp_path, mode):
    calls = []
    monkeypatch.setattr(hgb, "managed_destination", lambda path, area: path)
    monkeypatch.setattr(
        hgb, "optimize_hgb", lambda *a, **k: calls.append(k) or {"status": "success"}
    )
    args = ["optimize", str(tmp_path / "data")]
    if mode in {"local", "conflict"}:
        args += ["--tracking-root", str(tmp_path / "tracking")]
    if mode in {"http", "conflict"}:
        args += ["--tracking-uri", "http://127.0.0.1:15001/"]
    result = CliRunner().invoke(hgb.app, args)
    assert result.exit_code == (2 if mode == "conflict" else 0), result.output
    if mode == "conflict":
        assert not calls
    else:
        assert len(calls) == 1
        assert calls[0]["tracking_root"] == (
            None
            if mode == "http"
            else tmp_path / "tracking"
            if mode == "local"
            else hgb.tracking.DEFAULT_TRACKING
        )
        assert calls[0].get("http_uri") == ("http://127.0.0.1:15001" if mode == "http" else None)
        assert json.loads(result.stdout)["status"] == "success"
