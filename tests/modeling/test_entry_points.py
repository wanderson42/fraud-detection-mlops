"""Explicit modeling command entry points start without fitting models."""

import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "module,commands",
    [
        ("modeling.experiments.baseline_experiment", ["run", "verify"]),
        ("modeling.experiments.terminal_feature_ablation", ["run", "verify"]),
        ("modeling.experiments.candidate_freeze", ["build", "verify"]),
        ("modeling.experiments.final_holdout_evaluation", ["run", "verify"]),
        ("modeling.experiments.hgb_optimization", ["prepare", "optimize", "verify"]),
        ("integrations.mlflow_tracking", ["ui", "verify"]),
        ("evaluation.validation_diagnostics", ["run", "verify"]),
    ],
)
def test_commands_start_without_training(module, commands):
    result = subprocess.run(
        [sys.executable, "-m", f"fraud_detection_mlops.{module}", "--help"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert all(command in result.stdout for command in commands)
