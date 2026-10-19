"""Build once, qualify a synthetic release and export that exact local image.

No registry publication, historical model access or permanent deployment. All
retained outputs live in data/serving/deliveries; verification never loads an image.
"""

import argparse
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import time
from uuid import uuid4

from scripts.serving import blue_green_rehearsal as rollout
from scripts.serving import check_docker_runtime as checks

from fraud_detection_mlops.storage.managed_paths import managed_destination

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "fraud_serving_delivery_v1"
SCOPE = (
    "committed_runtime_image_synthetic_HTTP_and_blue_green_qualification_not_reference_promotion"
)
SOURCE_PATHS = (
    ".github/workflows/ci.yml",
    ".dockerignore",
    "docker/serving/Dockerfile",
    "README.md",
    "pyproject.toml",
    "poetry.lock",
    "fraud_detection_mlops",
    "examples/serving_smoke.py",
    "scripts/serving",
    "infra/serving",
)
MAX_IMAGE_BYTES = 1024**3


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def command(args, *, root, timeout=30, log=None):
    if log is None:
        result = subprocess.run(
            args, cwd=root, capture_output=True, text=True, timeout=timeout, check=False
        )
        checks.require(result.returncode == 0, f"Command failed: {args[0]} {args[1]}")
        return result.stdout.strip()
    with log.open("x") as stream:
        result = subprocess.run(
            args, cwd=root, stdout=stream, stderr=stream, timeout=timeout, check=False
        )
    checks.require(result.returncode == 0, f"Command failed; see {log.name}")
    return ""


def source_identity(root, expected):
    checks.require(
        re.fullmatch(r"[0-9a-f]{40}", expected) is not None, "Use the full source commit SHA"
    )
    head = command(["git", "rev-parse", "HEAD"], root=root)
    checks.require(head == expected, "Source commit differs from expected SHA")
    status = command(
        [
            "git",
            "--no-optional-locks",
            "status",
            "--porcelain",
            "--untracked-files=all",
            "--",
            *SOURCE_PATHS,
        ],
        root=root,
    )
    checks.require(
        not status,
        "Commit the delivery/build inputs first; personal VS Code settings are outside this check",
    )
    names = command(["git", "ls-files", "--", *SOURCE_PATHS], root=root).splitlines()
    checks.require(bool(names), "No committed delivery inputs")
    files = {}
    for name in names:
        path = root / name
        checks.require(path.is_file() and not path.is_symlink(), "Invalid source input: " + name)
        files[name] = digest(path)
    return {"commit": head, "scope": list(SOURCE_PATHS), "files": files}


def build_image(root, directory, tag, source):
    lock = json.loads((root / "infra/serving/images.lock.json").read_text())
    python = lock["build_python"]
    checks.require(
        re.fullmatch(r"python:3\.14\.4-slim-trixie@sha256:[0-9a-f]{64}", python) is not None,
        "Python base must be pinned",
    )
    checks.require(lock["gateway"] == rollout.GATEWAY_IMAGE, "Gateway pins differ")
    for name, image in [("python", python), ("gateway", lock["gateway"])]:
        command(
            ["docker", "pull", "--platform", "linux/amd64", image],
            root=root,
            timeout=300,
            log=directory / f"pull-{name}.log",
        )
    command(
        [
            "docker",
            "build",
            "--platform",
            "linux/amd64",
            "--pull=false",
            "--file",
            "docker/serving/Dockerfile",
            "--build-arg",
            "PYTHON_IMAGE=" + python,
            "--label",
            "org.opencontainers.image.revision=" + source["commit"],
            "--tag",
            tag,
            ".",
        ],
        root=root,
        timeout=900,
        log=directory / "build.log",
    )
    info = json.loads(command(["docker", "image", "inspect", tag], root=root))[0]
    checks.require(
        info["Os"] == "linux" and info["Architecture"] == "amd64", "Image platform differs"
    )
    checks.require(
        re.fullmatch(r"sha256:[0-9a-f]{64}", info["Id"]) is not None, "Invalid image ID"
    )
    checks.require(
        info["Config"]["Labels"]["org.opencontainers.image.revision"] == source["commit"],
        "Image source label differs",
    )
    return {
        "id": info["Id"],
        "local_tag": tag,
        "base": python,
        "registry_digest": None,
        "registry_published": False,
    }


def engine_ready(root):
    context = json.loads(command(["docker", "context", "inspect"], root=root))[0]
    endpoint = os.environ.get("DOCKER_HOST") or context["Endpoints"]["docker"]["Host"]
    checks.require(endpoint.startswith("unix://"), "Use a local Unix Docker engine")
    info = json.loads(command(["docker", "info", "--format", "{{json .}}"], root=root))
    checks.require(
        info["OSType"] == "linux" and int(info["ServerVersion"].split(".")[0]) >= 28,
        "Docker Linux 28+ required",
    )


def rehearse_image(directory, release, pin, image):
    docker = checks.Docker()
    images = rollout.local_images(docker, image)
    stage = directory / "stage"
    stage.mkdir(mode=0o755)
    report = {
        "events": [],
        "status": "failed",
        "temporary_resources_removed": False,
        "evidence_kind": "synthetic_smoke",
    }
    try:
        network = "fraud-delivery-" + uuid4().hex
        with rollout.private_network(docker, network, report):
            # Same controller/gates as the approved native rehearsal, without approved_inputs.
            # This source is generated below this job; no historical model is read.
            rollout.exercise(docker, image, images[1]["Id"], network, stage, release, pin, report)
        checks.require(
            report["events"] == list(rollout.EXPECTED_EVENTS) and report["rollback_verified"],
            "Rollout sequence incomplete",
        )
        report["status"] = "success"
        return report
    finally:
        if report["temporary_resources_removed"]:
            shutil.rmtree(stage)
        checks.save_report(directory / "rollout.json", report)


def inventory(directory):
    files = {}
    for path in sorted(directory.rglob("*")):
        checks.require(not path.is_symlink(), "Links are not accepted in a delivery")
        if path.is_file() and path.relative_to(directory).as_posix() not in {
            "delivery.json",
            "failure.json",
        }:
            files[path.relative_to(directory).as_posix()] = {
                "sha256": digest(path),
                "bytes": path.stat().st_size,
            }
    return files


def prepare_fixture_mount(release, manifest_sha256):
    """Normalize only the newly generated fixture; never a historical release."""
    manifest, _, original = checks.read_release(release, manifest_sha256)
    checks.require(
        manifest["evidence_kind"] == "synthetic_smoke",
        "Only generated fixtures may have permissions normalized",
    )
    release.chmod(0o755)
    for name in checks.RELEASE_FILES:
        (release / name).chmod(0o644)
    checks.require(
        checks.hashes(release) == original, "Fixture bytes changed during mount preparation"
    )


def qualify(expected_source_sha, *, root=ROOT):
    source = source_identity(root, expected_source_sha)
    directory = managed_destination(root / "data/serving/deliveries" / uuid4().hex, "serving")
    directory.mkdir(parents=True, mode=0o700, exist_ok=False)
    tag = "fraud-serving-delivery:" + uuid4().hex
    started = time.monotonic()
    image = None
    phase = "engine"
    try:
        engine_ready(root)
        checks.save_report(directory / "source.json", source)
        phase = "build"
        image = build_image(root, directory, tag, source)
        phase = "synthetic_release"
        from examples.serving_smoke import synthetic_release

        release = directory / "release"
        prepared = synthetic_release(release)
        pin = prepared["manifest_sha256"]
        phase = "fixture_mount_permissions"
        prepare_fixture_mount(release, pin)
        phase = "http_runtime"
        runtime = checks.assess(image["id"], release, pin, requests=10)
        checks.require(
            runtime["status"] == "success" and runtime["evidence_kind"] == "synthetic_smoke",
            "Synthetic runtime gate failed",
        )
        checks.save_report(directory / "runtime.json", runtime)
        phase = "blue_green"
        rehearsal = rehearse_image(directory, release, pin, image["id"])
        phase = "export_image"
        exported = json.loads(command(["docker", "image", "inspect", tag], root=root))[0]
        checks.require(exported["Id"] == image["id"], "Export image differs from tested image")
        command(
            ["docker", "image", "save", "--output", str(directory / "image.tar"), tag],
            root=root,
            timeout=180,
        )
        checks.require(
            0 < (directory / "image.tar").stat().st_size <= MAX_IMAGE_BYTES,
            "Image archive exceeds 1 GiB budget",
        )
        checks.require(
            source_identity(root, expected_source_sha) == source,
            "Source inputs changed during qualification",
        )
        phase = "owned_image_cleanup"
        command(["docker", "image", "rm", tag], root=root)
        image = None
        record = {
            "schema": SCHEMA,
            "status": "qualified_synthetic",
            "scope": SCOPE,
            "source_commit": source["commit"],
            "exported_local_tag": tag,
            "image": runtime["image"],
            "base_image": json.loads((root / "infra/serving/images.lock.json").read_text())[
                "build_python"
            ],
            "source_revision_label": source["commit"],
            "registry_published": False,
            "registry_digest": None,
            "release_manifest_sha256": pin,
            "serving_release_id": runtime["serving_release_id"],
            "model_sha256": runtime["http_score"]["model_sha256"],
            "evidence_kind": "synthetic_smoke",
            "historical_reference_accessed": False,
            "historical_model_refit": False,
            "synthetic_fixture_fitted": True,
            "production_promotion": False,
            "permanent_deployment_created": False,
            "archive_reload_validated": False,
            "rollback_verified": rehearsal["rollback_verified"],
            "temporary_resources_removed": rehearsal["temporary_resources_removed"],
            "owned_image_tag_removed": True,
            "elapsed_seconds": time.monotonic() - started,
            "files": inventory(directory),
        }
        checks.save_report(directory / "delivery.json", record)
        verify(directory, expected_source_sha, digest(directory / "delivery.json"))
        return {
            "status": record["status"],
            "directory": str(directory),
            "source_commit": source["commit"],
            "descriptor_sha256": digest(directory / "delivery.json"),
            "image_id": record["image"]["id"],
        }
    except BaseException as error:
        cleanup_error = None
        if image is not None or phase == "build":
            try:
                command(["docker", "image", "rm", tag], root=root)
            except (checks.CheckFailed, OSError, subprocess.SubprocessError) as cleanup:
                cleanup_error = type(cleanup).__name__
        (directory / "delivery.json").unlink(missing_ok=True)
        checks.save_report(
            directory / "failure.json",
            {
                "status": "failed",
                "owned_image_tag": tag,
                "directory": str(directory),
                "phase": phase,
                "error_type": type(error).__name__,
                "reason": str(error)[-2000:],
                "owned_image_cleanup_error": cleanup_error,
            },
        )
        print("Failure report: " + str(directory / "failure.json"), file=sys.stderr)
        raise


def verify(directory, expected_source_sha, expected_descriptor_sha256):
    directory = managed_destination(directory, "serving")
    checks.require(
        re.fullmatch(r"[0-9a-f]{40}", expected_source_sha) is not None, "Invalid expected commit"
    )
    checks.require(
        re.fullmatch(r"[0-9a-f]{64}", expected_descriptor_sha256) is not None,
        "Invalid expected descriptor hash",
    )
    descriptor = directory / "delivery.json"
    checks.require(
        not descriptor.is_symlink() and digest(descriptor) == expected_descriptor_sha256,
        "Delivery descriptor hash differs",
    )
    record = json.loads(descriptor.read_text())
    checks.require(
        record["schema"] == SCHEMA
        and record["scope"] == SCOPE
        and record["status"] == "qualified_synthetic",
        "Delivery is not qualified",
    )
    checks.require(
        record["source_commit"] == expected_source_sha
        and record["evidence_kind"] == "synthetic_smoke",
        "Source or evidence kind differs",
    )
    checks.require(
        not record["production_promotion"] and not record["registry_published"],
        "Unexpected promotion/publication",
    )
    checks.require(
        record["rollback_verified"]
        and record["temporary_resources_removed"]
        and record["owned_image_tag_removed"],
        "Incomplete gate or cleanup",
    )
    required = {
        "image.tar",
        "source.json",
        "runtime.json",
        "rollout.json",
        "candidate-manifest.json",
        *("release/" + n for n in checks.RELEASE_FILES),
    }
    files = record["files"]
    checks.require(required <= set(files), "Delivery payload is incomplete")
    for name in files:
        relative = PurePosixPath(name)
        checks.require(
            not relative.is_absolute()
            and ".." not in relative.parts
            and relative.as_posix() == name
            and "\\" not in name,
            "Unsafe delivery path",
        )
    checks.require(
        inventory(directory) == files and not (directory / "failure.json").exists(),
        "Delivery files changed",
    )
    checks.require(
        0 < files["image.tar"]["bytes"] <= MAX_IMAGE_BYTES, "Invalid image archive size"
    )
    manifest, _, _ = checks.read_release(directory / "release", record["release_manifest_sha256"])
    runtime = json.loads((directory / "runtime.json").read_text())
    rehearsal = json.loads((directory / "rollout.json").read_text())
    source = json.loads((directory / "source.json").read_text())
    checks.require(source["commit"] == expected_source_sha, "Source record differs")
    checks.require(
        runtime["image"]["id"] == record["image"]["id"] and runtime["status"] == "success",
        "Tested image differs",
    )
    checks.require(
        manifest["serving_release_id"]
        == record["serving_release_id"]
        == runtime["serving_release_id"],
        "Release identity differs",
    )
    checks.require(
        manifest["model_sha256"] == record["model_sha256"] == rehearsal["model_sha256"],
        "Model identity differs",
    )
    checks.require(
        rehearsal["status"] == "success"
        and rehearsal["events"] == list(rollout.EXPECTED_EVENTS)
        and rehearsal["rollback_verified"],
        "Rollback record differs",
    )
    checks.require(record["source_revision_label"] == expected_source_sha, "Source label differs")
    checks.require(
        runtime["manifest_sha256"]
        == record["release_manifest_sha256"]
        == rehearsal["baseline_manifest_sha256"],
        "Manifest records differ",
    )
    checks.require(
        runtime["evidence_kind"] == manifest["evidence_kind"] == "synthetic_smoke",
        "Historical evidence is not accepted here",
    )
    checks.require(
        runtime["invalid_input_http_status"] == 422
        and rehearsal["observed_failure_http_status"] == 502,
        "HTTP failure gates differ",
    )
    checks.require(
        rehearsal["baseline_release_id"] == record["serving_release_id"]
        and rehearsal["candidate_release_id"] != record["serving_release_id"],
        "Repackage identity differs",
    )
    checks.require(
        runtime["http_score"]["model_sha256"] == record["model_sha256"],
        "Runtime model hash differs",
    )
    for score in [
        runtime["http_score"]["score"],
        rehearsal["candidate_score"],
        rehearsal["rollback_score"],
    ]:
        checks.require(
            type(score) in (int, float)
            and math.isfinite(score)
            and abs(
                score
                - json.loads((directory / "release/smoke.json").read_text())["expected_score"]
            )
            <= checks.PARITY_TOLERANCE,
            "Recorded score parity differs",
        )
    return {
        "status": "verified_bytes_and_records",
        "source_commit": expected_source_sha,
        "image_loaded": False,
        "signature_verified": False,
        "production_promotion": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    run = commands.add_parser("run")
    run.add_argument("--expected-source-sha", required=True)
    check = commands.add_parser("verify")
    check.add_argument("--directory", required=True, type=Path)
    check.add_argument("--expected-source-sha", required=True)
    check.add_argument("--expected-descriptor-sha256", required=True)
    args = parser.parse_args()
    try:
        result = (
            qualify(args.expected_source_sha)
            if args.action == "run"
            else verify(args.directory, args.expected_source_sha, args.expected_descriptor_sha256)
        )
        print(json.dumps(result, indent=2, allow_nan=False))
    except (
        checks.CheckFailed,
        OSError,
        ValueError,
        KeyError,
        subprocess.SubprocessError,
    ) as error:
        print("Delivery gate stopped: " + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
