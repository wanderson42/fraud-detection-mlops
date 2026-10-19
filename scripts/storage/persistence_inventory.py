"""Inventory checkout metadata and declared producers without reading runtime file contents."""

import argparse
import ast
from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import tempfile

CATALOGUE = Path("references/persistence_catalogue_v1.json")


def normalized(value):
    path = PurePosixPath(value)
    if (
        not value
        or not path.parts
        or path.is_absolute()
        or ".." in path.parts
        or str(path) != value
        or "\\" in value
    ):
        raise ValueError("Expected normalized checkout-relative catalogue path")
    return value


def within(name, prefix):
    return name == prefix or name.startswith(prefix + "/")


def check_path(root, relative):
    path = root / relative
    for parent in [path, *path.parents]:
        if parent == root:
            break
        if parent.is_symlink():
            raise ValueError("Symlink in report/catalogue path")
    return path


def load_catalogue(root):
    path = check_path(root, CATALOGUE)
    raw = path.read_bytes()
    policy = json.loads(raw)
    if policy["schema"] != "fraud_persistence_catalogue_v1":
        raise ValueError("Unsupported persistence catalogue")
    prefixes = [normalized(r["prefix"]) for r in policy["locations"]]
    if len(prefixes) != len(set(prefixes)):
        raise ValueError("Duplicate destination prefix")
    for key in ("source_prefixes", "tooling_roots", "tooling_directory_names"):
        for name in policy[key]:
            normalized(name)
    for rule in policy["locations"]:
        for source in rule["producers"]:
            if not check_path(root, Path(normalized(source))).is_file():
                raise ValueError("Declared producer missing: " + source)
    return policy, hashlib.sha256(raw).hexdigest()


def git_files(root):
    top = (
        subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=root,
            capture_output=True,
            check=True,
            timeout=30,
        )
        .stdout.decode()
        .strip()
    )
    if Path(top).resolve() != root:
        raise ValueError("Choose the Git checkout root")
    files = subprocess.run(
        ["git", "ls-files", "-z", "--cached"],
        cwd=root,
        capture_output=True,
        check=True,
        timeout=30,
    ).stdout
    return {p.decode(errors="surrogateescape") for p in files.split(b"\0") if p}


def classify(name, tracked, policy):
    # Never read local credentials; recognise only their filenames.
    base = PurePosixPath(name).name
    if base == ".env" or (base.startswith(".env.") and base != ".env.example"):
        return "local_configuration_metadata", "local_operator", False
    if name in tracked and base == ".gitkeep":
        return "versioned_control", "git", False
    for rule in sorted(policy["locations"], key=lambda row: len(row["prefix"]), reverse=True):
        if within(name, rule["prefix"]):
            return rule["family"], rule["owner"], rule["review_required"]
    if name in tracked:
        return "versioned_control", "git", False
    if any(within(name, p) for p in policy["source_prefixes"]):
        return "unversioned_source_or_control", "git_review", False
    return "unclassified_review", "unassigned", True


def database_candidate(name):
    lower = name.lower()
    return lower.endswith(
        (
            ".db",
            ".sqlite",
            ".sqlite3",
            ".duckdb",
            ".db-wal",
            ".db-shm",
            ".db-journal",
            ".sqlite-wal",
            ".sqlite-shm",
            ".sqlite-journal",
            ".duckdb.wal",
        )
    )


def scan(root, tracked, policy):
    entries, issues, excluded = [], [], []
    totals = defaultdict(lambda: {"files": 0, "bytes": 0})

    def walk_error(error):
        path = Path(error.filename)
        issues.append(
            {"path": path.relative_to(root).as_posix(), "reason": "unreadable_directory"}
        )

    for directory, directories, files in os.walk(root, followlinks=False, onerror=walk_error):
        base = Path(directory)
        directories.sort()
        for name in directories.copy():
            path = base / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                directories.remove(name)
                issues.append({"path": relative, "reason": "symlink_not_followed"})
            elif relative in policy["tooling_roots"] or (
                not within(relative, "data") and name in policy["tooling_directory_names"]
            ):
                directories.remove(name)
                excluded.append(
                    {
                        "path": relative,
                        "reason": "registered_tooling_tree",
                        "recursive_bytes": None,
                    }
                )
        for name in sorted(files):
            path = base / name
            relative = path.relative_to(root).as_posix()
            if relative in policy["tooling_roots"]:
                excluded.append(
                    {
                        "path": relative,
                        "reason": "registered_tooling_tree",
                        "recursive_bytes": None,
                    }
                )
                continue
            try:
                info = path.lstat()
            except OSError:
                issues.append({"path": relative, "reason": "stat_failed"})
                continue
            if not stat.S_ISREG(info.st_mode):
                issues.append({"path": relative, "reason": "nonregular_or_symlink_not_read"})
                continue
            group, owner, review = classify(relative, tracked, policy)
            entries.append(
                {
                    "path": relative,
                    "bytes": info.st_size,
                    "mtime_ns": info.st_mtime_ns,
                    "family": group,
                    "owner": owner,
                    "review_required": review,
                    "tracked": relative in tracked,
                    "database_candidate": database_candidate(relative),
                }
            )
            totals[group]["files"] += 1
            totals[group]["bytes"] += info.st_size
    return {
        "entries": entries,
        "families": dict(totals),
        "issues": issues,
        "excluded_tooling_trees": excluded,
        "scan_complete": not issues,
        "files": len(entries),
        "logical_bytes": sum(e["bytes"] for e in entries),
    }


def inventory(root):
    root = root.resolve()
    policy, identity = load_catalogue(root)
    data = scan(root, git_files(root), policy)
    report = {
        "schema": "fraud_persistence_inventory_v1",
        "status": "review_required",
        "scope": "checkout_metadata_except_declared_tooling_trees_with_catalogue_and_Git_index",
        "catalogue_sha256": identity,
        **data,
        "declared_locations": policy["locations"],
        "external_boundaries": policy["external_boundaries"],
        "runtime_contents_read": False,
        "models_or_databases_opened": False,
        "services_contacted": False,
        "consistent_snapshot": False,
        "destination_policy_enforced": False,
        "project_wide_storage_inventory_complete": False,
        "recoverability_validated": False,
        "primary_storage_migrated": False,
        "report_written_after_scan": True,
    }
    destination = check_path(root, Path("data/storage/inventory"))
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix="persistence-", suffix=".json", dir=destination, delete=False
    ) as f:
        f.write((json.dumps(report, sort_keys=True, allow_nan=False) + "\n").encode())
        path = Path(f.name)
    return {
        k: report[k]
        for k in (
            "schema",
            "status",
            "scope",
            "scan_complete",
            "files",
            "logical_bytes",
            "families",
            "project_wide_storage_inventory_complete",
        )
    } | {
        "database_candidates": sum(e["database_candidate"] for e in data["entries"]),
        "files_requiring_review": sum(e["review_required"] for e in data["entries"]),
        "issues": len(data["issues"]),
        "excluded_tooling_trees": len(data["excluded_tooling_trees"]),
        "external_boundaries_not_inspected": len(policy["external_boundaries"]),
        "report": str(path),
    }


# Syntactic watchpoints, not alias resolution or an exhaustive call graph.
TRACKING_CALLS = {
    "local_tracking",
    "http_tracking",
    "MlflowClient",
    "download_pipeline",
    "load_tracked_model",
    "log_candidate",
    "tracking_uri",
    "download_artifacts",
    "reference_model",
    "reference_identity",
    "load_frozen_model",
    "recover_finished_run",
    "check_tracked_checkpoint",
    "get_tracking_uri",
    "set_tracking_uri",
}
TRACKING_SOURCE_ROOTS = ("fraud_detection_mlops", "scripts", "examples")


def tracking_sites(raw):
    """Parse source without imports, recording only declared syntactic watchpoints."""
    tree = ast.parse(raw)
    sites, symbols, owners = [], set(), []

    class Calls(ast.NodeVisitor):
        def visit_FunctionDef(self, node):
            owners.append(node.name)
            symbols.add(".".join(owners))
            self.generic_visit(node)
            owners.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_ClassDef(self, node):
            owners.append(node.name)
            self.generic_visit(node)
            owners.pop()

        def visit_Call(self, node):
            tail = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else (node.func.id if isinstance(node.func, ast.Name) else "")
            )
            if tail in TRACKING_CALLS:
                sites.append(
                    {"function": ".".join(owners) or "<module>", "call": tail, "line": node.lineno}
                )
            self.generic_visit(node)

    Calls().visit(tree)
    return sorted(sites, key=lambda item: (item["line"], item["call"])), symbols


def tracking_consumers(root):
    """Check a curated source review; runtime data and external services are not read."""
    root = root.resolve()
    policy, catalogue_hash = load_catalogue(root)
    tracked = git_files(root)
    review = policy.get("tracking_consumers_review", {})
    if review.get("schema") != "fraud_tracking_consumers_review_v1":
        raise ValueError("Tracking consumer review missing from catalogue")
    declared = review["consumers"]
    paths = [normalized(item["source"]) for item in declared]
    if len(paths) != len(set(paths)) or len({item["id"] for item in declared}) != len(declared):
        raise ValueError("Duplicate reviewed consumer source or identity")
    for name in paths:
        if not name.endswith(".py") or not any(within(name, p) for p in TRACKING_SOURCE_ROOTS):
            raise ValueError("Consumer review must name project Python sources")
    observed, issues = {}, []
    for source_root in TRACKING_SOURCE_ROOTS:
        directory = check_path(root, Path(source_root))
        if not directory.exists():
            continue
        for path in sorted(directory.rglob("*.py")):
            name = path.relative_to(root).as_posix()
            try:
                checked = check_path(root, Path(name))
                if checked.stat().st_size > 1024 * 1024:
                    raise ValueError("Python source exceeds review byte limit")
                raw = checked.read_bytes()
                sites, symbols = tracking_sites(raw)
                observed[name] = {
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "sites": sites,
                    "symbols": symbols,
                }
                if sites and name not in paths:
                    issues.append({"source": name, "reason": "unreviewed_tracking_call_source"})
            except OSError, ValueError, SyntaxError:
                issues.append({"source": name, "reason": "source_unreadable_unsafe_or_invalid"})
    consumers = []
    for item in declared:
        name = item["source"]
        actual = observed.get(name)
        valid = actual is not None and actual["sha256"] == item["source_sha256"]
        if not valid:
            issues.append({"source": name, "reason": "reviewed_source_missing_or_changed"})
        symbols_valid = actual is not None and set(item["functions"]) <= actual["symbols"]
        if not symbols_valid:
            issues.append({"source": name, "reason": "reviewed_function_missing"})
        consumers.append(
            {
                **item,
                "source_matches_review": valid,
                "functions_present": symbols_valid,
                "tracked_in_git_index": name in tracked,
                "call_sites": actual["sites"] if actual else [],
            }
        )
    # Detect changes during the scan instead of claiming an atomic worktree snapshot.
    for name in paths:
        actual = observed.get(name)
        if actual is None:
            continue
        try:
            digest = hashlib.sha256(check_path(root, Path(name)).read_bytes()).hexdigest()
            if digest != actual["sha256"]:
                issues.append({"source": name, "reason": "reviewed_source_changed_during_scan"})
        except OSError, ValueError:
            issues.append({"source": name, "reason": "reviewed_source_changed_during_scan"})
    if hashlib.sha256(check_path(root, CATALOGUE).read_bytes()).hexdigest() != catalogue_hash:
        raise ValueError("Consumer catalogue changed during scan")
    report = {
        "schema": "fraud_tracking_consumers_inventory_v1",
        "status": "review_required",
        "scope": "curated_source_hashes_functions_and_syntactic_watchpoints_not_runtime_lineage",
        "catalogue_sha256": catalogue_hash,
        "tracking_review_valid": not issues,
        "source_files_scanned": len(observed),
        "declared_consumers": len(consumers),
        "local_migration_blockers": sum(row["local_migration_blocker"] for row in consumers),
        "detected_call_sites": sum(len(row["sites"]) for row in observed.values()),
        "consumers": consumers,
        "issues": issues,
        "source_contents_read": True,
        "runtime_contents_read": False,
        "models_or_databases_opened": False,
        "services_contacted": False,
        "scientific_evaluation_executed": False,
        "consistent_snapshot": False,
        "postgres_backup_restore_validated": False,
        "primary_storage_migrated": False,
        "project_wide_storage_inventory_complete": False,
        "limits": [
            "Syntactic call names can miss aliases, dynamic calls, subprocesses and external plugins",
            "Reviewed records include indirect consumers and historical maintenance by manual review",
            "Valid review does not imply HTTP compatibility, recovery or permission to delete local stores",
        ],
    }
    destination = check_path(root, Path("data/storage/inventory"))
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix="tracking-consumers-", suffix=".json", dir=destination, delete=False
    ) as handle:
        handle.write((json.dumps(report, sort_keys=True, allow_nan=False) + "\n").encode())
        path = Path(handle.name)
    return {
        key: report[key]
        for key in (
            "schema",
            "status",
            "scope",
            "tracking_review_valid",
            "source_files_scanned",
            "declared_consumers",
            "local_migration_blockers",
            "detected_call_sites",
            "models_or_databases_opened",
            "services_contacted",
            "postgres_backup_restore_validated",
            "primary_storage_migrated",
            "project_wide_storage_inventory_complete",
        )
    } | {"issues": len(issues), "report": str(path)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--tracking-consumers",
        action="store_true",
        help="Review declared source consumers; never opens runtime data or services",
    )
    args = parser.parse_args(argv)
    try:
        action = tracking_consumers if args.tracking_consumers else inventory
        result = action(args.project_root)
        print(json.dumps(result, indent=2, allow_nan=False))
        if args.tracking_consumers and not result["tracking_review_valid"]:
            parser.exit(
                1, "Tracking consumer review differs; preserve the report and review sources\n"
            )
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        parser.exit(1, f"Persistence inventory stopped: {error}\n")


if __name__ == "__main__":
    main()
