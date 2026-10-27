"""Small rehearsal helpers: bounded workers and identity-bound concurrent observations."""

import copy
import sqlite3
from threading import Event, Lock, Thread
import time


def require(condition, message):
    if not condition:
        raise ValueError(message)


class Worker:
    def __init__(self, function):
        self.done = Event()
        self.result, self.error = None, None

        def run():
            try:
                self.result = function()
            except BaseException as exc:  # noqa: BLE001 — propagate worker failure to the test thread
                self.error = exc
            finally:
                self.done.set()

        self.thread = Thread(target=run, daemon=True)
        self.thread.start()

    def check(self):
        if self.error is not None:
            raise self.error
        return self.result

    def join(self, timeout=60):
        self.thread.join(timeout)
        require(not self.thread.is_alive(), "Producer did not stop; preserve the checkpoint")
        return self.check()


class Observer:
    def __init__(self, collect, publish, *, interval=2):
        self.collect, self.publish, self.interval = collect, publish, interval
        self.stop_event, self.lock = Event(), Lock()
        self.value, self.error, self.thread = None, None, None

    def start(self):
        require(self.thread is None, "Observer already started")

        def observe():
            try:
                initialization_deadline = time.monotonic() + 10
                while not self.stop_event.is_set():
                    try:
                        value = self.collect()
                    except sqlite3.OperationalError as exc:
                        empty = (
                            self.value is None
                            or self.value.get("counts", {}).get("events", 0) == 0
                        )
                        if (
                            empty
                            and "no such table" in str(exc)
                            and time.monotonic() < initialization_deadline
                        ):
                            self.stop_event.wait(0.05)
                            continue
                        raise
                    self.publish(value)
                    with self.lock:
                        self.value = copy.deepcopy(value)
                    self.stop_event.wait(self.interval)
            except BaseException as exc:  # noqa: BLE001 — propagate worker failure to the test thread
                self.error = exc

        self.thread = Thread(target=observe, daemon=True)
        self.thread.start()
        return self

    def latest(self):
        if self.error is not None:
            raise self.error
        with self.lock:
            return copy.deepcopy(self.value)

    def stop(self):
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(30)
            require(not self.thread.is_alive(), "Collector did not stop")
        return self.latest()


def progressing(records, expected):
    """Require actual Prometheus growth while the bound producer is running."""
    live = [
        r
        for r in records
        if r["producer_status"] == "running"
        and 0 < r["prometheus_decisions"] <= r["committed_decisions"] < expected
    ]
    return len(live) >= 2 and max(r["prometheus_decisions"] for r in live) > min(
        r["prometheus_decisions"] for r in live
    )


def sample(binding, aggregate, committed, prometheus, source_valid):
    require(aggregate["binding"] == binding, "Another session was collected")
    require(
        type(prometheus) in (int, float) and 0 <= prometheus <= committed,
        "Prometheus count exceeds committed decisions",
    )
    require(source_valid == 1, "Collection is not currently valid")
    require(
        aggregate["counts"]["decisions"] <= committed, "Collected count exceeds current checkpoint"
    )
    return {
        "observed_at": time.time(),
        "producer_status": aggregate["producer_status"],
        "collected_decisions": aggregate["counts"]["decisions"],
        "committed_decisions": committed,
        "prometheus_decisions": prometheus,
        "session_sha256": binding["session_sha256"],
    }
