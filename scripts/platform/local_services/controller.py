"""Start, inspect and stop owned local accesses with nohup and private logs."""

import argparse
from collections import deque
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from uuid import uuid4

from scripts.platform.local_services import processes, services

ROOT = services.ROOT


def folder(service):
    from fraud_detection_mlops.storage.managed_paths import managed_destination

    if service not in services.HEALTH:
        raise ValueError("Unknown local access service")
    return managed_destination(
        ROOT / "data/storage/kind-platform/local-access" / service, "storage"
    )


def private(path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.stat().st_mode & 0o077:
        raise ValueError("Local access workspace must be private")


@contextmanager
def locked(service):
    path = folder(service)
    private(path)
    lock = path / "operation.lock"
    if lock.is_symlink():
        raise ValueError("Symlink in access lock")
    with lock.open("a") as stream:
        os.chmod(lock, 0o600)
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield path


def write(path, record):
    if path.is_symlink():
        raise ValueError("Symlink in access receipt")
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".access-")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(record, stream, indent=2, allow_nan=False)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def read(service):
    path = folder(service) / "current.json"
    if path.is_symlink():
        raise ValueError("Symlink in access receipt")
    if not path.exists():
        return None
    if path.stat().st_mode & 0o077 or path.stat().st_size > 65536:
        raise ValueError("Invalid private access receipt")
    value = json.loads(path.read_bytes())
    if (
        value["schema"] != "fraud_local_access_v1"
        or value["service"] != service
        or value["root"] != str(ROOT)
        or not re.fullmatch(r"[0-9a-f]{32}\.log", Path(value["log"]).name)
        or Path(value["log"]).parent != folder(service)
        or value["pid"] != value["process"]["pid"]
        or value["process"]["group"] != value["pid"]
    ):
        raise ValueError("Foreign or malformed access receipt")
    if Path(value["log"]).is_symlink():
        raise ValueError("Symlink in access log")
    return value


def status(service):
    record = read(service)
    if record is None:
        return {"service": service, "status": "not_started"}
    alive = processes.matches(record)
    ready = alive and bool(record.get("url")) and services.probe(service, record["url"])
    return {
        "service": service,
        "status": "ready" if ready else "unreachable" if alive else "stopped_or_stale",
        "pid": record["pid"],
        "url": record.get("url"),
        "log": record["log"],
    }


def start(service, port=None, portal_id="investigation-v1", timeout=120):
    argv = services.command(service, port, portal_id)
    with locked(service) as path:
        existing = read(service)
        if existing and processes.matches(existing):
            if existing["process"]["argv"] != argv:
                raise ValueError("Access has different parameters; stop it explicitly first")
            result = status(service)
            if result["status"] != "ready":
                raise ValueError("Owned access is unreachable; inspect logs and stop/start it")
            return {**result, "status": "already_running"}
        services.free_port(service, port)
        nohup = shutil.which("nohup")
        if nohup is None:
            raise ValueError("nohup is required")
        log = path / (uuid4().hex + ".log")
        fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        allowed = {"PATH", "HOME", "LANG", "LC_ALL", "TZ", "TMPDIR", "XDG_CACHE_HOME"}
        env = {k: v for k, v in os.environ.items() if k in allowed}
        env.update(MLFLOW_DISABLE_TELEMETRY="true", DO_NOT_TRACK="true", PYTHONUNBUFFERED="1")
        with os.fdopen(fd, "wb") as output:
            process = subprocess.Popen(
                [nohup, *argv],
                cwd=ROOT,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        record = None
        try:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                actual = processes.identity(process.pid)
                if actual and actual["argv"] == argv:
                    record = {
                        "schema": "fraud_local_access_v1",
                        "service": service,
                        "root": str(ROOT),
                        "pid": process.pid,
                        "process": actual,
                        "log": str(log),
                        "url": None,
                    }
                    write(path / "current.json", record)
                    break
                if process.poll() is not None:
                    raise ValueError("Access exited during launch; inspect " + str(log))
                time.sleep(0.05)
            if record is None:
                raise ValueError("Access process identity not established; inspect " + str(log))
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise ValueError("Access exited before readiness; inspect " + str(log))
                text = log.read_text(errors="replace")[-65536:]
                urls = re.findall(r"http://127\.0\.0\.1:[0-9]+", text)
                if urls and services.probe(service, urls[0]):
                    record["url"] = urls[0]
                    write(path / "current.json", record)
                    result = status(service)
                    if result["status"] != "ready":
                        raise ValueError("Access lost readiness; inspect " + str(log))
                    return result
                time.sleep(0.2)
            raise ValueError("Access readiness timed out; inspect " + str(log))
        except BaseException:
            if record is not None:
                processes.stop(record, timeout=1)
            else:
                # This Popen is the child just created here, with its own session.
                try:
                    os.killpg(process.pid, 15)
                except ProcessLookupError:
                    pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            raise


def stop(service):
    with locked(service):
        record = read(service)
        result = "not_started" if record is None else processes.stop(record)
        return {"service": service, "status": result, "log": record["log"] if record else None}


def logs(service, lines):
    record = read(service)
    if record is None:
        raise ValueError("No access log for this service")
    with Path(record["log"]).open("rb") as stream:
        stream.seek(max(0, os.fstat(stream.fileno()).st_size - 65536))
        return "".join(
            deque(stream.read().decode(errors="replace").splitlines(True), maxlen=lines)
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "stop", "status", "logs", "up", "stop-all"))
    parser.add_argument("--service", choices=tuple(services.HEALTH), default="api")
    parser.add_argument("--port", type=int)
    parser.add_argument("--portal-id", default="investigation-v1")
    parser.add_argument("--lines", type=int, default=60)
    args = parser.parse_args()
    try:
        if not 1 <= args.lines <= 1000:
            raise ValueError("Use 1..1000 log lines")
        if args.action == "logs":
            print(logs(args.service, args.lines), end="")
            return
        if args.action in {"status", "stop-all", "up"}:
            names = services.COMMON if args.action == "up" else services.HEALTH
            action = {"status": status, "stop-all": stop, "up": start}[args.action]
            for name in names:
                result = (
                    action(name, portal_id=args.portal_id) if args.action == "up" else action(name)
                )
                print(json.dumps(result), flush=True)
        else:
            result = (
                start(args.service, args.port, args.portal_id)
                if args.action == "start"
                else stop(args.service)
            )
            print(json.dumps(result, indent=2), flush=True)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        parser.exit(1, "Local access stopped: " + str(exc) + "\n")


if __name__ == "__main__":
    main()
