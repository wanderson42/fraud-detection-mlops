"""Verify a qualified archive, load its exact image and check a separate target.

No build/fit, registry push or permanent deployment. The descriptor hash and
source commit must come from the selected trusted producer run, not the bundle.
"""

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from uuid import uuid4

from scripts.serving import check_docker_runtime as checks
from scripts.serving import delivery_gate as delivery

from fraud_detection_mlops.storage.managed_paths import managed_destination

ROOT = Path(__file__).resolve().parents[2]


def tagged_images(docker, tag):
    return docker.run(
        "image", "ls", "--quiet", "--no-trunc", "--filter", "reference=" + tag
    ).splitlines()


def load_archive(directory, destination, *, root):
    delivery.command(
        [
            "docker",
            "image",
            "load",
            "--platform",
            "linux/amd64",
            "--input",
            str(directory / "image.tar"),
        ],
        root=root,
        timeout=180,
        log=destination / "load.log",
    )


def consume(directory, expected_source_sha, expected_descriptor_sha256, *, root=ROOT):
    # Pin/byte verification must complete before any Docker access or load.
    directory = managed_destination(directory, "serving")
    delivery.verify(directory, expected_source_sha, expected_descriptor_sha256)
    descriptor = json.loads((directory / "delivery.json").read_text())
    image = descriptor["image"]["id"]
    tag = descriptor["exported_local_tag"]
    checks.require(re.fullmatch(r"sha256:[0-9a-f]{64}", image) is not None, "Invalid image ID")
    checks.require(
        re.fullmatch(r"fraud-serving-delivery:[0-9a-f]{32}", tag) is not None,
        "Unexpected archive tag",
    )
    destination = managed_destination(root / "data/serving/consumptions" / uuid4().hex, "serving")
    destination.mkdir(parents=True, mode=0o700, exist_ok=False)
    report = {
        "schema": "fraud_serving_consumption_v1",
        "status": "failed",
        "scope": "qualified_archive_load_and_separate_synthetic_HTTP_target_not_permanent_deployment",
        "source_delivery_directory": str(directory),
        "source_commit": expected_source_sha,
        "descriptor_sha256": expected_descriptor_sha256,
        "image_id": image,
        "import_tag": tag,
        "controller_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "archive_reload_validated": False,
        "isolated_target_container": True,
        "separate_engine_required": False,
        "cold_cache_validated": False,
        "build_executed": False,
        "fit_executed": False,
        "historical_reference_accessed": False,
        "production_promotion": False,
        "permanent_deployment_created": False,
        "original_delivery_bytes_unchanged": False,
        "owned_import_tag_removed": False,
        "http_target_completed_and_removed": False,
    }
    docker = checks.Docker()
    attempted_load = False
    phase = "engine"
    started = time.monotonic()
    try:
        delivery.engine_ready(root)
        phase = "tag_preflight"
        checks.require(
            not tagged_images(docker, tag),
            "Import tag already exists; preserve it and inspect before retrying",
        )
        report["image_present_before_load"] = image in set(
            docker.run("image", "ls", "--all", "--quiet", "--no-trunc").splitlines()
        )
        phase = "load"
        attempted_load = True  # A timed-out load may have created the tag.
        load_archive(directory, destination, root=root)
        phase = "loaded_identity"
        info = json.loads(docker.run("image", "inspect", tag))[0]
        checks.require(info["Id"] == image, "Loaded image ID differs from qualified image")
        checks.require(
            info["Os"] == "linux" and info["Architecture"] == "amd64", "Loaded platform differs"
        )
        checks.require(
            info["Config"]["Labels"].get("org.opencontainers.image.revision")
            == expected_source_sha,
            "Loaded image revision label differs",
        )
        phase = "target_fixture"
        release = destination / "release"
        shutil.copytree(directory / "release", release, copy_function=shutil.copyfile)
        delivery.prepare_fixture_mount(release, descriptor["release_manifest_sha256"])
        checks.require(
            checks.hashes(release) == checks.hashes(directory / "release"),
            "Copied fixture bytes differ",
        )
        phase = "http_target"
        runtime = checks.assess(image, release, descriptor["release_manifest_sha256"], requests=10)
        checks.require(
            runtime["status"] == "success" and runtime["image"]["id"] == image,
            "HTTP target image differs",
        )
        checks.require(
            runtime["manifest_sha256"] == descriptor["release_manifest_sha256"]
            and runtime["serving_release_id"] == descriptor["serving_release_id"]
            and runtime["http_score"]["model_sha256"] == descriptor["model_sha256"]
            and runtime["evidence_kind"] == "synthetic_smoke",
            "HTTP target release identity differs",
        )
        checks.save_report(destination / "runtime.json", runtime)
        # assess returns only after cleaning all containers it created.
        report["http_target_completed_and_removed"] = True
        phase = "verify_original"
        delivery.verify(directory, expected_source_sha, expected_descriptor_sha256)
        report["original_delivery_bytes_unchanged"] = True
        report.update(
            manifest_sha256=descriptor["release_manifest_sha256"],
            serving_release_id=descriptor["serving_release_id"],
            model_sha256=descriptor["model_sha256"],
            http_score=runtime["http_score"],
            invalid_input_http_status=runtime["invalid_input_http_status"],
        )
    except BaseException as error:
        report.update(
            failed_phase=phase, error_type=type(error).__name__, reason=str(error)[-3000:]
        )
        raise
    finally:
        try:
            if attempted_load:
                if tagged_images(docker, tag):
                    # Never remove by image ID or force-delete pre-existing resources.
                    docker.run("image", "rm", tag)
                checks.require(
                    not tagged_images(docker, tag), "Owned import tag cleanup incomplete"
                )
                report["owned_import_tag_removed"] = True
        except BaseException as error:
            report.update(
                cleanup_error_type=type(error).__name__, cleanup_reason=str(error)[-2000:]
            )
            raise
        finally:
            try:
                if report["http_target_completed_and_removed"]:
                    shutil.rmtree(destination / "release")
            except OSError as error:
                report.update(
                    cleanup_error_type=type(error).__name__, cleanup_reason=str(error)[-2000:]
                )
                raise
            finally:
                if not report.get("failed_phase") and not report.get("cleanup_error_type"):
                    report.update(status="success", archive_reload_validated=True)
                report["elapsed_seconds"] = time.monotonic() - started
                report["report"] = str(destination / "result.json")
                checks.save_report(destination / "result.json", report)
                print("Consumption report: " + str(destination / "result.json"), file=sys.stderr)

    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--expected-source-sha", required=True)
    parser.add_argument("--expected-descriptor-sha256", required=True)
    args = parser.parse_args()
    try:
        result = consume(args.directory, args.expected_source_sha, args.expected_descriptor_sha256)
        print(json.dumps(result, indent=2, allow_nan=False))
    except (
        checks.CheckFailed,
        OSError,
        ValueError,
        KeyError,
        subprocess.SubprocessError,
    ) as error:
        print("Delivery consumption stopped: " + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
