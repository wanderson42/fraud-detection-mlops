"""Owned SSH tunnels for Linux clients, using Python 3.10+ and OpenSSH only."""

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import signal
import socket
import subprocess
import tempfile
import time
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
HEALTH = {
    "portal": "/_stcore/health",
    "grafana": "/api/health",
    "mlflow": "/health",
    "api": "/ready",
    "prometheus": "/-/ready",
}


def private_directory(path):
    path = Path(path).absolute()
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError("Symlink in client state path")
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    if path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077:
        raise ValueError("Client state directory must belong to you with mode 0700")
    return path


def workspace(profile):
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,48}", profile):
        raise ValueError("Invalid access profile")
    base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
    key = hashlib.sha256(str(ROOT).encode()).hexdigest()[:16]
    return private_directory(base / "fraud-vm-access" / key / profile)


@contextmanager
def locked(path):
    fd = os.open(path / "operation.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def write(path, value):
    fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def identity(pid):
    try:
        path = Path("/proc") / str(pid)
        stat = (path / "stat").read_text().rsplit(") ", 1)[1].split()
        if stat[0] == "Z":
            return None
        return {
            "pid": pid,
            "start_ticks": stat[19],
            "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
            "argv": (path / "cmdline").read_bytes().decode().rstrip("\0").split("\0"),
            "cwd": str((path / "cwd").resolve(strict=True)),
        }
    except (OSError, ValueError, IndexError) as _exc:
        return None


def alive(record):
    return identity(record["process"]["pid"]) == record["process"]


def stop(record):
    # Signal only the exact SSH process identified in this boot. Never kill a port owner.
    if not alive(record):
        return
    pid = record["process"]["pid"]
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 3
    while alive(record) and time.monotonic() < deadline:
        time.sleep(0.05)
    if alive(record):
        os.kill(pid, signal.SIGKILL)


def read(path):
    target = path / "current.json"
    if target.is_symlink():
        raise ValueError("Symlink in client receipt")
    if not target.exists():
        return None
    if target.stat().st_mode & 0o077 or target.stat().st_size > 65536:
        raise ValueError("Invalid private client receipt")
    record = json.loads(target.read_bytes())
    if record["schema"] != "fraud_vm_access_v1" or record["root"] != str(ROOT):
        raise ValueError("Foreign client access receipt")
    for item in record["tunnels"]:
        if item["service"] not in HEALTH or item["process"]["cwd"] != str(ROOT):
            raise ValueError("Foreign tunnel receipt")
    return record


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def healthy(service, port):
    try:
        url = f"http://127.0.0.1:{port}" + HEALTH[service]
        with build_opener(ProxyHandler({}), NoRedirect()).open(url, timeout=1) as response:
            return response.status == 200
    except (OSError, HTTPError) as _exc:
        return False


def free_port(port=0):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", port))
        return sock.getsockname()[1]


def owns_listener(pid, port):
    """Read back the SSH-owned socket; another healthy port owner is not our tunnel."""
    try:
        inodes = {
            row.split()[9]
            for row in Path("/proc/net/tcp").read_text().splitlines()[1:]
            if row.split()[1] == f"0100007F:{port:04X}" and row.split()[3] == "0A"
        }
        return any(
            os.readlink(fd) in {f"socket:[{inode}]" for inode in inodes}
            for fd in (Path("/proc") / str(pid) / "fd").iterdir()
        )
    except OSError:
        return False


def assignments(values, selected):
    result = {}
    for value in values:
        service, sep, raw = value.partition("=")
        if not sep or service not in selected or service in result:
            raise ValueError("Use one selected service=PORT per port override")
        port = int(raw)
        if not 1024 <= port <= 65535:
            raise ValueError("Ports must be in 1024..65535")
        result[service] = port
    return result


def ssh_base(host, port, user):
    if not re.fullmatch(r"[a-zA-Z0-9_.:-]+", host) or host.startswith("-"):
        raise ValueError("Use a hostname, address or SSH config alias")
    if user and not re.fullmatch(r"[a-zA-Z0-9_.-]+", user):
        raise ValueError("Invalid SSH user")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("Invalid SSH port")
    # No shell, credential collection, host-key bypass or connection multiplex adoption.
    argv = [
        "ssh",
        "-T",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "ControlMaster=no",
        "-o",
        "ControlPath=none",
        "-o",
        "ForkAfterAuthentication=no",
        "-o",
        "PermitLocalCommand=no",
        "-o",
        "RemoteCommand=none",
        "-o",
        "ServerAliveInterval=15",
        "-o",
        "ServerAliveCountMax=3",
    ]
    if port is not None:
        argv += ["-p", str(port)]
    if user:
        argv += ["-l", user]
    return argv, host


def remote_port(base, host, root, service, portal_id, override):
    if not Path(root).is_absolute() or "\n" in root:
        raise ValueError("VM_ROOT must be an absolute remote checkout path")
    words = [
        ".venv/bin/python",
        "-m",
        "scripts.vm_access.remote",
        "--service",
        service,
        "--portal-id",
        portal_id,
    ]
    if override:
        words += ["--port", str(override)]
    command = "cd " + shlex.quote(root) + " && " + shlex.join(words)
    result = subprocess.run(
        base + [host, command], capture_output=True, text=True, timeout=150, check=False
    )
    if result.returncode:
        raise ValueError(f"Remote {service} preparation failed; inspect it on the VM")
    value = json.loads(result.stdout)
    match = re.fullmatch(r"http://127\.0\.0\.1:([0-9]{1,5})", value["url"])
    if value["service"] != service or not match or not 1 <= int(match[1]) <= 65535:
        raise ValueError("Invalid remote loopback response")
    return int(match[1])


def launch(path, base, host, service, backend, fixed=None):
    # bind(0) selects a free port. Retry boundedly if another process wins the SSH bind.
    for attempt in range(5 if fixed is None else 1):
        port = free_port(fixed or 0)
        argv = base + [
            "-o",
            "ExitOnForwardFailure=yes",
            "-N",
            "-L",
            f"127.0.0.1:{port}:127.0.0.1:{backend}",
            host,
        ]
        log = path / (uuid4().hex + ".log")
        fd = os.open(log, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "wb") as output:
            child = subprocess.Popen(
                argv,
                cwd=ROOT,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        record = None
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                actual = identity(child.pid)
                if actual and actual["argv"] == argv:
                    record = {
                        "service": service,
                        "port": port,
                        "remote_port": backend,
                        "url": f"http://127.0.0.1:{port}",
                        "process": actual,
                        "log": str(log),
                    }
                if child.poll() is not None:
                    break
                if record and owns_listener(child.pid, port) and healthy(service, port):
                    return record
                time.sleep(0.1)
        except BaseException:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=5)
            raise
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)
        # Retry only a reported local bind collision, not an auth/backend failure.
        if fixed is not None or "Address already in use" not in log.read_text(errors="replace"):
            raise ValueError(f"{service} SSH/HTTP readiness failed; inspect {log}")
    raise ValueError("No local port acquired after five SSH bind attempts")


def report(record):
    return [
        {
            "service": item["service"],
            "url": item["url"],
            "remote_port": item["remote_port"],
            "status": "ready"
            if alive(item)
            and owns_listener(item["process"]["pid"], item["port"])
            and healthy(item["service"], item["port"])
            else "unreachable_or_stopped",
        }
        for item in record["tunnels"]
    ]


def up(path, config):
    existing = read(path)
    if existing and any(alive(item) for item in existing["tunnels"]):
        if existing["config"] != config:
            raise ValueError("Profile has other parameters; stop its tunnels first")
        result = report(existing)
        if any(item["status"] != "ready" for item in result):
            raise ValueError("Profile partially unavailable; stop and restart it")
        return result
    base, host = ssh_base(config["host"], config["ssh_port"], config["user"])
    record = {"schema": "fraud_vm_access_v1", "root": str(ROOT), "config": config, "tunnels": []}
    try:
        for service in config["services"]:
            backend = remote_port(
                base,
                host,
                config["remote_root"],
                service,
                config["portal_id"],
                config["remote_ports"].get(service),
            )
            item = launch(path, base, host, service, backend, config["local_ports"].get(service))
            record["tunnels"].append(item)
            write(path / "current.json", record)
        return report(record)
    except BaseException:
        for item in record["tunnels"]:
            stop(item)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("up", "status", "stop"))
    parser.add_argument("--profile", default="default")
    parser.add_argument("--host")
    parser.add_argument("--user")
    parser.add_argument("--ssh-port", type=int)
    parser.add_argument("--remote-root")
    parser.add_argument("--portal-id", default="investigation-v1")
    parser.add_argument("--services", default="portal,grafana,mlflow")
    parser.add_argument("--local-port", action="append", default=[])
    parser.add_argument("--remote-port", action="append", default=[])
    args = parser.parse_args()
    try:
        path = workspace(args.profile)
        with locked(path):
            if args.action == "up":
                selected = args.services.split(",")
                if (
                    not selected
                    or len(set(selected)) != len(selected)
                    or set(selected) - HEALTH.keys()
                ):
                    raise ValueError(
                        "Select portal,grafana,mlflow,api,prometheus without duplicates"
                    )
                if not args.host or not args.remote_root:
                    raise ValueError("Supply --host and --remote-root")
                if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", args.portal_id):
                    raise ValueError("Invalid portal ID")
                config = {
                    "host": args.host,
                    "user": args.user,
                    "ssh_port": args.ssh_port,
                    "remote_root": args.remote_root,
                    "portal_id": args.portal_id,
                    "services": selected,
                    "local_ports": assignments(args.local_port, selected),
                    "remote_ports": assignments(args.remote_port, selected),
                }
                result = up(path, config)
            else:
                record = read(path)
                if args.action == "stop" and record:
                    for item in record["tunnels"]:
                        stop(item)
                result = report(record) if record else []
            print(json.dumps(result, indent=2))
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"VM access stopped: {exc}\n")


if __name__ == "__main__":
    main()
