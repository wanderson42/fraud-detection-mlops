"""Bounded source capture, append-only plans and serialized public commands."""

from contextlib import closing, contextmanager
import json
import sqlite3

import pytest
from scripts.product_flow import controller as c
from scripts.product_flow import inputs
from tests.product_flow.conftest import scorer

from fraud_detection_mlops.serving.model_release import ReleaseManifest


def test_capture_does_not_select_by_labels_or_old_scores(prepared, tmp_path):
    _, value, source = prepared
    with closing(sqlite3.connect(source)) as conn, conn:
        conn.execute("UPDATE decisions SET response='old scores deliberately unavailable'")
    out = tmp_path / "recapture.sqlite"
    result = inputs.capture(source, out, value["policy"])
    assert result["counts"] == value["capture"]["counts"]
    assert not result["source_scores_reused"]
    with closing(sqlite3.connect(out)) as conn:
        assert conn.execute("SELECT count(*) FROM stream").fetchone()[0] == 20


@pytest.mark.parametrize("corruption", ["missing_feedback", "event_clock", "event_digest"])
def test_capture_refuses_incomplete_or_inconsistent_source(prepared, tmp_path, corruption):
    _, value, source = prepared
    with closing(sqlite3.connect(source)) as conn, conn:
        if corruption == "missing_feedback":
            conn.execute("DELETE FROM feedback WHERE transaction_id=9")
        elif corruption == "event_clock":
            conn.execute("UPDATE feedback SET available_ns=available_ns+1 WHERE transaction_id=9")
        else:
            conn.execute("UPDATE events SET digest='wrong' WHERE event_id='transaction:9'")
    with pytest.raises(ValueError):
        inputs.capture(source, tmp_path / "rejected.sqlite", value["policy"])


def test_timestamp_peers_never_exceed_http_budget(prepared, tmp_path):
    _, value, source = prepared
    config = {
        **value["policy"],
        "scoring_start": "2018-04-01T00:00:00",
        "context_start": "2018-03-18T00:00:00",
        "scored_transactions": 1,
        "maximum_scored_with_timestamp_peers": 1,
    }
    # Source must actually cover context, so this fails before pretending partial history is complete.
    with pytest.raises(ValueError, match="context"):
        inputs.capture(source, tmp_path / "peers.sqlite", config)


def test_http_client_never_loads_model_weights(prepared, server, monkeypatch):
    folder, value, _ = prepared
    runtime = scorer(server[0], value)
    runtime.ready()
    from fraud_detection_mlops.integrations import skops_persistence

    def forbidden(*args, **kwargs):
        pytest.fail("The HTTP replay client must not deserialize model weights")

    monkeypatch.setattr(skops_persistence, "load_pipeline_bytes", forbidden)
    c.run_events(folder, value, runtime)
    assert c.verify_ledger(folder, value)["HTTP_receipts"] == 2


def test_network_outage_does_not_advance_scored_transaction(prepared):
    import socket
    from urllib.error import URLError

    folder, value, _ = prepared
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        url = "http://127.0.0.1:" + str(sock.getsockname()[1])
        # Bound but not listening: real connection refusal, without touching any existing service.
        from scripts.product_flow.http_scoring import HTTPScorer

        runtime = HTTPScorer(
            url, ReleaseManifest.model_validate(value["release_manifest"]), timeout=0.1
        )
        with pytest.raises(URLError):
            c.run_events(folder, value, runtime)
        assert runtime.requests == 3
    with closing(sqlite3.connect(folder / "checkpoint.sqlite")) as conn:
        assert conn.execute("SELECT count(*) FROM decisions").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM transactions").fetchone()[0] == 8


def test_real_make_requires_writer_declaration(dispatch):
    result, calls = dispatch("product-prepare")
    assert result.returncode != 0 and calls == []
    result, calls = dispatch("product-prepare", PRODUCT_NO_OTHER_WRITERS="1")
    assert result.returncode == 0, result.stderr
    assert calls[-1][3:] == [
        "scripts.product_flow.controller",
        "prepare",
        "--flow-id",
        "champion-http-v1",
        "--source-state",
        "data/replay/paced-native-v1",
        "--no-other-writers",
    ]


def test_make_parallel_goals_stop_after_prepare_failure(dispatch):
    result, calls = dispatch(
        "-j4",
        "product-prepare",
        "product-rehearse",
        "product-verify",
        PRODUCT_NO_OTHER_WRITERS="1",
        FAIL_ACTION="prepare",
    )
    assert result.returncode != 0
    assert [v[4] for v in calls] == ["prepare"]


def test_native_controller_records_receipt_only_after_target_rechecks(
    prepared, server, monkeypatch
):
    folder, value, _ = prepared
    monkeypatch.setattr(c, "work", lambda key: folder)
    monkeypatch.setattr(c, "read", lambda key: value)

    @contextmanager
    def changed_target(_):
        yield scorer(server[0], value)
        raise ValueError("registry changed during run")

    monkeypatch.setattr(c, "target", changed_target)
    with pytest.raises(ValueError, match="registry changed"):
        c.execute("fixture", "rehearse", True)
    assert not (folder / "rehearsal.json").exists()
    assert len(list(folder.glob("failure-*.json"))) == 1
    assert c.verify_ledger(folder, value)["HTTP_receipts"] == 2


def test_native_controller_rehearsal_reconciles_real_HTTP_without_Kubernetes(
    prepared, server, monkeypatch
):
    folder, value, _ = prepared
    monkeypatch.setattr(c, "work", lambda key: folder)
    monkeypatch.setattr(c, "read", lambda key: value)

    @contextmanager
    def target(_):
        runtime = scorer(server[0], value)
        runtime.ready()
        yield runtime
        runtime.ready()

    monkeypatch.setattr(c, "target", target)
    result = c.execute("fixture", "rehearse", True)
    assert result["status"] == "product_rehearsal_passed"
    assert result["completed_reinvocation_without_new_score_calls"]
    assert (folder / "rehearsal.json").is_file()
    with pytest.raises(ValueError, match="already recorded"):
        c.execute("fixture", "rehearse", True)
    assert json.loads((folder / "rehearsal.json").read_bytes()) == result
