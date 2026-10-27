"""Real nohup lifecycle, PID reuse protection, isolation and allowlisted commands."""

import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

import pytest
from scripts.platform.local_services import controller as c
from scripts.platform.local_services import processes, services

from fraud_detection_mlops.storage import managed_paths

pytestmark = pytest.mark.skipif(
    not Path("/proc").exists() or not shutil.which("nohup"), reason="Linux and nohup required"
)


@pytest.fixture
def access(tmp_path, monkeypatch):
    if int(Path("/proc/self/stat").read_text().split()[0]) != os.getpid():
        pytest.skip(
            "Executor exposes a different /proc PID namespace; run native Linux lifecycle tests"
        )
    monkeypatch.setattr(c, "ROOT", tmp_path)
    monkeypatch.setattr(managed_paths, "DATA_DIR", tmp_path / "data")
    script = tmp_path / "fixture.py"
    script.write_text("""import http.server,json,os,subprocess,sys,threading,time
server=http.server.ThreadingHTTPServer(('127.0.0.1',0),http.server.BaseHTTPRequestHandler)
def get(self):
 self.send_response(200); self.end_headers(); self.wfile.write(b'ready')
http.server.BaseHTTPRequestHandler.do_GET=get
child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)'])
threading.Thread(target=server.serve_forever,daemon=True).start()
print(json.dumps({'url':'http://127.0.0.1:'+str(server.server_port),'child':child.pid,'stdin':sys.stdin.read(),'aws':os.environ.get('AWS_SECRET_ACCESS_KEY')}),flush=True)
print('stderr captured',file=sys.stderr,flush=True)
while True: time.sleep(1)
""")
    monkeypatch.setattr(services, "command", lambda *a: [sys.executable, "-u", str(script)])
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "must-not-reach-access")
    yield tmp_path
    record = c.read("api")
    if record and processes.matches(record):
        processes.stop(record, timeout=0.2)
    if record:
        try:
            os.waitpid(record["pid"], os.WNOHANG)
        except ChildProcessError:
            pass


def test_real_nohup_readiness_duplicate_hangup_logs_and_scoped_stop(access):
    result = c.start("api", timeout=5)
    assert result["status"] == "ready"
    record = c.read("api")
    log = c.logs("api", 60)
    payload = json.loads(log.splitlines()[0])
    assert payload["stdin"] == "" and payload["aws"] is None
    assert "stderr captured" in log
    assert Path(record["log"]).stat().st_mode & 0o077 == 0
    assert c.start("api", timeout=5)["status"] == "already_running"
    assert c.read("api")["pid"] == record["pid"]
    os.kill(record["pid"], signal.SIGHUP)
    time.sleep(0.1)
    assert c.status("api")["status"] == "ready"
    other = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True
    )
    try:
        assert c.stop("api")["status"] == "stopped"
        assert other.poll() is None
        assert not services.probe("api", result["url"])
        assert c.stop("api")["status"] == "already_stopped"
        assert "stderr captured" in c.logs("api", 60)
    finally:
        other.terminate()
        other.wait(timeout=5)


def test_stale_pid_or_boot_identity_never_signals_the_running_process(access):
    c.start("api", timeout=5)
    record = c.read("api")
    changed = json.loads(json.dumps(record))
    changed["process"]["start_ticks"] = "wrong-start"
    c.write(c.folder("api") / "current.json", changed)
    try:
        with pytest.raises(ValueError, match="another process"):
            c.stop("api")
        assert processes.identity(record["pid"]) is not None
    finally:
        c.write(c.folder("api") / "current.json", record)


def test_failed_start_preserves_diagnostics_and_has_no_healthy_record(access, monkeypatch):
    monkeypatch.setattr(
        services,
        "command",
        lambda *a: [
            sys.executable,
            "-u",
            "-c",
            "import sys; print('failed fixture',flush=True); sys.exit(7)",
        ],
    )
    with pytest.raises(ValueError, match="inspect"):
        c.start("api", timeout=3)
    assert any("failed fixture" in p.read_text() for p in c.folder("api").glob("*.log"))
    assert c.status("api")["status"] != "ready"


def test_stopped_restart_keeps_both_logs(access):
    first = c.start("api", timeout=5)
    c.stop("api")
    second = c.start("api", timeout=5)
    assert first["log"] != second["log"]
    assert Path(first["log"]).is_file() and Path(second["log"]).is_file()


def test_symlink_workspace_or_log_is_refused_without_touching_target(tmp_path, monkeypatch):
    access = tmp_path
    monkeypatch.setattr(c, "ROOT", access)
    monkeypatch.setattr(managed_paths, "DATA_DIR", access / "data")
    outside = access / "outside"
    outside.mkdir()
    parent = access / "data/storage/kind-platform/local-access"
    parent.mkdir(parents=True)
    (parent / "api").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="Symbolic"):
        c.start("api")
    assert list(outside.iterdir()) == []
    (parent / "api").unlink()


def test_allowlist_ports_and_readiness_never_use_global_kube_context():
    assert services.command("api")[-2:] == ["scripts.platform.local_access", "api"]
    assert services.command("mlflow")[-1] == "mlflow"
    assert "--component" in services.command("prometheus")
    assert "19091" in services.command("prometheus")
    assert "13011" in services.command("grafana", 13011)
    assert services.command("portal", portal_id="other-portal")[-3:] == [
        "other-portal",
        "--port",
        "18501",
    ]
    for name, port in [("unknown", None), ("api", 8000), ("portal", 80)]:
        with pytest.raises(ValueError):
            services.command(name, port)
    assert not services.probe("api", "http://example.com:8000")


def test_private_runtime_paths_are_already_ignored_by_git_and_docker():
    root = services.ROOT
    path = "data/storage/kind-platform/local-access/api/receipt.log"
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", path],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    # Every current build context uses a deny-all allowlist without data exceptions.
    ignores = [root / ".dockerignore", *root.glob("infra/**/*.dockerignore")]
    assert len(ignores) >= 10
    for file in ignores:
        patterns = [
            v.strip() for v in file.read_text().splitlines() if v.strip() and not v.startswith("#")
        ]
        assert patterns[0] == "**", file
        assert not any(v.startswith("!data") or v in {"!**", "!**/*"} for v in patterns), file


def test_identity_uses_boot_start_time_argv_cwd_and_process_group(monkeypatch):
    class ProcPath:
        def __init__(self, path):
            self.path = str(path)

        def __truediv__(self, value):
            return ProcPath(self.path + "/" + str(value))

        def read_text(self):
            if self.path.endswith("boot_id"):
                return "boot-one\n"
            fields = ["S"] + ["0"] * 18 + ["87654321"]
            return "42 (python process) " + " ".join(fields)

        def read_bytes(self):
            return b"python\0-m\0approved.module\0"

        def resolve(self, strict=False):
            return Path("/approved/repo")

    monkeypatch.setattr(processes, "Path", ProcPath)
    monkeypatch.setattr(processes.os, "getpgid", lambda pid: pid)
    assert processes.identity(42) == {
        "pid": 42,
        "start_ticks": "87654321",
        "boot_id": "boot-one",
        "group": 42,
        "argv": ["python", "-m", "approved.module"],
        "cwd": "/approved/repo",
    }


def test_reused_pid_is_rejected_without_any_signal(monkeypatch):
    expected = {"pid": 42, "start_ticks": "old", "group": 42}
    monkeypatch.setattr(processes, "identity", lambda pid: {**expected, "start_ticks": "new"})
    signals = []
    monkeypatch.setattr(processes.os, "killpg", lambda *args: signals.append(args))
    with pytest.raises(ValueError, match="another process"):
        processes.stop({"pid": 42, "process": expected})
    assert signals == []


def test_already_stopped_process_does_not_send_signals(monkeypatch):
    monkeypatch.setattr(processes, "identity", lambda pid: None)
    signals = []
    monkeypatch.setattr(processes.os, "killpg", lambda *args: signals.append(args))
    assert processes.stop({"pid": 42, "process": {}}) == "already_stopped"
    assert signals == []


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setattr(c, "ROOT", tmp_path)
    monkeypatch.setattr(managed_paths, "DATA_DIR", tmp_path / "data")
    argv = ["python", "-m", "approved.access"]
    calls = []
    process = {
        "pid": 123456,
        "group": 123456,
        "argv": list(argv),
        "cwd": str(tmp_path),
        "start_ticks": "42",
        "boot_id": "boot-one",
    }

    class Child:
        pid = 123456

        def poll(self):
            return None

    def launch(command, **kwargs):
        calls.append((command, kwargs))
        kwargs["stdout"].write(b'{"url":"http://127.0.0.1:23456"}\n')
        kwargs["stdout"].flush()
        return Child()

    monkeypatch.setattr(services, "command", lambda *args: list(argv))
    monkeypatch.setattr(services, "free_port", lambda *args: None)
    monkeypatch.setattr(services, "probe", lambda *args: True)
    monkeypatch.setattr(c.subprocess, "Popen", launch)
    monkeypatch.setattr(processes, "identity", lambda pid: dict(process))
    return calls, argv


def test_launch_redirects_input_and_both_outputs_and_reuses_only_identical_command(manager):
    calls, argv = manager
    result = c.start("api", timeout=1)
    assert result["status"] == "ready"
    assert calls[0][0][0].endswith("nohup")
    assert calls[0][1]["stdin"] == subprocess.DEVNULL
    assert calls[0][1]["stderr"] == subprocess.STDOUT
    assert calls[0][1]["start_new_session"] is True
    assert c.start("api", timeout=1)["status"] == "already_running"
    assert len(calls) == 1
    argv.append("changed-setting")
    with pytest.raises(ValueError, match="different parameters"):
        c.start("api", timeout=1)
    assert len(calls) == 1


def test_start_refuses_a_held_service_lock_before_launch(manager):
    calls, _ = manager
    with c.locked("api"), pytest.raises(BlockingIOError):
        c.start("api")
    assert calls == []


def test_log_symlink_is_refused(manager, tmp_path):
    c.start("api", timeout=1)
    record = c.read("api")
    log = Path(record["log"])
    other = tmp_path / "unrelated.txt"
    other.write_text("private unrelated data")
    log.unlink()
    log.symlink_to(other)
    with pytest.raises(ValueError, match="Symlink"):
        c.logs("api", 10)
    assert other.read_text() == "private unrelated data"
