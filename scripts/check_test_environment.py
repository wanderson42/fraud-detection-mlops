"""Confirm Poetry targets the current tox environment before synchronizing packages."""

from pathlib import Path
import subprocess
import sys


def main() -> None:
    expected = Path(sys.prefix).resolve()
    actual = Path(
        subprocess.check_output(["poetry", "env", "info", "--path"], text=True).strip()
    ).resolve()
    if actual != expected:
        raise SystemExit(f"Poetry target mismatch: {actual} != {expected}")
    print(f"Python: {sys.version.split()[0]} | tox environment: {expected}")


if __name__ == "__main__":
    main()
