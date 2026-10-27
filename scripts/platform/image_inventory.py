"""Read-only image inventory for the fraud KinD node and its Docker host."""

import argparse
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import subprocess

from scripts.serving.image_footprint import publish

ROOT = Path(__file__).resolve().parents[2]
NODE = "fraud-platform-control-plane"
CONTEXT = "kind-fraud-platform"
NAMESPACE = "fraud-platform"
RESOURCE_TYPES = "pods,deployments,statefulsets,daemonsets,jobs,cronjobs"


def command(args, *, optional=False):
    result = subprocess.run(
        args, cwd=ROOT, capture_output=True, text=True, timeout=120, check=False
    )
    if result.returncode:
        if optional:
            return None
        raise ValueError(f"{args[0]} inventory command failed (exit {result.returncode})")
    return result.stdout


def consumers(document):
    """Select image links only; never serialize env, annotations, mounts or command args."""
    rows = []
    for item in document.get("items", []):
        kind, metadata = item["kind"], item["metadata"]
        spec = item.get("spec", {})
        if kind == "CronJob":
            spec = spec.get("jobTemplate", {}).get("spec", {})
        if kind != "Pod":
            spec = spec.get("template", {}).get("spec", {})
        statuses = {
            s["name"]: s.get("imageID")
            for field in ("containerStatuses", "initContainerStatuses")
            for s in item.get("status", {}).get(field, [])
        }
        for field in ("containers", "initContainers"):
            for container in spec.get(field, []):
                rows.append(
                    {
                        "kind": kind,
                        "name": metadata["name"],
                        "uid": metadata["uid"],
                        "component": metadata.get("labels", {}).get("app.kubernetes.io/component"),
                        "container_type": field,
                        "container": container["name"],
                        "image": container["image"],
                        "observed_image_id": statuses.get(container["name"]),
                    }
                )
    return sorted(
        rows, key=lambda row: (row["kind"], row["name"], row["container_type"], row["container"])
    )


def normalized_reference(value):
    value = value.removeprefix("docker-pullable://").removeprefix("containerd://")
    value = value.removeprefix("docker.io/")
    return value.removeprefix("library/")


def source_inventory():
    paths = set(ROOT.glob("infra/**/Dockerfile*"))
    paths.update(ROOT.glob("infra/**/*requirements*.txt"))
    paths.update(ROOT.glob("infra/**/images.lock.json"))
    paths.update(
        [
            ROOT / ".dockerignore",
            ROOT / "docker/serving/Dockerfile",
            ROOT / "poetry.lock",
            ROOT / "pyproject.toml",
        ]
    )
    return {
        path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(paths)
        if path.is_file()
    }


def locked_images():
    result = set()

    def visit(value):
        if isinstance(value, dict):
            for child in value.values():
                visit(child)
        elif isinstance(value, str) and "@sha256:" in value:
            result.add(value)

    for path in ROOT.glob("infra/**/images.lock.json"):
        visit(json.loads(path.read_text()))
    return result


def collect(runner=command):
    kube = [
        "kubectl",
        "--kubeconfig",
        str(ROOT / "data/storage/kind-platform/kubeconfig"),
        "--context",
        CONTEXT,
        "--namespace",
        NAMESPACE,
        "get",
        RESOURCE_TYPES,
        "-o",
        "json",
    ]
    source_before = source_inventory()
    links = consumers(json.loads(runner(kube)))
    node = json.loads(runner(["docker", "container", "inspect", NODE]))[0]
    if node.get("Config", {}).get("Labels", {}).get("io.x-k8s.kind.cluster") != "fraud-platform":
        raise ValueError("Docker node does not belong to fraud-platform")
    cri = json.loads(
        runner(
            [
                "docker",
                "exec",
                NODE,
                "crictl",
                "--runtime-endpoint",
                "unix:///run/containerd/containerd.sock",
                "images",
                "-o",
                "json",
            ]
        )
    )
    images = {}
    for image in cri.get("images", []):
        row = images.setdefault(
            image["id"], {"id": image["id"], "size_bytes": int(image["size"]), "aliases": set()}
        )
        row["aliases"].update(image.get("repoTags") or [])
        row["aliases"].update(image.get("repoDigests") or [])
    for row in images.values():
        row["aliases"] = sorted(row["aliases"])
        identities = {normalized_reference(value) for value in [*row["aliases"], row["id"]]}
        row["consumers"] = [
            link
            for link in links
            if normalized_reference(link["image"]) in identities
            or normalized_reference(link["observed_image_id"] or "") in identities
        ]
        row["reference_scope"] = (
            "current_workload_or_pod"
            if row["consumers"]
            else "no_current_reference_not_deletion_authorization"
        )
    host_ids = runner(
        ["docker", "image", "ls", "--no-trunc", "--quiet", "--filter", "reference=fraud-*"]
    ).splitlines()
    references = (
        set(host_ids) | {link["image"] for link in links} | locked_images() | {node["Image"]}
    )
    host, unavailable = {}, []
    for reference in sorted(references):
        raw = runner(["docker", "image", "inspect", reference], optional=True)
        if raw is None:
            unavailable.append(reference)
            continue
        item = json.loads(raw)[0]
        row = host.setdefault(
            item["Id"],
            {
                "id": item["Id"],
                "size_bytes": item["Size"],
                "os": item.get("Os"),
                "architecture": item.get("Architecture"),
                "repo_tags": item.get("RepoTags") or [],
                "repo_digests": item.get("RepoDigests") or [],
                "matched_references": [],
                "rootfs_diff_ids": item.get("RootFS", {}).get("Layers", []),
                "kind_node_image": item["Id"] == node["Image"],
            },
        )
        row["matched_references"].append(reference)
    daemon = runner(["docker", "system", "df", "--format", "{{json .}}"], optional=True)
    daemon_rows = []
    if daemon is not None:
        for line in daemon.splitlines():
            value = json.loads(line)
            daemon_rows.append(
                {
                    key: value.get(key)
                    for key in ("Type", "TotalCount", "Active", "Size", "Reclaimable")
                }
            )
    links_after = consumers(json.loads(runner(kube)))
    if source_before != source_inventory():
        raise ValueError(
            "Build sources changed during collection; preserve the checkout and retry"
        )
    git_head = runner(["git", "rev-parse", "HEAD"], optional=True)
    return {
        "schema": "fraud_platform_image_inventory_v1",
        "status": "image_inventory_collected",
        "collected_at": datetime.now(UTC).isoformat(),
        "git_head": git_head.strip() if git_head else None,
        "collector_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "build_source_sha256": source_before,
        "context": CONTEXT,
        "namespace": NAMESPACE,
        "node": NODE,
        "consumer_projection_unchanged_during_collection": links == links_after,
        "consumers": links,
        "consumers_after": links_after if links != links_after else None,
        "CRI_images": sorted(images.values(), key=lambda row: row["size_bytes"], reverse=True),
        "Docker_images": sorted(host.values(), key=lambda row: row["size_bytes"], reverse=True),
        "Docker_references_unavailable_locally": unavailable,
        "daemon_totals_display": daemon_rows,
        "accounting": {
            "CRI": "dedicated node image sizes; aliases grouped by CRI id; not unique physical disk usage",
            "Docker": "host inspect Size by unique Docker id; shared layers overlap; not additive with CRI",
            "daemon": "global Docker daemon including other projects; formatted display, not project attribution",
            "retained": "unreferenced images can be required for jobs, recovery or historical receipts",
            "scope": "inventory only; no dependency-usage, optimization, readiness or performance acceptance",
        },
        "deployment_modified": False,
        "champion_modified": False,
        "images_deleted": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    from fraud_detection_mlops.storage.managed_paths import managed_destination

    try:
        output = managed_destination(args.output, "reports")
        if output.exists() or output.is_symlink():
            raise ValueError("Output already exists; use another directory")
        report = collect()
        path = publish(report, output)
    except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
        parser.exit(1, f"Image inventory stopped: {exc}\n")
    print(
        json.dumps(
            {
                "status": report["status"],
                "report": str(path),
                "CRI_unique_images": len(report["CRI_images"]),
                "Docker_unique_images": len(report["Docker_images"]),
                "consumer_projection_unchanged_during_collection": report[
                    "consumer_projection_unchanged_during_collection"
                ],
                "deployment_modified": False,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
