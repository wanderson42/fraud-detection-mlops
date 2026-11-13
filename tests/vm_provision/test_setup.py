"""Run the Python setup phase against fake tools and a temporary home only."""

import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(os.geteuid() == 0, reason="Installer refuses root by design")


def executable(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/bash\nset -eu\n" + body + "\n")
    path.chmod(0o755)


@pytest.fixture
def setup_host(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".profile").write_text("# existing user profile\nexport KEEP_SETTING=yes\n")
    tools = tmp_path / "tools"
    tools.mkdir()
    log = tmp_path / "calls"
    executable(tools / "uname", "echo x86_64")
    executable(tools / "poetry", "echo 'Poetry (version 1.8.2)'")
    executable(tools / "uv", 'printf "uv %s\\n" "$*" >> "$CALLS"\necho /synthetic/python')
    executable(
        tools / "pipx",
        '''printf 'pipx %s\\n' "$*" >> "$CALLS"
test "$1" = install
test "$2" = poetry==2.4.3
mkdir -p "$HOME/.local/bin"
cat > "$HOME/.local/bin/poetry" <<'POETRY'
#!/bin/bash
if [[ "$1" == --version ]]; then echo 'Poetry (version 2.4.3)'; else
  printf 'user-poetry %s\\n' "$*" >> "$CALLS"
fi
POETRY
chmod +x "$HOME/.local/bin/poetry"''',
    )
    # Model the supported Ubuntu guard; the actual runner may be another Linux.
    env_file = tmp_path / "bash-env"
    env_file.write_text(
        'source() { if [[ "$1" == /etc/os-release ]]; then '
        'ID=ubuntu; VERSION_ID=24.04; else builtin source "$@"; fi; }\n'
    )
    env = dict(
        os.environ,
        HOME=str(home),
        PATH=f"{tools}:/usr/bin:/bin",
        CALLS=str(log),
        BASH_ENV=str(env_file),
    )
    return home, tools, log, env


def run_setup(env):
    return subprocess.run(
        ["bash", str(ROOT / "scripts/vm_setup.sh"), "python"],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_system_poetry_does_not_prevent_pinned_user_install_and_new_login_path(setup_host):
    home, tools, log, env = setup_host
    old_system = (tools / "poetry").read_bytes()
    result = run_setup(env)
    assert result.returncode == 0, result.stderr
    assert "pipx install poetry==2.4.3" in log.read_text()
    assert "user-poetry sync --only main,pipeline,dev" in log.read_text()
    assert (tools / "poetry").read_bytes() == old_system
    login_env = dict(env)
    login_env.pop("BASH_ENV")
    login = subprocess.run(
        [
            "bash",
            "--noprofile",
            "--norc",
            "-c",
            'source "$HOME/.profile"; command -v poetry; poetry --version; echo "$KEEP_SETTING"',
        ],
        env=login_env,
        text=True,
        capture_output=True,
        check=True,
    )
    assert str(home / ".local/bin/poetry") in login.stdout
    assert "Poetry (version 2.4.3)" in login.stdout
    assert login.stdout.endswith("yes\n")
    assert run_setup(env).returncode == 0
    assert (home / ".profile").read_text().count("# fraud-vm-setup:") == 1
    assert log.read_text().count("pipx install poetry==2.4.3") == 1


def test_wrong_existing_user_poetry_is_preserved_and_refused(setup_host):
    home, _, log, env = setup_host
    local = home / ".local/bin/poetry"
    executable(local, "echo 'Poetry (version 1.8.2)'")
    original = local.read_bytes()
    profile = (home / ".profile").read_bytes()
    result = run_setup(env)
    assert result.returncode == 2
    assert "existing installation was preserved" in result.stderr
    assert local.read_bytes() == original
    assert (home / ".profile").read_bytes() == profile
    assert not log.exists()
