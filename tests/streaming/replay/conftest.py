"""Shared generated native input; no real fraud data or persisted evidence."""

import json
import shutil
import subprocess

import pandas as pd
import pytest

from fraud_detection_mlops.artifacts import sha256, write_json
from fraud_detection_mlops.config import PROJECT_ROOT
from fraud_detection_mlops.data.contracts.transaction_schema import DTYPES
from fraud_detection_mlops.data.datasets.reference_assessment_dataset import window_days
from fraud_detection_mlops.streaming.contracts import native_replay_execution as bindings
from fraud_detection_mlops.streaming.contracts.customer_day_alerts import CustomerDayAlertConfig
from fraud_detection_mlops.streaming.replay import native_causal_replay as native


@pytest.fixture
def source(tmp_path, monkeypatch, replay_runtime, replay_release):
    root = tmp_path / "checkout"
    root.mkdir()
    shutil.copytree(
        PROJECT_ROOT / "fraud_detection_mlops",
        root / "fraud_detection_mlops",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    shutil.copytree(PROJECT_ROOT / "references", root / "references")
    for name in ("poetry.lock", "pyproject.toml"):
        shutil.copyfile(PROJECT_ROOT / name, root / name)
    protocol = json.loads((root / bindings.PROTOCOL_PATH).read_text())
    config = CustomerDayAlertConfig(
        schema_version=1,
        version="first_crossing_customer_day_v1",
        model_sha256=replay_runtime.manifest.model_sha256,
        serving_release_id=replay_runtime.manifest.serving_release_id,
        score_threshold=0.0,
        daily_customer_budget=1,
    )
    write_json(root / bindings.CONFIGURATION_PATH, config.model_dump())
    protocol["bindings"]["configuration_sha256"] = sha256(root / bindings.CONFIGURATION_PATH)
    protocol["bindings"]["policy_sha256"] = config.policy_sha256
    write_json(root / bindings.PROTOCOL_PATH, protocol)
    monkeypatch.setattr(bindings, "PROTOCOL_SHA256", sha256(root / bindings.PROTOCOL_PATH))
    monkeypatch.setattr(native, "identified_runtime", lambda *_: (replay_runtime, config))

    def git(*args):
        return subprocess.check_output(["git", *args], cwd=root, stderr=subprocess.PIPE)

    git("init")
    git("config", "user.name", "Generated Replay Fixture")
    git("config", "user.email", "fixture@example.invalid")
    git("add", ".")
    git("commit", "-m", "Commit generated adapter protocol before fixture bytes")
    silver = tmp_path / "silver"
    source_root = silver / protocol["source_commit"] / "silver_v1"
    inventory = json.loads((root / "references/handbook_source.json").read_text())
    by_day = {r["date"]: r for r in inventory["files"]}
    records = []
    days = ["2018-09-01", *window_days({"start": "2018-09-02", "end_exclusive": "2018-10-01"})]
    for i, day in enumerate(days):
        path = source_root / f"transactions/tx_date={day}/part-00000.parquet"
        path.parent.mkdir(parents=True)
        if day == "2018-09-01":
            path.write_bytes(b"must never be read: forbidden malformed parquet")
            rows = 1
        else:
            # Include simultaneous peers and feedback at exactly a scored transaction clock.
            stamps = pd.to_datetime([day + "T01:00:00", day + "T01:00:00", day + "T02:00:00"])
            seconds = (stamps - pd.Timestamp("2018-04-01")).total_seconds().astype("int64")
            frame = pd.DataFrame(
                {
                    "TRANSACTION_ID": [i * 3 + j for j in range(3)],
                    "TX_DATETIME": stamps,
                    "CUSTOMER_ID": [1, 2, 1],
                    "TERMINAL_ID": [1, 1, 1],
                    "TX_AMOUNT": [0.0, float(i + 1), 50.5],
                    "TX_TIME_SECONDS": seconds,
                    "TX_TIME_DAYS": seconds // 86400,
                    "TX_FRAUD": [0, 0, 1],
                    "TX_FRAUD_SCENARIO": [0, 0, 1],
                }
            ).astype(DTYPES)
            frame.to_parquet(path, index=False)
            rows = len(frame)
        item = by_day[day]
        records.append(
            {
                "path": path.relative_to(source_root).as_posix(),
                "input_filename": item["filename"],
                "input_size_bytes": item["size_bytes"],
                "input_git_blob_sha1": item["git_blob_sha1"],
                "rows": rows,
                "size_bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
        )
    write_json(
        source_root / "manifest.json",
        {
            "source": inventory["source"],
            "layer": "silver",
            "schema_version": 1,
            "contract_version": "silver_v1",
            "contract_sha256": sha256(root / "references/silver_contract_v1.json"),
            "inventory_sha256": sha256(root / "references/handbook_source.json"),
            "files": records,
        },
    )
    return {
        "root": root,
        "silver": silver,
        "source_root": source_root,
        "release": replay_release[0],
        "threshold": tmp_path / "generated-threshold",
        "input": tmp_path / "input",
        "output": tmp_path / "job",
        "protocol": protocol,
        "runtime": replay_runtime,
        "config": config,
    }
