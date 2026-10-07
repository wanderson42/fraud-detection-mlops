from datetime import date, timedelta
import hashlib
from io import BytesIO
import json
from pathlib import Path

import pandas as pd
import pytest
from typer.testing import CliRunner

from fraud_detection_mlops import bronze, eda, silver, temporal


@pytest.fixture(scope="module")
def prepared(tmp_path_factory):
    root = tmp_path_factory.mktemp("eda")
    files, payloads = [], {}
    for i in range(19):
        day = date(2018, 4, 1) + timedelta(days=i)
        stamps = pd.to_datetime([f"{day} 00:00:00", f"{day} 12:00:00", f"{day} 23:59:59"])
        seconds = ((stamps - pd.Timestamp("2018-04-01")).total_seconds()).astype(int)
        frame = pd.DataFrame(
            {
                "TRANSACTION_ID": [3 * i, 3 * i + 1, 3 * i + 2],
                "TX_DATETIME": stamps,
                "CUSTOMER_ID": [1, 1, 2],
                "TERMINAL_ID": [10, 20, 20],
                # Holdout amounts intentionally differ strongly from training amounts.
                "TX_AMOUNT": [0.0, 10.0 if i < 2 else 9000.0, 250.0 if i < 2 else 8000.0],
                "TX_TIME_SECONDS": seconds,
                "TX_TIME_DAYS": seconds // 86400,
                "TX_FRAUD": [0, 0, 1],
                "TX_FRAUD_SCENARIO": [0, 0, 1],
            }
        )
        buffer = BytesIO()
        frame.to_pickle(buffer)
        payload = buffer.getvalue()
        filename = f"{day}.pkl"
        payloads[filename] = payload
        files.append(
            {
                "date": str(day),
                "filename": filename,
                "source_path": f"data/{filename}",
                "size_bytes": len(payload),
                "git_blob_sha1": hashlib.sha1(
                    f"blob {len(payload)}".encode() + b"\0" + payload
                ).hexdigest(),
            }
        )
    inventory = root / "inventory.json"
    inventory.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source": {"repository": bronze.SOURCE_REPOSITORY, "commit": "a" * 40},
                "files": files,
            }
        )
    )
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            bronze,
            "urlopen",
            lambda request, timeout: BytesIO(payloads[request.full_url.rsplit("/", 1)[-1]]),
        )
        bronze.extract_bronze(root / "bronze", inventory_path=inventory)
    silver.build_silver(root / "bronze", root / "interim", inventory_path=inventory)
    protocol = json.loads(temporal.DEFAULT_PROTOCOL.read_text())
    protocol["source_commit"] = "a" * 40
    protocol["windows"] = {
        "train": {"start": "2018-04-01", "end_exclusive": "2018-04-03"},
        "validation": {"start": "2018-04-10", "end_exclusive": "2018-04-11"},
        "test": {"start": "2018-04-19", "end_exclusive": "2018-04-20"},
    }
    return root / "interim", inventory, protocol


@pytest.fixture
def inputs(prepared, tmp_path):
    output, inventory, specification = prepared
    protocol = tmp_path / "protocol.json"
    protocol.write_text(json.dumps(specification))
    return output, inventory, protocol


def test_training_only_eda_reconciles_boundary_rows_preserves_zeros_and_verifies(inputs):
    output, inventory, protocol = inputs
    source_dir = output / ("a" * 40) / "silver_v1"
    before = {
        str(p): (eda._sha256(p), p.stat().st_mtime_ns)
        for p in source_dir.rglob("*")
        if p.is_file()
    }
    result = eda.build_eda(output, inventory_path=inventory, protocol_path=protocol)
    assert result["rows"] == 6
    assert result["training_partitions"] == 2
    assert result["fraud_count"] == 2
    assert result["fraud_rate"] == pytest.approx(1 / 3)
    path = Path(result["eda_path"])
    report = json.loads((path / "report.json").read_text())
    assert report["scope"] == "training_only_descriptive"
    assert report["summary"]["zero_amounts"] == 2
    assert report["summary"]["customers"] == report["summary"]["terminals"] == 2
    assert [item["input_filename"] for item in report["inputs"]] == [
        "2018-04-01.pkl",
        "2018-04-02.pkl",
    ]
    daily = pd.read_csv(path / "daily.csv")
    assert daily["transactions"].tolist() == [3, 3]
    assert daily["frauds"].tolist() == [1, 1]
    amounts = pd.read_csv(path / "amount_by_label.csv").set_index("label")
    assert amounts.loc[0, "mean"] == 5.0
    assert amounts["maximum"].max() == 250.0
    bins = pd.read_csv(path / "amount_bins.csv")
    assert bins.groupby("label")["label_fraction"].sum().tolist() == pytest.approx([1, 1])
    assert (path / "training_overview.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert eda.verify_eda(path)["verified_outputs"] == 9
    audit = json.loads(Path(result["audit_path"]).read_text())
    assert audit["status"] == "success"
    assert audit["protocol_sha256"] == eda._sha256(protocol)
    assert before == {
        str(p): (eda._sha256(p), p.stat().st_mtime_ns)
        for p in source_dir.rglob("*")
        if p.is_file()
    }


@pytest.mark.parametrize(
    "change",
    [
        lambda p: p.__setitem__("source_commit", "b" * 40),
        lambda p: p.__setitem__("eda_window", "test"),
        lambda p: p.__setitem__("label_delay_days", 0),
        lambda p: p.__setitem__("refit_before_test", True),
        lambda p: p["windows"]["validation"].__setitem__("start", "2018-04-09"),
        lambda p: p["windows"]["train"].__setitem__("end_exclusive", "2018-04-01"),
        lambda p: p["windows"]["test"].__setitem__("end_exclusive", "2018-04-21"),
        lambda p: p["windows"]["train"].__setitem__("start", "20180401"),
    ],
)
def test_invalid_protocol_is_rejected_before_silver_access(inputs, monkeypatch, change):
    output, inventory, protocol = inputs
    specification = json.loads(protocol.read_text())
    change(specification)
    protocol.write_text(json.dumps(specification))
    monkeypatch.setattr(eda, "verify_silver", lambda *a, **k: pytest.fail("Silver accessed"))
    with pytest.raises(temporal.ProtocolError):
        eda.build_eda(output, inventory_path=inventory, protocol_path=protocol)


def test_plot_failure_records_audit_and_publishes_no_bundle(inputs, monkeypatch):
    output, inventory, protocol = inputs
    parent = output / ("a" * 40) / "eda_v1"
    before = set(parent.iterdir()) if parent.exists() else set()
    monkeypatch.setattr(eda, "_plot", lambda *a: (_ for _ in ()).throw(OSError("plot failure")))
    with pytest.raises(OSError, match="plot failure"):
        eda.build_eda(output, inventory_path=inventory, protocol_path=protocol)
    assert set(parent.iterdir()) - before <= {parent / "runs"}
    audits = [json.loads(p.read_text()) for p in (parent / "runs").glob("*.json")]
    assert any(
        audit["status"] == "failed" and "plot failure" in audit["error"] for audit in audits
    )
    assert not list(parent.glob(".eda-staging-*"))


def test_input_changed_during_eda_is_rejected(inputs, monkeypatch):
    output, inventory, protocol = inputs
    original = eda._plot

    def change_protocol(tables, path):
        original(tables, path)
        protocol.write_text(protocol.read_text() + "\n")

    monkeypatch.setattr(eda, "_plot", change_protocol)
    with pytest.raises(ValueError, match="Inputs changed"):
        eda.build_eda(output, inventory_path=inventory, protocol_path=protocol)


def test_cli_build_and_corruption_detection(inputs):
    output, inventory, protocol = inputs
    runner = CliRunner()
    result = runner.invoke(
        eda.app,
        [
            "build",
            "--silver-root",
            str(output),
            "--inventory",
            str(inventory),
            "--protocol",
            str(protocol),
        ],
    )
    assert result.exit_code == 0, result.output
    path = Path(json.loads(result.output)["eda_path"])
    assert runner.invoke(eda.app, ["verify", str(path)]).exit_code == 0
    with (path / "daily.csv").open("a") as handle:
        handle.write("corrupted\n")
    assert runner.invoke(eda.app, ["verify", str(path)]).exit_code == 1


def test_production_protocol_has_expected_windows():
    inventory = bronze.load_inventory(bronze.DEFAULT_INVENTORY)
    protocol = temporal.load_protocol(temporal.DEFAULT_PROTOCOL, inventory)
    assert protocol["windows"]["train"] == {"start": "2018-04-01", "end_exclusive": "2018-04-29"}
    assert protocol["windows"]["test"] == {"start": "2018-05-20", "end_exclusive": "2018-05-27"}
