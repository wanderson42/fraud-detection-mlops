"""Inventory blind spots, metadata-only boundaries and conservative completeness."""

import json
from pathlib import Path
import subprocess

import pytest
from scripts.storage import persistence_inventory as inv


@pytest.fixture
def checkout(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "references").mkdir()
    (root / "producer.py").write_text("# fixture producer\n")
    policy = {
        "schema": "fraud_persistence_catalogue_v1",
        "locations": [
            {
                "prefix": "data/storage",
                "family": "workspace",
                "owner": "storage",
                "producers": ["producer.py"],
                "review_required": False,
            },
            {
                "prefix": "data/tracking",
                "family": "tracking",
                "owner": "tracking",
                "producers": [],
                "review_required": False,
            },
            {
                "prefix": "reports",
                "family": "legacy_review",
                "owner": "scaffold",
                "producers": [],
                "review_required": True,
            },
        ],
        "source_prefixes": ["references", "scripts"],
        "tooling_roots": [".git", ".venv", ".tox"],
        "tooling_directory_names": ["__pycache__"],
        "external_boundaries": [{"id": "docker", "inspection": "declared_only"}],
    }
    (root / inv.CATALOGUE).write_text(json.dumps(policy))
    subprocess.run(["git", "add", "producer.py", str(inv.CATALOGUE)], cwd=root, check=True)
    return root


def put(root, name, data=b"unread runtime contents"):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_includes_storage_root_strays_and_databases_without_reading_contents(
    checkout, monkeypatch
):
    paths = [
        "data/storage/restored/bundle/model.skops",
        "data/tracking/mlflow.db",
        ".env",
        "lost.sqlite",
        "data/unclassified/output.parquet",
        "reports/figure.png",
    ]
    for path in paths:
        put(checkout, path)
    original_open = Path.open

    def read_guard(path, mode="r", *args, **kwargs):
        if "r" in mode and path != checkout / inv.CATALOGUE:
            pytest.fail("Runtime contents opened: " + str(path))
        return original_open(path, mode, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", read_guard)
        summary = inv.inventory(checkout)
    report_path = Path(summary["report"])
    report = json.loads(report_path.read_bytes())
    by_name = {e["path"]: e for e in report["entries"]}
    assert set(paths) <= set(by_name)
    assert by_name[paths[0]]["family"] == "workspace"
    assert by_name[".env"]["family"] == "local_configuration_metadata"
    assert by_name["lost.sqlite"]["review_required"]
    assert by_name["data/unclassified/output.parquet"]["review_required"]
    assert by_name["reports/figure.png"]["review_required"]
    assert summary["database_candidates"] == 2
    assert report["scan_complete"] and report["status"] == "review_required"
    assert not report["project_wide_storage_inventory_complete"]
    assert not report["models_or_databases_opened"] and not report["services_contacted"]
    assert not report["consistent_snapshot"] and not report["destination_policy_enforced"]
    assert report_path.stat().st_mode & 0o777 == 0o600
    assert report_path.relative_to(checkout).as_posix() not in by_name
    assert report["report_written_after_scan"]


def test_runtime_files_staged_in_git_keep_their_runtime_classification(checkout):
    put(checkout, "data/tracking/mlflow.db")
    subprocess.run(["git", "add", "data/tracking/mlflow.db"], cwd=checkout, check=True)
    report = json.loads(Path(inv.inventory(checkout)["report"]).read_bytes())
    row = next(e for e in report["entries"] if e["path"] == "data/tracking/mlflow.db")
    assert row["family"] == "tracking" and row["tracked"]


def test_tooling_exclusions_are_explicit_but_do_not_hide_workspace_data(checkout):
    put(checkout, ".venv/private.db")
    put(checkout, "scripts/__pycache__/module.pyc")
    put(checkout, "data/storage/__pycache__/artifact.db")
    report = json.loads(Path(inv.inventory(checkout)["report"]).read_bytes())
    names = {e["path"] for e in report["entries"]}
    assert ".venv/private.db" not in names and "scripts/__pycache__/module.pyc" not in names
    assert "data/storage/__pycache__/artifact.db" in names
    excluded = {e["path"] for e in report["excluded_tooling_trees"]}
    assert {".git", ".venv", "scripts/__pycache__"} <= excluded
    assert all(e["recursive_bytes"] is None for e in report["excluded_tooling_trees"])


def test_symlinks_are_not_followed_and_make_the_local_scan_incomplete(checkout, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = put(outside, "secret.sqlite")
    (checkout / "outside_link").symlink_to(outside, target_is_directory=True)
    (checkout / "file_link").symlink_to(secret)
    report = json.loads(Path(inv.inventory(checkout)["report"]).read_bytes())
    assert not report["scan_complete"]
    assert len(report["issues"]) == 2
    assert not any("secret.sqlite" in row["path"] for row in report["entries"])


def test_symlink_in_report_destination_is_rejected_without_external_write(checkout, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (checkout / "data").mkdir()
    (checkout / "data/storage").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="Symlink"):
        inv.inventory(checkout)
    assert not list(outside.iterdir())


def test_walk_error_does_not_claim_complete_scan(checkout, monkeypatch):
    def interrupted(root, *, followlinks, onerror):
        onerror(PermissionError(13, "denied", str(root / "unreadable")))
        yield str(root), [], []

    policy, _ = inv.load_catalogue(checkout)
    monkeypatch.setattr(inv.os, "walk", interrupted)
    result = inv.scan(checkout, set(), policy)
    assert not result["scan_complete"]
    assert result["issues"] == [{"path": "unreadable", "reason": "unreadable_directory"}]


@pytest.mark.parametrize(
    "bad", ["../outside", "/tmp/output", "data/../escape", "data\\escape", "."]
)
def test_bad_catalogue_paths_fail_before_report_write(checkout, bad):
    path = checkout / inv.CATALOGUE
    policy = json.loads(path.read_bytes())
    policy["locations"][0]["prefix"] = bad
    path.write_text(json.dumps(policy))
    with pytest.raises(ValueError):
        inv.inventory(checkout)
    assert not (checkout / "data/storage/inventory").exists()


def test_missing_producer_requires_catalogue_review(checkout):
    (checkout / "producer.py").unlink()
    with pytest.raises(ValueError, match="producer missing"):
        inv.inventory(checkout)


def test_catalogue_in_project_names_existing_producers():
    root = Path(__file__).resolve().parents[2]
    policy, identity = inv.load_catalogue(root)
    from fraud_detection_mlops.config import REPORTS_DIR

    rules = {row["prefix"]: row for row in policy["locations"]}
    assert {
        "data/raw",
        "data/interim",
        "data/processed",
        "data/tracking",
        "data/replay",
        "data/storage",
    } <= rules.keys()
    assert len(identity) == 64
    reports = rules[REPORTS_DIR.relative_to(root).as_posix()]
    assert not reports["review_required"]
    assert rules["reports"]["review_required"]
    assert not policy["decisions"]["recoverability_inferred_from_classification"]


def test_git_worktree_pointer_is_an_explicit_tooling_exclusion(checkout, tmp_path):
    root = tmp_path / "worktree"
    root.mkdir()
    put(root, ".git", b"gitdir: /outside/not-read")
    policy, _ = inv.load_catalogue(checkout)
    report = inv.scan(root, set(), policy)
    assert report["entries"] == [] and report["scan_complete"]
    assert report["excluded_tooling_trees"] == [
        {"path": ".git", "reason": "registered_tooling_tree", "recursive_bytes": None}
    ]


@pytest.fixture
def reviewed_consumer(checkout):
    import hashlib

    code = (
        b'raise RuntimeError("Source must never be imported by inventory")\n'
        b"def read_reference():\n"
        b"    return tracking.local_tracking(root)\n"
    )
    source = put(checkout, "scripts/consumer.py", code)
    policy = json.loads((checkout / inv.CATALOGUE).read_bytes())
    policy["tracking_consumers_review"] = {
        "schema": "fraud_tracking_consumers_review_v1",
        "consumers": [
            {
                "id": "fixture_reference",
                "source": "scripts/consumer.py",
                "source_sha256": hashlib.sha256(code).hexdigest(),
                "functions": ["read_reference"],
                "kind": "local_consumer",
                "local_migration_blocker": True,
                "migration_gate": "Preserve the frozen ownership and byte hashes",
            }
        ],
    }
    (checkout / inv.CATALOGUE).write_text(json.dumps(policy))
    return checkout, source


def test_consumer_review_reads_only_code_and_keeps_runtime_untouched(
    reviewed_consumer, monkeypatch
):
    root, source = reviewed_consumer
    for name in ["data/tracking/mlflow.db", "data/models/model.skops", "infra/tracking/.env"]:
        put(root, name, b"must not read")
    original = Path.open

    def guard(path, mode="r", *args, **kwargs):
        if "r" in mode and path not in {root / inv.CATALOGUE, source}:
            pytest.fail("Runtime content read: " + str(path))
        return original(path, mode, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", guard)
        summary = inv.tracking_consumers(root)
    report = json.loads(Path(summary["report"]).read_text())
    assert summary["tracking_review_valid"] and summary["issues"] == 0
    assert summary["declared_consumers"] == summary["local_migration_blockers"] == 1
    assert report["consumers"][0]["call_sites"] == [
        {"function": "read_reference", "call": "local_tracking", "line": 3}
    ]
    assert report["consumers"][0]["source_matches_review"]
    assert not report["models_or_databases_opened"] and not report["services_contacted"]
    assert (
        not report["postgres_backup_restore_validated"] and not report["primary_storage_migrated"]
    )
    assert not report["project_wide_storage_inventory_complete"]
    assert Path(summary["report"]).stat().st_mode & 0o777 == 0o600


def test_changed_reviewed_source_requires_manual_review(reviewed_consumer):
    root, source = reviewed_consumer
    source.write_text(source.read_text() + "\n# source changed\n")
    summary = inv.tracking_consumers(root)
    report = json.loads(Path(summary["report"]).read_text())
    assert not summary["tracking_review_valid"]
    assert {x["reason"] for x in report["issues"]} == {"reviewed_source_missing_or_changed"}


def test_new_syntactic_tracking_consumer_is_flagged(reviewed_consumer):
    root, _ = reviewed_consumer
    put(root, "scripts/new_reader.py", b"def consume():\n    return mlflow.MlflowClient()\n")
    summary = inv.tracking_consumers(root)
    report = json.loads(Path(summary["report"]).read_text())
    assert not summary["tracking_review_valid"]
    assert {
        "source": "scripts/new_reader.py",
        "reason": "unreviewed_tracking_call_source",
    } in report["issues"]


def test_missing_reviewed_function_is_flagged(reviewed_consumer):
    root, _ = reviewed_consumer
    policy = json.loads((root / inv.CATALOGUE).read_text())
    policy["tracking_consumers_review"]["consumers"][0]["functions"] = ["missing"]
    (root / inv.CATALOGUE).write_text(json.dumps(policy))
    result = inv.tracking_consumers(root)
    assert not result["tracking_review_valid"] and result["issues"] == 1


def test_duplicate_consumer_identity_is_rejected_before_report(reviewed_consumer):
    root, _ = reviewed_consumer
    policy = json.loads((root / inv.CATALOGUE).read_text())
    policy["tracking_consumers_review"]["consumers"] *= 2
    (root / inv.CATALOGUE).write_text(json.dumps(policy))
    with pytest.raises(ValueError, match="Duplicate"):
        inv.tracking_consumers(root)
    assert not (root / "data/storage/inventory").exists()


def test_symlinked_consumer_is_not_read(reviewed_consumer, tmp_path):
    root, source = reviewed_consumer
    outside = tmp_path / "outside.py"
    outside.write_text("raise RuntimeError('must not read')\n")
    source.unlink()
    source.symlink_to(outside)
    result = inv.tracking_consumers(root)
    report = json.loads(Path(result["report"]).read_text())
    assert not result["tracking_review_valid"]
    assert any(x["reason"] == "source_unreadable_unsafe_or_invalid" for x in report["issues"])


def test_consumer_cli_uses_explicit_read_only_mode(reviewed_consumer, capsys):
    root, _ = reviewed_consumer
    inv.main(["--project-root", str(root), "--tracking-consumers"])
    summary = json.loads(capsys.readouterr().out)
    assert summary["schema"] == "fraud_tracking_consumers_inventory_v1"
    assert summary["tracking_review_valid"] and not summary["services_contacted"]


def test_project_tracking_review_matches_current_sources():
    root = Path(__file__).resolve().parents[2]
    result = inv.tracking_consumers(root)
    report = json.loads(Path(result["report"]).read_text())
    assert result["tracking_review_valid"], report["issues"]
    assert result["local_migration_blockers"] > 0
    assert any(x["id"] == "serving_runtime" and not x["call_sites"] for x in report["consumers"])


def test_consumer_cli_returns_failure_but_preserves_changed_source_report(
    reviewed_consumer, capsys
):
    root, source = reviewed_consumer
    source.write_text(source.read_text() + "\n# review required\n")
    with pytest.raises(SystemExit) as error:
        inv.main(["--project-root", str(root), "--tracking-consumers"])
    assert error.value.code == 1
    output = capsys.readouterr()
    summary = json.loads(output.out)
    assert not summary["tracking_review_valid"] and Path(summary["report"]).is_file()
    assert "review sources" in output.err
