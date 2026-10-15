"""Cached read-only Prometheus exporter and aggregate summary, no ML dependencies."""

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
import threading
import time

from scripts.monitoring.collector import exposition, snapshot


class Monitor:
    def __init__(self, directory, interval):
        if not 0.1 <= interval <= 3600:
            raise ValueError("Refresh interval must be 0.1..3600 seconds")
        self.directory = directory
        self.interval = interval
        self.lock = threading.Lock()
        self.data = None
        self.rendered = ""
        self.errors = 0
        self.stopped = threading.Event()

    def refresh(self):
        try:
            value = snapshot(self.directory)
            rendered = exposition(value)
            value.pop("durations")
        except ValueError, OSError, KeyError, TypeError, sqlite3.Error:
            with self.lock:
                self.data = None
                self.errors += 1
        else:
            with self.lock:
                self.data = value
                self.rendered = rendered

    def loop(self):
        while not self.stopped.is_set():
            self.refresh()
            self.stopped.wait(self.interval)

    def response(self, path):
        with self.lock:
            data = self.data
            errors = self.errors
            rendered = self.rendered
        ready = data is not None and time.time() - data["snapshot_timestamp"] <= max(
            2 * self.interval, 2
        )
        if path == "/healthz":
            return 200, "text/plain", b"up\n"
        if path == "/readyz":
            return (200 if ready else 503), "text/plain", (b"ready\n" if ready else b"not ready\n")
        if path == "/metrics":
            content = (
                rendered
                if ready
                else "# HELP fraud_monitor_collector_up Snapshot validity.\n# TYPE fraud_monitor_collector_up gauge\nfraud_monitor_collector_up 0\n"
            )
            content += f"# HELP fraud_monitor_collection_errors_total Failed collection attempts.\n# TYPE fraud_monitor_collection_errors_total counter\nfraud_monitor_collection_errors_total {errors}\n"
            return 200, "text/plain; version=0.0.4; charset=utf-8", content.encode()
        if path == "/summary":
            if not ready:
                return 503, "application/json", b'{"status":"not_ready"}\n'
            # Histograms expose aggregates; raw event IDs, features and labels are never served.
            summary = {k: v for k, v in data.items() if k != "durations"}
            return 200, "application/json", (json.dumps(summary, allow_nan=False) + "\n").encode()
        return 404, "text/plain", b"not found\n"


def server(monitor, host="127.0.0.1", port=19100):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            status, mime, body = monitor.response(self.path)
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    return ThreadingHTTPServer((host, port), Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=19100)
    parser.add_argument("--interval", type=float, default=10)
    args = parser.parse_args()
    monitor = Monitor(args.state, args.interval)
    thread = threading.Thread(target=monitor.loop, daemon=True)
    thread.start()
    http = server(monitor, args.host, args.port)
    try:
        http.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        monitor.stopped.set()
        http.server_close()
        thread.join()


if __name__ == "__main__":
    main()
