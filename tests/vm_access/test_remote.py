"""VM-side ownership handoff: never compete with a healthy systemd Grafana."""

import subprocess

import pytest
from scripts.vm_access import remote


def test_supervised_grafana_is_reused_without_starting_local_controller(monkeypatch):
    monkeypatch.setattr(
        remote.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0)
    )
    monkeypatch.setattr(remote.services, "probe", lambda *a: True)
    monkeypatch.setattr(remote.controller, "start", lambda *a, **k: pytest.fail("competing start"))
    assert remote.prepare("grafana", "demo")["owner"] == "systemd"
    with pytest.raises(ValueError, match="13001"):
        remote.prepare("grafana", "demo", 13002)
    monkeypatch.setattr(remote.services, "probe", lambda *a: False)
    with pytest.raises(ValueError, match="not healthy"):
        remote.prepare("grafana", "demo")


def test_other_accesses_use_existing_controller_and_portal_identity(monkeypatch):
    seen = []
    monkeypatch.setattr(
        remote.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 3)
    )

    def start(service, **kwargs):
        seen.append((service, kwargs))
        return {"url": "http://127.0.0.1:18521"}

    monkeypatch.setattr(remote.controller, "start", start)
    result = remote.prepare("portal", "own-portal", 18521)
    assert result["owner"] == "local_services"
    assert seen == [("portal", {"port": 18521, "portal_id": "own-portal"})]
    with pytest.raises(ValueError, match="Unsupported"):
        remote.prepare("postgres", "own-portal")
