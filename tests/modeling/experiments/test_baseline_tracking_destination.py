"""Explicit baseline destinations must be validated before any experiment writes."""

import pytest
from typer.testing import CliRunner

from fraud_detection_mlops.modeling.experiments import baseline_experiment as baseline
from fraud_detection_mlops.storage import managed_paths


def test_api_rejects_ambiguous_destination_before_loading_data(tmp_path):
    with pytest.raises(ValueError, match="not both"):
        baseline.run_baseline(output_root=tmp_path, http_uri="http://127.0.0.1:15001")
    assert not list(tmp_path.iterdir())


def test_api_rejects_invalid_uri_before_loading_data(tmp_path):
    with pytest.raises(ValueError, match="credentials"):
        baseline.run_baseline(
            output_root=tmp_path, tracking_root=None, http_uri="http://user:secret@host"
        )
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("mode", ["default", "local", "http", "no-track"])
def test_cli_preserves_default_and_routes_explicit_options(tmp_path, monkeypatch, mode):
    data = tmp_path / "data"
    monkeypatch.setattr(managed_paths, "DATA_DIR", data)
    monkeypatch.setattr(baseline, "DEFAULT_HTTP_BASELINE", data / "storage/tracking-lab/baselines")
    calls = []
    monkeypatch.setattr(
        baseline, "run_baseline", lambda *a, **k: calls.append((a, k)) or {"status": "success"}
    )
    args = ["run"]
    if mode == "local":
        args += ["--tracking-root", str(data / "tracking/explicit")]
    elif mode == "http":
        args += ["--tracking-uri", "http://127.0.0.1:15001/"]
    elif mode == "no-track":
        args += ["--no-track"]
    result = CliRunner().invoke(baseline.app, args)
    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    positional, options = calls[0]
    assert positional[1] == (
        baseline.DEFAULT_HTTP_BASELINE if mode == "http" else baseline.DEFAULT_BASELINE
    )
    assert options["tracking_root"] == (
        data / "tracking/explicit"
        if mode == "local"
        else baseline.tracking.DEFAULT_TRACKING
        if mode == "default"
        else None
    )
    assert options.get("http_uri") == ("http://127.0.0.1:15001" if mode == "http" else None)
    assert not data.exists()


@pytest.mark.parametrize("mode", ["conflict", "disabled", "outside", "symlink"])
def test_http_cli_rejects_invalid_destinations_before_fit(tmp_path, monkeypatch, mode):
    data = tmp_path / "data"
    monkeypatch.setattr(managed_paths, "DATA_DIR", data)
    monkeypatch.setattr(baseline, "run_baseline", lambda *a, **k: pytest.fail("Fit reached"))
    output = data / "storage/baseline"
    args = ["run", "--tracking-uri", "http://127.0.0.1:15001"]
    if mode == "conflict":
        args += ["--tracking-root", str(data / "tracking/explicit")]
    elif mode == "disabled":
        args += ["--no-track"]
    elif mode == "outside":
        output = tmp_path / "loose-output"
    else:
        (data / "storage").mkdir(parents=True)
        output.symlink_to(data / "storage/missing")
    args += ["--output-root", str(output)]
    response = CliRunner().invoke(baseline.app, args)
    assert response.exit_code == 2, response.output
    assert not output.exists()
