"""Capture only the dedicated lab, restarting its original MLflow container in finally."""

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import sys
import time
from uuid import uuid4

from scripts.tracking import capture_worker as worker
from scripts.tracking import local_stack as stack

ROOT = Path(__file__).resolve().parents[2]
CAPTURE_SECONDS = 300
RECOVERY_SECONDS = 90
PG_IMAGE = "sha256:3645570cccdfa447589da9f57dd740faa29b30938e861289a5574b6ca6b03826"


def run(args, deadline, *, destination=None, input_path=None, limit=1024 * 1024, environment=None):
    """Bound stdout/stderr while streaming binary dump bytes, without a shell."""
    worker.require(time.monotonic() < deadline, "Capture deadline exceeded")
    input_stream = input_path.open("rb") if input_path else subprocess.DEVNULL
    output = None
    process = None
    try:
        if destination:
            output = destination.open("xb")
            os.chmod(destination, 0o600)
        process = subprocess.Popen(
            args,
            stdin=input_stream,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            env=environment,
        )
        result, error, total = bytearray(), bytearray(), 0
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, "stdout")
            selector.register(process.stderr, selectors.EVENT_READ, "stderr")
            while selector.get_map():
                remaining = deadline - time.monotonic()
                worker.require(remaining > 0, "Capture deadline exceeded")
                for key, _ in selector.select(min(remaining, 1)):
                    data = os.read(key.fileobj.fileno(), 65536)
                    if not data:
                        selector.unregister(key.fileobj)
                        continue
                    if key.data == "stderr":
                        error.extend(data)
                        worker.require(len(error) <= 65536, "Command stderr budget exceeded")
                    else:
                        total += len(data)
                        worker.require(total <= limit, "Command output byte budget exceeded")
                        if output:
                            output.write(data)
                        else:
                            result.extend(data)
            code = process.wait(timeout=max(0.01, deadline - time.monotonic()))
        # Dump warnings are errors for this restricted laboratory capture.
        worker.require(
            code == 0 and not (destination and error),
            "Capture command failed; inspect service status (output withheld)",
        )
        return bytes(result)
    finally:
        if process is not None:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            process.stdout.close()
            process.stderr.close()
        if output:
            output.close()
        if input_path:
            input_stream.close()


def sha(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            value.update(chunk)
    return value.hexdigest()


def file_record(path, directory):
    return {
        "file": path.relative_to(directory).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha(path),
    }


def verify(directory, expected_manifest_sha256=None):
    directory = stack.checked(directory)
    manifest_path = stack.checked(directory / "manifest.json")
    worker.require(
        manifest_path.is_file() and manifest_path.stat().st_size <= 2 * 1024**2,
        "Missing or oversized capture manifest",
    )
    if expected_manifest_sha256:
        worker.require(sha(manifest_path) == expected_manifest_sha256, "Manifest hash differs")
    manifest = json.loads(manifest_path.read_bytes())
    worker.require(
        manifest["schema"] == worker.SCHEMA
        and manifest["status"] == "captured_verified"
        and manifest["source_recovered"] is True
        and manifest["capture_id"] == directory.name
        and manifest["bucket"] == worker.BUCKET
        and manifest["prefix"] == worker.PREFIX,
        "Incomplete capture",
    )
    records = manifest["files"]
    worker.require(len(records) <= worker.MAX_OBJECTS + 3, "File count exceeds capture budget")
    paths = set()
    for record in records:
        name = record["file"]
        worker.require(
            name in {"postgres.dump", "metadata.json", "objects.json"}
            or re.fullmatch(r"objects/[0-9]{4}\.bin", name),
            "Unmanaged capture member",
        )
        worker.require(name not in paths, "Duplicate capture member")
        paths.add(name)
        path = stack.checked(directory / name)
        worker.require(
            path.is_file() and path.stat().st_size == record["bytes"],
            "Capture file missing or resized",
        )
        bound = worker.MAX_DUMP if name == "postgres.dump" else worker.MAX_BYTES
        worker.require(
            0 <= record["bytes"] <= bound and sha(path) == record["sha256"],
            "Capture file integrity differs",
        )
    worker.require(
        {"postgres.dump", "metadata.json", "objects.json"} <= paths, "Missing control files"
    )
    actual = {p.relative_to(directory).as_posix() for p in directory.rglob("*") if p.is_file()}
    worker.require(actual == paths | {"manifest.json"}, "Unexpected capture members")
    worker.require(not any(p.is_symlink() for p in directory.rglob("*")), "Symlink in capture")
    objects = json.loads((directory / "objects.json").read_bytes())
    metadata = json.loads((directory / "metadata.json").read_bytes())
    worker.require(
        len(objects) <= worker.MAX_OBJECTS
        and sum(o["bytes"] for o in objects) <= worker.MAX_BYTES,
        "Object capture budget exceeded",
    )
    by_path = {r["file"]: r for r in records}
    worker.require(
        len({o["key"] for o in objects}) == len(objects)
        and len({o["file"] for o in objects}) == len(objects),
        "Duplicate captured object",
    )
    worker.require(
        {o["file"] for o in objects} == paths - {"postgres.dump", "metadata.json", "objects.json"},
        "Object mapping differs",
    )
    for obj in objects:
        worker.require(
            all(obj[k] == by_path[obj["file"]][k] for k in ["bytes", "sha256"]),
            "Object checksum mapping differs",
        )
    worker.coverage(metadata, objects)
    with (directory / "postgres.dump").open("rb") as stream:
        worker.require(stream.read(5) == b"PGDMP", "Not a PostgreSQL custom archive")
    return {
        "status": "verified_locally",
        "capture_id": directory.name,
        "manifest_sha256": sha(manifest_path),
        "objects": len(objects),
        "artifact_bytes": sum(o["bytes"] for o in objects),
        "postgres_backup_restore_validated": False,
    }


def capture_directory(root, identity):
    worker.require(bool(re.fullmatch(r"[0-9a-f]{32}", identity)), "Invalid capture ID")
    return stack.checked(stack.workspace(root) / "captures" / identity)


def tool(root, directory, action, deadline):
    name = "fraud-capture-" + directory.name + "-" + action
    # Compose interpolation receives only this managed path in the child environment.
    args = stack.compose(root)
    args += [
        "-f",
        str(root / "infra/tracking/capture.compose.yaml"),
        "run",
        "--rm",
        "--no-deps",
        "--name",
        name,
        "capture",
        action,
    ]
    # Never change the caller's environment, including its secrets or tracking URI.
    environment = dict(os.environ, TRACKING_CAPTURE_DIRECTORY=str(directory))
    try:
        result = json.loads(run(args, deadline, environment=environment))
        expected = {"check": "ready", "inventory": "inventoried", "objects": "objects_captured"}[
            action
        ]
        worker.require(result.get("status") == expected, "Invalid worker result")
        worker.require(
            result.get("worker_code_sha256") == sha(Path(worker.__file__)),
            "Capture image contains different worker code; rebuild it",
        )
        return result
    finally:
        # Killing a Compose client need not stop its one-off container.
        try:
            subprocess.run(
                ["docker", "rm", "-f", name], capture_output=True, timeout=15, check=False
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError("Capture worker cleanup failed; inspect " + name) from None


@contextmanager
def recover_on_term():
    def interrupted(signum, frame):
        raise InterruptedError("Capture interrupted")

    previous = signal.signal(signal.SIGTERM, interrupted)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


def stop(root, before, deadline):
    run(stack.compose(root, "stop", "--timeout", "20", "mlflow"), deadline)
    running = run(
        ["docker", "inspect", "--format", "{{.State.Running}}", before["mlflow"]["container_id"]],
        deadline,
    )
    worker.require(running.strip() == b"false", "MLflow did not stop")


def restart(root, before):
    deadline = time.monotonic() + RECOVERY_SECONDS
    # Start the exact existing container. Never up/build/recreate or replace a volume here.
    run(["docker", "start", before["mlflow"]["container_id"]], deadline)
    while time.monotonic() < deadline:
        try:
            after = {s: stack.inspect_service(root, s) for s in ["postgres", "mlflow"]}
        except ValueError:
            time.sleep(1)
            continue
        worker.require(after == before, "Source container/image/volume identity changed")
        return after
    raise RuntimeError("Source MLflow did not recover health; run tracking.local_stack status")


def capture(root=ROOT, *, no_other_writers=False):
    worker.require(
        no_other_writers, "Declare no other PostgreSQL/S3 writers with --no-other-writers"
    )
    with stack.exclusive(root), recover_on_term():
        identity = uuid4().hex
        directory = capture_directory(root, identity)
        directory.mkdir(mode=0o700, parents=True, exist_ok=False)
        receipt = stack.workspace(root) / "receipts" / ("capture-" + identity + ".json")
        report = {
            "schema": worker.SCHEMA,
            "status": "failed",
            "capture_id": identity,
            "scope": "coordinated_lab_PostgreSQL_custom_dump_and_S3_capture_not_restore_or_cutover",
            "directory": str(directory),
            "report": str(receipt),
            "source_recovered": False,
            "postgres_backup_restore_validated": False,
            "primary_storage_migrated": False,
            "project_wide_storage_inventory_complete": False,
            "other_writers_absent_operator_declared": True,
            "capture_phase": "preflight",
            "refit": False,
            "models_loaded": False,
            "capture_code_sha256": sha(Path(__file__)),
            "worker_code_sha256": sha(Path(worker.__file__)),
        }
        origin = time.monotonic()
        requested = False
        try:
            deadline = origin + CAPTURE_SECONDS
            before = {s: stack.inspect_service(root, s) for s in ["postgres", "mlflow"]}
            worker.require(
                before["postgres"]["image_id"] == PG_IMAGE, "PostgreSQL image differs from pin"
            )
            report["containers_before"] = before
            # Verify tool image/code BEFORE pausing the source.
            report["capture_phase"] = "worker_check"
            tool(root, directory, "check", deadline)
            requested = True  # Even an interrupted stop must enter recovery.
            try:
                report["capture_phase"] = "stop_mlflow"
                stop(root, before, deadline)
                print("source_mlflow_stopped", file=sys.stderr, flush=True)
                report["capture_phase"] = "metadata_inventory"
                tool(root, directory, "inventory", deadline)
                report["capture_phase"] = "database_dump"
                pg = before["postgres"]["container_id"]
                run(
                    [
                        "docker",
                        "exec",
                        "-e",
                        "PGOPTIONS=-c statement_timeout=180000",
                        pg,
                        "pg_dump",
                        "-U",
                        "mlflow",
                        "-d",
                        "mlflow_lab",
                        "--format=custom",
                        "--lock-wait-timeout=5000",
                        "--no-password",
                    ],
                    deadline,
                    destination=directory / "postgres.dump",
                    limit=worker.MAX_DUMP,
                )
                report["capture_phase"] = "dump_table_of_contents"
                toc = run(
                    ["docker", "exec", "-i", pg, "pg_restore", "--list"],
                    deadline,
                    input_path=directory / "postgres.dump",
                    limit=4 * 1024**2,
                )
                worker.require(
                    b"TABLE DATA" in toc and b"alembic_version" in toc,
                    "Incomplete dump table of contents",
                )
                report["dump_toc_sha256"] = worker.digest(toc)
                report["capture_phase"] = "s3_copy_and_readback"
                tool(root, directory, "objects", deadline)
                report["capture_seconds"] = time.monotonic() - origin
            finally:
                report["containers_after"] = restart(root, before)
                report["source_recovered"] = True
                print("source_mlflow_healthy", file=sys.stderr, flush=True)
            report["capture_phase"] = "local_verification"
            files = [
                file_record(p, directory) for p in sorted(directory.rglob("*")) if p.is_file()
            ]
            manifest = {
                "schema": worker.SCHEMA,
                "status": "captured_verified",
                "capture_id": identity,
                "source_recovered": True,
                "files": files,
                "bucket": worker.BUCKET,
                "prefix": worker.PREFIX,
                "containers": before,
                "worker_code_sha256": report["worker_code_sha256"],
                "dump_toc_sha256": report["dump_toc_sha256"],
            }
            worker.save(directory / "manifest.json", manifest)
            report.update(verify(directory))
            metadata = json.loads((directory / "metadata.json").read_bytes())
            report["metadata_rows"] = {
                name: row["rows"] for name, row in metadata["tables"].items()
            }
            report["dump_bytes"] = (directory / "postgres.dump").stat().st_size
            report["status"] = "captured_verified"
            report["capture_phase"] = "complete"
        except BaseException as exc:
            report["failure_reason"] = (
                str(exc) if isinstance(exc, worker.CaptureError) else "Details withheld"
            )
            report["error_type"] = type(exc).__name__  # Never serialize SDK credentials/URLs.
            if (directory / "manifest.json").exists():
                (directory / "manifest.json").unlink()
            raise
        finally:
            report["source_stop_requested"] = requested
            report["elapsed_seconds"] = time.monotonic() - origin
            stack.save_json(receipt, report)
            print("Relatório: " + str(receipt), file=sys.stderr, flush=True)
        return report


def build(root=ROOT):
    stack.workspace(root)
    stack.command(
        stack.compose(root)
        + ["-f", str(root / "infra/tracking/capture.compose.yaml"), "build", "capture"],
        timeout=900,
    )
    return {"status": "built", "image": "fraud-tracking-capture:lab-v1"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("build")
    sub.add_parser("capture").add_argument("--no-other-writers", action="store_true")
    verify_parser = sub.add_parser("verify")
    verify_parser.add_argument("--capture-id", required=True)
    verify_parser.add_argument("--manifest-sha256")
    args = parser.parse_args()
    try:
        if args.action == "build":
            result = build()
        elif args.action == "capture":
            result = capture(no_other_writers=args.no_other_writers)
        else:
            result = verify(capture_directory(ROOT, args.capture_id), args.manifest_sha256)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(
            f"Tracking capture stopped ({type(exc).__name__}); see preserved receipt and service status",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
