"""Capture owned KinD tracking; restore only to fresh, private disposable resources."""

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
from queue import Empty, Queue
import re
import secrets
import shutil
import subprocess
from threading import Thread
import time
from uuid import uuid4

from scripts.platform import kind_foundation as f
from scripts.platform import kind_operations as operations
from scripts.platform import kind_tracking as tracking
from scripts.platform.tracking_backup import archive as a
from scripts.tracking import capture as streams
from scripts.tracking import capture_worker as shared

ROOT = f.ROOT
INFRA = ROOT / "infra/kind/tracking-backup"


def work():
    p = f.managed_destination(f.workspace() / "tracking-backup", "storage")
    p.mkdir(mode=0o700, exist_ok=True)
    a.require(p.stat().st_mode & 0o077 == 0, "Private backup workspace required")
    return p


def identifier(value):
    a.require(
        isinstance(value, str) and re.fullmatch(r"[a-f0-9]{32}", value), "Invalid capture ID"
    )
    return value


def directory(identity):
    return a.checked(work() / "captures" / identifier(identity))


def source_pins():
    paths = [
        *INFRA.rglob("*"),
        *(ROOT / "scripts/platform/tracking_backup").glob("*.py"),
        *(ROOT / "scripts/tracking").glob("*.py"),
        *(ROOT / "scripts/platform").glob("*.py"),
        ROOT / "scripts/fraud_portal/session_acceptance.py",
        ROOT / "scripts/product_session/controller.py",
        *(ROOT / "fraud_detection_mlops").rglob("*.py"),
        ROOT / "infra/tracking/requirements.txt",
    ]
    return {
        p.relative_to(ROOT).as_posix(): a.sha(p)
        for p in sorted(set(paths))
        if p.is_file() and "__pycache__" not in p.parts
    }


def committed():
    pins = source_pins()
    for name, digest in pins.items():
        got = subprocess.run(
            ["git", "show", "HEAD:" + name], cwd=ROOT, capture_output=True, check=False
        )
        a.require(
            got.returncode == 0 and shared.digest(got.stdout) == digest,
            "Commit reviewed backup implementation first: " + name,
        )
    return pins


def command(args, *, timeout=60, destination=None, input_path=None, limit=1024**2):
    return streams.run(
        [str(x) for x in args],
        time.monotonic() + timeout,
        destination=destination,
        input_path=input_path,
        limit=limit,
    )


def build():
    pins = committed()
    f.owner()
    path = work() / "image.json"
    if path.exists():
        return {"status": "backup_image_already_built", **image_record()}
    tag = "fraud-kind-tracking-backup:" + uuid4().hex
    command(
        ["docker", "build", "--file", INFRA / "Dockerfile", "--tag", tag, ROOT],
        timeout=900,
        limit=4 * 1024**2,
    )
    image = json.loads(command(["docker", "image", "inspect", tag]))[0]
    # Disposable target images are explicitly pinned; no serving image is rebuilt.
    for role in ("postgres", "rustfs"):
        command(["docker", "pull", f.images()[role]], timeout=300, limit=4 * 1024**2)
    a.require(source_pins() == pins, "Backup sources changed during build")
    record = {"image": tag, "image_id": image["Id"], "source_pins": pins}
    f.private_json(path, record)
    return {
        "status": "backup_image_built",
        "image": tag,
        "image_id": image["Id"],
        "source_modified": False,
    }


def image_record():
    value = a.document(work() / "image.json")
    a.require(
        value["source_pins"] == source_pins(),
        "Backup image sources changed; preserve image record",
    )
    actual = json.loads(command(["docker", "image", "inspect", value["image_id"]]))[0]
    a.require(actual["Id"] == value["image_id"], "Backup image identity differs")
    return value


@contextmanager
def forward(service, port):
    a.require(
        (service, port) in {("fraud-platform-postgres", 5432), ("fraud-platform-rustfs", 9000)},
        "Only reviewed storage forwards allowed",
    )
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
        "service/" + service,
        ":" + str(port),
    ]
    with subprocess.Popen(
        args, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    ) as process:
        ready = Queue(maxsize=1)

        def drain():
            for line in iter(process.stdout.readline, b""):
                found = re.search(rb"Forwarding from 127\.0\.0\.1:([0-9]+) -> ", line)
                if found and ready.empty():
                    ready.put_nowait(int(found[1]))

        thread = Thread(target=drain, daemon=True)
        thread.start()
        try:
            try:
                allocated = ready.get(timeout=30)
            except Empty as exc:
                raise ValueError("Storage forward did not become ready") from exc
            a.require(process.poll() is None, "Storage forward exited")
            yield allocated
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            thread.join(timeout=5)


def write_environment(path, values):
    a.require(
        all(
            isinstance(v, str) and not any(c in v for c in '\n\r\x00"$`\\ ,\t')
            for v in values.values()
        ),
        "Unsafe worker environment",
    )
    with a.checked(path).open("x") as stream:
        os.chmod(path, 0o600)
        stream.write("".join(k + "=" + v + "\n" for k, v in values.items()))


@contextmanager
def source_environment():
    own, foundation = tracking.credentials(), f.credentials()
    path = work() / (".source-" + uuid4().hex + ".env")
    with (
        forward("fraud-platform-postgres", 5432) as pg,
        forward("fraud-platform-rustfs", 9000) as s3,
    ):
        values = {
            "BACKUP_PG_HOST": "127.0.0.1",
            "BACKUP_PG_PORT": str(pg),
            "BACKUP_PG_PASSWORD": own["password"],
            "BACKUP_TRACKING_OWNER": own["owner_id"],
            "BACKUP_S3_ENDPOINT": "http://127.0.0.1:" + str(s3),
            "AWS_ACCESS_KEY_ID": foundation["rustfs-access-key"],
            "AWS_SECRET_ACCESS_KEY": foundation["rustfs-secret-key"],
            "AWS_EC2_METADATA_DISABLED": "true",
            "AWS_CONFIG_FILE": "/dev/null",
            "AWS_SHARED_CREDENTIALS_FILE": "/dev/null",
            "MLFLOW_DISABLE_TELEMETRY": "true",
            "DO_NOT_TRACK": "true",
        }
        try:
            write_environment(path, values)
            yield path
        finally:
            path.unlink(missing_ok=True)


def worker_result(raw):
    item = json.loads(raw)
    a.require(
        item.get("worker_sha256") == a.sha(ROOT / "scripts/platform/tracking_backup/worker.py"),
        "Worker code differs from reviewed source",
    )
    a.require(
        item.get("status") != "failed",
        "Backup worker failed: " + str(item.get("reason", "private details withheld")),
    )
    return item


def source_worker(action, environment, output=None):
    image = image_record()
    name = "fraud-backup-tool-" + uuid4().hex
    args = [
        "docker",
        "run",
        "--rm",
        "--pull",
        "never",
        "--name",
        name,
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        "64",
        "--memory",
        "512m",
        "--cpus",
        "1",
        "--network",
        "host",
        "--user",
        str(os.getuid()) + ":" + str(os.getgid()),
        "--tmpfs",
        "/tmp",
        "--env-file",
        environment,
    ]
    if output:
        args += ["--mount", "type=bind,src=" + str(a.checked(output)) + ",dst=/capture"]
    try:
        return worker_result(
            command(
                [
                    *args,
                    image["image_id"],
                    "python",
                    "-m",
                    "scripts.platform.tracking_backup.worker",
                    action,
                ],
                timeout=300,
                limit=4 * 1024**2,
            )
        )
    finally:
        # Only the just-created random tool name is eligible for cleanup.
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=15, check=False)


def snapshot(session_id=None):
    result = {
        "owner": operations.docker_owner(),
        "resources": operations.resources(),
        "active_sha256": operations.digest(operations.active_state()),
    }
    if session_id:
        from scripts.fraud_portal import session_acceptance
        from scripts.product_session import controller as sessions

        folder, _ = sessions.load(session_id)
        result["session_files"] = session_acceptance.source_files(folder)
    return result


def preflight(session_id=None):
    committed()
    f.owner()
    tracking.inventory()
    operations.no_jobs_or_bare_writers()
    before = snapshot(session_id)
    a.require(shutil.disk_usage(work()).free >= 2 * 1024**3, "At least 2 GiB free disk required")
    with source_environment() as env:
        source_worker("runtime-check", env)
        result = source_worker("preflight", env)
    a.require(snapshot(session_id) == before, "Source changed during preflight")
    return {
        **result,
        "source_unchanged": True,
        "restore_executed": False,
        "scope": "mlflow_kind_and_fraud_kind_mlflow_only_not_platform_backup",
    }


def model_control():
    from mlflow import MlflowClient

    from fraud_detection_mlops.serving.model_release import (
        ReleaseManifest,
        SmokeCase,
        checked_bytes,
    )

    active = operations.active_state()
    slot = active["slot"]
    release = active["plan"]["releases"][slot]
    folder = a.checked(Path(active["paths"][slot]))
    manifest = ReleaseManifest.model_validate_json(
        checked_bytes(folder / "manifest.json", release["manifest_sha256"])
    )
    smoke = SmokeCase.model_validate_json(
        checked_bytes(folder / manifest.smoke_file, manifest.smoke_sha256)
    )
    a.require(
        manifest.model_dump() == release["manifest"]
        and manifest.model_sha256 == active["model_sha256"],
        "Active release control differs",
    )
    with tracking.port_forward() as uri:
        client = MlflowClient(tracking_uri=uri, registry_uri=uri)
        version = client.get_model_version_by_alias("fraud-detector", "champion")
        a.require(
            str(version.version) == str(active["version"])
            and version.run_id == manifest.source["mlflow_run_id"]
            and version.tags.get("fraud.model_sha256") == manifest.model_sha256
            and re.fullmatch(r"models:/m-[a-f0-9]+", version.source),
            "Champion control does not match active release",
        )
    frame = smoke.request.feature_frame()
    return {
        "registered_model": "fraud-detector",
        "registry_version": str(version.version),
        "model_uri": version.source,
        "run_id": version.run_id,
        "model_sha256": manifest.model_sha256,
        "serving_release_id": manifest.serving_release_id,
        "columns": list(frame.columns),
        "rows": frame.to_numpy().tolist(),
        "scores": [smoke.expected_score],
        "expected_scores_source": "accepted_release_smoke_not_recomputed",
        "source_pins": {"manifest": release["manifest_sha256"], "smoke": manifest.smoke_sha256},
    }


def capture(no_other_writers, session_id=None):
    a.require(no_other_writers, "Declare --no-other-writers")
    pins = committed()
    preflight(session_id)
    identity = uuid4().hex
    output = directory(identity)
    output.mkdir(mode=0o700, parents=True)
    report = {
        "schema": a.SCHEMA,
        "status": "failed",
        "capture_id": identity,
        "source_recovered": False,
        "postgres_backup_restore_validated": False,
    }
    paused = False
    before = snapshot(session_id)
    a.require(
        before["resources"]["Deployment/fraud-tracking"]["replicas"] == 1,
        "Source tracking must have one replica",
    )
    plan = {"resources": before["resources"]}
    shared.save(
        work() / ("capture-" + identity + "-plan.json"),
        {
            "schema": a.SCHEMA,
            "capture_id": identity,
            "before": before,
            "session_id": session_id,
            "source_pins": pins,
        },
    )
    try:
        shared.save(output / "control.json", model_control())
        with source_environment() as env:
            paused = True  # Also recover when scaling is interrupted.
            operations.change_replica(plan, "deployment", "fraud-tracking", 0)
            source_worker("inventory", env, output)
            argv = [
                "kubectl",
                "--kubeconfig",
                f.workspace() / "kubeconfig",
                "--context",
                f.CONTEXT,
                "--namespace",
                f.NAMESPACE,
                "exec",
                "statefulset/fraud-platform-postgres",
                "--",
                "pg_dump",
                "-U",
                a.ROLE,
                "-d",
                a.DATABASE,
                "--format=custom",
                "--lock-wait-timeout=5000",
                "--no-password",
            ]
            command(argv, timeout=180, destination=output / "postgres.dump", limit=shared.MAX_DUMP)
            toc = command(
                [
                    "kubectl",
                    "--kubeconfig",
                    f.workspace() / "kubeconfig",
                    "--context",
                    f.CONTEXT,
                    "--namespace",
                    f.NAMESPACE,
                    "exec",
                    "-i",
                    "statefulset/fraud-platform-postgres",
                    "--",
                    "pg_restore",
                    "--list",
                ],
                input_path=output / "postgres.dump",
                limit=4 * 1024**2,
            )
            a.require(b"TABLE DATA" in toc and b"alembic_version" in toc, "Dump TOC incomplete")
            report["objects"] = source_worker("objects", env, output)
        operations.change_replica(plan, "deployment", "fraud-tracking", 1)
        f.kube("rollout", "status", "deployment/fraud-tracking", "--timeout=120s", timeout=130)
        tracking.inventory()
        a.require(
            snapshot(session_id) == before and source_pins() == pins,
            "Source ownership/resources/identity changed during capture",
        )
        paused = False
        report.update(source_recovered=True)
        control = a.document(output / "control.json")
        manifest = {
            **report,
            "status": "captured_verified",
            "database": a.DATABASE,
            "bucket": a.BUCKET,
            "tracking_owner_id": tracking.credentials()["owner_id"],
            "model_sha256": control["model_sha256"],
            "source": before,
            "session_id": session_id,
            "source_pins": pins,
        }
        digest = a.seal(output, manifest)
        report.update(
            status="captured_verified",
            manifest_sha256=digest,
            directory=str(output.relative_to(ROOT)),
        )
        return report
    finally:
        try:
            if paused:
                operations.change_replica(plan, "deployment", "fraud-tracking", 1)
                f.kube(
                    "rollout", "status", "deployment/fraud-tracking", "--timeout=120s", timeout=130
                )
                report["source_recovered"] = snapshot(session_id) == before
        finally:
            shared.save(work() / ("capture-" + identity + ".json"), report)


def recover(identity):
    """Resume the original MLflow only; an interrupted capture is never accepted."""
    pins = committed()
    identity = identifier(identity)
    plan = a.document(work() / ("capture-" + identity + "-plan.json"))
    a.require(
        plan["schema"] == a.SCHEMA
        and plan["capture_id"] == identity
        and plan["source_pins"] == pins,
        "Recovery plan differs",
    )
    before = plan["before"]
    current = snapshot(plan["session_id"])
    key = "Deployment/fraud-tracking"
    a.require(current["resources"][key]["replicas"] in (0, 1), "Unexpected source replicas")
    current["resources"][key]["replicas"] = before["resources"][key]["replicas"]
    a.require(
        current == before and before["resources"][key]["replicas"] == 1,
        "Protected source changed; inspect manually",
    )
    operations.change_replica(
        {"resources": before["resources"]}, "deployment", "fraud-tracking", 1
    )
    f.kube("rollout", "status", "deployment/fraud-tracking", "--timeout=120s", timeout=130)
    tracking.inventory()
    a.require(snapshot(plan["session_id"]) == before, "Source recovery readback differs")
    report = {
        "status": "original_tracking_resumed",
        "capture_id": identity,
        "source_recovered": True,
        "backup_accepted": False,
    }
    shared.save(work() / ("recovery-" + uuid4().hex + ".json"), report)
    return report


def target_command(project, env, *args):
    a.require(
        re.fullmatch(r"fraud-kind-restore-[a-f0-9]{12}", project), "Unmanaged restore project"
    )
    return [
        "docker",
        "compose",
        "--project-name",
        project,
        "--env-file",
        str(env),
        "-f",
        str(INFRA / "restore.compose.yaml"),
        *args,
    ]


def target_identity(project, env, service):
    ids = command(target_command(project, env, "ps", "-a", "-q", service)).decode().strip()
    a.require(re.fullmatch(r"[a-f0-9]{12,64}", ids), "One target container required")
    row = json.loads(command(["docker", "inspect", ids]))[0]
    a.require(
        row["Config"]["Labels"].get("com.docker.compose.project") == project
        and row["Config"]["Labels"].get("com.docker.compose.service") == service,
        "Foreign target container",
    )
    a.require(
        set(row["NetworkSettings"]["Networks"]) == {project + "_default"}, "Target network differs"
    )
    a.require(
        row["State"]["Running"] and row["State"].get("Health", {}).get("Status") == "healthy",
        "Target not healthy",
    )
    mounts = [m for m in row["Mounts"] if m["Type"] != "tmpfs"]
    if service in {"postgres", "rustfs"}:
        volume = project + "_" + service + "-data"
        volumes = [m for m in mounts if m["Type"] == "volume"]
        a.require(len(volumes) == 1 and volumes[0]["Name"] == volume, "Foreign target volume")
        allowed = [
            {
                "Type": "volume",
                "Destination": "/var/lib/postgresql/data" if service == "postgres" else "/data",
            }
        ]
        if service == "postgres":
            binds = [m for m in mounts if m["Type"] == "bind"]
            a.require(
                len(binds) == 1
                and binds[0]["Source"] == str(INFRA / "init-postgres.sh")
                and not binds[0]["RW"]
                and binds[0]["Destination"] == "/docker-entrypoint-initdb.d/10-mlflow.sh",
                "Foreign target init bind",
            )
            a.require(len(mounts) == 2, "Unexpected PostgreSQL mount")
        else:
            a.require(len(mounts) == 1, "Unexpected RustFS mount")
        a.require(
            volumes[0]["Destination"] == allowed[0]["Destination"],
            "Target volume destination differs",
        )
        pinned = json.loads(command(["docker", "image", "inspect", f.images()[service]]))[0]["Id"]
    else:
        a.require(service == "mlflow" and not mounts, "Unexpected MLflow mount")
        pinned = image_record()["image_id"]
    a.require(row["Image"] == pinned, "Target image differs")
    return {
        "container_id": row["Id"],
        "image_id": row["Image"],
        "project": project,
        "network": project + "_default",
    }


def restore(identity, digest, no_other_writers):
    a.require(no_other_writers, "Declare --no-other-writers")
    committed()
    output = directory(identity)
    manifest = a.verify(output, digest)
    image = image_record()
    before = snapshot(manifest.get("session_id"))
    project = "fraud-kind-restore-" + uuid4().hex[:12]
    folder = work() / "restores" / project
    folder.mkdir(mode=0o700, parents=True)
    proof = folder / "proof"
    proof.mkdir(mode=0o700)
    env = folder / "target.env"
    values = {
        "TARGET_PG_PASSWORD": secrets.token_hex(24),
        "TARGET_PG_ADMIN_PASSWORD": secrets.token_hex(24),
        "TARGET_S3_ACCESS_KEY": secrets.token_hex(10).upper(),
        "TARGET_S3_SECRET_KEY": secrets.token_hex(24),
        "CAPTURE_DIRECTORY": str(output),
        "PROOF_DIRECTORY": str(proof),
        "BACKUP_MANIFEST_SHA256": digest,
        "BACKUP_CAPTURE_ID": identifier(identity),
        "BACKUP_IMAGE": image["image_id"],
        "RESTORE_UID": str(os.getuid()),
        "RESTORE_GID": str(os.getgid()),
    }
    write_environment(env, values)
    report = {
        "schema": "fraud_kind_tracking_restore_v1",
        "status": "failed",
        "phase": "target_absence_check",
        "project": project,
        "capture_id": identity,
        "manifest_sha256": digest,
        "postgres_backup_restore_validated": False,
        "source_modified": None,
        "fit_executed": False,
        "platform_backup_restore_validated": False,
    }
    created = False

    def compose(*args, **kwargs):
        return command(target_command(project, env, *args), **kwargs)

    def worker(service, action):
        return worker_result(
            compose("run", "--rm", "--no-deps", service, action, timeout=300, limit=4 * 1024**2)
        )

    try:
        for kind in ("container", "volume", "network"):
            args = ["docker", "ps", "-aq"] if kind == "container" else ["docker", kind, "ls", "-q"]
            a.require(
                not command(
                    [*args, "--filter", "label=com.docker.compose.project=" + project]
                ).strip(),
                "Target already exists",
            )
        created = True
        report["phase"] = "client_runtime_check"
        worker("client", "runtime-check")
        report["phase"] = "target_storage_start"
        compose("up", "-d", "--wait", "--wait-timeout", "120", "postgres", "rustfs", timeout=180)
        report["phase"] = "target_storage_identity"
        targets = {s: target_identity(project, env, s) for s in ("postgres", "rustfs")}
        node = before["owner"]["container_id"]
        a.require(
            all(x["container_id"] != node for x in targets.values()), "Target/source overlap"
        )
        report["phase"] = "postgres_restore"
        command(
            [
                "docker",
                "exec",
                "-i",
                targets["postgres"]["container_id"],
                "pg_restore",
                "--exit-on-error",
                "--single-transaction",
                "-U",
                "tracking_admin",
                "-d",
                a.DATABASE,
                "--no-password",
            ],
            input_path=output / "postgres.dump",
            timeout=180,
        )
        report["phase"] = "s3_seed_and_data_verification"
        report["restore"] = worker("seed", "seed")
        a.require(
            report["restore"]["status"] == "restored_data_verified", "Restored data not verified"
        )
        report["phase"] = "target_mlflow_start"
        compose("up", "-d", "--wait", "--wait-timeout", "120", "mlflow", timeout=180)
        targets["mlflow"] = target_identity(project, env, "mlflow")
        report["target_resources"] = targets
        report["phase"] = "model_client_verification"
        report["client"] = worker("client", "client")
        a.require(
            report["client"]["status"] == "target_client_verified"
            and report["client"]["model_reload_validated"],
            "Target model client failed",
        )
        report["phase"] = "target_final_readback"
        compose("stop", "--timeout", "15", "mlflow")
        report["after_readback"] = worker("seed", "verify")
        a.require(
            report["after_readback"]["status"] == "restored_data_verified",
            "Final target data differs",
        )
        report["phase"] = "source_and_archive_readback"
        a.verify(output, digest)
        report["source_unchanged"] = snapshot(manifest.get("session_id")) == before
        report["source_modified"] = not report["source_unchanged"]
        a.require(report["source_unchanged"], "Original source changed during isolated restore")
        report.update(
            status="verified",
            postgres_backup_restore_validated=True,
            source_unchanged=True,
            capture_unchanged=True,
        )
        return report
    except BaseException as exc:
        report.update(failed_phase=report["phase"], error_type=type(exc).__name__)
        raise
    finally:
        try:
            if created:
                # Cleanup is restricted to this newly allocated Compose project.
                compose("down", "--volumes", "--remove-orphans", timeout=120)
                for kind in ("container", "volume", "network"):
                    args = (
                        ["docker", "ps", "-aq"]
                        if kind == "container"
                        else ["docker", kind, "ls", "-q"]
                    )
                    a.require(
                        not command(
                            [*args, "--filter", "label=com.docker.compose.project=" + project]
                        ).strip(),
                        "Disposable target cleanup incomplete",
                    )
                report["temporary_resources_removed"] = True
                if report["status"] == "verified":
                    report["phase"] = "completed"
        except BaseException:
            report.update(
                status="failed",
                failed_phase="cleanup",
                postgres_backup_restore_validated=False,
                temporary_resources_removed=False,
            )
            raise
        finally:
            env.unlink(missing_ok=True)
            shared.save(work() / (project + ".json"), report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=("build", "preflight", "capture", "verify", "restore", "recover")
    )
    parser.add_argument("--capture-id")
    parser.add_argument("--expected-manifest-sha256")
    parser.add_argument("--session-id")
    parser.add_argument("--no-other-writers", action="store_true")
    args = parser.parse_args()
    try:
        with f.exclusive(), streams.recover_on_term():
            if args.action == "build":
                result = build()
            elif args.action == "preflight":
                result = preflight(args.session_id)
            elif args.action == "capture":
                result = capture(args.no_other_writers, args.session_id)
            elif args.action == "recover":
                result = recover(args.capture_id)
            elif args.action == "verify":
                m = a.verify(directory(args.capture_id), args.expected_manifest_sha256)
                result = {
                    "status": "backup_files_verified",
                    "capture_id": m["capture_id"],
                    "postgres_backup_restore_validated": False,
                }
            else:
                result = restore(
                    args.capture_id, args.expected_manifest_sha256, args.no_other_writers
                )
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as exc:
        parser.exit(
            1,
            "Tracking backup stopped ("
            + type(exc).__name__
            + "); private details withheld. Preserve receipts and inspect source status.\n",
        )
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
