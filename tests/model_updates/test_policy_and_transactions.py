"""Relative quality and failure recovery are independent of the search provider."""

import copy

import pandas as pd
import pytest
from scripts.model_updates import policy, transaction


def metrics(ap, p100):
    return {"average_precision": ap, "daily_customer_precision_at_100": p100}


@pytest.mark.parametrize(
    "ap,p100,accepted",
    [(0.7, 0.55, True), (0.6, 0.55, False), (0.7, 0.48, False), (0.49, 0.55, False)],
)
def test_both_absolute_and_relative_gates(ap, p100, accepted):
    p = policy.load()
    values = {
        "dummy_prior": metrics(0.01, 0.01),
        "logistic_regression": metrics(0.4, 0.4),
        "hist_gradient_boosting": metrics(ap, p100),
    }
    assert policy.compare(values, metrics(0.6, 0.5), p)["passed"] is accepted


def test_maturity_uses_label_availability_not_transaction_or_wall_clock():
    meta = pd.DataFrame({"LABEL_AVAILABLE_AT": pd.to_datetime(["2018-05-20T12:00:00"])})
    with pytest.raises(ValueError, match="not mature"):
        policy.mature(meta, "2018-05-20T00:00:00")
    policy.mature(meta, "2018-05-20T12:00:00")


@pytest.mark.parametrize("change", ["dummy", "budget", "replacement", "mode"])
def test_unsafe_optimization_policies_refused(change):
    p = policy.load()
    if change == "dummy":
        p["optimization"]["dummy_prior"]["mode"] = "grid"
    elif change == "budget":
        p["optimization"]["logistic_regression"] = {
            "mode": "optuna",
            "space": {"classifier__C": [1.0]},
            "trials": 999,
            "timeout_seconds": 10,
        }
    elif change == "replacement":
        p["optimization"]["logistic_regression"]["parameters"] = {"classifier": None}
    else:
        p["optimization"]["logistic_regression"]["mode"] = "unknown"
    with pytest.raises(ValueError):
        policy.validate(p)


class Backend:
    def __init__(self, failure=None):
        self.version = "1"
        self.slot = "blue"
        self.failure = failure
        self.calls = []
        self.finalized = None

    def alias(self):
        return self.version

    def route(self):
        return self.slot

    def set_route(self, slot):
        self.calls.append(("route", slot))
        self.slot = slot

    def set_alias(self, version, expected):
        assert self.version == expected
        self.version = version
        self.calls.append(("alias", version))
        if version == "2" and self.failure == "alias_after_write":
            raise ValueError("injected alias readback failure")

    def check(self, identity):
        assert self.slot == identity["slot"]
        if identity["version"] == "2" and self.failure == "candidate":
            raise ValueError("injected candidate outage")
        if identity["version"] == "1" and self.failure == "recovery":
            raise ValueError("injected recovery outage")

    def finalize(self, identity):
        self.finalized = identity["version"]
        if identity["version"] == "2" and self.failure == "cleanup":
            raise ValueError("injected cleanup failure")

    def prepare_recovery(self, identity):
        self.calls.append(("start_previous", identity["version"]))


def intent():
    return {
        "approval_sha256": "a" * 64,
        "before": {"version": "1", "slot": "blue"},
        "after": {"version": "2", "slot": "green"},
    }


def test_training_or_staging_does_not_imply_release_authorization():
    backend = Backend()
    journal = []
    for approval, writers in [("", True), ("wrong", True), ("a" * 64, False)]:
        with pytest.raises(ValueError):
            transaction.activate(
                intent(), backend, journal.append, approval=approval, no_other_writers=writers
            )
    assert not backend.calls and not journal


@pytest.mark.parametrize("failure", [None, "candidate", "alias_after_write", "cleanup"])
def test_release_and_compensation_cover_partial_mutations(failure):
    backend = Backend(failure)
    events = []
    save = lambda v: events.append(copy.deepcopy(v))
    if failure:
        with pytest.raises(ValueError, match="injected"):
            transaction.activate(intent(), backend, save, approval="a" * 64, no_other_writers=True)
        assert (backend.version, backend.slot, events[-1]["phase"]) == ("1", "blue", "rolled_back")
        assert ("start_previous", "1") in backend.calls
    else:
        value = transaction.activate(
            intent(), backend, save, approval="a" * 64, no_other_writers=True
        )
        assert (backend.version, backend.slot, value["phase"]) == ("2", "green", "active")
        assert events[0]["phase"] == "release_authorized"


def test_recovery_failure_is_recorded_and_foreign_alias_is_not_overwritten():
    backend = Backend("recovery")
    backend.slot = "green"
    backend.version = "2"
    events = []
    save = lambda v: events.append(copy.deepcopy(v))
    with pytest.raises(ValueError):
        transaction.restore(intent(), backend, save)
    assert events[-1]["phase"] == "recovery_required"
    backend = Backend()
    backend.version = "3"
    backend.slot = "green"
    with pytest.raises(ValueError, match="Foreign alias"):
        transaction.restore(intent(), backend, save)
    assert backend.version == "3" and not backend.calls


def test_resume_recovery_after_process_exit_between_route_and_alias():
    backend = Backend()
    backend.slot = "green"
    events = []
    assert (
        transaction.restore(intent(), backend, lambda v: events.append(copy.deepcopy(v)))["phase"]
        == "rolled_back"
    )
    assert backend.slot == "blue" and backend.version == "1"
