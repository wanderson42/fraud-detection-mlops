"""Invalid operational destinations must fail before fitting, Docker or filesystem writes."""

import json
from pathlib import Path
import sys

import pytest

from fraud_detection_mlops.storage import managed_paths as paths


@pytest.fixture
def data(tmp_path, monkeypatch):
    root = tmp_path / "checkout/data"
    monkeypatch.setattr(paths, "DATA_DIR", root)
    return root


@pytest.mark.parametrize("area", sorted(paths.AREAS))
def test_valid_absolute_and_relative_paths_are_not_created(data, monkeypatch, area):
    monkeypatch.chdir(data.parent.parent)
    relative = Path("checkout/data") / area / "new/output"
    assert paths.managed_destination(relative, area) == data / area / "new/output"
    assert (
        paths.managed_destination(data / area / "new/output", area) == data / area / "new/output"
    )
    assert not data.exists()


@pytest.mark.parametrize("kind", ["outside", "sibling", "traversal", "area_root", "wrong_area"])
def test_unmanaged_paths_fail_without_creating_anything(data, kind):
    destination = {
        "outside": data.parent.parent / "outside/file",
        "sibling": data / "serving-extra/file",
        "traversal": data / "serving/../serving/file",
        "area_root": data / "serving",
        "wrong_area": data / "tracking/file",
    }[kind]
    with pytest.raises(ValueError):
        paths.managed_destination(destination, "serving")
    assert not data.exists()


@pytest.mark.parametrize("kind", ["parent", "leaf", "dangling", "data_root"])
def test_symlink_aliases_are_rejected_even_if_the_target_is_inside(data, kind):
    base = data / "serving"
    base.mkdir(parents=True)
    if kind == "data_root":
        alias = data.parent / "alias"
        alias.symlink_to(data, target_is_directory=True)
        paths.DATA_DIR = alias
        destination = alias / "serving/new"
    elif kind == "parent":
        (base / "link").symlink_to(base, target_is_directory=True)
        destination = base / "link/new"
    else:
        (base / "link").symlink_to(base / "missing")
        destination = base / "link" if kind == "leaf" else base / "link/new"
    with pytest.raises(ValueError, match="Symbolic links"):
        paths.managed_destination(destination, "serving")
    assert not (base / "missing").exists()


def test_file_ancestor_and_unknown_area_are_rejected(data):
    data.mkdir(parents=True)
    (data / "serving").write_text("existing")
    with pytest.raises(ValueError, match="component"):
        paths.managed_destination(data / "serving/new", "serving")
    with pytest.raises(ValueError, match="Unknown"):
        paths.managed_destination(data / "serving/new", "../external")
    assert (data / "serving").read_text() == "existing"


@pytest.mark.parametrize("bad_argument", ["output", "tracking"])
def test_contributor_rejects_before_model_fit_or_tracking(data, monkeypatch, bad_argument):
    from examples import model_contribution as example

    output = data / "examples/new" if bad_argument == "tracking" else data.parent / "loose"
    arguments = ["example", "--output", str(output)]
    if bad_argument == "tracking":
        arguments += ["--tracking-root", str(data.parent / "unmanaged-tracking")]
    monkeypatch.setattr(sys, "argv", arguments)
    monkeypatch.setattr(example, "run", lambda *a: pytest.fail("Training reached"))
    with pytest.raises(SystemExit) as exc:
        example.main()
    assert exc.value.code == 2
    assert not data.exists()


def test_contributor_accepts_managed_destinations(data, monkeypatch, capsys):
    from examples import model_contribution as example

    output, tracking = data / "examples/new", data / "tracking/example"
    monkeypatch.setattr(
        sys, "argv", ["example", "--output", str(output), "--tracking-root", str(tracking)]
    )
    calls = []
    monkeypatch.setattr(example, "run", lambda *args: calls.append(args) or {"status": "stub"})
    example.main()
    assert calls == [(output, tracking)]
    assert json.loads(capsys.readouterr().out)["status"] == "stub"
    assert not data.exists()


@pytest.mark.parametrize("valid", [False, True])
def test_synthetic_cli_checks_before_fitting(data, monkeypatch, valid):
    from examples import serving_smoke as example

    output = data / "serving/synthetic" if valid else data.parent / "loose"
    monkeypatch.setattr(sys, "argv", ["example", "--output", str(output)])
    calls = []
    monkeypatch.setattr(example, "synthetic_release", lambda p: calls.append(p) or {})
    if valid:
        example.main()
        assert calls == [output]
    else:
        with pytest.raises(SystemExit) as exc:
            example.main()
        assert exc.value.code == 2 and calls == []
    assert not data.exists()


@pytest.mark.parametrize("valid", [False, True])
def test_stack_init_validates_before_credentials_or_state_creation(data, monkeypatch, valid):
    from scripts.monitoring import local_stack as stack

    output = data / "replay/new" if valid else data.parent / "loose"
    monkeypatch.setattr(sys, "argv", ["stack", "init", "--state", str(output)])
    calls = []
    monkeypatch.setattr(
        stack, "initialize", lambda p: calls.append(p) or {"environment_file": "stub"}
    )
    if valid:
        stack.main()
        assert calls == [output]
    else:
        with pytest.raises(SystemExit) as exc:
            stack.main()
        assert exc.value.code == 2 and calls == []
    assert not data.exists()


@pytest.mark.parametrize("valid", [False, True])
def test_docker_cli_checks_receipt_before_docker(data, monkeypatch, valid):
    from scripts.serving import check_docker_runtime as checks

    release = data / "serving/reference"
    release.mkdir(parents=True)
    output = data / "serving/check.json" if valid else data.parent / "loose.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "check",
            "--image",
            "test",
            "--release",
            str(release),
            "--manifest-sha256",
            "f" * 64,
            "--output",
            str(output),
        ],
    )
    calls = []
    monkeypatch.setattr(checks, "assess", lambda *a: calls.append(a) or {"status": "stub"})
    assert checks.main() == (0 if valid else 1)
    assert bool(calls) is valid and output.exists() is valid
    assert not valid or json.loads(output.read_text())["status"] == "stub"
