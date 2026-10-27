"""Authenticated aggregate receiver/exporter; volatile projection, bounded TTL and HTTP body."""

import argparse
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
from threading import RLock
import time

from scripts.session_monitoring import contract as c


class Projection:
    def __init__(self, binding, *, ttl=30, clock=time.time):
        self.binding = c.binding(binding)
        c.require(type(ttl) is int and 10 <= ttl <= 120, "TTL must be 10..120 seconds")
        self.ttl, self.clock = ttl, clock
        self.value, self.received, self.invalid, self.rejected = None, 0, False, 0
        self.lock = RLock()

    def publish(self, value):
        with self.lock:
            try:
                c.validate(value, self.binding, self.clock())
                if self.value:
                    old = self.value
                    c.require(
                        value["captured_at"] > old["captured_at"], "Collection did not advance"
                    )
                    c.require(
                        all(value["counts"][k] >= old["counts"][k] for k in c.COUNTS),
                        "Counts regressed",
                    )
                    c.require(
                        old["watermark"] == [None] * 3
                        or value["watermark"] != [None] * 3
                        and value["watermark"] >= old["watermark"],
                        "Watermark regressed",
                    )
                self.value = copy.deepcopy(value)
                self.received, self.invalid = self.clock(), False
            except (ValueError, TypeError, KeyError) as exc:
                self.rejected += 1
                raise ValueError("Rejected aggregate publication") from exc

    def summary(self):
        with self.lock:
            now, value = self.clock(), copy.deepcopy(self.value)
            age = max(0, now - value["captured_at"]) if value else None
            active = value and value["producer_status"] in {
                "warming_up",
                "running",
                "draining_feedback",
            }
            producer_stale = bool(active and now - value["producer_at"] > 15)
            valid = bool(
                value
                and not self.invalid
                and age <= self.ttl
                and not producer_stale
                and value["producer_status"] != "failed"
            )
            return {
                "binding": self.binding,
                "source_valid": valid,
                "collection_age_seconds": age,
                "producer_stale": producer_stale,
                "rejected_publications": self.rejected,
                "projection": value,
            }

    def metrics(self):
        s = self.summary()
        labels = (
            '{session_id="'
            + self.binding["session_id"]
            + '",session_sha256="'
            + self.binding["session_sha256"]
            + '"}'
        )
        lines = []

        def gauge(name, value, description):
            lines.extend(
                [
                    "# HELP fraud_session_" + name + " " + description,
                    "# TYPE fraud_session_" + name + " gauge",
                    "fraud_session_" + name + labels + " " + str(float(value)),
                ]
            )

        gauge(
            "source_valid",
            int(s["source_valid"]),
            "Current bound source collected within TTL and producer healthy",
        )
        gauge(
            "publications_rejected",
            s["rejected_publications"],
            "Rejected aggregate publications since receiver start",
        )
        for name in ("expected_events", "expected_decisions"):
            gauge(name, self.binding[name], "Prepared capture bound")
        b = self.binding
        lines.append(
            'fraud_session_model_info{session_id="'
            + b["session_id"]
            + '",session_sha256="'
            + b["session_sha256"]
            + '",model_sha256="'
            + b["model_sha256"]
            + '",serving_release_id="'
            + b["serving_release_id"]
            + '"} 1'
        )
        value = s["projection"]
        if value:
            gauge(
                "collection_age_seconds",
                s["collection_age_seconds"],
                "Wall time since source read; historical event clock is separate",
            )
            gauge("collected_timestamp_seconds", value["captured_at"], "Source read wall clock")
            gauge(
                "producer_age_seconds",
                max(0, self.clock() - value["producer_at"]),
                "Producer heartbeat age; paused and complete may be idle",
            )
            gauge(
                "producer_failed",
                int(value["producer_status"] == "failed"),
                "Producer reported failure",
            )
            gauge(
                "producer_stale",
                s["producer_stale"],
                "Active producer heartbeat older than 15 seconds",
            )
            gauge(
                "complete",
                value["counts"]["events"] == b["expected_events"]
                and value["counts"]["decisions"] == b["expected_decisions"],
                "Committed capture and delayed feedback drained",
            )
            for name, count in value["counts"].items():
                gauge(
                    name, count, "Committed session count; retained values require source_valid=1"
                )
            for name, ms in value["latency_ms"].items():
                gauge(
                    "http_latency_" + name + "_ms",
                    ms,
                    "All committed client HTTP receipts including retries; excludes warmup and pacing",
                )
            gauge(
                "http_retries",
                value["counts"]["http_attempts"] - value["counts"]["decisions"],
                "Retries in committed successful receipts; not total errors",
            )
            if value["watermark"][0] is not None:
                gauge(
                    "event_clock_seconds",
                    value["watermark"][0] / 1e9,
                    "Historical causal event clock",
                )
        return "\n".join(lines) + "\n"


def handler(projection, token):
    c.require(isinstance(token, str) and len(token) >= 32, "Private token required")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, status, value, content="application/json"):
            data = value.encode() if isinstance(value, str) else c.dumps(value)
            self.send_response(status)
            self.send_header("Content-Type", content)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/healthz":
                self.reply(200, {"status": "receiver_ready"})
            elif self.path == "/metrics":
                self.reply(200, projection.metrics(), "text/plain; version=0.0.4")
            elif self.path == "/summary":
                self.reply(200, projection.summary())
            else:
                self.reply(404, {"error": "not_found"})

        def do_PUT(self):
            if not secrets.compare_digest(
                self.headers.get("Authorization", ""), "Bearer " + token
            ):
                self.reply(401, {"error": "unauthorized"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                c.require(0 < length <= 32768, "Body exceeds limit")
                value = json.loads(self.rfile.read(length))
                if self.path == "/projection":
                    projection.publish(value)
                elif self.path == "/invalidate" and value == {"binding": projection.binding}:
                    with projection.lock:
                        projection.invalid = True
                else:
                    raise ValueError("Unexpected route or identity")
            except ValueError, TypeError, KeyError:
                self.reply(400, {"error": "invalid_publication"})
                return
            self.reply(200, {"accepted": True})

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binding", type=Path, required=True)
    parser.add_argument("--port", type=int, default=19102)
    args = parser.parse_args()
    projection = Projection(json.loads(args.binding.read_bytes()))
    server = ThreadingHTTPServer(
        ("0.0.0.0", args.port), handler(projection, os.environ["FRAUD_SESSION_TOKEN"])
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
