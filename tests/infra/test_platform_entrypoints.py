"""Real Make dispatch and scoped forward lifecycle, without Docker or data writes."""

from contextlib import contextmanager
from importlib.util import module_from_spec, spec_from_file_location
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(shutil.which("make"), "GNU Make required")
class MakeEntrypoints(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name)
        self.log = directory / "calls.jsonl"
        executable = directory / "poetry-recorder"
        executable.write_text(
            "#!" + sys.executable + "\nimport json,os,sys\n"
            "with open(os.environ['CALL_LOG'], 'a') as f:\n"
            " f.write(json.dumps(sys.argv[1:])+'\\n')\n"
            "raise SystemExit(17 if os.environ.get('FAIL_ACTION') in sys.argv[1:] else 0)\n"
        )
        executable.chmod(0o700)
        self.environment = {
            k: v for k, v in os.environ.items() if not k.startswith(("PLATFORM_", "MAKE"))
        }
        self.environment.update(POETRY=str(executable), CALL_LOG=str(self.log))

    def run_make(self, *goals, **environment):
        return subprocess.run(
            ["make", "--no-print-directory", *goals],
            cwd=ROOT,
            env={**self.environment, **environment},
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )

    def calls(self):
        return (
            [json.loads(x) for x in self.log.read_text().splitlines()] if self.log.exists() else []
        )

    def test_default_help_without_poetry_includes_both_styles(self):
        result = self.run_make()
        self.assertEqual(result.returncode, 0, result.stderr)
        for target in ("experiments", "verify-data", "platform-start", "platform-api"):
            self.assertIn(target, result.stdout)
        self.assertEqual(self.calls(), [])

    def test_status_does_not_provision_or_train(self):
        self.assertEqual(self.run_make("platform-status").returncode, 0)
        self.assertEqual(
            self.calls(), [["run", "python", "-m", "scripts.platform.kind_operations", "status"]]
        )

    def test_initialization_precedes_host_lock_and_create(self):
        self.assertEqual(self.run_make("platform-foundation").returncode, 0)
        self.assertEqual(
            [c[-1] for c in self.calls()], ["init", "host-check", "validate", "create", "up"]
        )

    def test_failure_prevents_next_mutation(self):
        self.assertNotEqual(
            self.run_make("platform-foundation", FAIL_ACTION="host-check").returncode, 0
        )
        self.assertEqual([c[-1] for c in self.calls()], ["init", "host-check"])

    def test_missing_parameters_do_not_dispatch(self):
        for target in (
            "platform-baseline",
            "platform-stop",
            "platform-start",
            "platform-verify",
            "platform-reboot-verify",
            "platform-serving-prepare",
            "platform-monitoring-prepare",
            "platform-operations-test",
        ):
            with self.subTest(target=target):
                self.assertNotEqual(self.run_make(target).returncode, 0)
                self.assertEqual(self.calls(), [])

    def test_writer_declaration_is_not_invented(self):
        for target in (
            "platform-stop",
            "platform-start",
            "platform-monitoring-prepare",
            "platform-operations-test",
        ):
            with self.subTest(target=target):
                result = self.run_make(
                    target, PLATFORM_BASELINE="b", PLATFORM_TRACKING_REPORT="r", PLATFORM_STATE="s"
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.calls(), [])

    def test_quoted_paths_are_single_arguments(self):
        path = "data/storage/baseline with spaces; echo injected.json"
        result = self.run_make(
            "platform-stop", PLATFORM_BASELINE=path, PLATFORM_NO_OTHER_WRITERS="1"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls()[0][-3:], ["--baseline", path, "--no-other-writers"])

    def test_j_does_not_parallelize_operational_goals(self):
        result = self.run_make(
            "-j4",
            "platform-start",
            "platform-verify",
            PLATFORM_BASELINE="b",
            PLATFORM_NO_OTHER_WRITERS="1",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([c[4] for c in self.calls()], ["start", "verify"])

    def test_reboot_check_is_only_verification(self):
        self.assertEqual(
            self.run_make("platform-reboot-verify", PLATFORM_BASELINE="b").returncode, 0
        )
        self.assertEqual(
            self.calls(),
            [
                [
                    "run",
                    "python",
                    "-m",
                    "scripts.platform.kind_operations",
                    "verify",
                    "--baseline",
                    "b",
                    "--expect-reboot",
                ]
            ],
        )

    def test_delivery_requires_and_forwards_identity_pins(self):
        result = self.run_make(
            "platform-serving-prepare",
            PLATFORM_DELIVERY="delivery dir",
            PLATFORM_SOURCE_SHA="abc",
            PLATFORM_DESCRIPTOR_SHA256="def",
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            self.calls()[0][4:],
            [
                "prepare",
                "--directory",
                "delivery dir",
                "--expected-source-sha",
                "abc",
                "--expected-descriptor-sha256",
                "def",
            ],
        )

    def test_ordinary_access_is_separate_from_rollout(self):
        self.assertEqual(self.run_make("platform-api").returncode, 0)
        self.assertEqual(self.calls()[0][3:], ["scripts.platform.local_access", "api"])
        self.assertEqual(self.run_make("platform-rollout-test").returncode, 0)
        self.assertEqual(self.calls()[1][3:], ["scripts.platform.kind_serving", "rehearse"])

    def test_data_jobs_only_invoke_existing_producer_chain(self):
        self.assertEqual(self.run_make("platform-data-jobs").returncode, 0)
        self.assertEqual([c[3] for c in self.calls()], ["scripts.platform.kind_pipeline"] * 3)
        self.assertEqual([c[4] for c in self.calls()], ["validate", "build", "rehearse"])

    def test_grafana_stays_foreground_and_scoped(self):
        self.assertEqual(
            self.run_make("platform-grafana", PLATFORM_GRAFANA_PORT="13011").returncode, 0
        )
        self.assertEqual(
            self.calls()[0][3:],
            [
                "scripts.platform.kind_observability",
                "open",
                "--component",
                "grafana",
                "--port",
                "13011",
            ],
        )


class LocalAccess(unittest.TestCase):
    def setUp(self):
        spec = spec_from_file_location(
            "local_access_fixture", ROOT / "scripts/platform/local_access.py"
        )
        self.module = module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.calls = []

        @contextmanager
        def forward(*args):
            self.calls.append(("open", args))
            try:
                yield "http://127.0.0.1:32123"
            finally:
                self.calls.append("close-own-forward")

        self.foundation = SimpleNamespace(owner=lambda: self.calls.append("owner"))
        self.tracking = SimpleNamespace(
            inventory=lambda: self.calls.append("tracking-owned"), port_forward=forward
        )
        self.delivery = SimpleNamespace(plan=lambda: {"pinned": True}, forward=forward)
        self.serving = SimpleNamespace(
            observe=lambda _: {"pods": {"gateway": {"name": "owned-gateway"}}},
            wait_route=lambda *args: {"score": 0.1, "invalid_input_http_status": 422},
        )
        parent = ModuleType("scripts.platform")
        for name, value in (
            ("kind_foundation", self.foundation),
            ("kind_tracking", self.tracking),
            ("kind_serving_delivery", self.delivery),
            ("kind_serving", self.serving),
        ):
            setattr(parent, name, value)
        context = patch.dict(sys.modules, {"scripts.platform": parent})
        context.start()
        self.addCleanup(context.stop)

    def test_tracking_checks_ownership_and_closes_own_forward(self):
        with self.module.connection("mlflow") as result:
            self.assertEqual(result["service"], "mlflow")
            self.assertEqual(self.calls[:2], ["owner", "tracking-owned"])
        self.assertEqual(self.calls[-1], "close-own-forward")

    def test_api_checks_gateway_release_and_422(self):
        with self.module.connection("api") as result:
            self.assertEqual(result["verification"]["invalid_input_http_status"], 422)
            self.assertIn(("open", ("owned-gateway",)), self.calls)
        self.assertEqual(self.calls[-1], "close-own-forward")

    def test_failed_api_check_still_closes_forward(self):
        def fail(*args):
            raise ValueError("release differs")

        self.serving.wait_route = fail
        with self.assertRaisesRegex(ValueError, "release differs"), self.module.connection("api"):
            self.fail("must not yield an unchecked URL")
        self.assertEqual(self.calls[-1], "close-own-forward")

    def test_foreign_cluster_does_not_open_forward(self):
        def fail():
            raise ValueError("foreign")

        self.foundation.owner = fail
        with self.assertRaisesRegex(ValueError, "foreign"), self.module.connection("mlflow"):
            self.fail("must not open")
        self.assertEqual(self.calls, [])

    def test_ctrl_c_only_closes_owned_context(self):
        with (
            patch.object(self.module.time, "sleep", side_effect=KeyboardInterrupt),
            patch("sys.stdout", new=io.StringIO()),
        ):
            self.module.open_local("api")
        self.assertEqual(self.calls[-1], "close-own-forward")


if __name__ == "__main__":
    unittest.main()
