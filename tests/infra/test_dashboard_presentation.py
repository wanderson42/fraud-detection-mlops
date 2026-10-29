"""Standalone presentation checks; no cluster or local operational state required."""

from contextlib import nullcontext
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PresentationTests(unittest.TestCase):
    def test_feature_tables_have_one_query_and_two_columns(self):
        data = json.loads(
            (ROOT / "infra/helm/fraud-observability/files/components/features.json").read_text()
        )
        tables = [p for p in data["panels"] if p["type"] == "table"]
        self.assertEqual(len(tables), 2)
        for panel in tables:
            self.assertEqual(len(panel["targets"]), 1)
            query = panel["targets"][0]
            self.assertTrue(query["instant"])
            self.assertEqual(query["format"], "table")
            self.assertIn("fraud_session_source_valid == 1", query["expr"])
            self.assertIn("fraud_session_details_available == 1", query["expr"])
            self.assertIn("fraud_session_detail_feature_current_rows > 0", query["expr"])
            self.assertNotIn("or vector(0)", query["expr"])
            self.assertEqual(
                panel["transformations"][0]["options"]["include"]["names"], ["feature", "Value"]
            )
            self.assertEqual(
                panel["transformations"][1]["options"]["renameByName"],
                {"feature": "Feature", "Value": "Valor"},
            )
            # The selector is bounded to all 19 model features, with no wildcard additions.
            self.assertEqual(query["expr"].split("|").__len__(), 19)

    def test_stats_have_compact_titles(self):
        for path in (ROOT / "infra/helm/fraud-observability/files/components").glob("*.json"):
            for panel in json.loads(path.read_text())["panels"]:
                if panel["type"] == "stat":
                    self.assertLessEqual(len(panel["title"]), 31, panel["title"])

    def test_exact_transition_accepts_old_receipts_and_rejects_tampering(self):
        compat = load("presentation_compat", ROOT / "scripts/platform/source_compatibility.py")
        declaration = json.loads((ROOT / compat.PRESENTATION_TRANSITION).read_text())
        self.assertEqual(set(declaration["changes"]), compat.PRESENTATION_ALLOWED)
        old = {n: h["before"] for n, h in declaration["changes"].items()}
        new = {n: h["after"] for n, h in declaration["changes"].items()}
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in (*new, compat.PRESENTATION_TRANSITION):
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((ROOT / name).read_bytes())
            subprocess.run(["git", "init", "-q", tmp], check=True)
            subprocess.run(["git", "add", "."], cwd=tmp, check=True)
            subprocess.run(
                [
                    "git",
                    "-c",
                    "user.name=Test",
                    "-c",
                    "user.email=test@example.invalid",
                    "commit",
                    "-qm",
                    "fixture",
                ],
                cwd=tmp,
                check=True,
            )
            saved = dict(old)
            compat.accept(saved, new, root)
            self.assertEqual(saved, old)
            bad = dict(new, **{"model.py": "0" * 64})
            with self.assertRaises(ValueError):
                compat.accept(saved, bad, root)
            (root / next(iter(new))).write_text("tampered")
            with self.assertRaises(ValueError):
                compat.accept(saved, new, root)

    def module(self, tmp):
        names = [
            "scripts",
            "scripts.platform",
            "scripts.session_monitoring",
            "scripts.platform.kind_foundation",
            "scripts.platform.kind_observability",
            "scripts.platform.session_observability",
            "scripts.session_monitoring.components",
            "scripts.session_monitoring.controller",
        ]
        modules = {name: ModuleType(name) for name in names}
        with patch.dict(sys.modules, modules):
            module = load(
                "presentation_operation",
                ROOT / "scripts/platform/session_dashboard_presentation.py",
            )
        module.mode.committed_file = Mock()
        module.mode.profile = Mock()
        module.mode.digest = Mock(return_value="digest")
        module.s.committed = Mock()
        module.s.image_record = Mock()
        module.s.pins = Mock(return_value={"source": "digest"})
        module.s.bound = Mock(return_value=(Path(tmp), {"session_id": "session"}))
        module.s.work = Mock(return_value=Path(tmp))
        module.c.enabled = Mock(return_value=True)
        module.c.work = Mock(return_value=Path(tmp))
        module.c.no_watch = Mock(side_effect=lambda: nullcontext())
        module.c.sources = Mock(return_value={"checkpoint": "digest"})
        module.c.protected = Mock(return_value={"PVC": "same"})
        module.c.FILES = ("image.json", "prepared.json", "enabled.json")
        module.c.MARKER = "components.json"
        module.f.ROOT = ROOT

        def require(condition, message):
            if not condition:
                raise ValueError(message)

        module.f.require = require
        module.f.private_json = lambda p, v: p.write_text(json.dumps(v))
        module.k.RELEASE = "observability"
        module.k.values = Mock(return_value={"image": "same"})
        module.k.helm = Mock(return_value=SimpleNamespace(stdout='{"image":"same"}'))
        module.k.install = Mock()
        module.revision = Mock(return_value=5)
        module.verify = Mock(return_value={"status": "verified"})
        return module

    def test_apply_backs_up_before_helm_and_preserves_receipts(self):
        with TemporaryDirectory() as tmp:
            m = self.module(tmp)
            m.k.install.side_effect = lambda ready: self.assertTrue(
                (Path(tmp) / "presentation-plan.json").exists()
            )
            result = m.operate("id", "session", "apply")
            self.assertEqual(result["status"], "verified")
            m.k.install.assert_called_once_with(True)
            plan = json.loads((Path(tmp) / "presentation-plan.json").read_text())
            self.assertEqual(plan["previous_revision"], 5)
            self.assertEqual(len(plan["receipt_hashes"]), 4)
            self.assertFalse((Path(tmp) / "image.json").exists())

    def test_failed_install_retains_backup_and_allows_rollback(self):
        with TemporaryDirectory() as tmp:
            m = self.module(tmp)
            m.k.install.side_effect = RuntimeError("rollout failed")
            with self.assertRaisesRegex(RuntimeError, "rollout failed"):
                m.operate("id", "session", "apply")
            self.assertTrue((Path(tmp) / "presentation-plan.json").exists())
            m.revision.return_value = 6
            result = m.operate("id", "session", "rollback")
            self.assertEqual(result["status"], "session_dashboard_presentation_rolled_back")
            self.assertEqual(m.k.helm.call_args.args[:3], ("rollback", "observability", "5"))

    def test_other_maintenance_and_locked_watch_block_mutation(self):
        with TemporaryDirectory() as tmp:
            m = self.module(tmp)
            m.operate("id", "session", "apply")
            m.k.install.reset_mock()
            m.revision.return_value = 8
            with self.assertRaisesRegex(ValueError, "Another Helm maintenance"):
                m.operate("id", "session", "apply")
            m.k.install.assert_not_called()
            m.c.no_watch.side_effect = BlockingIOError("watch active")
            with self.assertRaises(BlockingIOError):
                m.operate("id", "session", "apply")
            m.k.install.assert_not_called()

    def test_verify_does_not_install(self):
        with TemporaryDirectory() as tmp:
            m = self.module(tmp)
            with self.assertRaisesRegex(ValueError, "No presentation backup"):
                m.operate("id", "session", "verify")
            m.k.install.assert_not_called()


if __name__ == "__main__":
    unittest.main()
