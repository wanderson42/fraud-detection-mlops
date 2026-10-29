"""Standalone user-unit and process supervision checks; no native VM acceptance."""

from contextlib import nullcontext
import fcntl
from http.server import BaseHTTPRequestHandler, HTTPServer
import importlib.util
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Thread
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "host_supervision", ROOT / "scripts/platform/host_supervision.py"
)
h = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(h)


class SupervisionTests(unittest.TestCase):
    def fixture(self, root):
        path = root / "repo/.venv/bin/python"
        path.parent.mkdir(parents=True)
        path.symlink_to(sys.executable)
        record = (
            root / "repo/data/storage/kind-platform/observability/session-monitoring/enabled.json"
        )
        record.parent.mkdir(parents=True)
        record.write_text(
            json.dumps({"binding": {"session_id": "session", "session_sha256": "a" * 64}})
        )
        record.chmod(0o600)
        return root / "repo", root / "home"

    def ctl(self, *args, **_kwargs):
        return SimpleNamespace(stdout="not-found\n" if args[0] == "show" else "")

    def test_install_does_not_start_or_enable_and_preserves_binding(self):
        with TemporaryDirectory() as tmp:
            root, home = self.fixture(Path(tmp))
            p = root / "data/storage/kind-platform/observability/session-monitoring/enabled.json"
            before = p.read_bytes()
            with patch.object(h, "systemctl", side_effect=self.ctl) as ctl:
                result = h.install(root, "session", home, "/usr/bin:/bin")
                self.assertEqual(result["status"], "vm_user_units_installed_not_started")
                self.assertEqual(
                    [c.args[0] for c in ctl.call_args_list], ["show", "show", "daemon-reload"]
                )
                h.install(root, "session", home, "/usr/bin:/bin")
            self.assertEqual(p.read_bytes(), before)
            for name in h.UNITS:
                content = (home / ".config/systemd/user" / name).read_text()
                self.assertIn("KillMode=control-group", content)
                self.assertIn("Restart=always", content)
                self.assertNotIn("nohup", content)
                self.assertNotIn("sudo", content)

    def test_foreign_units_block_all_writes(self):
        with TemporaryDirectory() as tmp:
            root, home = self.fixture(Path(tmp))
            directory = home / ".config/systemd/user"
            directory.mkdir(parents=True)
            foreign = directory / h.UNITS[1]
            foreign.write_text("foreign")
            with (
                patch.object(h, "systemctl", side_effect=self.ctl),
                self.assertRaisesRegex(ValueError, "Existing unit differs"),
            ):
                h.install(root, "session", home, "/usr/bin")
            self.assertFalse((directory / h.UNITS[0]).exists())
            self.assertEqual(foreign.read_text(), "foreign")

    def test_different_session_and_public_binding_are_rejected(self):
        with TemporaryDirectory() as tmp:
            root, _ = self.fixture(Path(tmp))
            with self.assertRaisesRegex(ValueError, "Installed session differs"):
                h.binding(root, "other")
            p = root / "data/storage/kind-platform/observability/session-monitoring/enabled.json"
            p.chmod(0o644)
            with self.assertRaisesRegex(ValueError, "Private monitoring"):
                h.binding(root, "session")

    def test_unit_escaping_and_path_validation(self):
        self.assertEqual(h.quote('a%"$b', exec_arg=True), '"a%%\\"$$b"')
        with self.assertRaises(ValueError):
            h.quote("bad\nunit")
        with TemporaryDirectory() as tmp:
            root, _ = self.fixture(Path(tmp))
            with self.assertRaisesRegex(ValueError, "absolute PATH"):
                h.render(root, "session", "/usr/bin:relative")
            with self.assertRaisesRegex(ValueError, "absolute PATH"):
                h.render(root, "session", "/usr/bin:")

    def child(self):
        return Mock(poll=Mock(return_value=None))

    def test_health_loss_after_readiness_terminates_child(self):
        child = self.child()
        with (
            patch.object(h.time, "sleep"),
            patch.object(h.time, "monotonic", return_value=0),
            self.assertRaisesRegex(ValueError, "lost health"),
        ):
            h.supervise(
                ["foreground"],
                probe=Mock(side_effect=[True, False, False, False]),
                popen=Mock(return_value=child),
            )
        child.terminate.assert_called_once()
        child.wait.assert_called_once_with(timeout=5)

    def test_transient_probe_failure_does_not_restart_child(self):
        child = self.child()
        probe = Mock(side_effect=[True, False, True, KeyboardInterrupt])
        with (
            patch.object(h.time, "sleep"),
            patch.object(h.time, "monotonic", return_value=0),
            self.assertRaises(KeyboardInterrupt),
        ):
            h.supervise(["foreground"], probe=probe, popen=Mock(return_value=child))
        self.assertEqual(probe.call_count, 4)
        child.terminate.assert_called_once()

    def test_dead_child_is_not_reported_healthy(self):
        child = Mock(poll=Mock(return_value=0))
        probe = Mock(return_value=True)
        with self.assertRaisesRegex(ValueError, "exited"):
            h.supervise(["foreground"], probe=probe, popen=Mock(return_value=child))
        probe.assert_not_called()
        child.terminate.assert_not_called()

    def test_startup_deadline_and_forced_cleanup(self):
        child = self.child()
        child.wait.side_effect = [subprocess.TimeoutExpired("child", 5), 0]
        with (
            patch.object(h.time, "monotonic", side_effect=[0, 100]),
            self.assertRaisesRegex(ValueError, "startup timed out"),
        ):
            h.supervise(["foreground"], probe=lambda: False, popen=Mock(return_value=child))
        child.kill.assert_called_once()

    def test_actual_foreground_child_exit(self):
        with (
            TemporaryDirectory() as tmp,
            patch.object(h, "ROOT", Path(tmp)),
            self.assertRaisesRegex(ValueError, "exited"),
        ):
            h.supervise(
                [sys.executable, "-c", "raise SystemExit(7)"],
                probe=lambda: False,
                interval=0.02,
                startup=3,
            )

    def modules(self):
        modules = {
            n: ModuleType(n)
            for n in ("scripts", "scripts.platform", "scripts.platform.local_services")
        }
        service = modules["scripts.platform.local_services"]
        service.controller = SimpleNamespace(
            read=Mock(return_value=None), locked=lambda _: nullcontext()
        )
        service.processes = SimpleNamespace(matches=Mock(return_value=False))
        service.services = SimpleNamespace(free_port=Mock())
        return modules, service

    @patch.object(h, "port_available")
    def test_existing_observer_lock_blocks_handoff_without_signals(self, probe):
        with TemporaryDirectory() as tmp:
            root, _ = self.fixture(Path(tmp))
            lock = root / "data/storage/kind-platform/observability/session-monitoring/watch.lock"
            lock.touch()
            modules, _service = self.modules()
            with (
                lock.open("r") as stream,
                patch.object(h, "ROOT", root),
                patch.dict(sys.modules, modules),
            ):
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with self.assertRaises(BlockingIOError):
                    h.preflight("session")
                probe.assert_not_called()
            with patch.object(h, "ROOT", root), patch.dict(sys.modules, modules):
                self.assertEqual(h.preflight("session")["status"], "vm_supervision_handoff_ready")

    def test_manual_grafana_access_is_never_adopted_or_killed(self):
        modules, service = self.modules()
        service.controller.read.return_value = {"pid": 123}
        service.processes.matches.return_value = True
        with (
            patch.dict(sys.modules, modules),
            patch.object(h, "supervise") as supervise,
            self.assertRaisesRegex(ValueError, "legacy Grafana access"),
        ):
            h.grafana()
            supervise.assert_not_called()

    def test_rehearsal_requires_new_pid_and_increased_restart_count(self):
        old = {"MainPID": "10", "NRestarts": "0", "ActiveState": "active", "SubState": "running"}
        new = {**old, "MainPID": "11", "NRestarts": "1"}
        baseline = {
            "binding": {"session_id": "session"},
            "counts": {"decisions": 200},
            "accepted_sources_unchanged": {"session.json": "same"},
        }
        states = [{"units": {n: row for n in h.UNITS}} for row in (old, new, old, new)]
        with (
            patch.object(h, "check", return_value=baseline),
            patch.object(h, "status", side_effect=states),
            patch.object(h, "systemctl") as ctl,
        ):
            result = h.rehearse("session")
        self.assertEqual(result["status"], "vm_user_service_recovery_verified")
        self.assertEqual(ctl.call_count, 2)
        for call, name in zip(ctl.call_args_list, h.UNITS, strict=True):
            self.assertEqual(call.args, ("kill", "--kill-whom=main", "--signal=SIGKILL", name))

    @unittest.skipUnless(shutil.which("systemd-analyze"), "systemd-analyze required")
    def test_real_systemd_unit_syntax(self):
        with TemporaryDirectory() as tmp:
            root, _ = self.fixture(Path(tmp))
            units = h.render(root, "session", "/usr/local/bin:/usr/bin:/bin")
            paths = []
            for name, content in units.items():
                target = Path(tmp) / name
                target.write_text(content)
                paths.append(str(target))
            done = subprocess.run(
                ["systemd-analyze", "verify", *paths], capture_output=True, text=True, check=False
            )
            self.assertEqual(done.returncode, 0, done.stderr)

    def test_rehearsal_rejects_changed_accepted_source_without_retrying(self):
        old = {"MainPID": "10", "NRestarts": "0", "ActiveState": "active", "SubState": "running"}
        new = {**old, "MainPID": "11", "NRestarts": "1"}
        baseline = {
            "binding": {"session_id": "session"},
            "counts": {"decisions": 200},
            "accepted_sources_unchanged": {"session.json": "same"},
        }
        changed = {**baseline, "accepted_sources_unchanged": {"session.json": "changed"}}
        states = [{"units": {n: row for n in h.UNITS}} for row in (old, new)]
        with (
            patch.object(h, "check", side_effect=[baseline, changed]),
            patch.object(h, "status", side_effect=states),
            patch.object(h, "systemctl"),
            self.assertRaisesRegex(ValueError, "accepted sources changed"),
        ):
            h.rehearse("session")

    def test_actual_time_wait_does_not_block_reusable_port_check(self):
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
            listener.listen()
            with socket.create_connection(("127.0.0.1", port)) as client:
                connection, _ = listener.accept()
                with connection:
                    connection.shutdown(socket.SHUT_WR)
                    self.assertEqual(client.recv(1), b"")
            # Listener is closed below, but accepted connection leaves TIME_WAIT.
        with socket.socket() as old_probe, self.assertRaises(OSError):
            old_probe.bind(("127.0.0.1", port))
        h.port_available(port)

    def test_actual_listening_socket_remains_blocked(self):
        for reuse in (0, 1):
            with self.subTest(reuse=reuse), socket.socket() as listener:
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, reuse)
                listener.bind(("127.0.0.1", 0))
                listener.listen()
                with self.assertRaises(OSError):
                    h.port_available(listener.getsockname()[1])

    def test_unoccupied_port_is_available(self):
        h.port_available(0)

    def test_actual_loopback_health_accepts_database_ok_only(self):
        class Handler(BaseHTTPRequestHandler):
            database = "ok"

            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps({"database": self.database}).encode())

            def log_message(self, *_args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.object(h, "PORT", server.server_port):
                self.assertTrue(h.healthy())
                Handler.database = "fail"
                self.assertFalse(h.healthy())
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
