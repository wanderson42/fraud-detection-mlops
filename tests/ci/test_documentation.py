"""Exercise real Git comparisons, fail-closed selection and notebook preservation."""

import ast
import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from scripts.ci import documentation as d
import yaml

ROOT = Path(__file__).resolve().parents[2]


def run(*args):
    return subprocess.check_output(["git", *args], stderr=subprocess.PIPE).decode().strip()


def save(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def snapshot():
    run("add", ".")
    run(
        "-c",
        "user.name=CI Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-qm",
        "fixture",
    )
    return run("rev-parse", "HEAD")


@pytest.fixture
def history(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run("init", "-q")
    nb = {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {},
        "cells": [
            {"cell_type": "markdown", "id": "intro", "metadata": {}, "source": ["Introduction"]},
            {
                "cell_type": "code",
                "id": "code",
                "metadata": {},
                "source": ["print(1)"],
                "execution_count": 1,
                "outputs": [{"output_type": "stream", "name": "stdout", "text": ["1\n"]}],
            },
        ],
    }
    save("README.md", "# Fixture\n[Existing](docs/existing.md)\n[Old missing](old-missing.md)\n")
    save("docs/existing.md", "# Existing\n")
    save("notebooks/example.ipynb", json.dumps(nb))
    return snapshot(), nb


def event(base, head, kind="pull_request"):
    if kind == "push":
        return {"ref": "refs/heads/main", "before": base, "after": head}
    return {"pull_request": {"base": {"sha": base}, "head": {"sha": head}}}


@pytest.mark.parametrize("kind", ["pull_request", "push"])
def test_documentation_only_pr_and_main_merge_take_light_path(history, kind):
    base, _ = history
    save("docs/existing.md", "# Improved documentation\n")
    head = snapshot()
    assert d.classify(kind, event(base, head, kind))["full"] is False
    assert d.check(base, head)["notebooks_executed"] is False


def test_markdown_cells_only_preserve_all_executable_notebook_content(history):
    base, nb = history
    nb["cells"].insert(
        0, {"cell_type": "markdown", "id": "new", "metadata": {}, "source": ["Guide"]}
    )
    save("notebooks/example.ipynb", json.dumps(nb, indent=1))
    head = snapshot()
    assert d.classify("pull_request", event(base, head))["full"] is False


@pytest.mark.parametrize(
    "change", ["source", "outputs", "execution_count", "metadata", "new_code"]
)
def test_executable_notebook_changes_require_complete_gate(history, change):
    base, original = history
    nb = copy.deepcopy(original)
    if change == "metadata":
        nb["metadata"]["kernelspec"] = {"name": "another-kernel"}
    elif change == "new_code":
        nb["cells"].append(copy.deepcopy(nb["cells"][1]))
    else:
        nb["cells"][1][change] = {"source": ["print(2)"], "outputs": [], "execution_count": 2}[
            change
        ]
    save("notebooks/example.ipynb", json.dumps(nb))
    head = snapshot()
    assert d.classify("pull_request", event(base, head))["full"] is True


@pytest.mark.parametrize(
    "path",
    [
        "scripts/task.py",
        ".github/workflows/ci.yml",
        "poetry.lock",
        "references/evidence/receipt.json",
        "infra/helm/chart.yaml",
        "unknown.txt",
    ],
)
def test_unknown_or_executable_paths_never_take_documentation_shortcut(history, path):
    base, _ = history
    save(path, "new content")
    head = snapshot()
    assert d.classify("pull_request", event(base, head))["full"] is True


def test_pr_compares_whole_branch_even_if_last_commit_only_changes_markdown(history):
    base, _ = history
    save("scripts/task.py", "print(1)")
    snapshot()
    save("docs/existing.md", "# Last documentation commit")
    head = snapshot()
    assert d.classify("pull_request", event(base, head))["full"] is True


def test_missing_history_malformed_event_and_manual_execution_require_full(history):
    base, _ = history
    assert d.classify("workflow_dispatch", {})["full"] is True
    assert (
        d.classify("push", {"before": "0" * 40, "after": base, "ref": "refs/heads/main"})["full"]
        is True
    )
    assert d.classify("pull_request", {"pull_request": {}})["full"] is True
    assert d.classify("pull_request", [])["full"] is True


def test_new_broken_link_and_deleted_link_target_fail_but_old_debt_is_preserved(history):
    base, _ = history
    save("docs/existing.md", "[New missing](new-missing.md)")
    head = snapshot()
    with pytest.raises(ValueError, match="New broken"):
        d.check(base, head)
    run("rm", "docs/existing.md")
    deleted = snapshot()
    with pytest.raises(ValueError, match="docs/existing.md"):
        d.check(base, deleted)


def test_malformed_notebook_routes_full_and_fails_structure_gate(history):
    base, _ = history
    save("notebooks/example.ipynb", "{not valid JSON")
    head = snapshot()
    assert d.classify("pull_request", event(base, head))["full"] is True
    with pytest.raises(ValueError):
        d.check(base, head)


def test_scope_command_writes_verified_github_outputs(history, tmp_path):
    base, _ = history
    save("docs/existing.md", "# Only documentation")
    head = snapshot()
    event_file = tmp_path / "event.json"
    event_file.write_text(json.dumps(event(base, head)))
    output = tmp_path / "github-output"
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/ci/documentation.py"), "scope"],
        env=dict(
            os.environ,
            GITHUB_EVENT_NAME="pull_request",
            GITHUB_EVENT_PATH=str(event_file),
            GITHUB_OUTPUT=str(output),
        ),
        text=True,
        capture_output=True,
        check=True,
    )
    assert json.loads(result.stdout)["full"] is False
    assert output.read_text().splitlines() == [
        "full=false",
        "base=" + base,
        "head=" + head,
        "reason=documentation_only",
    ]


def test_workflow_keeps_required_quality_name_and_serving_dependency_guards():
    workflow = yaml.load((ROOT / ".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader)
    assert workflow["on"]["push"]["branches"] == ["main"]
    assert "pull_request" in workflow["on"] and "workflow_dispatch" in workflow["on"]
    assert workflow["permissions"] == {"contents": "read"}
    quality = workflow["jobs"]["quality"]
    assert quality["name"] == "Lint, formatting and tests"
    assert quality["if"] == "${{ !cancelled() }}"
    assert quality["outputs"]["full"] == "${{ needs.changes.outputs.full != 'false' }}"
    steps = {step["name"]: step for step in quality["steps"]}
    for name in [
        "Set up Python",
        "Install Poetry",
        "Validate project and lockfile",
        "Install dependencies and project",
        "Run isolated quality checks with tox",
    ]:
        assert steps[name]["if"] == "needs.changes.outputs.full != 'false'"
    assert "Check documentation paths and notebook structure" in steps
    assert (
        workflow["jobs"]["terraform-templates"]["if"]
        == "${{ !cancelled() && needs.changes.outputs.full != 'false' }}"
    )
    delivery = workflow["jobs"]["serving-delivery"]
    assert delivery["needs"] == "quality"
    assert delivery["if"] == "needs.quality.outputs.full == 'true'"
    assert workflow["jobs"]["serving-consumer"]["needs"] == "serving-delivery"


def test_selector_supports_system_python_312_syntax():
    source = (ROOT / "scripts/ci/documentation.py").read_text()
    ast.parse(source, feature_version=(3, 12))
