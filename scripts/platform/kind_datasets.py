"""Publish and independently restore prepared datasets on the owned KinD RustFS."""

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
from queue import Empty, Queue
import re
import subprocess
from threading import Thread
from uuid import uuid4

from scripts.platform import kind_foundation as f
from scripts.storage import archive
from scripts.storage import bronze_s3_worker as transport

LAYERS = dict(
    zip(("bronze", "silver", "gold", "development-gold"), archive.DATASET_SCHEMAS, strict=True)
)
SCOPE = "prepared_dataset_bundle_kind_S3_roundtrip_not_primary_storage_migration"


def work():
    return f.managed_destination(f.workspace() / "datasets", "storage")


def source_pins():
    names = [
        "infra/kind/datasets/Dockerfile",
        "infra/kind/datasets/Dockerfile.dockerignore",
        "infra/observability/storage-requirements.txt",
        "scripts/platform/kind_datasets_worker.py",
        "scripts/storage/archive.py",
        "scripts/storage/bronze_s3_worker.py",
        "scripts/storage/mlflow_inventory.py",
        "scripts/storage/mlflow_backup.py",
    ]
    return {name: archive.digest(f.ROOT / name) for name in names}


def initialize():
    owner = f.owner()
    work().mkdir(mode=0o700, exist_ok=True)
    f.require(work().stat().st_mode & 0o077 == 0, "Dataset workspace must be private")
    path = f.managed_destination(work() / "owner.json", "storage")
    if not path.exists():
        f.require(not any(work().iterdir()), "Incomplete dataset workspace; preserve for review")
        f.private_json(path, {"owner_id": uuid4().hex, "node_uid": owner["node_uid"]})
    value = json.loads(path.read_text())
    f.require(
        set(value) == {"owner_id", "node_uid"}
        and re.fullmatch(r"[a-f0-9]{32}", value["owner_id"])
        and value["node_uid"] == owner["node_uid"],
        "Dataset owner differs; do not adopt another node or bucket",
    )
    return value


def build():
    initialize()
    path = work() / "image.json"
    if path.exists():
        image_record()
        return {"status": "dataset_image_already_built", "record": str(path)}
    tag = "fraud-kind-datasets:" + uuid4().hex
    print("Building the locked dataset transport...", flush=True)
    f.run(
        ["docker", "build", "--file", "infra/kind/datasets/Dockerfile", "--tag", tag, "."],
        timeout=600,
    )
    image = json.loads(f.run(["docker", "image", "inspect", tag]).stdout)[0]
    f.private_json(
        path,
        {
            "image_id": image["Id"],
            "local_tag": tag,
            "source_pins": source_pins(),
            "registry_published": False,
        },
    )
    return {"status": "dataset_image_built", "image_id": image["Id"], "record": str(path)}


def image_record():
    value = json.loads(f.managed_destination(work() / "image.json", "storage").read_text())
    f.require(
        value["source_pins"] == source_pins(),
        "Dataset image sources changed; rebuild requires review",
    )
    f.require(
        re.fullmatch(r"sha256:[a-f0-9]{64}", value["image_id"]), "Invalid dataset image identity"
    )
    info = json.loads(f.run(["docker", "image", "inspect", value["image_id"]]).stdout)[0]
    f.require(info["Id"] == value["image_id"], "Dataset runtime identity differs")
    return value


def available():
    """Read bounded bundle manifests only; do not open originals, models or databases."""
    root = archive.checked_path(archive.workspace(f.ROOT), Path("bundles"))
    rows = []
    if root.exists():
        for path in sorted(root.iterdir()):
            if not re.fullmatch(r"[a-f0-9]{64}", path.name):
                continue
            path = archive.checked_path(root, Path(path.name))
            marker = archive.checked_path(path, Path("manifest.json"))
            if not marker.is_file():
                continue
            f.require(
                marker.stat().st_size <= archive.BRONZE_MAX_MANIFEST_BYTES, "Manifest too large"
            )
            document = json.loads(marker.read_bytes())
            if document.get("schema") not in archive.DATASET_SCHEMAS:
                continue
            document, _ = transport.manifest_at(path, path.name, document["schema"])
            rows.append(
                {
                    "bundle_id": path.name,
                    "layer": transport.layer(document["schema"]),
                    "files": len(document["files"]),
                    "bytes": sum(r["bytes"] for r in document["files"]),
                }
            )
    return {
        "status": "prepared_dataset_manifests_listed",
        "bundles": rows,
        "payloads_verified": False,
    }


def verify_directory(directory, identity, schema):
    from importlib import import_module

    document, raw = transport.manifest_at(directory, identity, schema)
    module_name = {
        archive.BRONZE_SCHEMA: "bronze_archive",
        archive.SILVER_SCHEMA: "silver_archive",
        archive.GOLD_SCHEMA: "gold_archive",
        archive.DEVELOPMENT_SCHEMA: "development_gold_archive",
    }[schema]
    result = import_module("scripts.storage." + module_name).verify_directory(directory, document)
    return document, raw, result


@contextmanager
def port_forward():
    args = [
        "kubectl",
        "--kubeconfig",
        str(f.workspace() / "kubeconfig"),
        "--cache-dir",
        str(f.workspace() / "kube-cache"),
        "--context",
        f.CONTEXT,
        "--namespace",
        f.NAMESPACE,
        "port-forward",
        "--address",
        "127.0.0.1",
        "service/fraud-platform-rustfs",
        ":9000",
    ]
    with subprocess.Popen(
        args, cwd=f.ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    ) as process:
        ready = Queue(maxsize=1)

        def drain():
            # kubectl emits one line per connection. Drain for the whole transfer so
            # a large bundle cannot fill the pipe and deadlock the forwarding process.
            for line in iter(process.stdout.readline, b""):
                match = re.search(rb"Forwarding from 127\.0\.0\.1:([0-9]+) -> 9000", line)
                if match and ready.empty():
                    ready.put_nowait("http://127.0.0.1:" + match[1].decode())

        reader = Thread(target=drain, daemon=True)
        reader.start()
        try:
            try:
                endpoint = ready.get(timeout=30)
            except Empty as exc:
                raise ValueError(
                    "RustFS loopback port-forward failed; inspect foundation status"
                ) from exc
            f.require(process.poll() is None, "RustFS port-forward exited before the transfer")
            yield endpoint
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            reader.join(timeout=5)


def run_worker(action, identity, schema, attempt, source=None):
    own = initialize()
    image = image_record()
    creds = f.credentials()
    downloads = f.managed_destination(attempt / "downloads", "storage")
    downloads.mkdir(mode=0o700)
    secret_file = f.managed_destination(attempt / "worker.env", "storage")
    name = "fraud-kind-datasets-" + uuid4().hex
    with port_forward() as endpoint:
        values = {
            "AWS_ACCESS_KEY_ID": creds["rustfs-access-key"],
            "AWS_SECRET_ACCESS_KEY": creds["rustfs-secret-key"],
            "AWS_EC2_METADATA_DISABLED": "true",
            "KIND_DATASETS_ENDPOINT": endpoint,
            "KIND_DATASETS_OWNER": own["owner_id"],
        }
        f.require(
            all(not any(c in v for c in "\r\n\0") for v in values.values()),
            "Invalid worker environment",
        )
        with secret_file.open("x") as stream:
            secret_file.chmod(0o600)
            stream.write("".join(f"{k}={v}\n" for k, v in values.items()))
        args = [
            "docker",
            "run",
            "--rm",
            "--name",
            name,
            "--pull",
            "never",
            "--network",
            "host",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            "64",
            "--memory",
            "256m",
            "--cpus",
            "1",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m",
            "--env",
            "HOME=/tmp",
            "--env-file",
            str(secret_file),
            "--mount",
            f"type=bind,src={downloads},dst=/work/downloads",
        ]
        if source is not None:
            args += ["--mount", f"type=bind,src={source},dst=/work/bundles/{identity},readonly"]
        args += [image["image_id"], action, "--bundle-id", identity, "--schema", schema]
        try:
            completed = f.run(args, timeout=900, check=False)
            result = json.loads(completed.stdout or "{}")
            f.require(
                completed.returncode == 0,
                "Dataset worker failed: "
                + str(result.get("error_type", "inspect Docker runtime")),
            )
            f.require(
                result.get("bundle_id") == identity
                and result.get("bucket") == transport.BUCKET
                and result.get("prefix") == transport.prefix(identity, schema)
                and result.get("endpoint_scope") == "owned_kind_rustfs"
                and result.get("dataset_owner_id") == own["owner_id"],
                "Unexpected dataset worker receipt",
            )
            return result
        finally:
            # Remove only the random worker owned by this invocation, including after timeout.
            try:
                f.run(["docker", "rm", "--force", name], check=False)
            finally:
                secret_file.unlink(missing_ok=True)


def transfer(action, identity, layer, worker=run_worker):
    identity = archive.bundle_id(identity)
    schema = LAYERS[layer]
    initialize()
    before = f.inventory()
    image = image_record()
    source = None
    if action == "publish":
        source = archive.checked_path(archive.workspace(f.ROOT), Path("bundles") / identity)
        document, raw, verified = verify_directory(source, identity, schema)
    else:
        f.require(action == "restore", "Unknown dataset operation")
    attempt = f.managed_destination(work() / (action + "-" + uuid4().hex), "storage")
    attempt.mkdir(mode=0o700)
    receipt = attempt / "result.json"
    result = {
        "schema": "fraud_kind_dataset_transfer_v1",
        "status": "failed",
        "scope": SCOPE,
        "action": action,
        "layer": layer,
        "bundle_id": identity,
        "report": str(receipt),
        "controller_sha256": archive.digest(Path(__file__)),
        "worker_image_id": image["image_id"],
        "worker_source_pins": image["source_pins"],
        "primary_storage_migrated": False,
        "historical_tracking_migrated": False,
        "project_wide_storage_inventory_complete": False,
        "fit_executed": False,
        "pickle_deserialized": False,
        "production_promotion": False,
        "original_source_read": False,
        "foundation_before": before,
    }
    try:
        remote = worker(
            "publish" if action == "publish" else "download", identity, schema, attempt, source
        )
        f.require(
            remote.get("status")
            == ("success" if action == "publish" else "downloaded_quarantined"),
            "Worker did not complete transfer",
        )
        if action == "publish":
            # Rehash the archived source after transfer; never mutate original datasets.
            after_doc, after_raw, _ = verify_directory(source, identity, schema)
            f.require(after_raw == raw and after_doc == document, "Source archive changed")
            result.update(source_bundle_unchanged=True)
        else:
            relative = Path(remote.get("candidate", ""))
            f.require(
                len(relative.parts) == 2
                and relative.parts[0] == "downloads"
                and relative.parts[1].startswith(layer + "-"),
                "Invalid quarantine destination",
            )
            candidate = archive.checked_path(attempt, relative)
            document, raw, verified = verify_directory(candidate, identity, schema)
            destination = f.managed_destination(attempt / "restored", "storage")

        after = f.inventory()
        f.require(before == after, "Foundation identities changed during dataset transfer")
        if action == "restore":
            candidate.rename(destination)
            result.update(
                restored_directory=str(destination), independent_restored_bundle_verified=True
            )
        result.update(
            status="published_verified" if action == "publish" else "restored_verified",
            transport={k: v for k, v in remote.items() if k != "candidate"},
            semantic_verification=verified,
            manifest_sha256=hashlib.sha256(raw).hexdigest(),
            files=len(document["files"]),
            bytes=sum(r["bytes"] for r in document["files"]),
            foundation_after=after,
            foundation_identities_preserved=True,
        )
        return result
    except BaseException as exc:
        result.update(error_type=type(exc).__name__)
        raise
    finally:
        f.private_json(receipt, result)
        print("Relatório: " + str(receipt), flush=True)


def rehearse():
    """Close the four prepared dataset roundtrips without choosing ambiguous inputs."""
    rows = available()["bundles"]
    selected = {}
    for layer in LAYERS:
        matches = [row for row in rows if row["layer"] == layer]
        f.require(
            len(matches) == 1,
            "Expected exactly one prepared "
            + layer
            + " bundle; inspect inventory and use explicit publish/restore commands",
        )
        selected[layer] = matches[0]["bundle_id"]
    initialize()
    image_record()
    receipt = work() / ("rehearsal-" + uuid4().hex + ".json")
    result = {
        "schema": "fraud_kind_datasets_rehearsal_v1",
        "status": "failed",
        "scope": SCOPE,
        "report": str(receipt),
        "datasets": {},
        "primary_storage_migrated": False,
        "project_wide_storage_inventory_complete": False,
        "production_promotion": False,
        "fit_executed": False,
        "features_recomputed": False,
    }
    try:
        for layer, identity in selected.items():
            first = transfer("publish", identity, layer)
            again = transfer("publish", identity, layer)
            f.require(
                again["transport"]["uploaded_objects"] == 0,
                "Republication unexpectedly uploaded objects",
            )
            restored = transfer("restore", identity, layer)
            result["datasets"][layer] = {
                "bundle_id": identity,
                "files": restored["files"],
                "bytes": restored["bytes"],
                "manifest_sha256": restored["manifest_sha256"],
                "uploaded_objects": first["transport"]["uploaded_objects"],
                "reused_objects": again["transport"]["reused_objects"],
                "verified_objects": restored["transport"]["verified_objects"],
                "semantic_verification": restored["semantic_verification"],
                "publication_report": first["report"],
                "republication_report": again["report"],
                "restore_report": restored["report"],
                "restored_directory": restored["restored_directory"],
                "independent_restored_bundle_verified": restored[
                    "independent_restored_bundle_verified"
                ],
                "foundation_identities_preserved": restored["foundation_identities_preserved"],
            }
        result["status"] = "success"
        return result
    except BaseException as exc:
        result["error_type"] = type(exc).__name__
        raise
    finally:
        f.private_json(receipt, result)
        print("Relatório do recorte: " + str(receipt), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=["inventory", "build", "publish", "restore", "rehearse"]
    )
    parser.add_argument("--bundle-id")
    parser.add_argument("--layer", choices=LAYERS)
    args = parser.parse_args()
    try:
        if args.command == "inventory":
            result = available()
        else:
            with f.exclusive():
                if args.command == "build":
                    result = build()
                elif args.command == "rehearse":
                    result = rehearse()
                else:
                    f.require(
                        bool(args.bundle_id and args.layer),
                        "Both --bundle-id and --layer are required",
                    )
                    result = transfer(args.command, args.bundle_id, args.layer)
        print(json.dumps(result, indent=2, allow_nan=False))
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"KinD dataset transfer stopped: {exc}\n")


if __name__ == "__main__":
    main()
