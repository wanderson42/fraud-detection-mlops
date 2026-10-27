"""Linux process identity and scoped termination; never search by executable name."""

import os
from pathlib import Path
import signal
import time


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
            "group": os.getpgid(pid),
            "argv": (path / "cmdline").read_bytes().decode().rstrip("\0").split("\0"),
            "cwd": str((path / "cwd").resolve(strict=True)),
        }
    except OSError, ValueError, IndexError:
        return None


def matches(record):
    actual = identity(record["pid"])
    return actual is not None and actual == record["process"] and actual["group"] == actual["pid"]


def stop(record, timeout=5):
    if not matches(record):
        if identity(record["pid"]) is not None:
            raise ValueError("PID belongs to another process; no signal sent")
        return "already_stopped"
    group = record["pid"]
    try:
        os.killpg(group, signal.SIGTERM)
    except ProcessLookupError:
        return "already_stopped"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.killpg(group, 0)
        except ProcessLookupError:
            break
        # Reap a direct child when called from tests or a single long-lived client.
        try:
            os.waitpid(group, os.WNOHANG)
        except ChildProcessError:
            pass
        time.sleep(0.05)
    else:
        # The group was identified before TERM; terminate any remaining children.
        try:
            os.killpg(group, signal.SIGKILL)
        except ProcessLookupError:
            pass
    return "stopped"
