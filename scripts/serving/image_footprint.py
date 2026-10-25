"""Inspect a local Python image without deploying, pulling or mounting project data."""

import argparse
from collections import defaultdict
from datetime import UTC, datetime
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sysconfig
import tempfile
import uuid


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def installed_inventory(roots=None, distributions=None):
    """Count readable regular files; never import model/scientific packages.

    Filesystem bytes and distribution RECORD bytes are separate views and must
    not be added. Neither view measures compressed layers or reclaimable space.
    """
    if roots is None:
        roots = [Path(sysconfig.get_path(key)) for key in ("purelib", "platlib")]
    roots = sorted({Path(root).resolve() for root in roots})
    totals = defaultdict(int)
    folders = defaultdict(int)
    unreadable = 0
    seen = set()
    for root in roots:
        for directory, dirs, names in os.walk(root, followlinks=False):
            parent = Path(directory)
            dirs[:] = [name for name in dirs if not (parent / name).is_symlink()]
            for name in names:
                path = parent / name
                if path.is_symlink() or path in seen:
                    continue
                seen.add(path)
                try:
                    info = path.stat()
                    if not path.is_file():
                        continue
                except OSError:
                    unreadable += 1
                    continue
                relative = path.relative_to(root)
                totals["logical_bytes"] += info.st_size
                totals["allocated_bytes"] += getattr(info, "st_blocks", 0) * 512
                totals["files"] += 1
                folders[relative.parts[0]] += info.st_size
                if path.suffix == ".pyc":
                    totals["bytecode_bytes"] += info.st_size
                if "tests" in relative.parts or "test" in relative.parts:
                    totals["test_path_bytes"] += info.st_size
    rows = []
    if distributions is None:
        distributions = metadata.distributions(path=[str(root) for root in roots])
    for distribution in distributions:
        owned = set()
        size = 0
        missing = 0
        for entry in distribution.files or ():
            path = Path(distribution.locate_file(entry))
            if path.is_symlink():
                continue
            resolved = path.resolve()
            if not any(resolved.is_relative_to(root) for root in roots):
                continue
            if resolved in owned:
                continue
            owned.add(resolved)
            try:
                if resolved.is_file():
                    size += resolved.stat().st_size
                else:
                    missing += 1
            except OSError:
                missing += 1
        rows.append(
            {
                "name": distribution.metadata.get("Name", "unknown"),
                "version": distribution.version,
                "record_logical_bytes": size,
                "record_missing_files": missing,
            }
        )
    return {
        "python": platform.python_version(),
        "machine": platform.machine(),
        "site_packages": dict(totals),
        "unreadable_files": unreadable,
        "top_level_directories": sorted(
            ({"name": key, "logical_bytes": value} for key, value in folders.items()),
            key=lambda row: (-row["logical_bytes"], row["name"]),
        ),
        "distributions": sorted(rows, key=lambda row: (-row["record_logical_bytes"], row["name"])),
        "scope": "readable_site_packages_regular_files_not_whole_image_or_reclaimable_bytes",
    }


def command(args, *, input_text=None):
    result = subprocess.run(
        args, input=input_text, capture_output=True, text=True, check=False, timeout=180
    )
    if result.returncode:
        # Raw stderr can include unrelated daemon configuration; do not publish it.
        raise ValueError(
            f"{args[0]} {args[1]} failed (exit {result.returncode}); no report published"
        )
    return result.stdout


def collect(image, interpreter="/opt/runtime/bin/python", runner=command):
    if not image or image.startswith("-"):
        raise ValueError("An explicit local image reference is required")
    raw = json.loads(runner(["docker", "image", "inspect", image]))
    if len(raw) != 1 or not raw[0].get("Id", "").startswith("sha256:"):
        raise ValueError("Expected exactly one immutable local image ID")
    item = raw[0]
    identity = item["Id"]
    # Execute this same stdlib-only source in an isolated disposable container.
    # The image ID pins the inspected bytes even if the original tag changes.
    source = Path(__file__).read_text(encoding="utf-8")
    container_name = "fraud-image-audit-" + uuid.uuid4().hex
    try:
        probe = runner(
            [
                "docker",
                "run",
                "--rm",
                "--name",
                container_name,
                "--pull=never",
                "--network=none",
                "--read-only",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges",
                "--pids-limit=64",
                "--memory=1g",
                "--cpus=1",
                "--user=10001:10001",
                "--env=PYTHONDONTWRITEBYTECODE=1",
                "--entrypoint",
                interpreter,
                "-i",
                identity,
                "-I",
                "-",
                "--inside",
            ],
            input_text=source,
        )
    except BaseException:
        try:
            runner(["docker", "container", "rm", "--force", container_name])
        except (ValueError, OSError, subprocess.TimeoutExpired) as cleanup_error:
            raise ValueError(
                f"Inspection failed; confirm cleanup of {container_name}"
            ) from cleanup_error
        raise
    inventory = json.loads(probe)
    history = runner(
        [
            "docker",
            "image",
            "history",
            "--no-trunc",
            "--format",
            '{"id":{{json .ID}},"size_display":{{json .Size}}}',
            identity,
        ]
    )
    return {
        "schema": "fraud_python_image_footprint_v1",
        "status": "local_image_inspected",
        "collected_at": datetime.now(UTC).isoformat(),
        "requested_reference": image,
        "image": {
            key: item.get(key)
            for key in ("Id", "RepoDigests", "Os", "Architecture", "Variant", "Size")
        },
        "rootfs_diff_ids": item.get("RootFS", {}).get("Layers", []),
        "history": [json.loads(line) for line in history.splitlines() if line.strip()],
        "python_inventory": inventory,
        "collector_sha256": sha256(source.encode()),
        "container_options": "no_network_no_mounts_readonly_nonroot_no_capabilities",
        "image_size_scope": "Docker_image_inspect_Size_not_CRI_size_or_unique_disk_usage",
        "deployment_modified": False,
        "champion_modified": False,
        "optimization_validated": False,
    }


def publish(report, output):
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise ValueError("Output already exists; preserve it and choose another directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".image-footprint-", dir=output.parent))
    try:
        data = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode()
        (temporary / "report.json").write_bytes(data)
        (temporary / "report.sha256").write_text(sha256(data) + "\n", encoding="utf-8")
        os.rename(temporary, output)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return output / "report.json"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--python", default="/opt/runtime/bin/python")
    parser.add_argument("--inside", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.inside:
        print(json.dumps(installed_inventory()))
        return
    if not args.image or args.output is None:
        parser.error("--image and --output are required")
    if args.output.exists() or args.output.is_symlink():
        parser.error("output already exists; choose another directory")
    try:
        report = collect(args.image, args.python)
        location = publish(report, args.output)
    except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
        parser.exit(1, f"Image inspection stopped: {exc}\n")
    print(
        json.dumps(
            {
                "status": report["status"],
                "report": str(location),
                "image_id": report["image"]["Id"],
                "image_bytes": report["image"]["Size"],
                "largest_distributions": report["python_inventory"]["distributions"][:12],
                "deployment_modified": False,
                "optimization_validated": False,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
