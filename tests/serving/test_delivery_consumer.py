"""Archive pin before Docker, imported-image identity and scoped cleanup contracts."""

import json
from pathlib import Path
import stat

import pytest
from scripts.serving import delivery_consumer as consumer
from scripts.serving import delivery_gate as delivery
from tests.serving.test_delivery_gate import controlled_delivery  # noqa: F401
import yaml

SHA = "a" * 40
IMAGE = "sha256:" + "b" * 64


@pytest.fixture
def packaged(controlled_delivery, monkeypatch):  # noqa: F811 - shared producer fixture
    root, _, _, _ = controlled_delivery
    result = delivery.qualify(SHA, root=root)
    directory = Path(result["directory"])
    descriptor = json.loads((directory / "delivery.json").read_text())
    tag = descriptor["exported_local_tag"]
    real_assess = consumer.checks.assess

    class Docker:
        def __init__(self):
            self.tags = {"preexisting-user-image:v1": IMAGE}
            self.calls = []
            self.identity = IMAGE
            self.revision = SHA
            self.platform = "amd64"
            self.fail_rm = False

        def run(self, *args):
            self.calls.append(args)
            if args[:2] == ("image", "ls"):
                if "--filter" in args:
                    ref = args[-1].removeprefix("reference=")
                    return self.tags.get(ref, "")
                return "\n".join(self.tags.values())
            if args[:2] == ("image", "inspect"):
                assert args[-1] == tag
                return json.dumps(
                    [
                        {
                            "Id": self.identity,
                            "Os": "linux",
                            "Architecture": self.platform,
                            "Config": {
                                "Labels": {"org.opencontainers.image.revision": self.revision}
                            },
                        }
                    ]
                )
            if args[:2] == ("image", "rm"):
                assert args[-1] == tag and "--force" not in args
                if self.fail_rm:
                    raise consumer.checks.CheckFailed("owned tag cleanup failed")
                del self.tags[tag]
                return ""
            pytest.fail("Unexpected Docker operation: " + repr(args))

    docker = Docker()
    monkeypatch.setattr(consumer.checks, "Docker", lambda: docker)
    loaded = []

    def load(source, destination, **kwargs):
        assert source == directory
        loaded.append(source)
        docker.tags[tag] = IMAGE
        (destination / "load.log").write_text("loaded controlled image\n")

    def assess(image, release, digest, **kwargs):
        assert image == IMAGE and release != directory / "release"
        assert stat.S_IMODE(release.stat().st_mode) == 0o755
        assert all(
            stat.S_IMODE((release / n).stat().st_mode) == 0o644
            for n in consumer.checks.RELEASE_FILES
        )
        return real_assess(image, release, digest, **kwargs)

    monkeypatch.setattr(consumer, "load_archive", load)
    monkeypatch.setattr(consumer.checks, "assess", assess)
    return root, result, directory, descriptor, docker, loaded


def run(packaged):
    root, result, directory, _, _, _ = packaged
    return consumer.consume(directory, SHA, result["descriptor_sha256"], root=root)


def receipt(packaged):
    root, *_ = packaged
    return json.loads(next((root / "data/serving/consumptions").glob("*/result.json")).read_text())


def test_consumes_archive_on_new_target_preserves_origin_and_foreign_image(packaged, monkeypatch):
    _, _, directory, _descriptor, docker, loaded = packaged
    before = delivery.inventory(directory)
    for name in consumer.checks.RELEASE_FILES:
        (directory / "release" / name).chmod(0o600)
    source_modes = {
        name: (directory / "release" / name).stat().st_mode
        for name in consumer.checks.RELEASE_FILES
    }
    monkeypatch.setattr(delivery, "build_image", lambda *a, **kw: pytest.fail("rebuild"))
    monkeypatch.setattr("examples.serving_smoke.synthetic_release", lambda *a: pytest.fail("fit"))
    result = run(packaged)
    assert result["status"] == "success" and result["archive_reload_validated"]
    assert result["image_present_before_load"] is True  # Cache is recorded, not denied.
    assert result["http_target_completed_and_removed"] and result["owned_import_tag_removed"]
    assert result["original_delivery_bytes_unchanged"] and loaded == [directory]
    assert docker.tags == {"preexisting-user-image:v1": IMAGE}
    assert delivery.inventory(directory) == before
    assert {
        name: (directory / "release" / name).stat().st_mode
        for name in consumer.checks.RELEASE_FILES
    } == source_modes
    assert not (Path(result["report"]).parent / "release").exists()
    assert (
        json.loads((directory / "delivery.json").read_text())["archive_reload_validated"] is False
    )


@pytest.mark.parametrize("error", ["descriptor", "source", "archive"])
def test_pins_or_modified_archive_rejected_before_any_docker(packaged, monkeypatch, error):
    root, result, directory, _, docker, loaded = packaged
    if error == "archive":
        (directory / "image.tar").write_bytes(b"corrupted")
    monkeypatch.setattr(
        delivery, "engine_ready", lambda *a: pytest.fail("engine before pin verification")
    )
    with pytest.raises(consumer.checks.CheckFailed):
        consumer.consume(
            directory,
            "f" * 40 if error == "source" else SHA,
            "0" * 64 if error == "descriptor" else result["descriptor_sha256"],
            root=root,
        )
    assert not docker.calls and not loaded
    assert not (root / "data/serving/consumptions").exists()


def test_existing_import_tag_is_preserved_and_not_loaded(packaged):
    _, _, _, descriptor, docker, loaded = packaged
    docker.tags[descriptor["exported_local_tag"]] = "existing-image"
    with pytest.raises(consumer.checks.CheckFailed, match="already exists"):
        run(packaged)
    assert not loaded and docker.tags[descriptor["exported_local_tag"]] == "existing-image"
    assert receipt(packaged)["failed_phase"] == "tag_preflight"
    assert not any(c[:2] == ("image", "rm") for c in docker.calls)


@pytest.mark.parametrize("mismatch", ["id", "revision", "platform"])
def test_loaded_identity_mismatch_blocks_http_and_cleans_only_imported_tag(
    packaged, monkeypatch, mismatch
):
    *_, docker, _ = packaged
    if mismatch == "id":
        docker.identity = "sha256:" + "f" * 64
    elif mismatch == "revision":
        docker.revision = "f" * 40
    else:
        docker.platform = "arm64"
    monkeypatch.setattr(
        consumer.checks, "assess", lambda *a, **kw: pytest.fail("HTTP after mismatch")
    )
    with pytest.raises(consumer.checks.CheckFailed):
        run(packaged)
    result = receipt(packaged)
    assert result["status"] == "failed" and not result["archive_reload_validated"]
    assert result["owned_import_tag_removed"]
    assert docker.tags == {"preexisting-user-image:v1": IMAGE}


@pytest.mark.parametrize("error", ["load", "http", "cleanup", "origin_changed"])
def test_failure_never_records_success_and_preserves_foreign_resources(
    packaged, monkeypatch, error
):
    _, _, directory, descriptor, docker, _ = packaged
    if error == "load":

        def partial_load(*args, **kwargs):
            docker.tags[descriptor["exported_local_tag"]] = IMAGE
            raise consumer.checks.CheckFailed("partial load")

        monkeypatch.setattr(consumer, "load_archive", partial_load)
    elif error == "http":
        monkeypatch.setattr(
            consumer.checks,
            "assess",
            lambda *a, **kw: (_ for _ in ()).throw(consumer.checks.CheckFailed("HTTP")),
        )
    elif error == "cleanup":
        docker.fail_rm = True
    else:
        original = consumer.checks.assess

        def assess(*args, **kwargs):
            result = original(*args, **kwargs)
            (directory / "image.tar").write_bytes(b"changed during target test")
            return result

        monkeypatch.setattr(consumer.checks, "assess", assess)
    with pytest.raises(consumer.checks.CheckFailed):
        run(packaged)
    result = receipt(packaged)
    assert result["status"] == "failed" and not result["archive_reload_validated"]
    assert docker.tags["preexisting-user-image:v1"] == IMAGE
    if error == "cleanup":
        assert descriptor["exported_local_tag"] in docker.tags
    else:
        assert descriptor["exported_local_tag"] not in docker.tags


def test_archive_load_command_is_platform_pinned_without_build(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(delivery, "command", lambda args, **kwargs: calls.append((args, kwargs)))
    consumer.load_archive(tmp_path, tmp_path, root=tmp_path)
    assert calls[0][0] == [
        "docker",
        "image",
        "load",
        "--platform",
        "linux/amd64",
        "--input",
        str(tmp_path / "image.tar"),
    ]
    assert calls[0][1]["log"] == tmp_path / "load.log"


def test_ci_consumer_uses_producer_outputs_in_a_separate_job_without_build():
    workflow = yaml.load(
        (consumer.ROOT / ".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader
    )
    producer = workflow["jobs"]["serving-delivery"]
    job = workflow["jobs"]["serving-consumer"]
    assert workflow["permissions"] == {"contents": "read"}
    assert job["needs"] == "serving-delivery" and job["runs-on"] == "ubuntu-24.04"
    assert producer["outputs"]["artifact_id"] == "${{ steps.package.outputs.artifact-id }}"
    assert (
        producer["outputs"]["descriptor_sha256"]
        == "${{ steps.delivery.outputs.descriptor_sha256 }}"
    )
    download = next(s for s in job["steps"] if s["name"] == "Download only this producer artifact")
    assert download["with"]["artifact-ids"] == "${{ needs.serving-delivery.outputs.artifact_id }}"
    assert (
        job["env"]["EXPECTED_DESCRIPTOR_SHA256"]
        == "${{ needs.serving-delivery.outputs.descriptor_sha256 }}"
    )
    script = "\n".join(s.get("run", "") for s in job["steps"])
    assert "delivery_consumer" in script and '"$GITHUB_SHA"' in script
    assert "docker build" not in script and "delivery_gate run" not in script
    assert "secrets." not in json.dumps(job)
