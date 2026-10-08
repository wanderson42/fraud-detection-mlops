"""Synthetic, offline source fixtures shared by modeling tests."""

import hashlib
from io import BytesIO
import json
import shutil

import numpy as np
import pandas as pd
import pytest

from fraud_detection_mlops import bronze, gold, silver, temporal


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
