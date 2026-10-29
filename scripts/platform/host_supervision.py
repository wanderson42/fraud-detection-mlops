"""Install user units for an existing VM session; never deploy or score transactions."""

import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import time
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parents[2]
UNITS = ("fraud-session-observer.service", "fraud-grafana-access.service")
PORT = 13001


def require(condition, message):
    if not condition:
        raise ValueError(message)


def systemctl(*args, check=True):
    return subprocess.run(
        ["systemctl", "--user", *args], check=check, capture_output=True, text=True
    )


def quote(value, exec_arg=False):
    require(not any(c in str(value) for c in "\n\r\x00"), "Invalid unit value")
    value = str(value).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    if exec_arg:
        value = value.replace("$", "$$")
    return '"' + value + '"'


def binding(root, key):
    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", key), "Invalid session ID")
    path = root / "data/storage/kind-platform/observability/session-monitoring/enabled.json"
    require(path.is_file() and not path.is_symlink(), "Existing monitoring binding required")
    require(path.stat().st_mode & 0o077 == 0, "Private monitoring binding required")
    item = json.loads(path.read_bytes())["binding"]
    require(item["session_id"] == key, "Installed session differs")
    require(re.fullmatch(r"[a-f0-9]{64}", item["session_sha256"]), "Invalid session SHA")
    return item


def render(root, key, path):
    require(root.is_absolute(), "Absolute repository path required")
    require(
        not any(c.isspace() for c in str(root)) and "\\" not in str(root),
        "Use a checkout path without whitespace or backslashes",
    )
    require((root / ".venv/bin/python").is_file(), "Project .venv Python required")
    paths = path.split(os.pathsep)
    require(paths and all(p and Path(p).is_absolute() for p in paths), "Use an absolute PATH")
    python = root / ".venv/bin/python"
    result = {}
    for name, command in (
        (
            UNITS[0],
            [
                str(python),
                "-u",
                "-m",
                "scripts.session_monitoring.controller",
                "watch",
                "--session-id",
                key,
                "--interval",
                "5",
            ],
        ),
        (UNITS[1], [str(python), "-u", "-m", "scripts.platform.host_supervision", "grafana"]),
    ):
        result[name] = (
            "# Managed by fraud-detection-mlops host_supervision; do not edit manually.\n"
            "[Unit]\nDescription=Fraud VM " + name.removesuffix(".service") + "\n"
            "StartLimitIntervalSec=0\n\n[Service]\nType=simple\n"
            "WorkingDirectory=" + str(root).replace("%", "%%") + "\n"
            "Environment=" + quote("PATH=" + path) + "\n"
            "Environment=PYTHONUNBUFFERED=1 MLFLOW_DISABLE_TELEMETRY=true DO_NOT_TRACK=true\n"
            "UMask=0077\nRestart=always\nRestartSec=10\nKillMode=control-group\n"
            "TimeoutStopSec=20\nStandardOutput=journal\nStandardError=journal\n"
            "ExecStart=" + " ".join(quote(v, exec_arg=True) for v in command) + "\n"
            "\n[Install]\nWantedBy=default.target\n"
        )
    return result


def install(root, key, home, path):
    item = binding(root, key)
    units = render(root, key, path)
    directory = home / ".config/systemd/user"
    require(
        not any(p.is_symlink() for p in (home / ".config", home / ".config/systemd", directory)),
        "Symlink in unit directory; inspect before installation",
    )
    directory.mkdir(parents=True, exist_ok=True)
    # Validate the whole set before writing any unit; refuse adoption or silent reconfiguration.
    for name, content in units.items():
        dest = directory / name
        require(not dest.is_symlink(), "Symlink in unit file")
        require(
            not dest.exists() or dest.read_text() == content,
            "Existing unit differs; preserve and inspect: " + name,
        )
        state = systemctl("show", name, "--property=LoadState", "--value", check=False)
        require(
            state.stdout.strip() in ("", "not-found") or dest.exists(),
            "Unit name belongs to another installation: " + name,
        )
    for name, content in units.items():
        dest = directory / name
        if not dest.exists():
            with dest.open("x") as stream:
                os.chmod(dest, 0o600)
                stream.write(content)
    systemctl("daemon-reload")
    return {
        "status": "vm_user_units_installed_not_started",
        "binding": item,
        "units": list(units),
        "deployment_modified": False,
        "session_modified": False,
    }


def healthy():
    request = build_opener(ProxyHandler({}))
    try:
        with request.open(f"http://127.0.0.1:{PORT}/api/health", timeout=2) as response:
            value = json.load(response)
            return response.status == 200 and value.get("database") == "ok"
    except OSError, ValueError:
        return False


def supervise(command, probe=healthy, startup=90, interval=5, failures=3, popen=subprocess.Popen):
    """Keep one foreground child, abort on health failure; systemd owns the cgroup."""
    child = popen(command, cwd=ROOT)
    try:
        deadline = time.monotonic() + startup
        ready = False
        missed = 0
        while True:
            if child.poll() is not None:
                raise ValueError("Grafana foreground access exited")
            if probe():
                if not ready:
                    print(json.dumps({"status": "grafana_access_ready", "port": PORT}), flush=True)
                ready = True
                missed = 0
            elif ready:
                missed += 1
                require(missed < failures, "Grafana access lost health; restarting unit")
            else:
                require(time.monotonic() < deadline, "Grafana access startup timed out")
            time.sleep(interval)
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)


def port_available(port=PORT):
    # Match kubectl/Go's reusable listener behavior: TIME_WAIT is not a listener.
    # The actual port-forward still owns the definitive bind and listen.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(("127.0.0.1", port))


def grafana():
    # Refuse a manual legacy access rather than create a second port-forward.
    from scripts.platform.local_services import controller, processes

    with controller.locked("grafana"):
        record = controller.read("grafana")
        require(
            not record or not processes.matches(record),
            "Stop the legacy Grafana access with make access-stop ACCESS_SERVICE=grafana",
        )
        port_available()
        command = [
            str(ROOT / ".venv/bin/python"),
            "-u",
            "-m",
            "scripts.platform.kind_observability",
            "open",
            "--component",
            "grafana",
            "--port",
            str(PORT),
        ]
        supervise(command)


def preflight(key):
    item = binding(ROOT, key)
    lock = ROOT / "data/storage/kind-platform/observability/session-monitoring/watch.lock"
    require(lock.is_file() and not lock.is_symlink(), "Existing watch lock required")
    with lock.open("r") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    from scripts.platform.local_services import controller, processes

    record = controller.read("grafana")
    require(not record or not processes.matches(record), "Stop the legacy Grafana access first")
    port_available()
    return {"status": "vm_supervision_handoff_ready", "binding": item}


def status():
    rows = {}
    for name in UNITS:
        result = systemctl(
            "show",
            name,
            "--property=LoadState,ActiveState,SubState,MainPID,NRestarts,Result",
            check=False,
        )
        rows[name] = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    return {"units": rows, "grafana_http_healthy": healthy()}


def check(key):
    from scripts.platform import kind_observability as k
    from scripts.session_monitoring import components as c
    from scripts.session_monitoring import controller as monitor

    deadline = time.monotonic() + 120
    while True:
        state = status()
        require(
            all(row.get("LoadState") == "loaded" for row in state["units"].values()),
            "Install both user services before verification",
        )
        running = all(
            row.get("ActiveState") == "active" and row.get("SubState") == "running"
            for row in state["units"].values()
        )
        if running and state["grafana_http_healthy"]:
            break
        require(
            time.monotonic() < deadline, "User services did not become healthy; inspect journal"
        )
        time.sleep(2)
    directory, bound = monitor.bound(key)
    hashes = c.sources(directory)
    protected = c.protected()
    projection = monitor.collector.collect(directory, bound, include_details=True)
    with (
        monitor.forward() as receiver,
        k.forward("prometheus") as prom,
        k.forward("grafana") as graf,
    ):
        monitor.check_projection(receiver, prom, graf, projection, True)
    require(
        c.sources(directory) == hashes and c.protected() == protected,
        "Accepted sources or protected resources changed",
    )
    return {
        "status": "vm_supervision_same_session_verified",
        "binding": bound,
        "counts": projection["counts"],
        "services": state,
        "accepted_sources_unchanged": hashes,
        "protected_resources_unchanged": True,
        "new_inferences_executed": False,
        "reboot_validated": False,
        "logout_persistence_validated": False,
    }


def rehearse(key):
    before = check(key)
    results = {}
    for name in UNITS:
        initial = status()["units"][name]
        print("Testing automatic restart: " + name, flush=True)
        systemctl("kill", "--kill-whom=main", "--signal=SIGKILL", name)
        deadline = time.monotonic() + 120
        while True:
            row = status()["units"][name]
            if (
                row.get("ActiveState") == "active"
                and row.get("SubState") == "running"
                and row.get("MainPID") not in (None, "0", initial.get("MainPID"))
                and int(row.get("NRestarts", "0")) > int(initial.get("NRestarts", "0"))
            ):
                try:
                    result = check(key)
                except ValueError, OSError, subprocess.SubprocessError:
                    if time.monotonic() >= deadline:
                        raise
                else:
                    require(
                        result["binding"] == before["binding"]
                        and result["counts"] == before["counts"]
                        and result["accepted_sources_unchanged"]
                        == before["accepted_sources_unchanged"],
                        "Session or accepted sources changed during restart",
                    )
                    results[name] = {
                        "previous_pid": initial["MainPID"],
                        "current_pid": row["MainPID"],
                        "restart_counter_increased": True,
                    }
                    break
            require(time.monotonic() < deadline, "Automatic restart verification timed out")
            time.sleep(2)
    return {
        "status": "vm_user_service_recovery_verified",
        "binding": before["binding"],
        "services": results,
        "same_session_metrics_verified": True,
        "new_inferences_executed": False,
        "reboot_validated": False,
        "logout_persistence_validated": False,
    }


def interrupted(_signum, _frame):
    raise KeyboardInterrupt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=("install", "preflight", "status", "grafana", "check", "rehearse")
    )
    parser.add_argument("--session-id")
    args = parser.parse_args()
    try:
        if args.action == "grafana":
            signal.signal(signal.SIGTERM, interrupted)
            grafana()
            return
        if args.action in ("install", "preflight", "check", "rehearse"):
            require(args.session_id is not None, "Explicit --session-id required")
        if args.action == "install":
            result = install(ROOT, args.session_id, Path.home(), os.environ.get("PATH", ""))
        elif args.action == "preflight":
            result = preflight(args.session_id)
        elif args.action == "check":
            result = check(args.session_id)
        elif args.action == "rehearse":
            result = rehearse(args.session_id)
        else:
            result = status()
        print(json.dumps(result, indent=2))
    except KeyboardInterrupt:
        pass
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        parser.exit(1, "VM supervision stopped: " + str(exc) + "\n")


if __name__ == "__main__":
    main()
