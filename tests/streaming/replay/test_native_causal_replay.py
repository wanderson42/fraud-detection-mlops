"""Restricted adapter integration with generated Silver, release and policy only."""

from contextlib import closing
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess

import pandas as pd
import pytest

from fraud_detection_mlops.artifacts import sha256, write_json
from fraud_detection_mlops.config import PROJECT_ROOT
from fraud_detection_mlops.data.contracts.transaction_schema import DTYPES
from fraud_detection_mlops.data.datasets.native_replay_dataset import stream_events
from fraud_detection_mlops.data.datasets.reference_assessment_dataset import window_days
from fraud_detection_mlops.streaming.contracts import native_replay_execution as bindings
from fraud_detection_mlops.streaming.contracts.customer_day_alerts import CustomerDayAlertConfig
from fraud_detection_mlops.streaming.contracts.transaction_events import (
    LABEL_DELAY_NS,
    TransactionEvent,
)
from fraud_detection_mlops.streaming.evaluation.native_triage_assessment import triage_quality
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


def prepare_source(source):
    return native.prepare(
        source["silver"], source["release"], source["threshold"], source["input"], source["root"]
    )


def execute(source, receipt, **kwargs):
    return native.run(
        source["input"],
        receipt["manifest_sha256"],
        source["release"],
        source["threshold"],
        source["output"],
        source["root"],
        **kwargs,
    )


def test_plan_reads_metadata_only_and_has_no_model_or_partition_access(source, monkeypatch):
    original = Path.open

    def guarded(path, *args, **kwargs):
        assert path.suffix not in (".parquet", ".skops")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded)
    monkeypatch.setattr(native, "identified_runtime", lambda *_: pytest.fail("plan loaded scorer"))
    result = native.plan(source["silver"], source["root"])
    assert result["partitions"] == 29
    assert result["transactions_including_context"] == 87
    assert result["partition_bytes_opened"] is False


def test_generated_full_workflow_resumes_and_verifies_without_silver_or_rescoring(
    source, monkeypatch
):
    receipt = prepare_source(source)
    assert receipt["counts"] == {"events": 174, "transactions": 87, "feedback": 87}
    events = list(stream_events(source["input"] / "events.sqlite"))
    transactions = {e.transaction_id: e for e in events if isinstance(e, TransactionEvent)}
    for event in events:
        if event.event_type == "transaction":
            assert "fraud_label" not in event.model_dump()
        else:
            assert (
                event.event_clock_ns
                == transactions[event.transaction_id].event_clock_ns + LABEL_DELAY_NS
            )
    stopped = execute(source, receipt, stop_after=110)
    assert not stopped["complete"]
    original = source["runtime"].score
    scored = []

    def counted(request):
        assert "TX_FRAUD" not in request.feature_frame()
        scored.append(request.transaction_id)
        return original(request)

    monkeypatch.setattr(source["runtime"], "score", counted)
    shutil.rmtree(source["source_root"])
    result = execute(source, receipt)
    assert (
        result["complete"]
        and result["feature_parity_evaluated"]
        and result["causal_decision_parity_evaluated"]
    )
    assert result["counts"]["decisions"] == 45
    assert result["counts"]["alerts"] == 15
    assert len(scored) < 45
    assert len(scored) == len(set(scored))
    daily = result["quality"]["daily_causal_triage"]
    assert all(
        d["actual_alert_precision"] == 1.0 and d["captured_customer_days"] == 1 for d in daily
    )
    assert all(d["qualified_customers_suppressed_by_budget"] == 1 for d in daily)
    state_hash = sha256(source["output"] / "checkpoint.sqlite")
    monkeypatch.setattr(
        source["runtime"], "score", lambda *_: pytest.fail("completed job rescored")
    )
    assert execute(source, receipt)["reused"] is True
    verified = native.verify(
        source["input"],
        receipt["manifest_sha256"],
        source["output"],
        source["root"],
        result["receipt_sha256"],
    )
    assert verified["quality"] == result["quality"]
    assert sha256(source["output"] / "checkpoint.sqlite") == state_hash


@pytest.mark.parametrize("changed", ["protocol", "config", "code", "release", "threshold"])
def test_guards_fail_before_any_selected_partition_bytes(source, monkeypatch, changed):
    def fail_read(*_):
        pytest.fail("partition access preceded identity guards")

    monkeypatch.setattr(native, "build_assessment_features", fail_read)
    if changed == "protocol":
        (source["root"] / bindings.PROTOCOL_PATH).write_text("{}")
    elif changed == "config":
        (source["root"] / bindings.CONFIGURATION_PATH).write_text("{}")
    elif changed == "code":
        (
            source["root"] / "fraud_detection_mlops/streaming/replay/native_causal_replay.py"
        ).write_text("# changed")
    else:
        monkeypatch.setattr(
            native, "identified_runtime", lambda *_: (_ for _ in ()).throw(ValueError(changed))
        )
    with pytest.raises((ValueError, subprocess.CalledProcessError)):
        prepare_source(source)
    assert not source["input"].exists()


@pytest.mark.parametrize(
    "invalid", ["schema", "day", "id", "amount", "label", "hash", "symlink", "missing_day"]
)
def test_selected_source_rejection_publishes_nothing(source, invalid):
    root = source["source_root"]
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    record = manifest["files"][1]
    path = root / record["path"]
    if invalid == "missing_day":
        manifest["files"].pop(1)
    elif invalid == "symlink":
        moved = path.with_suffix(".original")
        path.rename(moved)
        path.symlink_to(moved)
    elif invalid == "hash":
        path.write_bytes(b"changed")
    else:
        frame = pd.read_parquet(path)
        if invalid == "schema":
            frame = frame.drop(columns="TX_TIME_DAYS")
        elif invalid == "day":
            frame.loc[0, "TX_DATETIME"] += pd.Timedelta(days=1)
        elif invalid == "id":
            frame.loc[0, "TRANSACTION_ID"] = frame.loc[1, "TRANSACTION_ID"]
        elif invalid == "amount":
            frame.loc[0, "TX_AMOUNT"] = -1.0
        elif invalid == "label":
            frame.loc[0, "TX_FRAUD"] = 2
        frame.to_parquet(path, index=False)
        record.update(size_bytes=path.stat().st_size, sha256=sha256(path))
    write_json(manifest_path, manifest)
    with pytest.raises((ValueError, sqlite3.Error)):
        prepare_source(source)
    assert not source["input"].exists()


@pytest.mark.parametrize(
    "name", ["events.sqlite", "oracle.parquet", "features/2018-09-16.parquet", "manifest.json"]
)
def test_altered_prepared_input_rejected_before_checkpoint(source, name):
    receipt = prepare_source(source)
    (source["input"] / name).write_bytes(b"changed")
    with pytest.raises(ValueError):
        execute(source, receipt)
    assert not (source["output"] / "checkpoint.sqlite").exists()


def test_parity_failure_keeps_ledger_and_retry_can_publish_atomic_assessment(source, monkeypatch):
    receipt = prepare_source(source)
    original = native.assessment_report
    monkeypatch.setattr(
        native, "assessment_report", lambda *_: (_ for _ in ()).throw(ValueError("parity failed"))
    )
    with pytest.raises(ValueError, match="parity failed"):
        execute(source, receipt)
    assert (source["output"] / "checkpoint.sqlite").exists()
    assert not (source["output"] / "assessment").exists()
    monkeypatch.setattr(native, "assessment_report", original)
    monkeypatch.setattr(
        source["runtime"], "score", lambda *_: pytest.fail("committed replay rescored")
    )
    assert execute(source, receipt)["complete"]


def test_failure_between_result_and_receipt_does_not_publish_half_assessment(source, monkeypatch):
    receipt = prepare_source(source)
    original = native.write_json

    def fail_receipt(path, *args, **kwargs):
        if path.name == "receipt.json":
            raise OSError("interrupted final publication")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(native, "write_json", fail_receipt)
    with pytest.raises(OSError, match="interrupted final"):
        execute(source, receipt)
    state_hash = sha256(source["output"] / "checkpoint.sqlite")
    assert not (source["output"] / "assessment").exists()
    monkeypatch.setattr(native, "write_json", original)
    monkeypatch.setattr(
        source["runtime"], "score", lambda *_: pytest.fail("committed replay rescored")
    )
    assert execute(source, receipt)["complete"]
    assert sha256(source["output"] / "checkpoint.sqlite") == state_hash


def test_result_external_digest_and_artifact_integrity_are_checked_read_only(source):
    receipt = prepare_source(source)
    result = execute(source, receipt)
    state_hash = sha256(source["output"] / "checkpoint.sqlite")
    with pytest.raises(ValueError, match="external receipt"):
        native.verify(
            source["input"], receipt["manifest_sha256"], source["output"], source["root"], "0" * 64
        )
    (source["output"] / "assessment/result.json").write_text("{}")
    with pytest.raises(ValueError, match="artifact changed"):
        native.verify(
            source["input"],
            receipt["manifest_sha256"],
            source["output"],
            source["root"],
            result["receipt_sha256"],
        )
    assert sha256(source["output"] / "checkpoint.sqlite") == state_hash


def test_changed_input_on_resume_preserves_committed_prefix(source):
    receipt = prepare_source(source)
    execute(source, receipt, stop_after=110)
    state = source["output"] / "checkpoint.sqlite"
    before = sha256(state)
    (source["input"] / "events.sqlite").write_bytes(b"changed")
    with pytest.raises(ValueError, match="input artifact"):
        execute(source, receipt)
    assert sha256(state) == before


@pytest.mark.parametrize("change", ["feature", "score", "decision", "feedback", "event", "alert"])
def test_independent_assessment_rejects_semantic_ledger_changes(source, change):
    receipt = prepare_source(source)
    execute(source, receipt)
    state = source["output"] / "checkpoint.sqlite"
    with closing(sqlite3.connect(state)) as connection, connection:
        tx_id, request, response = connection.execute("SELECT * FROM decisions LIMIT 1").fetchone()
        if change == "feature":
            value = json.loads(request)
            value["features"]["TX_AMOUNT"] += 1.0
            connection.execute(
                "UPDATE decisions SET request=? WHERE transaction_id=?", (json.dumps(value), tx_id)
            )
        elif change == "score":
            value = json.loads(response)
            value["score"] = 0.123
            connection.execute(
                "UPDATE decisions SET response=? WHERE transaction_id=?",
                (json.dumps(value), tx_id),
            )
        elif change == "decision":
            value = json.loads(
                connection.execute(
                    "SELECT payload FROM alert_decisions WHERE transaction_id=?", (tx_id,)
                ).fetchone()[0]
            )
            value["status"] = "below_threshold"
            connection.execute(
                "UPDATE alert_decisions SET payload=? WHERE transaction_id=?",
                (json.dumps(value), tx_id),
            )
        elif change == "feedback":
            connection.execute(
                "UPDATE feedback SET fraud_label=1-fraud_label WHERE transaction_id=?", (tx_id,)
            )
        elif change == "event":
            connection.execute(
                "UPDATE events SET payload='{}' WHERE event_id=?", (f"transaction:{tx_id}",)
            )
        else:
            connection.execute("DELETE FROM alerts WHERE transaction_id=?", (tx_id,))
    manifest = json.loads((source["input"] / "manifest.json").read_text())
    with pytest.raises(ValueError):
        native.assessment_report(source["input"], manifest, state)


def test_original_protocol_and_legacy_executor_fingerprints_stay_unchanged():
    from fraud_detection_mlops.streaming.replay.temporal_replay_executor import source_identity

    assert (
        sha256(PROJECT_ROOT / "references/causal_replay_protocol_v1.json")
        == "a932d674509444665cbafb093225c6255ce6f0b0ef109cc6a3b2dcac3af43cc3"
    )
    assert source_identity() == "1ee063aa986ef3a6326706b2deeda0cdd7857baa64dfc1c196e90e82d2ed397a"


def test_registered_native_artifact_bindings_reject_different_threshold_and_model(
    tmp_path, monkeypatch, replay_runtime
):
    protocol = bindings.execution_protocol(PROJECT_ROOT)
    native_config = CustomerDayAlertConfig.model_validate_json(
        (PROJECT_ROOT / bindings.CONFIGURATION_PATH).read_bytes()
    )
    threshold = tmp_path / "threshold"
    threshold.mkdir()
    write_json(threshold / "policy.json", native_config.model_dump())
    calls = []

    def verify_threshold(directory, digest, root):
        calls.append(digest)
        return {"policy_sha256": native_config.policy_sha256}

    monkeypatch.setattr(bindings, "verify_threshold", verify_threshold)
    monkeypatch.setattr(bindings, "load_release", lambda *_: replay_runtime)
    with pytest.raises(ValueError, match="frozen April"):
        bindings.identified_runtime(PROJECT_ROOT, tmp_path / "release", threshold, protocol)
    assert calls == ["6fc96c1550c0c7840701215541d168928bac660d34c8fa2d06c655d95d0a1afa"]
    wrong = native_config.model_copy(update={"score_threshold": 0.7})
    write_json(threshold / "policy.json", wrong.model_dump())
    monkeypatch.setattr(
        bindings,
        "load_release",
        lambda *_: pytest.fail("threshold mismatch must precede model load"),
    )
    with pytest.raises(ValueError, match="registered triage"):
        bindings.identified_runtime(PROJECT_ROOT, tmp_path / "release", threshold, protocol)


def test_actual_precision_empty_budget_and_customer_day_capture_semantics():
    config = CustomerDayAlertConfig(
        schema_version=1,
        version="first_crossing_customer_day_v1",
        model_sha256="a" * 64,
        serving_release_id="b" * 32,
        score_threshold=0.5,
        daily_customer_budget=3,
    )
    frame = pd.DataFrame(
        {
            "TRANSACTION_ID": [1, 2, 3, 4],
            "CUSTOMER_ID": [1, 1, 2, 3],
            "TX_DATETIME": pd.to_datetime(
                ["2018-09-16T01:00", "2018-09-16T02:00", "2018-09-16T03:00", "2018-09-17T01:00"]
            ),
            "TX_FRAUD": [0, 1, 0, 0],
            "score": [0.6, 0.9, 0.7, 0.1],
            "alert_status": ["alert", "already_alerted", "alert", "below_threshold"],
        }
    )
    result = triage_quality(frame, config, {"start": "2018-09-16", "end_exclusive": "2018-09-18"})
    first, second = result["daily_causal_triage"]
    assert first["actual_alert_precision"] == 0.5
    assert (
        first["alerts"] == 2
        and first["unused_budget"] == 1
        and first["budget_exhaustion_clock"] is None
    )
    assert second["actual_alert_precision"] is None and second["customer_recall"] is None
    assert result["alert_minus_first_fraud_minutes"]["p50"] == -60
    with pytest.raises(ValueError, match="every prespecified"):
        triage_quality(frame, config, {"start": "2018-09-16", "end_exclusive": "2018-09-19"})
