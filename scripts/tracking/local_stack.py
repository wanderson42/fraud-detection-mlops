"""Operate only the isolated tracking lab; never migrate or open historical stores."""

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
from tempfile import TemporaryDirectory
import time
from urllib.request import urlopen
from uuid import uuid4

from scripts.tracking.probe import SCOPE, payload, require

ROOT = Path(__file__).resolve().parents[2]
PROJECT = "fraud-tracking-lab"
VOLUME = PROJECT + "_postgres-data"


def checked(path):
    path = path.absolute()
    require(path == path.resolve(), "Symlinks or parent traversal in lab paths are not accepted")
    return path


def workspace(root):
    directory = checked(root / "data/storage/tracking-lab")
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def initialize(root=ROOT):
    checked(root / "infra/observability/.env")
    require((root / "infra/observability/.env").is_file(), "Initialize observability first")
    directory = workspace(root)
    destination = checked(root / "infra/tracking/.env")
    # Exclusive create preserves credentials from earlier runs.
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        for name in ["TRACKING_PG_PASSWORD", "TRACKING_PG_ADMIN_PASSWORD"]:
            stream.write(name + '="' + secrets.token_hex(24) + '"\n')
    return {
        "status": "initialized",
        "environment_file": str(destination),
        "workspace": str(directory),
    }


def compose(root, *arguments):
    for relative in ["infra/observability/.env", "infra/tracking/.env"]:
        require(
            checked(root / relative).is_file(), "Required environment file missing; run init first"
        )
    return [
        "docker",
        "compose",
        "--project-name",
        PROJECT,
        "--env-file",
        str(root / "infra/observability/.env"),
        "--env-file",
        str(root / "infra/tracking/.env"),
        "-f",
        str(root / "infra/tracking/compose.yaml"),
        *arguments,
    ]


def command(arguments, *, capture=False, timeout=180):
    try:
        return subprocess.run(
            arguments, capture_output=capture, text=True, check=True, timeout=timeout
        ).stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        # Captured worker/inspect output may contain credentials; don't propagate it.
        raise RuntimeError(
            "Tracking lab command failed; inspect local service status/logs"
        ) from exc


def up(root=ROOT):
    workspace(root)
    with urlopen("http://127.0.0.1:19000/health/ready", timeout=5) as response:
        require(response.status == 200, "RustFS must be ready")
    command(compose(root, "build", "mlflow"), timeout=900)
    command(compose(root, "run", "--rm", "--no-deps", "storage-check", "prepare"))
    command(compose(root, "up", "-d", "--wait", "--wait-timeout", "120", "postgres", "mlflow"))


def inspect_service(root, service):
    identity = command(compose(root, "ps", "-q", service), capture=True).strip()
    require(
        bool(re.fullmatch(r"[0-9a-f]{12,64}", identity)),
        "One lab container per service is required",
    )
    rows = json.loads(command(["docker", "inspect", identity], capture=True))
    require(len(rows) == 1, "Ambiguous lab container")
    row = rows[0]
    labels = row["Config"]["Labels"]
    require(
        labels.get("com.docker.compose.project") == PROJECT
        and labels.get("com.docker.compose.service") == service,
        "Refuse operation on a different Compose project/service",
    )
    require(
        row["State"]["Running"] and row["State"].get("Health", {}).get("Status") == "healthy",
        "Lab service must be healthy",
    )
    mounts = [m for m in row["Mounts"] if m["Type"] != "tmpfs"]
    environment = dict(item.split("=", 1) for item in row["Config"]["Env"] if "=" in item)
    if service == "postgres":
        volumes = [mount for mount in mounts if mount["Type"] == "volume"]
        binds = [mount for mount in mounts if mount["Type"] == "bind"]
        require(
            len(volumes) == 1
            and volumes[0]["Name"] == VOLUME
            and volumes[0]["Destination"] == "/var/lib/postgresql/data",
            "Unexpected PostgreSQL persistence mount",
        )
        require(
            len(mounts) == 2
            and len(binds) == 1
            and Path(binds[0]["Source"]) == checked(root / "infra/tracking/init-postgres.sh")
            and binds[0]["Destination"] == "/docker-entrypoint-initdb.d/10-mlflow.sh"
            and not binds[0]["RW"],
            "Unexpected PostgreSQL bootstrap mount",
        )
        require(environment.get("POSTGRES_DB") == "mlflow_lab", "Wrong PostgreSQL database")
    else:
        require(not mounts, "MLflow must not mount local tracking/artifact directories")
        backend = environment.get("MLFLOW_BACKEND_STORE_URI", "")
        require(
            backend.startswith("postgresql+psycopg://mlflow:")
            and backend.endswith("@postgres:5432/mlflow_lab"),
            "PostgreSQL backend is required",
        )
        require(
            environment.get("MLFLOW_S3_ENDPOINT_URL") == "http://rustfs:9000",
            "Wrong object endpoint",
        )
        require(
            environment.get("MLFLOW_SERVER_ENABLE_JOB_EXECUTION") == "false",
            "Unexpected server jobs",
        )
        args = row["Config"]["Cmd"]
        require(
            "--artifacts-destination" in args
            and args[args.index("--artifacts-destination") + 1] == "s3://fraud-tracking-lab/v1",
            "Wrong artifact destination",
        )
        require(
            "--no-serve-artifacts" not in args and "--default-artifact-root" not in args,
            "Artifact proxy configuration differs",
        )
    return {
        "container_id": identity,
        "image_id": row["Image"],
        "postgres_volume": VOLUME if service == "postgres" else None,
    }


def worker(root, service, action, record=None):
    args = ["run", "--rm", "--no-deps", service, action]
    if record is not None:
        args += ["--record", "/work/" + record.relative_to(workspace(root)).as_posix()]
    raw = command(compose(root, *args), capture=True)
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Worker did not return a JSON result") from exc


def sql_readback(root, record):
    run_id = record["run_id"]
    require(bool(re.fullmatch(r"[0-9a-f]{32}", run_id)), "Invalid run ID for SQL verification")
    query = (
        "SELECT json_build_object('role_is_superuser', (SELECT rolsuper FROM pg_roles WHERE rolname=current_user), 'runs', (SELECT count(*) FROM runs WHERE run_uuid='"
        + run_id
        + "'),"
        "'params', (SELECT count(*) FROM params WHERE run_uuid='" + run_id + "'),"
        "'metrics', (SELECT count(*) FROM metrics WHERE run_uuid='" + run_id + "'),"
        "'artifact_uri', (SELECT artifact_uri FROM runs WHERE run_uuid='" + run_id + "'),"
        "'probe_id', (SELECT value FROM params WHERE run_uuid='"
        + run_id
        + "' AND key='probe_id'));"
    )
    result = json.loads(
        command(
            compose(
                root,
                "exec",
                "-T",
                "postgres",
                "psql",
                "-X",
                "-q",
                "-t",
                "-A",
                "-v",
                "ON_ERROR_STOP=1",
                "-U",
                "mlflow",
                "-d",
                "mlflow_lab",
                "-c",
                "BEGIN READ ONLY; " + query + " COMMIT;",
            ),
            capture=True,
        )
    )
    require(
        result
        == {
            "role_is_superuser": False,
            "runs": 1,
            "params": 1,
            "metrics": 2,
            "artifact_uri": record["artifact_uri"],
            "probe_id": record["probe_id"],
        },
        "Independent PostgreSQL readback differs",
    )
    return result


def save_json(path, data):
    path = checked(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(data, stream, indent=2, allow_nan=False)
        stream.write("\n")


@contextmanager
def exclusive(root):
    path = checked(workspace(root) / ".rehearsal.lock")
    with path.open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("Another tracking rehearsal is running") from exc
        yield


def rehearse(root=ROOT):
    with exclusive(root):
        return _rehearse(root)


def _rehearse(root):
    start = time.monotonic()
    report = {
        "controller_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "schema": "fraud_tracking_rehearsal_v1",
        "scope": SCOPE,
        "status": "running",
        "historical_stores_mounted": False,
        "migration_implemented": False,
        "primary_storage_migrated": False,
        "production_promotion": False,
        "rustfs_restarted": False,
    }
    receipt = workspace(root) / "receipts" / ("rehearsal-" + uuid4().hex + ".json")
    recreating = False
    try:
        before = {name: inspect_service(root, name) for name in ["postgres", "mlflow"]}
        print("lab_baseline_verified", flush=True)
        folder = checked(workspace(root) / "rehearsals")
        folder.mkdir(exist_ok=True)
        with TemporaryDirectory(prefix="probe-", dir=folder) as temporary:
            record = worker(root, "probe", "write")
            payload(record["probe_id"])
            require(
                record.get("worker_code_sha256")
                == hashlib.sha256((ROOT / "scripts/tracking/probe.py").read_bytes()).hexdigest(),
                "Stale probe image; rebuild the lab runtime",
            )
            require(
                record.get("metadata_verified") is True
                and record.get("artifact_verified") is True,
                "Initial proxy probe failed",
            )
            report["probe"] = record
            path = Path(temporary) / "record.json"
            save_json(path, record)
            initial_sql = sql_readback(root, record)
            initial_s3 = worker(root, "storage-check", "verify-object", path)
            require(
                initial_s3.get("s3_object_verified") is True
                and initial_s3.get("artifact_sha256") == record["artifact_sha256"],
                "Initial S3 readback failed",
            )
            print("postgres_and_s3_write_verified", flush=True)
            recreating = True
            command(compose(root, "stop", "mlflow"))
            for service in ["postgres", "mlflow"]:
                command(
                    compose(
                        root,
                        "up",
                        "-d",
                        "--no-deps",
                        "--force-recreate",
                        "--wait",
                        "--wait-timeout",
                        "120",
                        service,
                    )
                )
                print(service + "_recreated", flush=True)
            after = {name: inspect_service(root, name) for name in ["postgres", "mlflow"]}
            require(
                all(
                    before[name]["container_id"] != after[name]["container_id"] for name in before
                ),
                "Containers were not recreated",
            )
            require(
                all(before[name]["image_id"] == after[name]["image_id"] for name in before),
                "Container image changed during rehearsal",
            )
            require(
                before["postgres"]["postgres_volume"] == after["postgres"]["postgres_volume"],
                "PostgreSQL volume changed",
            )
            restored = worker(root, "probe", "read", path)
            require(restored == record, "Proxy state differs after recreation")
            require(
                sql_readback(root, record) == initial_sql,
                "PostgreSQL contents differ after recreation",
            )
            require(
                worker(root, "storage-check", "verify-object", path) == initial_s3,
                "S3 object differs after recreation",
            )
            report.update(
                status="success",
                containers_before=before,
                containers_after=after,
                postgres_verified=True,
                s3_verified=True,
                proxy_verified=True,
                object=initial_s3,
                container_recreation_validated=True,
            )
            print("persistence_verified", flush=True)
    except BaseException as exc:
        report.update(status="failed", error_type=type(exc).__name__)
        if recreating:
            try:
                command(
                    compose(
                        root,
                        "up",
                        "-d",
                        "--no-deps",
                        "--wait",
                        "--wait-timeout",
                        "120",
                        "postgres",
                        "mlflow",
                    )
                )
                report["service_recovery_attempt"] = "completed"
            except ValueError, OSError, RuntimeError:
                report["service_recovery_attempt"] = "failed"
        raise
    finally:
        report["elapsed_seconds"] = time.monotonic() - start
        save_json(receipt, report)
        print("Relatório: " + str(receipt), flush=True)
    return {**report, "report": str(receipt)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["init", "up", "status", "down"])
    args = parser.parse_args()
    try:
        if args.action == "init":
            print(json.dumps(initialize(), indent=2))
        elif args.action == "up":
            up()
        else:
            command(compose(ROOT, *(["ps"] if args.action == "status" else ["down"])))
    except (ValueError, OSError, RuntimeError) as exc:
        parser.exit(1, f"Tracking lab stopped: {exc}\n")


if __name__ == "__main__":
    main()
