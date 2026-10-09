"""Synthetic, offline source fixtures shared by modeling tests."""

from datetime import date, timedelta
import hashlib
from io import BytesIO
import json
from pathlib import Path
import shutil

import numpy as np
import pandas as pd
import pytest

from fraud_detection_mlops import bronze, gold, silver, temporal
from fraud_detection_mlops.artifacts import sha256, write_json
from fraud_detection_mlops.bronze import SOURCE_REPOSITORY
from fraud_detection_mlops.modeling import development
from fraud_detection_mlops.silver import DEFAULT_CONTRACT, DTYPES


@pytest.fixture(scope="session")
def prepared_baseline(tmp_path_factory):
    root = tmp_path_factory.mktemp("baseline-source")
    frames, records = {}, []
    # Enough observations for both classes and a nonconstant tree baseline.
    for day_number, day in enumerate(pd.date_range("2018-04-01", periods=21)):
        stamp = day + pd.to_timedelta(np.arange(80) * 900, unit="s")
        seconds = ((stamp - pd.Timestamp("2018-04-01")).total_seconds()).astype(int)
        labels = (np.arange(80) % 10 == 0).astype("int64")
        frame = pd.DataFrame(
            {
                "TRANSACTION_ID": np.arange(80) + 80 * day_number,
                "TX_DATETIME": stamp,
                "CUSTOMER_ID": np.arange(80) % 20,
                "TERMINAL_ID": np.arange(80) % 4,
                "TX_AMOUNT": np.where(labels, 200.0, 10.0) + day_number,
                "TX_TIME_SECONDS": seconds,
                "TX_TIME_DAYS": seconds // 86400,
                "TX_FRAUD": labels,
                "TX_FRAUD_SCENARIO": labels,
            }
        )
        buffer = BytesIO()
        frame.to_pickle(buffer)
        data = buffer.getvalue()
        filename = f"{day.date()}.pkl"
        frames[filename] = data
        records.append(
            {
                "date": str(day.date()),
                "filename": filename,
                "source_path": f"data/{filename}",
                "size_bytes": len(data),
                "git_blob_sha1": hashlib.sha1(
                    f"blob {len(data)}".encode() + b"\0" + data
                ).hexdigest(),
            }
        )
    inventory = root / "inventory.json"
    inventory.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source": {"repository": bronze.SOURCE_REPOSITORY, "commit": "b" * 40},
                "files": records,
            }
        )
    )
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            bronze,
            "urlopen",
            lambda request, timeout: BytesIO(frames[request.full_url.rsplit("/", 1)[-1]]),
        )
        bronze.extract_bronze(root / "bronze", inventory_path=inventory)
    silver.build_silver(root / "bronze", root / "silver", inventory_path=inventory)
    protocol = json.loads(temporal.DEFAULT_PROTOCOL.read_text())
    protocol["source_commit"] = "b" * 40
    protocol["windows"] = {
        "train": {"start": "2018-04-01", "end_exclusive": "2018-04-03"},
        "validation": {"start": "2018-04-10", "end_exclusive": "2018-04-12"},
        "test": {"start": "2018-04-19", "end_exclusive": "2018-04-21"},
    }
    (root / "protocol.json").write_text(json.dumps(protocol))
    gold.build_gold(
        root / "silver",
        root / "gold",
        inventory_path=inventory,
        protocol_path=root / "protocol.json",
    )
    return root


@pytest.fixture
def baseline_source(prepared_baseline, tmp_path):
    shutil.copytree(prepared_baseline / "gold", tmp_path / "gold")
    for name in ("inventory.json", "protocol.json"):
        shutil.copy2(prepared_baseline / name, tmp_path / name)
    return tmp_path


@pytest.fixture
def toy_pipeline():
    from fraud_detection_mlops.features import FEATURE_COLUMNS
    from fraud_detection_mlops.modeling.baseline import make_model

    features = pd.DataFrame(np.zeros((8, len(FEATURE_COLUMNS))), columns=FEATURE_COLUMNS)
    model = make_model("dummy_prior").fit(features, [0, 1] * 4)
    return model, features


@pytest.fixture(scope="module")
def silver_fixture(tmp_path_factory):
    root = tmp_path_factory.mktemp("study-source")
    policy = development.load_policy()
    source = root / "silver" / policy["source_commit"] / "silver_v1"
    items, records = [], []
    # Extra reserved bytes are deliberately invalid: preparation must not open them.
    for i in range(128):
        day = str(date(2018, 5, 27) + timedelta(days=i))
        path = source / f"transactions/tx_date={day}/part-00000.parquet"
        path.parent.mkdir(parents=True)
        stamps = pd.to_datetime([day + " 01:00", day + " 12:00", day + " 23:00"])
        seconds = ((stamps - pd.Timestamp("2018-04-01")).total_seconds()).astype(int)
        frame = pd.DataFrame(
            {
                "TRANSACTION_ID": np.arange(3 * i, 3 * i + 3),
                "TX_DATETIME": stamps,
                "CUSTOMER_ID": [1, 2, 3],
                "TERMINAL_ID": [1, 1, 1],
                "TX_AMOUNT": [10.0, 20.0, 150.0],
                "TX_TIME_SECONDS": seconds,
                "TX_TIME_DAYS": seconds // 86400,
                "TX_FRAUD": [0, 0, 1],
                "TX_FRAUD_SCENARIO": [0, 0, 1],
            }
        ).astype(DTYPES)
        frame.to_parquet(path, engine="pyarrow", index=False, version="2.6")
        item = {
            "date": day,
            "filename": day + ".pkl",
            "source_path": "data/" + day + ".pkl",
            "size_bytes": 1,
            "git_blob_sha1": "b" * 40,
        }
        items.append(item)
        records.append(
            {
                "path": str(path.relative_to(source)),
                "input_filename": item["filename"],
                "input_size_bytes": 1,
                "input_git_blob_sha1": item["git_blob_sha1"],
                "input_sha256": "c" * 64,
                "size_bytes": path.stat().st_size,
                "sha256": sha256(path),
                "rows": 3,
            }
        )
    inventory = root / "inventory.json"
    write_json(
        inventory,
        {
            "schema_version": 1,
            "source": {"repository": SOURCE_REPOSITORY, "commit": policy["source_commit"]},
            "files": items,
        },
    )
    write_json(
        source / "manifest.json",
        {
            "schema_version": 1,
            "layer": "silver",
            "source": json.loads(inventory.read_text())["source"],
            "contract_version": "silver_v1",
            "contract_sha256": sha256(DEFAULT_CONTRACT),
            "inventory_sha256": sha256(inventory),
            "files": records,
        },
    )
    (source / "transactions/tx_date=2018-09-02/part-00000.parquet").write_bytes(
        b"RESERVED; MUST NOT READ"
    )
    return root


@pytest.fixture
def policy_path(tmp_path):
    policy = development.load_policy()
    policy["budget"]["max_trials"] = 2
    policy["budget"]["max_fit_attempts"] = 9
    path = tmp_path / "policy.json"
    write_json(path, policy)
    return path


@pytest.fixture
def prepared_data(silver_fixture, tmp_path, monkeypatch, policy_path):
    monkeypatch.setattr(
        development,
        "committed_inputs",
        lambda p: {"git_revision": "test", "files": {"test": "d" * 64}},
    )
    result = development.prepare_data(
        silver_fixture / "silver",
        tmp_path / "output",
        policy_path=policy_path,
        inventory_path=silver_fixture / "inventory.json",
    )
    return Path(result["development_path"]), policy_path
