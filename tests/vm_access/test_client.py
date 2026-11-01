"""Exercise real loopback sockets and owned processes through a local SSH stand-in."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import socket
import subprocess
import sys
from threading import Thread

import pytest
from scripts.vm_access import client as c
from scripts.vm_access.entry import arguments


@pytest.fixture
def state(tmp_path):
    path = tmp_path / "state"
    path.mkdir(mode=0o700)
    return path


@pytest.fixture
def ssh(monkeypatch, tmp_path):
    # A stand-in implements -L loopback binding, the collision diagnostic and HTTP.
    # It does not claim to validate authentication or a native VM SSH connection.
    binary = tmp_path / "ssh"
    binary.write_text(
        f"#!{sys.executable}\n"
        + """import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
p = int(sys.argv[sys.argv.index("-L") + 1].split(":")[1])
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")
    def log_message(self, *args): pass
try:
    server = HTTPServer(("127.0.0.1", p), Handler)
except OSError:
    print("Address already in use", file=sys.stderr, flush=True)
    sys.exit(1)
server.serve_forever()
"""
    )
    binary.chmod(0o700)
    monkeypatch.setenv("PATH", str(tmp_path) + ":" + os.environ["PATH"])
    original_popen = c.subprocess.Popen
    children = {}

    def popen(argv, **kwargs):
        process = original_popen(argv, **kwargs)
        children[process.pid] = (process, argv)
        return process

    def identity(pid):
        pair = children.get(pid)
        if pair is None or pair[0].poll() is not None:
            return None
        return {
            "pid": pid,
            "start_ticks": str(id(pair[0])),
            "boot_id": "test-boot",
            "argv": pair[1],
            "cwd": str(c.ROOT),
        }

    # The execution host restricts /proc, so mock that observation boundary only.
    # Binding, retry, HTTP, process termination and profile persistence remain real.
    def owns(pid, port):
        pair = children.get(pid)
        if pair is None or pair[0].poll() is not None:
            return False
        try:
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", port))
        except OSError:
            return True
        return False

    monkeypatch.setattr(c.subprocess, "Popen", popen)
    monkeypatch.setattr(c, "identity", identity)
    monkeypatch.setattr(c, "owns_listener", owns)
    return c.ssh_base("example", None, None)[0]


def test_auto_collision_retries_and_stop_preserves_other_listener(state, ssh, monkeypatch):
    other = socket.socket()
    other.bind(("127.0.0.1", 0))
    other.listen()
    busy = other.getsockname()[1]
    original = c.free_port
    choices = iter([busy, original()])
    monkeypatch.setattr(c, "free_port", lambda _port=0: next(choices))
    record = c.launch(state, ssh, "example", "grafana", 13001)
    try:
        assert record["port"] != busy
        assert c.owns_listener(record["process"]["pid"], record["port"])
        assert c.healthy("grafana", record["port"])
        assert c.report({"tunnels": [record]})[0]["status"] == "ready"
    finally:
        c.stop(record)
    assert not c.alive(record)
    assert other.getsockname()[1] == busy
    other.close()


def test_explicit_busy_port_fails_without_signal_or_fallback(state, ssh):
    with socket.socket() as other:
        other.bind(("127.0.0.1", 0))
        other.listen()
        with pytest.raises(OSError):
            c.launch(state, ssh, "example", "portal", 18501, other.getsockname()[1])
        assert other.fileno() >= 0
    assert not list(state.glob("*.log"))


def test_profile_reuse_checks_config_and_preserves_tunnel(state, ssh, monkeypatch):
    monkeypatch.setattr(c, "remote_port", lambda *args: 13001)
    config = {
        "host": "example",
        "user": None,
        "ssh_port": None,
        "remote_root": "/project",
        "portal_id": "demo",
        "services": ["grafana"],
        "local_ports": {},
        "remote_ports": {},
    }
    first = c.up(state, config)
    record = c.read(state)
    try:
        assert c.up(state, config) == first
        assert c.read(state)["tunnels"][0]["process"] == record["tunnels"][0]["process"]
        with pytest.raises(ValueError, match="other parameters"):
            c.up(state, {**config, "portal_id": "other"})
        assert c.alive(record["tunnels"][0])
    finally:
        c.stop(record["tunnels"][0])


def test_partial_start_failure_cleans_only_new_client_tunnels(state, ssh, monkeypatch):
    monkeypatch.setattr(c, "remote_port", lambda *args: 13001)
    launch = c.launch
    records = []

    def fail_second(*args):
        if records:
            raise ValueError("backend down")
        result = launch(*args)
        records.append(result)
        return result

    monkeypatch.setattr(c, "launch", fail_second)
    config = {
        "host": "example",
        "user": None,
        "ssh_port": None,
        "remote_root": "/project",
        "portal_id": "demo",
        "services": ["grafana", "portal"],
        "local_ports": {},
        "remote_ports": {},
    }
    with pytest.raises(ValueError, match="backend down"):
        c.up(state, config)
    assert not c.alive(records[0])


def test_foreign_pid_receipt_sends_no_signal(monkeypatch):
    record = {"process": {"pid": os.getpid(), "start_ticks": "foreign"}}
    monkeypatch.setattr(c.os, "kill", lambda *args: pytest.fail("must not signal"))
    c.stop(record)


def test_state_symlinks_and_receipt_permissions_rejected(tmp_path, state):
    link = tmp_path / "link"
    link.symlink_to(state, target_is_directory=True)
    with pytest.raises(ValueError, match="Symlink"):
        c.private_directory(link / "child")
    (state / "current.json").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(ValueError, match="Symlink"):
        c.read(state)


@pytest.mark.parametrize("host", ["-oProxyCommand=bad", "a;touch b", "user@host", "x\ny"])
def test_ssh_host_cannot_inject_options(host):
    with pytest.raises(ValueError):
        c.ssh_base(host, None, None)


def test_remote_path_is_shell_quoted_and_bad_response_rejected(monkeypatch):
    seen = []

    def run(argv, **kwargs):
        seen.append(argv)
        return subprocess.CompletedProcess(
            argv, 0, json.dumps({"service": "grafana", "url": "http://127.0.0.1:13001"})
        )

    monkeypatch.setattr(c.subprocess, "run", run)
    root = "/project with 'quote;$(touch nope)"
    assert c.remote_port(["ssh"], "example", root, "grafana", "demo", None) == 13001
    import shlex

    assert "cd " + shlex.quote(root) + " && " in seen[0][-1]
    with pytest.raises(ValueError, match="absolute"):
        c.remote_port(["ssh"], "example", "relative", "grafana", "demo", None)


def test_make_environment_is_data_and_does_not_expand_shell():
    env = {
        "VM_HOST": "alias",
        "VM_ROOT": "/path with spaces/$(literal)",
        "VM_LOCAL_PORTS": "portal=18511,grafana=13011",
        "VM_SSH_PORT": "2223",
    }
    result = arguments(["up"], env)
    assert result[result.index("--remote-root") + 1] == env["VM_ROOT"]
    assert result.count("--local-port") == 2
    assert "--host" not in arguments(["stop"], env)


def test_health_does_not_follow_redirects():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", "http://example.invalid/")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert not c.healthy("grafana", server.server_port)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_proc_identity_and_socket_ownership_readback(tmp_path, monkeypatch):
    original_path = c.Path
    proc = tmp_path / "proc"
    process = proc / "321"
    (process / "fd").mkdir(parents=True)
    # Fields following the command name: state through starttime.
    fields = ["S", *(["0"] * 18), "12345"]
    (process / "stat").write_text("321 (ssh) " + " ".join(fields))
    (process / "cmdline").write_bytes(b"ssh\0-N\0example\0")
    (process / "cwd").symlink_to(tmp_path, target_is_directory=True)
    (process / "fd/3").symlink_to("socket:[4567]")
    (proc / "sys/kernel/random").mkdir(parents=True)
    (proc / "sys/kernel/random/boot_id").write_text("boot-A\n")
    (proc / "net").mkdir()
    tcp = proc / "net/tcp"
    tcp.write_text("header\n0: 0100007F:32C9 00000000:0000 0A 0 0 0 0 0 4567\n")

    def path(value, *parts):
        text = str(value)
        if text == "/proc" or text.startswith("/proc/"):
            return proc / original_path(text).relative_to("/proc") / original_path(*parts)
        return original_path(value, *parts)

    monkeypatch.setattr(c, "Path", path)
    value = c.identity(321)
    assert value["boot_id"] == "boot-A"
    assert value["start_ticks"] == "12345"
    assert value["argv"] == ["ssh", "-N", "example"]
    assert c.owns_listener(321, 13001)
    assert not c.owns_listener(321, 13002)
    tcp.write_text("header\n0: 00000000:32C9 00000000:0000 0A 0 0 0 0 0 4567\n")
    assert not c.owns_listener(321, 13001)
