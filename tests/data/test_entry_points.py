"""Data command entry points preserve their options and checkout data directories."""

import subprocess
import sys

import pytest

from fraud_detection_mlops.config import PROJECT_ROOT


@pytest.mark.parametrize(
    "module,commands",
    [
        ("data.ingestion.handbook_download", ["extract", "verify"]),
        ("data.datasets.silver_dataset", ["build", "verify"]),
        ("data.datasets.gold_dataset", ["build", "verify"]),
        ("data.quality.transaction_profile", ["--bronze-root", "--report-path"]),
        ("data.quality.training_eda", ["build", "verify"]),
    ],
)
def test_command_help_does_not_build_or_download(module, commands):
    result = subprocess.run(
        [sys.executable, "-m", f"fraud_detection_mlops.{module}", "--help"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert all(command in result.stdout for command in commands)


def test_nested_modules_keep_the_original_checkout_data_directories():
    from fraud_detection_mlops.data.datasets.gold_dataset import DEFAULT_GOLD
    from fraud_detection_mlops.data.datasets.silver_dataset import DEFAULT_OUTPUT
    from fraud_detection_mlops.data.ingestion.handbook_bronze import DEFAULT_BRONZE

    assert DEFAULT_BRONZE == PROJECT_ROOT / "data/raw/handbook"
    assert DEFAULT_OUTPUT == PROJECT_ROOT / "data/interim/handbook"
    assert DEFAULT_GOLD == PROJECT_ROOT / "data/processed/handbook"
