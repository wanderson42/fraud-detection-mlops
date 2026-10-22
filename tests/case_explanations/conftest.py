"""Reuse the real synthetic HTTP replay fixtures; no historical artifacts or Kubernetes."""

from contextlib import contextmanager
import json

import pytest
from scripts.case_explanations import controller as c
from scripts.product_flow import controller as flow
from tests.operational_round.test_make import dispatch  # noqa: F401
from tests.product_flow.conftest import prepared, release, scorer, server  # noqa: F401


@pytest.fixture
def accepted(prepared, release, server, tmp_path, monkeypatch):  # noqa: F811
    folder, value, _ = prepared
    path, manifest_sha = release
    value["active"] = {
        "slot": "blue",
        "paths": {"blue": str(path)},
        "plan": {
            "releases": {
                "blue": {"manifest_sha256": manifest_sha, "manifest": value["release_manifest"]}
            }
        },
    }
    (folder / "input.json").write_text(json.dumps(value))
    monkeypatch.setattr(flow, "read", lambda _: value)
    monkeypatch.setattr(flow, "work", lambda _: folder)

    @contextmanager
    def target(_):
        client = scorer(server[0], value)
        client.ready()
        yield client
        client.ready()

    monkeypatch.setattr(flow, "target", target)
    flow.execute("fixture", "rehearse", True)
    monkeypatch.setattr(c, "resolve_active", lambda _: value["active"])
    monkeypatch.setattr(c, "work", lambda key: tmp_path / "reports/case-explanations" / key)
    monkeypatch.setattr(c, "committed", c.pins)
    # Release loading is path-validated in production; this fixture uses pytest's isolated release.
    managed = c.managed_destination
    monkeypatch.setattr(
        c, "managed_destination", lambda p, area: p if p == path else managed(p, area)
    )
    return folder, value, server
