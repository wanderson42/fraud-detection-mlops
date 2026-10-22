"""Reuse the durable causal/triage algorithm with an HTTP scorer and atomic receipts."""

import json
import math
from pathlib import Path
import sqlite3

from scripts.product_flow.http_scoring import checked_response, digest

from fraud_detection_mlops.streaming.replay.durable_triage_replay import (
    SCHEMA,
    DurableTriageReplay,
    source_identity,
)
from fraud_detection_mlops.streaming.replay.temporal_replay_executor import json_text

EXCHANGES = (
    "CREATE TABLE http_exchanges (transaction_id INTEGER PRIMARY KEY REFERENCES decisions "
    "DEFERRABLE INITIALLY DEFERRED, request_sha256 TEXT NOT NULL, response_sha256 TEXT NOT NULL, "
    "attempts INTEGER NOT NULL CHECK(attempts BETWEEN 1 AND 3), elapsed_ms REAL NOT NULL)"
)


class HTTPReplay(DurableTriageReplay):
    """Same inherited handle/summary; separate checkpoint identity and HTTP-only runtime.

    Initialization deliberately belongs to this adapter: the accepted local executor
    requires an exact local ScoringRuntime. Its scoring/feature/triage code is not
    patched, bypassed via monkeypatching or copied into another event processor.
    """

    def __init__(self, path, *, scorer, policy, binding, scoring_window_ns):
        from fraud_detection_mlops.streaming.contracts.customer_day_alerts import (
            CustomerDayAlertConfig,
        )

        self._policy = CustomerDayAlertConfig.model_validate(policy)
        if (
            self._policy.model_sha256 != scorer.manifest.model_sha256
            or self._policy.serving_release_id != scorer.manifest.serving_release_id
        ):
            raise ValueError("HTTP scorer and alert policy identify different releases")
        self.runtime, self._scoring_window = scorer, tuple(scoring_window_ns)
        if len(self._scoring_window) != 2 or self._scoring_window[0] >= self._scoring_window[1]:
            raise ValueError("Invalid scoring window")
        self.identity = {
            "version": "fraud_product_http_replay_v1",
            "schema_version": 2,
            "binding": binding,
            "scoring": scorer.information(),
            "policy": self._policy.model_dump(),
            "policy_sha256": self._policy.policy_sha256,
            "model_sha256": scorer.manifest.model_sha256,
            "source_sha256": source_identity(),
            "scoring_window_ns": list(self._scoring_window),
            "input_sha256": binding["input_sha256"],
        }
        if str(path) != ":memory:":
            path = Path(path).absolute()
            if path.resolve() != path:
                raise ValueError("HTTP checkpoint cannot be redirected")
            path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, autocommit=True, timeout=5)
        connection = self.connection
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("BEGIN IMMEDIATE")
            tables = connection.execute(
                "SELECT name FROM sqlite_schema WHERE type='table'"
            ).fetchall()
            if not tables:
                if connection.execute("PRAGMA user_version").fetchone()[0] != 0:
                    raise ValueError("Foreign HTTP checkpoint")
                for statement in (*SCHEMA, EXCHANGES):
                    connection.execute(statement)
                connection.execute("PRAGMA user_version=2")
                connection.execute(
                    "INSERT INTO identity VALUES (1,?)", (json_text(self.identity),)
                )
                connection.execute("INSERT INTO checkpoint VALUES (1,NULL,NULL,NULL)")
            else:
                saved = connection.execute(
                    "SELECT payload FROM identity WHERE singleton=1"
                ).fetchone()
                if saved is None or json.loads(saved[0]) != self.identity:
                    raise ValueError(
                        "HTTP checkpoint belongs to another input, model, policy or implementation"
                    )
                if (
                    connection.execute("PRAGMA user_version").fetchone()[0] != 2
                    or connection.execute("PRAGMA quick_check").fetchone()[0] != "ok"
                    or connection.execute("PRAGMA foreign_key_check").fetchone() is not None
                ):
                    raise ValueError("HTTP checkpoint integrity failed")
            connection.execute("COMMIT")
            scorer.record = self.record_exchange
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            connection.close()
            raise

    def record_exchange(self, request, response, exchange):
        if not self.connection.in_transaction:
            raise ValueError("HTTP receipt must share the event transaction")
        checked_response(response.model_dump(), request, self.runtime.manifest)
        if (
            exchange["request_sha256"] != digest(request.model_dump())
            or exchange["response_sha256"] != digest(response.model_dump())
            or exchange["elapsed_ms"] < 0
            or not math.isfinite(exchange["elapsed_ms"])
        ):
            raise ValueError("HTTP exchange digests or duration differ")
        self.connection.execute(
            "INSERT INTO http_exchanges VALUES (?,?,?,?,?)",
            (
                request.transaction_id,
                exchange["request_sha256"],
                exchange["response_sha256"],
                exchange["attempts"],
                exchange["elapsed_ms"],
            ),
        )
