"""Real HTTP arrivals, partial views, delayed feedback and restart identity."""

from contextlib import closing
import json
import sqlite3

import pytest
from scripts.product_flow import controller as flow
from scripts.product_session import controller as session
from scripts.product_session import reader
from tests.product_flow.conftest import scorer


def receipt(folder, value):
    (folder / "input.json").write_text(json.dumps(value))
    (folder / "session.json").write_text(
        json.dumps(
            {
                "schema": "fraud_product_session_v1",
                "session_id": "fixture",
                "input_sha256": reader.sha(folder / "input.json"),
            }
        )
    )
    return reader.sha(folder / "session.json")


def test_arrivals_pause_view_resume_delayed_feedback_and_reconcile(prepared, server):
    folder, value, source = prepared
    pin = receipt(folder, value)
    original = reader.sha(source)
    calls = []
    assert reader.read(folder, pin)["counts"]["events"] == 0
    first = session.pump(
        folder, value, scorer(server[0], value), interval=0.05, stop_after=1, wait=calls.append
    )
    assert first["status"] == "paused" and calls == [0.05]
    view = reader.read(folder, pin)
    assert len(view["cases"]) == 1
    assert view["cases"][0]["transaction_feedback"] is None
    assert view["counts"]["decisions"] == 1
    assert (
        session.pump(folder, value, scorer(server[0], value), interval=0.05, wait=calls.append)[
            "status"
        ]
        == "awaiting_feedback"
    )
    view = reader.read(folder, pin)
    assert view["counts"]["decisions"] == 2 and view["counts"]["events"] < 20
    assert all(c["transaction_feedback"] is None for c in view["cases"])
    scores = server[1]["scores"]
    assert (
        session.pump(
            folder,
            value,
            scorer(server[0], value),
            interval=0.05,
            feedback=True,
            wait=calls.append,
        )["status"]
        == "complete"
    )
    final = reader.read(folder, pin)
    assert final["counts"]["events"] == 20
    assert all(c["transaction_feedback"] is not None for c in final["cases"])
    assert flow.verify_ledger(folder, value)["counts"]["decisions"] == 2
    session.pump(
        folder, value, scorer(server[0], value), interval=0.05, feedback=True, wait=calls.append
    )
    assert server[1]["scores"] == scores == 2
    assert reader.sha(source) == original


def test_pause_request_during_wait_never_commits_the_next_event(prepared, server):
    folder, value, _ = prepared
    pin = receipt(folder, value)

    def request_pause(_):
        session.atomic(folder / "pause.request", {"requested_at": 1})

    assert (
        session.pump(folder, value, scorer(server[0], value), interval=0.05, wait=request_pause)[
            "status"
        ]
        == "paused"
    )
    assert server[1]["scores"] == 0
    assert not reader.read(folder, pin)["cases"]
    session.pump(folder, value, scorer(server[0], value), interval=0.05, wait=lambda _: None)
    assert len(reader.read(folder, pin)["cases"]) == 2


def test_failed_HTTP_does_not_advance_then_resumes(prepared, server):
    folder, value, _ = prepared
    pin = receipt(folder, value)
    server[1]["wrong_identity"] = True
    with pytest.raises(ValueError):
        session.pump(folder, value, scorer(server[0], value), interval=0.05, wait=lambda _: None)
    assert reader.read(folder, pin)["producer"]["status"] == "failed"
    assert reader.read(folder, pin)["counts"]["decisions"] == 0
    server[1]["wrong_identity"] = False
    session.pump(folder, value, scorer(server[0], value), interval=0.05, wait=lambda _: None)
    assert reader.read(folder, pin)["counts"]["decisions"] == 2


@pytest.mark.parametrize("change", ["input", "receipt", "heartbeat", "response", "binding"])
def test_view_fails_closed_on_identity_or_bytes_changed(prepared, server, change):
    folder, value, _ = prepared
    pin = receipt(folder, value)
    session.pump(folder, value, scorer(server[0], value), interval=0.05, wait=lambda _: None)
    if change in {"input", "receipt"}:
        name = "input.json" if change == "input" else "session.json"
        (folder / name).write_text("{}")
    elif change == "heartbeat":
        session.atomic(
            folder / "heartbeat.json",
            {"session_sha256": "f" * 64, "timestamp": 1, "status": "running"},
        )
    else:
        with closing(sqlite3.connect(folder / "checkpoint.sqlite")) as conn, conn:
            if change == "response":
                conn.execute("UPDATE decisions SET response='{}'")
            else:
                payload = json.loads(conn.execute("SELECT payload FROM identity").fetchone()[0])
                payload["binding"]["input_receipt_sha256"] = "f" * 64
                conn.execute("UPDATE identity SET payload=?", (json.dumps(payload),))
    with pytest.raises(ValueError):
        reader.read(folder, pin)


def test_stale_heartbeat_does_not_claim_live(prepared):
    folder, value, _ = prepared
    pin = receipt(folder, value)
    session.atomic(
        folder / "heartbeat.json", {"session_sha256": pin, "timestamp": 0, "status": "running"}
    )
    assert reader.read(folder, pin)["producer"]["status"] == "stale"


@pytest.mark.parametrize("interval", [0, -1, float("nan"), float("inf"), 61])
def test_invalid_pacing_is_rejected_before_checkpoint(prepared, interval):
    folder, value, _ = prepared
    with pytest.raises(ValueError):
        session.pump(folder, value, None, interval=interval)
    assert not (folder / "checkpoint.sqlite").exists()


def test_view_can_follow_commits_while_producer_holds_writer_lock(prepared, server):
    folder, value, _ = prepared
    pin = receipt(folder, value)
    seen = []

    def observe(_):
        seen.append(reader.read(folder, pin)["counts"]["decisions"])

    session.pump(folder, value, scorer(server[0], value), interval=0.05, wait=observe)
    assert seen == [0, 1]


@pytest.mark.parametrize(
    "target", ["session-prepare", "session-prepare-expanded", "session-run", "session-feedback"]
)
def test_writer_declaration_is_required_before_dispatch(dispatch, target):
    result, calls = dispatch(target, SESSION_NO_OTHER_WRITERS="0")
    assert result.returncode != 0 and not calls


def test_parallel_goals_stop_after_failed_producer(dispatch):
    result, calls = dispatch(
        "-j4", "session-run", "session-feedback", SESSION_NO_OTHER_WRITERS="1", FAIL_ACTION="run"
    )
    assert result.returncode != 0 and len(calls) == 1
    assert calls[0][3:5] == ["scripts.product_session.controller", "run"]


def test_prepare_requires_reviewed_code_and_load_preserves_exact_session_identity(
    prepared, monkeypatch, tmp_path
):
    from types import SimpleNamespace

    directory, value, _ = prepared
    value["source_state"] = str(directory)
    code_root = tmp_path / "checkout"
    source = code_root / "scripts/product_session/controller.py"
    source.parent.mkdir(parents=True)
    source.write_text("reviewed implementation\n")
    monkeypatch.setattr(session.f, "ROOT", code_root)
    monkeypatch.setattr(flow, "work", lambda _: directory)
    monkeypatch.setattr(flow, "read", lambda _: value)
    called = []

    def capture(*args):
        called.append(args)
        (directory / "input.json").write_text(json.dumps(value))

    monkeypatch.setattr(flow, "prepare", capture)
    monkeypatch.setattr(
        session.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout=b""),
    )
    with pytest.raises(ValueError, match="Commit reviewed"):
        session.prepare("fixture", directory, True)
    assert not called
    monkeypatch.setattr(
        session.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout=source.read_bytes()),
    )
    first = session.prepare("fixture", directory, True)
    assert first == session.prepare("fixture", directory, True)
    assert session.load("fixture") == (directory, value)
    source.write_text("unreviewed change\n")
    with pytest.raises(ValueError, match="code identity"):
        session.load("fixture")
