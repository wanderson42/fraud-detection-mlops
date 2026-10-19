"""The operational adapter reuses verified materialized input without touching Silver."""

import json
import shutil
import subprocess

import pytest
from scripts.monitoring.collector import snapshot
from scripts.replay.assess_paced_replay import assess
from scripts.replay.paced_triage_replay import execute, make_metadata, operations_identity
from tests.streaming.replay.test_numerical_corrective_replay import (
    correction_source as correction_source,  # noqa: PLC0414 - register the shared pytest fixture
)
from tests.streaming.replay.test_numerical_corrective_replay import (
    prepare,
)

from fraud_detection_mlops.artifacts import sha256, write_json
from fraud_detection_mlops.config import PROJECT_ROOT
from fraud_detection_mlops.data.datasets.native_replay_dataset import stream_events


def test_native_operational_parity_reuse_and_receipt_guard(correction_source):
    s = correction_source
    shutil.rmtree(s["source_root"])
    receipt = prepare(s)
    for folder in ("scripts/monitoring", "scripts/replay"):
        shutil.copytree(
            PROJECT_ROOT / folder, s["root"] / folder, ignore=shutil.ignore_patterns("__pycache__")
        )
    subprocess.run(["git", "add", "scripts"], cwd=s["root"], check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Commit generated operational identity"],
        cwd=s["root"],
        check=True,
        capture_output=True,
    )
    code = operations_identity(s["root"])
    write_json(s["root"] / "data/replay/native-numerical-input-receipt.json", receipt)
    manifest = json.loads((s["corrective_input"] / "manifest.json").read_text())
    window = manifest["protocol"]["windows"]["scored_replay"]
    metadata = make_metadata(
        input_sha256=receipt["manifest_sha256"],
        runtime=s["runtime"],
        policy=s["config"],
        speed=1e9,
        capacity=2,
        window=(window["start"] + "T00:00:00", window["end_exclusive"] + "T00:00:00"),
        expected_counts=manifest["counts"],
        baseline=[1] * 10,
        source_scope="materialized_native_events_operational_reuse_not_new_quality_confirmation",
        code=code,
    )
    output = s["output"].with_name("paced-job")
    result = execute(
        lambda after: stream_events(s["input"] / "events.sqlite", after=after),
        output,
        metadata,
        s["runtime"],
        s["config"],
    )
    assert result["complete"] and result["counts"]["decisions"] == 45
    before = sha256(output / "checkpoint.sqlite")
    report = assess(output, s["root"])
    assert not report["sla_validated"] and not report["continuous_1x_lab_target_evaluable"]
    assert assess(output, s["root"])["status"] == "reused"
    quality = json.loads((output / "assessment/result.json").read_text())["quality"]
    monitored = snapshot(output)["quality"]
    assert monitored["captured_customer_days"] == quality["captured_customer_days"] == 15
    assert monitored["missed_customer_days"] == quality["missed_customer_days"] == 0
    assert sha256(output / "checkpoint.sqlite") == before
    assert sha256(s["state"]) == s["original_hash"]
    receipt_path = output / "assessment/receipt.json"
    receipt_path.write_text("{}")
    with pytest.raises(ValueError, match="receipt differs"):
        assess(output, s["root"])
