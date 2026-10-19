"""Committed source binding, failed publication and portable handoff verification.

Docker orchestration is controlled here. The existing serving tests cover real
HTTP/model loading; native image/build/network proof is a separate opt-in gate.
"""

from contextlib import contextmanager
import json
from pathlib import Path
import shutil
import subprocess

import pytest
from scripts.serving import blue_green_rehearsal as rollout
from scripts.serving import delivery_gate as gate
import yaml

from fraud_detection_mlops.storage import managed_paths

SHA = "a" * 40
IMAGE = "sha256:" + "b" * 64


def git(root, *args):
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


@pytest.fixture
def committed_source(tmp_path):
    root = tmp_path / "checkout"
    root.mkdir()
    git(root, "init", "-q")
    (root / "README.md").write_text("Source\n")
    (root / "scripts/serving").mkdir(parents=True)
    (root / "scripts/serving/delivery_gate.py").write_text("# committed\n")
    git(root, "add", ".")
    git(
        root,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.test",
        "commit",
        "-qm",
        "Source",
    )
    return root, git(root, "rev-parse", "HEAD")


def test_source_binding_uses_committed_inputs_and_preserves_personal_index(committed_source):
    root, commit = committed_source
    before = gate.source_identity(root, commit)
    (root / ".vscode").mkdir()
    (root / ".vscode/settings.json").write_text('{"editor.fontSize": 17}\n')
    git(root, "add", ".vscode/settings.json")
    index = (root / ".git/index").read_bytes()
    assert gate.source_identity(root, commit) == before
    assert (root / ".git/index").read_bytes() == index
    with pytest.raises(gate.checks.CheckFailed, match="differs"):
        gate.source_identity(root, "f" * 40)


@pytest.mark.parametrize("change", ["modified", "staged", "new", "link"])
def test_dirty_delivery_inputs_are_rejected(committed_source, change):
    root, commit = committed_source
    path = root / "scripts/serving/delivery_gate.py"
    if change == "new":
        path.with_name("uncommitted.py").write_text("# extra\n")
    elif change == "link":
        path.unlink()
        path.symlink_to(root / "README.md")
    else:
        path.write_text("# changed\n")
        if change == "staged":
            git(root, "add", str(path))
            path.write_text("# committed\n")
    with pytest.raises(gate.checks.CheckFailed, match="Commit"):
        gate.source_identity(root, commit)


@pytest.fixture
def controlled_delivery(tmp_path, monkeypatch, serving_release):
    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.setattr(managed_paths, "DATA_DIR", root / "data")
    (root / "infra/serving").mkdir(parents=True)
    (root / "infra/serving/images.lock.json").write_text(json.dumps({"build_python": "pinned"}))
    source = {"commit": SHA, "scope": list(gate.SOURCE_PATHS), "files": {"poetry.lock": "c" * 64}}
    monkeypatch.setattr(gate, "source_identity", lambda *a: source)
    monkeypatch.setattr(gate, "engine_ready", lambda *a: None)
    built = []

    def build(root, directory, tag, record):
        built.append(tag)
        (directory / "build.log").write_text("build completed\n")
        return {"id": IMAGE, "local_tag": tag}

    monkeypatch.setattr(gate, "build_image", build)
    original, pin = serving_release

    def prepare(directory):
        shutil.copytree(original, directory)
        return {"manifest_sha256": pin}

    monkeypatch.setattr("examples.serving_smoke.synthetic_release", prepare)
    manifest = json.loads((original / "manifest.json").read_text())
    smoke = json.loads((original / "smoke.json").read_text())
    runtime = {
        "status": "success",
        "evidence_kind": "synthetic_smoke",
        "image": {"id": IMAGE},
        "serving_release_id": manifest["serving_release_id"],
        "manifest_sha256": pin,
        "http_score": {"model_sha256": manifest["model_sha256"], "score": smoke["expected_score"]},
        "invalid_input_http_status": 422,
    }
    monkeypatch.setattr(gate.checks, "assess", lambda *a, **kw: runtime)

    def rehearse(directory, release, pin, image):
        result = {
            "status": "success",
            "events": list(rollout.EXPECTED_EVENTS),
            "rollback_verified": True,
            "temporary_resources_removed": True,
            "model_sha256": manifest["model_sha256"],
        }
        result.update(
            baseline_manifest_sha256=pin,
            baseline_release_id=manifest["serving_release_id"],
            candidate_release_id="different",
            observed_failure_http_status=502,
            candidate_score=smoke["expected_score"],
            rollback_score=smoke["expected_score"],
        )
        gate.checks.save_report(directory / "rollout.json", result)
        gate.checks.save_report(directory / "candidate-manifest.json", manifest)
        return result

    monkeypatch.setattr(gate, "rehearse_image", rehearse)
    calls = []

    def command(args, **kwargs):
        calls.append(args)
        if args[:3] == ["docker", "image", "save"]:
            Path(args[4]).write_bytes(b"controlled archive; not a native Docker image")
        if args[:3] == ["docker", "image", "inspect"]:
            return json.dumps([{"Id": IMAGE}])
        return ""

    monkeypatch.setattr(gate, "command", command)
    return root, built, calls, source


def test_one_build_gates_export_same_image_and_verify_without_services(
    controlled_delivery, monkeypatch
):
    root, built, calls, _ = controlled_delivery
    result = gate.qualify(SHA, root=root)
    directory = Path(result["directory"])
    assert len(built) == 1
    assert [c[2] for c in calls if c[:2] == ["docker", "image"]] == ["inspect", "save", "rm"]
    assert calls[0][-1] == calls[-1][-1] == built[0]
    monkeypatch.setattr(
        gate, "command", lambda *a, **kw: pytest.fail("offline verify contacted a service")
    )
    verified = gate.verify(directory, SHA, result["descriptor_sha256"])
    assert verified["image_loaded"] is False and verified["production_promotion"] is False
    record = json.loads((directory / "delivery.json").read_text())
    assert record["image"]["id"] == IMAGE and record["registry_digest"] is None
    assert record["exported_local_tag"] == built[0]
    assert record["historical_reference_accessed"] is False
    assert record["synthetic_fixture_fitted"] is True
    assert not record["archive_reload_validated"]


@pytest.mark.parametrize(
    "failure", ["http", "rollout", "archive", "source_mutation", "image_changed", "cleanup"]
)
def test_failed_gate_cannot_leave_success_descriptor(controlled_delivery, monkeypatch, failure):
    root, _, calls, source = controlled_delivery
    real_command = gate.command
    if failure == "http":
        monkeypatch.setattr(
            gate.checks,
            "assess",
            lambda *a, **kw: (_ for _ in ()).throw(gate.checks.CheckFailed("HTTP gate")),
        )
    elif failure == "rollout":
        monkeypatch.setattr(
            gate,
            "rehearse_image",
            lambda *a: (_ for _ in ()).throw(gate.checks.CheckFailed("rollout gate")),
        )
    elif failure == "source_mutation":
        sequence = iter([source, {**source, "commit": "f" * 40}])
        monkeypatch.setattr(gate, "source_identity", lambda *a: next(sequence))
    else:

        def command(args, **kwargs):
            if failure == "archive" and args[:3] == ["docker", "image", "save"]:
                raise gate.checks.CheckFailed("export failed")
            if failure == "cleanup" and args[:3] == ["docker", "image", "rm"]:
                raise gate.checks.CheckFailed("cleanup failed")
            if failure == "image_changed" and args[:3] == ["docker", "image", "inspect"]:
                return json.dumps([{"Id": "sha256:" + "d" * 64}])
            return real_command(args, **kwargs)

        monkeypatch.setattr(gate, "command", command)
    with pytest.raises(gate.checks.CheckFailed):
        gate.qualify(SHA, root=root)
    directory = next((root / "data/serving/deliveries").iterdir())
    assert not (directory / "delivery.json").exists()
    failed = json.loads((directory / "failure.json").read_text())
    assert failed["status"] == "failed"
    assert failed["owned_image_tag"].startswith("fraud-serving-delivery:")
    if failure != "cleanup":
        assert any(c[:3] == ["docker", "image", "rm"] for c in calls)


@pytest.mark.parametrize(
    "tamper", ["archive", "missing", "extra", "symlink", "descriptor", "wrong_source"]
)
def test_offline_verification_rejects_tampering(controlled_delivery, tamper):
    root, _, _, _ = controlled_delivery
    result = gate.qualify(SHA, root=root)
    directory = Path(result["directory"])
    if tamper == "archive":
        (directory / "image.tar").write_bytes(b"different archive")
    elif tamper == "missing":
        (directory / "runtime.json").unlink()
    elif tamper == "extra":
        (directory / "unexpected.txt").write_text("extra")
    elif tamper == "symlink":
        (directory / "alias").symlink_to(directory / "image.tar")
    elif tamper == "descriptor":
        with (directory / "delivery.json").open("a") as stream:
            stream.write(" ")
    expected = "f" * 40 if tamper == "wrong_source" else SHA
    with pytest.raises(gate.checks.CheckFailed):
        gate.verify(directory, expected, result["descriptor_sha256"])


def test_rehearsal_uses_the_same_owned_private_network(tmp_path, serving_release, monkeypatch):
    release, pin = serving_release
    observed = []
    monkeypatch.setattr(rollout, "local_images", lambda *a: [{"Id": IMAGE}, {"Id": "gateway"}])

    @contextmanager
    def network(docker, name, report):
        observed.append(name)
        yield
        report["temporary_resources_removed"] = True

    def exercise(docker, image, gateway, name, *args):
        assert name == observed[0]
        args[-1].update(events=list(rollout.EXPECTED_EVENTS), rollback_verified=True)

    monkeypatch.setattr(rollout, "private_network", network)
    monkeypatch.setattr(rollout, "exercise", exercise)
    assert gate.rehearse_image(tmp_path, release, pin, IMAGE)["status"] == "success"
    assert not (tmp_path / "stage").exists()


def test_workflow_gates_artifact_after_quality_without_publication_or_secrets():
    workflow = yaml.load(
        (gate.ROOT / ".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader
    )
    assert workflow["permissions"] == {"contents": "read"}
    job = workflow["jobs"]["serving-delivery"]
    assert job["needs"] == "quality"
    steps = job["steps"]
    gate_step = next(s for s in steps if s.get("id") == "delivery")
    assert '--expected-source-sha "$GITHUB_SHA"' in gate_step["run"]
    assert "GITHUB_OUTPUT" in gate_step["run"] and "descriptor_sha256" in gate_step["run"]
    upload = next(
        s for s in steps if s["name"] == "Preserve the exact image and qualification records"
    )
    assert steps.index(upload) > steps.index(gate_step)
    assert upload["with"]["retention-days"] == "3" and "if" not in upload
    assert upload["with"]["if-no-files-found"] == "error"
    assert "secrets." not in json.dumps(workflow)
    assert "docker push" not in json.dumps(workflow)


@pytest.mark.parametrize("changed", [None, "label", "platform"])
def test_build_is_once_with_pinned_bases_and_source_label(tmp_path, monkeypatch, changed):
    (tmp_path / "infra/serving").mkdir(parents=True)
    lock = json.loads((gate.ROOT / "infra/serving/images.lock.json").read_text())
    (tmp_path / "infra/serving/images.lock.json").write_text(json.dumps(lock))
    calls = []

    def command(args, **kwargs):
        calls.append(args)
        if args[:3] == ["docker", "image", "inspect"]:
            return json.dumps(
                [
                    {
                        "Id": IMAGE,
                        "Os": "linux",
                        "Architecture": "arm64" if changed == "platform" else "amd64",
                        "Config": {
                            "Labels": {
                                "org.opencontainers.image.revision": "f" * 40
                                if changed == "label"
                                else SHA
                            }
                        },
                    }
                ]
            )
        return ""

    monkeypatch.setattr(gate, "command", command)
    if changed:
        with pytest.raises(gate.checks.CheckFailed):
            gate.build_image(tmp_path, tmp_path, "owned-tag", {"commit": SHA})
    else:
        assert gate.build_image(tmp_path, tmp_path, "owned-tag", {"commit": SHA})["id"] == IMAGE
    builds = [c for c in calls if c[:2] == ["docker", "build"]]
    assert len(builds) == 1
    assert "PYTHON_IMAGE=" + lock["build_python"] in builds[0]
    assert "org.opencontainers.image.revision=" + SHA in builds[0]
    assert [c[-1] for c in calls if c[:2] == ["docker", "pull"]] == [
        lock["build_python"],
        rollout.GATEWAY_IMAGE,
    ]
