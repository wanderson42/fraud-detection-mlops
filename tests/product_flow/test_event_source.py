"""A fresh Silver source needs no model, Registry or old replay checkpoint."""

from contextlib import closing
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess

import pandas as pd
import pytest
from scripts.product_flow import event_source as source
from scripts.product_flow import inputs

from fraud_detection_mlops.data.contracts.transaction_schema import DTYPES
from fraud_detection_mlops.storage import managed_paths

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def fresh_silver(tmp_path, monkeypatch):
    root = tmp_path / "checkout"
    root.mkdir()
    for name in [
        source.CODE,
        "poetry.lock",
        "references/handbook_source.json",
        "references/silver_contract_v1.json",
        "references/gold_contract_v1.json",
    ]:
        dest = root / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, dest)
    shutil.copytree(
        ROOT / "fraud_detection_mlops",
        root / "fraud_detection_mlops",
        ignore=shutil.ignore_patterns("__pycache__"),
    )

    def git(*args):
        return subprocess.check_output(["git", *args], cwd=root, stderr=subprocess.PIPE)

    git("init", "-q")
    git("add", ".")
    git(
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-qm",
        "source",
    )
    inventory = json.loads((root / "references/handbook_source.json").read_bytes())
    indexed = {r["date"]: r for r in inventory["files"]}
    silver = root / "data/interim/handbook"
    directory = silver / inventory["source"]["commit"] / "silver_v1"
    records = []
    for number, stamp in enumerate(pd.date_range("2018-09-02", "2018-09-16")):
        day = stamp.strftime("%Y-%m-%d")
        stamps = [stamp + pd.Timedelta(hours=1), stamp + pd.Timedelta(hours=2)]
        seconds = [int((s - pd.Timestamp("2018-04-01")).total_seconds()) for s in stamps]
        frame = pd.DataFrame(
            {
                "TRANSACTION_ID": [number * 2 + 1, number * 2 + 2],
                "TX_DATETIME": stamps,
                "CUSTOMER_ID": [1, 2],
                "TERMINAL_ID": [3, 3],
                "TX_AMOUNT": [10.0, 20.0],
                "TX_TIME_SECONDS": seconds,
                "TX_TIME_DAYS": [s // 86400 for s in seconds],
                "TX_FRAUD": [0, 1],
                "TX_FRAUD_SCENARIO": [0, 1],
            }
        ).astype(DTYPES)
        path = directory / f"transactions/tx_date={day}/part-00000.parquet"
        path.parent.mkdir(parents=True)
        frame.to_parquet(path, index=False)
        item = indexed[day]
        records.append(
            {
                "path": path.relative_to(directory).as_posix(),
                "input_filename": item["filename"],
                "input_size_bytes": item["size_bytes"],
                "input_git_blob_sha1": item["git_blob_sha1"],
                "rows": 2,
                "size_bytes": path.stat().st_size,
                "sha256": source.sha(path),
            }
        )
    manifest = {
        "source": inventory["source"],
        "layer": "silver",
        "schema_version": 1,
        "contract_version": "silver_v1",
        "contract_sha256": source.sha(root / "references/silver_contract_v1.json"),
        "inventory_sha256": source.sha(root / "references/handbook_source.json"),
        "files": records,
    }
    (directory / "manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(managed_paths, "DATA_DIR", root / "data")
    return root, silver, directory


def test_source_is_model_free_and_capture_matches_events(fresh_silver, monkeypatch):
    root, silver, directory = fresh_silver
    before = {p: source.sha(p) for p in directory.rglob("*") if p.is_file()}
    # Fail hard on any attempt to load/train models or issue HTTP requests.
    import urllib.request

    import mlflow

    monkeypatch.setattr(mlflow.pyfunc, "load_model", lambda *_a, **_k: pytest.fail("model load"))
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_a, **_k: pytest.fail("HTTP"))
    result = source.prepare(silver, "fresh-v1", "2018-09-16", "2018-09-17", root)
    output = Path(result["directory"])
    source.verify(output, result["manifest_sha256"])
    assert result["counts"] == {"events": 60, "transactions": 30, "feedback": 30}
    assert result["fit_executed"] is result["scoring_executed"] is False
    with closing(sqlite3.connect(output / "checkpoint.sqlite")) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
        assert not conn.execute(
            "SELECT name FROM sqlite_schema WHERE name IN ('decisions','alerts','checkpoint')"
        ).fetchall()
        for (payload,) in conn.execute(
            "SELECT payload FROM events WHERE event_id LIKE 'transaction:%'"
        ):
            assert "fraud_label" not in json.loads(payload)
    policy = {
        "context_start": "2018-09-02T00:00:00",
        "scoring_start": "2018-09-16T00:00:00",
        "scored_transactions": 2,
        "maximum_scored_with_timestamp_peers": 4,
    }
    snapshot = inputs.capture(output / "checkpoint.sqlite", output / "selected.sqlite", policy)
    assert snapshot["counts"] == {"events": 60, "transactions": 30, "feedback": 30, "decisions": 2}
    assert snapshot["source_scores_reused"] is False
    assert before == {p: source.sha(p) for p in before}
    digest = source.sha(output / "checkpoint.sqlite")
    with pytest.raises(ValueError, match="exists"):
        source.prepare(silver, "fresh-v1", "2018-09-16", "2018-09-17", root)
    assert source.sha(output / "checkpoint.sqlite") == digest
    with pytest.raises(ValueError, match="manifest"):
        source.verify(output, "f" * 64)
    with (output / "checkpoint.sqlite").open("ab") as stream:
        stream.write(b"corrupt")
    with pytest.raises(ValueError, match="bytes"):
        inputs.capture(output / "checkpoint.sqlite", output / "bad.sqlite", policy)


def test_corrupt_silver_rejected_without_publishing(fresh_silver):
    root, silver, directory = fresh_silver
    next(directory.rglob("*.parquet")).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="integrity"):
        source.prepare(silver, "bad", "2018-09-16", "2018-09-17", root)
    assert not (root / "data/replay/session-sources/bad").exists()


def test_fresh_source_real_HTTP_reconciles_and_completed_repeat_does_not_score(
    fresh_silver, release, server
):
    from scripts.product_flow import controller as flow
    from tests.product_flow.conftest import scorer

    from fraud_detection_mlops.serving.model_release import load_release

    root, silver, _ = fresh_silver
    result = source.prepare(silver, "http-v1", "2018-09-16", "2018-09-17", root)
    directory = Path(result["directory"])
    before = source.sha(directory / "checkpoint.sqlite")
    target = root / "data/storage/new-flow"
    target.mkdir(parents=True, exist_ok=True)
    policy = flow.policy()
    policy.update(scored_transactions=2, maximum_scored_with_timestamp_peers=4)
    capture = inputs.capture(directory / "checkpoint.sqlite", target / "events.sqlite", policy)
    manifest = load_release(*release).manifest.model_dump()
    value = {
        "flow_id": "fresh-http",
        "capture": capture,
        "policy": policy,
        "release_manifest": manifest,
        "alert_policy": {
            "schema_version": 1,
            "version": "first_crossing_customer_day_v1",
            "model_sha256": manifest["model_sha256"],
            "serving_release_id": manifest["serving_release_id"],
            "score_threshold": 0.5,
            "daily_customer_budget": 100,
        },
    }
    runtime = scorer(server[0], value)
    assert flow.run_events(target, value, runtime)["status"] == "complete"
    assert flow.verify_ledger(target, value)["HTTP_receipts"] == 2
    assert flow.run_events(target, value, runtime)["scoring_HTTP_attempts_this_invocation"] == 0
    assert server[1]["scores"] == 2
    assert source.sha(directory / "checkpoint.sqlite") == before


def test_source_with_fabricated_decision_table_is_rejected(fresh_silver):
    root, silver, _ = fresh_silver
    result = source.prepare(silver, "extra-table", "2018-09-16", "2018-09-17", root)
    output = Path(result["directory"])
    checkpoint = output / "checkpoint.sqlite"
    with closing(sqlite3.connect(checkpoint)) as conn, conn:
        conn.execute("CREATE TABLE decisions(score REAL)")
    manifest = json.loads((output / "source.json").read_bytes())
    manifest["checkpoint_sha256"] = source.sha(checkpoint)
    (output / "source.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="schema"):
        source.verify(output)


@pytest.mark.parametrize("start,end", [("2018-09-16", "2018-09-16"), ("2018-09-16", "2018-09-24")])
def test_unbounded_windows_are_rejected(start, end):
    with pytest.raises(ValueError, match="one to seven"):
        source.protocol(ROOT, start, end)


def test_uncommitted_source_and_redirected_destination_are_rejected(fresh_silver):
    root, silver, _ = fresh_silver
    with (root / source.CODE).open("a") as stream:
        stream.write("\n# unreviewed\n")
    with pytest.raises(ValueError, match="Commit"):
        source.prepare(silver, "unreviewed", "2018-09-16", "2018-09-17", root)
    target = root / "data/replay/session-sources"
    target.mkdir(parents=True, exist_ok=True)
    (target / "redirected").symlink_to(root, target_is_directory=True)
    with pytest.raises(ValueError, match="Symbolic"):
        source.prepare(silver, "redirected", "2018-09-16", "2018-09-17", root)


def test_plan_does_not_open_partition_payloads_or_write_state(fresh_silver, monkeypatch):
    root, silver, _ = fresh_silver
    monkeypatch.setattr(pd, "read_parquet", lambda *_a, **_k: pytest.fail("payload opened"))
    result = source.plan(silver, "2018-09-16", "2018-09-17", root)
    assert result["partitions"] == 15
    assert result["transactions_including_context"] == 30
    assert result["partition_payloads_verified"] is False
    assert not (root / "data/replay").exists()


def test_make_source_entrypoints_bind_inputs_and_require_manifest_hash(dispatch):
    result, calls = dispatch(
        "session-source-plan",
        SESSION_EVENT_SOURCE_ID="owned-v1",
        SESSION_SILVER_ROOT="data/interim/own-silver",
    )
    assert result.returncode == 0
    assert calls[-1] == [
        "run",
        "python",
        "-m",
        "scripts.product_flow.event_source",
        "plan",
        "--source-id",
        "owned-v1",
        "--silver-root",
        "data/interim/own-silver",
        "--start",
        "2018-09-16",
        "--end",
        "2018-09-17",
    ]
    result, _ = dispatch("session-source-verify")
    assert result.returncode != 0
