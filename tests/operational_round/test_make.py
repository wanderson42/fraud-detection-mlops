"""Real Make must require the writer declaration and stop after a failed stage."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def dispatch(tmp_path):
    if not shutil.which("make"):
        pytest.skip("GNU Make required")
    log = tmp_path / "calls.jsonl"
    recorder = tmp_path / "poetry"
    recorder.write_text(
        "#!" + sys.executable + "\nimport json,os,sys\n"
        "with open(os.environ['CALL_LOG'], 'a') as f:\n"
        " f.write(json.dumps(sys.argv[1:])+'\\n')\n"
        "raise SystemExit(17 if os.environ.get('FAIL_ACTION') in sys.argv[1:] else 0)\n"
    )
    recorder.chmod(0o700)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ROUND_", "MAKE"))}
    env.update(POETRY=str(recorder), CALL_LOG=str(log))

    def run(*goals, **overrides):
        result = subprocess.run(
            ["make", "--no-print-directory", *goals],
            cwd=ROOT,
            env={**env, **overrides},
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        calls = [json.loads(v) for v in log.read_text().splitlines()] if log.exists() else []
        return result, calls

    return run


def test_round_promotion_requires_operator_declaration(dispatch):
    result, calls = dispatch("round-promote")
    assert result.returncode != 0 and calls == []
    result, calls = dispatch("round-promote", ROUND_NO_OTHER_WRITERS="1")
    assert result.returncode == 0, result.stderr
    assert calls == [
        [
            "run",
            "python",
            "-m",
            "scripts.operational_round.controller",
            "promote",
            "--round-id",
            "portfolio-v1",
            "--no-other-writers",
        ]
    ]


def test_round_parallel_goals_stop_before_training_after_build_failure(dispatch):
    result, calls = dispatch(
        "-j4", "round-data", "round-build", "round-train", FAIL_ACTION="build"
    )
    assert result.returncode != 0
    assert [v[4] for v in calls] == ["data", "build"]
