"""Local access stays loopback-only and forwards no scientific credentials."""

from types import SimpleNamespace

import pytest
from scripts.fraud_portal import controller as c


def test_launcher_uses_isolated_runtime_and_no_inherited_service_credentials(
    tmp_path, monkeypatch
):
    runtime = tmp_path / "runtime"
    (runtime / "bin").mkdir(parents=True)
    (runtime / "bin/python").touch()
    monkeypatch.setattr(c, "ENVIRONMENT", runtime)
    monkeypatch.setattr(
        c,
        "config",
        lambda key: ({"directory": str(tmp_path / "bundle"), "manifest_sha256": "a" * 64}, {}, {}),
    )
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "not-forwarded")
    monkeypatch.setenv("POSTGRES_PASSWORD", "not-forwarded")
    calls = []
    monkeypatch.setattr(
        c.subprocess, "run", lambda *a, **k: calls.append((a, k)) or SimpleNamespace(returncode=0)
    )
    assert c.open_portal("test", 18501) == 0
    args, kwargs = calls[0]
    assert "--server.address=127.0.0.1" in args[0]
    assert "--theme.base=dark" in args[0]
    assert "--theme.textColor=#E7EEF8" in args[0]
    assert "--theme.backgroundColor=#0B1220" in args[0]
    assert "--theme.sidebar.textColor=#F1F5F9" in args[0]
    assert "--client.toolbarMode=viewer" in args[0]
    assert args[0][0] == str(runtime / "bin/python")
    assert (
        "AWS_SECRET_ACCESS_KEY" not in kwargs["env"] and "POSTGRES_PASSWORD" not in kwargs["env"]
    )
    assert kwargs["env"]["STREAMLIT_BROWSER_GATHER_USAGE_STATS"] == "false"


@pytest.mark.parametrize("name", ["../escape", "/absolute", "bad id", ""])
def test_identifiers_do_not_escape_managed_source_paths(name):
    with pytest.raises(ValueError):
        c.identifier(name)
