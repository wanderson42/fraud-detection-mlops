"""Select complete checks unless a Git comparison proves documentation-only changes."""

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import subprocess
from urllib.parse import unquote, urlsplit


def git(*args):
    return subprocess.check_output(["git", *args], stderr=subprocess.PIPE)


def commit(value):
    if not re.fullmatch(r"[0-9a-f]{40,64}", value):
        raise ValueError("Invalid commit identifier")
    return git("rev-parse", "--verify", value + "^{commit}").decode().strip()


def document_path(path):
    return path == "README.md" or (path.startswith("docs/") and path.endswith(".md"))


def notebook(path):
    return path.startswith("notebooks/") and path.endswith(".ipynb")


def changed(base, head):
    return [
        p
        for p in git("diff", "--no-renames", "--name-only", "-z", base, head).decode().split("\0")
        if p
    ]


def read(ref, path):
    return git("show", f"{ref}:{path}").decode()


def load_notebook(text):
    doc = json.loads(text)
    if not isinstance(doc, dict) or doc.get("nbformat") != 4:
        raise ValueError("Expected notebook format 4")
    if not isinstance(doc.get("cells"), list) or not isinstance(doc.get("metadata"), dict):
        raise TypeError("Invalid notebook structure")
    for cell in doc["cells"]:
        if not isinstance(cell, dict) or cell.get("cell_type") not in {"markdown", "code", "raw"}:
            raise ValueError("Invalid notebook cell")
        source = cell.get("source")
        if not isinstance(source, str) and not (
            isinstance(source, list) and all(isinstance(line, str) for line in source)
        ):
            raise ValueError("Invalid cell source")
        if not isinstance(cell.get("metadata"), dict):
            raise TypeError("Invalid cell metadata")
    return doc


def executable_notebook(text):
    doc = load_notebook(text)
    return {**doc, "cells": [c for c in doc["cells"] if c["cell_type"] != "markdown"]}


def comparison(event, payload):
    head = commit(
        payload.get("after") or payload.get("pull_request", {}).get("head", {}).get("sha", "")
    )
    if event == "pull_request":
        base = commit(payload["pull_request"]["base"]["sha"])
        return git("merge-base", base, head).decode().strip(), head
    if event == "push" and payload.get("ref") == "refs/heads/main":
        return commit(payload["before"]), head
    raise ValueError("Manual or unsupported event: complete checks required")


def classify(event, payload):
    result = {"full": True, "base": "", "head": "", "reason": "comparison_unavailable"}
    try:
        if not isinstance(payload, dict):
            return result
        if event not in {"push", "pull_request"}:
            return {**result, "reason": "manual_or_unknown_event"}
        base, head = comparison(event, payload)
        result.update(base=base, head=head)
        files = changed(base, head)
        if not files:
            return {**result, "reason": "empty_comparison"}
        for path in files:
            if document_path(path):
                continue
            if notebook(path) and executable_notebook(read(base, path)) == executable_notebook(
                read(head, path)
            ):
                continue
            return {**result, "reason": "executable_or_unknown_change"}
        return {**result, "full": False, "reason": "documentation_only"}
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError):  # fmt: skip
        # Unknown history, new/deleted notebooks, malformed JSON: never skip the full gate.
        return result


def tree(ref):
    rows = git("ls-tree", "-r", "-z", ref).decode().split("\0")
    return {
        path: mode
        for row in rows
        if row
        for meta, path in [row.split("\t", 1)]
        for mode in [meta.split()[0]]
    }


def broken_links(ref, entries):
    targets = set(entries)
    for path in entries:
        targets.update(str(p) for p in PurePosixPath(path).parents)
    broken = set()
    for path in entries:
        if not document_path(path):
            continue
        if entries[path] != "100644" and entries[path] != "100755":
            raise ValueError("Documentation must be regular files: " + path)
        for url in re.findall(r"!?\[[^\]\n]*\]\(([^\s)]+)\)", read(ref, path)):
            parts = urlsplit(url)
            if parts.scheme or parts.netloc or not parts.path:
                continue
            target = posixpath.normpath(
                posixpath.join(posixpath.dirname(path), unquote(parts.path))
            )
            if target not in targets:
                broken.add((path, url))
    return broken


def check(base, head):
    base, head = commit(base), commit(head)
    previous, current = tree(base), tree(head)
    for path in changed(base, head):
        if notebook(path) and path in current:
            if current[path] not in {"100644", "100755"}:
                raise ValueError("Notebook must be a regular file")
            load_notebook(read(head, path))
    new_broken = broken_links(head, current) - broken_links(base, previous)
    if new_broken:
        raise ValueError("New broken local file links: " + json.dumps(sorted(new_broken)))
    return {
        "status": "documentation_checked",
        "notebooks_executed": False,
        "local_file_links": "no_new_broken_paths",
        "remote_urls_and_anchors_checked": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["scope", "check"])
    parser.add_argument(
        "--event", default=os.environ.get("GITHUB_EVENT_NAME", "workflow_dispatch")
    )
    parser.add_argument("--event-file", default=os.environ.get("GITHUB_EVENT_PATH"))
    parser.add_argument("--base")
    parser.add_argument("--head")
    args = parser.parse_args()
    if args.action == "scope":
        try:
            payload = json.loads(Path(args.event_file).read_text()) if args.event_file else {}
            result = classify(args.event, payload)
        except (ValueError, TypeError, OSError):  # fmt: skip
            result = {"full": True, "base": "", "head": "", "reason": "event_unavailable"}
        if output := os.environ.get("GITHUB_OUTPUT"):
            with open(output, "a") as stream:
                for key, value in result.items():
                    print(
                        f"{key}={str(value).lower() if isinstance(value, bool) else value}",
                        file=stream,
                    )
        print(json.dumps(result, indent=2))
    else:
        print(json.dumps(check(args.base, args.head), indent=2))


if __name__ == "__main__":
    main()
