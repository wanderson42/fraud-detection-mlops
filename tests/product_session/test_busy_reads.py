"""Busy SQLite readers retry complete snapshots and preserve identity failures."""

from contextlib import closing
import copy
import hashlib
import json
import sqlite3
import subprocess
from threading import Event
import time

import pytest
from scripts.product_session import configuration as config
from scripts.product_session import controller as session
from scripts.product_session import reader
from scripts.session_monitoring import collector
from tests.product_flow.conftest import scorer
from tests.product_session.operations import Worker


def test_portal_and_collector_recover_after_real_exclusive_writer_lock(prepared, server):
    directory, value, _ = prepared
    (directory / "input.json").write_text(json.dumps(value))
    (directory / "session.json").write_text(
        json.dumps(
            {
                "schema": "fraud_product_session_v1",
                "session_id": "fixture",
                "input_sha256": reader.sha(directory / "input.json"),
            }
        )
    )
    session.pump(directory, value, scorer(server[0], value), interval=0.05, wait=lambda _: None)
    pin = reader.sha(directory / "session.json")
    binding, _ = collector.identity(directory)
    checkpoint = directory / "checkpoint.sqlite"
    before = reader.sha(checkpoint)
    with closing(sqlite3.connect(checkpoint)) as writer:
        writer.execute("BEGIN EXCLUSIVE")
        started = [Event(), Event()]

        def invoke(index, function):
            started[index].set()
            return function()

        readers = [
            Worker(lambda: invoke(0, lambda: reader.read(directory, pin))),
            Worker(lambda: invoke(1, lambda: collector.collect(directory, binding))),
        ]
        try:
            assert all(v.wait(1) for v in started)
            # Both initial sqlite busy timeouts are 2s; force a full failed attempt.
            time.sleep(2.4)
            assert all(not v.done.is_set() for v in readers)
        finally:
            writer.rollback()
    portal, metric = [worker.join(5) for worker in readers]
    assert portal["counts"]["decisions"] == metric["counts"]["decisions"] == 2
    assert metric["binding"] == binding
    assert portal["totals"]["feedback"] == metric["counts"]["decision_feedback"] == 0
    assert reader.sha(checkpoint) == before
    # A successful retry must not weaken the original identity check.
    source = directory / "input.json"
    source.write_text(source.read_text() + " ")
    with pytest.raises(ValueError, match="identity"):
        reader.read(directory, pin)
    with pytest.raises(ValueError, match="source changed"):
        collector.collect(directory, binding)


@pytest.mark.parametrize("code", [sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED, 517])
def test_busy_retry_has_a_deadline_and_preserves_original_exception(monkeypatch, code):
    clock, attempts = [0.0], []
    monkeypatch.setattr(reader.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(reader.time, "sleep", lambda _: None)
    failure = sqlite3.OperationalError("database is locked")
    failure.sqlite_errorcode = code

    @reader.retry_busy
    def blocked():
        attempts.append(1)
        clock[0] += 8
        raise failure

    with pytest.raises(sqlite3.OperationalError) as caught:
        blocked()
    assert caught.value is failure and len(attempts) == 2


@pytest.mark.parametrize("code", [None, sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_ERROR])
def test_other_database_errors_are_not_retried(code):
    attempts = []
    failure = sqlite3.OperationalError("no such table: identity")
    if code is not None:
        failure.sqlite_errorcode = code

    @reader.retry_busy
    def invalid():
        attempts.append(1)
        raise failure

    with pytest.raises(sqlite3.OperationalError) as caught:
        invalid()
    assert caught.value is failure and len(attempts) == 1


def commit(root, *paths):
    subprocess.run(["git", "add", "--", *paths], cwd=root, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "reviewed",
        ],
        cwd=root,
        check=True,
    )


def test_exact_busy_transition_keeps_scale_and_legacy_receipts_readable(tmp_path, monkeypatch):
    source_root = config.f.ROOT
    paths = [config.TRANSITION, "references/session_busy_source_transition_v1.json"]
    for path in paths:
        dest = tmp_path / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes((source_root / path).read_bytes())
    for path in (source_root / "scripts/product_session").glob("*.py"):
        dest = tmp_path / "scripts/product_session" / path.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(path.read_bytes())
    monkeypatch.setattr(config.f, "ROOT", tmp_path)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    commit(tmp_path, ".")
    scale = json.loads((tmp_path / paths[0]).read_bytes())
    busy = json.loads((tmp_path / paths[1]).read_bytes())
    for saved in (scale["before"], busy["before"], config.code_pins()):
        original = copy.deepcopy(saved)
        config.accept(saved)
        assert original == saved
    # A different complete source map cannot be blessed by the reviewed declaration.
    invalid = copy.deepcopy(busy["before"])
    invalid["reader.py"] = "f" * 64
    with pytest.raises(ValueError, match="code identity"):
        config.accept(invalid)
    path = tmp_path / "scripts/product_session/reader.py"
    path.write_text(path.read_text() + "\n# unreviewed\n")
    with pytest.raises(ValueError, match="code identity"):
        config.accept(busy["before"])
    # Reject an uncommitted declaration even if its after-map fits local bytes.
    busy["after"] = config.code_pins()
    (tmp_path / paths[1]).write_text(json.dumps(busy))
    with pytest.raises(ValueError, match="Commit"):
        config.accept(busy["before"])
    assert hashlib.sha256(path.read_bytes()).hexdigest() == busy["after"]["reader.py"]
