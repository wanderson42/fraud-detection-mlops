"""Initialize and operate the local stack without deleting persistent volumes."""

import argparse
import os
from pathlib import Path
import secrets
import subprocess

ROOT = Path(__file__).resolve().parents[2]
INFRA = ROOT / "infra/observability"


def initialize(state, env_file=INFRA / ".env"):
    state = state.absolute()
    if state.resolve() != state or any(c in str(state) for c in "\n\r$#\"'"):
        raise ValueError("Use an unredirected local state path without dotenv metacharacters")
    if env_file.exists() or env_file.is_symlink():
        raise ValueError("Environment already exists; preserve credentials and state selection")
    state.mkdir(parents=True, exist_ok=True)
    values = {
        "LOCAL_UID": str(os.getuid()),
        "LOCAL_GID": str(os.getgid()),
        "STATE_DIRECTORY": str(state),
        "GRAFANA_ADMIN_PASSWORD": secrets.token_hex(20),
        "RUSTFS_ACCESS_KEY": secrets.token_hex(10),
        "RUSTFS_SECRET_KEY": secrets.token_hex(20),
    }
    descriptor = os.open(env_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as file:
        file.write("".join(f'{key}="{value}"\n' for key, value in values.items()))
    return {"state_directory": str(state), "environment_file": str(env_file)}


def compose_args(action, storage=False):
    command = [
        "docker",
        "compose",
        "--env-file",
        str(INFRA / ".env"),
        "-f",
        str(INFRA / "compose.yaml"),
    ]
    if storage:
        command += ["--profile", "storage"]
    command += {
        "up": ["up", "-d", "--build"],
        "check": ["config", "--quiet"],
        "status": ["ps"],
        "down": ["down"],
    }[action]
    return command


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["init", "up", "check", "status", "down"])
    parser.add_argument("--state", type=Path, default=ROOT / "data/replay/paced-native-v1")
    parser.add_argument("--storage", action="store_true")
    args = parser.parse_args()
    if args.action == "init":
        result = initialize(args.state)
        print(
            f"Initialized {result['environment_file']}; credentials remain in that ignored file."
        )
    else:
        if not (INFRA / ".env").is_file():
            parser.error("Run init first")
        subprocess.run(compose_args(args.action, args.storage), cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
