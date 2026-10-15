"""Durable operational journal, separate from the frozen atomic decision ledger."""

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import threading
import time
from urllib.parse import quote

VERSION = "paced_monitoring_v1"


def readonly(path: Path):
    path = path.absolute()
    if path.resolve() != path or not path.is_file():
        raise ValueError("Missing or redirected monitoring input")
    return sqlite3.connect("file:" + quote(str(path), safe="/") + "?mode=ro", uri=True, timeout=5)


class Journal:
    def __init__(self, path: Path, identity: dict):
        self.path = path
        self.local = threading.local()
        self.connections = []
        connection = self.connection()
        connection.executescript(
            "CREATE TABLE IF NOT EXISTS identity (id INTEGER PRIMARY KEY CHECK(id=1),payload TEXT);"
            "CREATE TABLE IF NOT EXISTS sessions (id INTEGER PRIMARY KEY, started REAL, ended REAL, "
            "heartbeat REAL, state TEXT, warmup_events INTEGER DEFAULT 0,warmup_seconds REAL DEFAULT 0);"
            "CREATE TABLE IF NOT EXISTS timings (session_id INTEGER,event_id TEXT,source_ns INTEGER,"
            "event_type TEXT,scheduled REAL,emitted REAL,dequeued REAL,committed REAL,"
            "processing_seconds REAL,queue_depth INTEGER,status TEXT,PRIMARY KEY(session_id,event_id));"
        )
        value = json.dumps(identity, sort_keys=True)
        saved = connection.execute("SELECT payload FROM identity WHERE id=1").fetchone()
        if saved is None:
            connection.execute("INSERT INTO identity VALUES(1,?)", (value,))
        elif saved[0] != value:
            raise ValueError("Operational journal identity differs; preserve it")

    def connection(self):
        if not hasattr(self.local, "connection"):
            con = sqlite3.connect(
                self.path, isolation_level=None, timeout=10, check_same_thread=False
            )
            # DELETE permits an actually read-only bind mount even after the last
            # writer exits; WAL readers can otherwise need to create -shm files.
            con.execute("PRAGMA journal_mode=DELETE")
            con.execute("PRAGMA synchronous=FULL")
            self.local.connection = con
            self.connections.append(con)
        return self.local.connection

    def start(self):
        now = time.time()
        return (
            self.connection()
            .execute(
                "INSERT INTO sessions(started,heartbeat,state) VALUES(?,?,'running')", (now, now)
            )
            .lastrowid
        )

    def heartbeat(self, session):
        self.connection().execute(
            "UPDATE sessions SET heartbeat=? WHERE id=?", (time.time(), session)
        )

    def warmup(self, session, count, seconds):
        self.connection().execute(
            "UPDATE sessions SET warmup_events=?,warmup_seconds=?,heartbeat=? WHERE id=?",
            (count, seconds, time.time(), session),
        )

    def emitted(self, session, event, scheduled, emitted, depth):
        self.connection().execute(
            "INSERT INTO timings VALUES(?,?,?,?,?,?,NULL,NULL,NULL,?,'emitted')",
            (
                session,
                event.event_id,
                event.event_clock_ns,
                event.event_type,
                scheduled,
                emitted,
                depth,
            ),
        )
        self.heartbeat(session)

    def dequeued(self, session, event, stamp):
        self.connection().execute(
            "UPDATE timings SET dequeued=? WHERE session_id=? AND event_id=?",
            (stamp, session, event.event_id),
        )

    def committed(self, session, event, stamp, processing):
        self.connection().execute(
            "UPDATE timings SET committed=?,processing_seconds=?,status='measured' WHERE session_id=? AND event_id=?",
            (stamp, processing, session, event.event_id),
        )
        self.heartbeat(session)

    def finish(self, session, state):
        now = time.time()
        self.connection().execute(
            "UPDATE sessions SET state=?,ended=?,heartbeat=? WHERE id=?",
            (state, now, now, session),
        )

    def reconcile(self, checkpoint: Path):
        """A crash after decision commit can leave a timing gap; never invent a duration."""
        with closing(readonly(checkpoint)) as core:
            for session, event_id in (
                self.connection()
                .execute("SELECT session_id,event_id FROM timings WHERE status='emitted'")
                .fetchall()
            ):
                if core.execute("SELECT 1 FROM events WHERE event_id=?", (event_id,)).fetchone():
                    measured = (
                        self.connection()
                        .execute(
                            "SELECT 1 FROM timings WHERE event_id=? AND status='measured' LIMIT 1",
                            (event_id,),
                        )
                        .fetchone()
                    )
                    self.connection().execute(
                        "UPDATE timings SET status=? WHERE session_id=? AND event_id=?",
                        ("superseded" if measured else "recovered_unmeasured", session, event_id),
                    )
        self.connection().execute("UPDATE sessions SET state='interrupted' WHERE state='running'")

    def close(self):
        for connection in self.connections:
            connection.close()
