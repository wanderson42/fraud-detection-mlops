"""Causal budget decisions on generated scores, without native data or fitting."""

from dataclasses import FrozenInstanceError

from pydantic import ValidationError
import pytest

from fraud_detection_mlops.serving.request_schema import ScoreResponse
from fraud_detection_mlops.streaming.contracts.customer_day_alerts import CustomerDayAlertConfig
from fraud_detection_mlops.streaming.contracts.transaction_events import TransactionEvent
from fraud_detection_mlops.streaming.decisions.customer_day_alert_policy import (
    AlertPolicyError,
    CustomerDayAlertPolicy,
)


def config(**changes):
    return CustomerDayAlertConfig.model_validate(
        {
            "schema_version": 1,
            "version": "first_crossing_customer_day_v1",
            "model_sha256": "a" * 64,
            "serving_release_id": "b" * 32,
            "score_threshold": 0.7,
            "daily_customer_budget": 2,
            **changes,
        }
    )


def scored(transaction_id, customer_id, score, clock="2018-04-01T10:00:00"):
    event = TransactionEvent(
        schema_version=1,
        event_type="transaction",
        transaction_id=transaction_id,
        customer_id=customer_id,
        terminal_id=1,
        amount=10.0,
        tx_datetime=clock,
    )
    response = ScoreResponse(
        schema_version=1,
        transaction_id=transaction_id,
        score=score,
        score_semantics="uncalibrated_ranking",
        model_id="hist_gradient_boosting",
        model_sha256="a" * 64,
        feature_contract_version="gold_v1",
        serving_release_id="b" * 32,
    )
    return event, response


def test_first_crossing_uses_customer_budget_without_replacing_earlier_alerts():
    policy = CustomerDayAlertPolicy(config())
    inputs = [(1, 5, 0.6), (2, 5, 0.7), (3, 5, 0.9), (4, 9, 0.8), (5, 1, 1.0)]
    decisions = [policy.handle(*scored(*item)) for item in inputs]
    assert [d.status for d in decisions] == [
        "below_threshold",
        "alert",
        "already_alerted",
        "alert",
        "budget_exhausted",
    ]
    assert [d.daily_budget_used for d in decisions] == [0, 1, 1, 2, 2]
    assert [d.customer_id for d in decisions if d.alert_id is not None] == [5, 9]


def test_same_customer_can_alert_next_calendar_day_and_budget_resets():
    policy = CustomerDayAlertPolicy(config(daily_customer_budget=1))
    first = policy.handle(*scored(1, 5, 0.8, "2018-04-01T23:59:59.999999999"))
    second = policy.handle(*scored(2, 5, 0.8, "2018-04-02T00:00:00"))
    assert first.status == second.status == "alert"
    assert first.daily_budget_used == second.daily_budget_used == 1
    assert first.alert_id != second.alert_id


def test_old_duplicate_keeps_original_decision_and_does_not_consume_next_day_budget():
    policy = CustomerDayAlertPolicy(config(daily_customer_budget=1))
    first = policy.handle(*scored(1, 5, 0.8))
    policy.handle(*scored(2, 5, 0.8, "2018-04-02T10:00:00"))
    assert policy.handle(*scored(1, 5, 0.8)) == first
    assert policy.handle(*scored(3, 9, 0.9, "2018-04-02T10:00:00")).status == "budget_exhausted"


def test_equivalent_fractional_clocks_are_the_same_delivery():
    policy = CustomerDayAlertPolicy(config())
    first = policy.handle(*scored(1, 1, 0.8, "2018-04-01T10:00:00.1"))
    assert policy.handle(*scored(1, 1, 0.8, "2018-04-01T10:00:00.100000000")) == first


@pytest.mark.parametrize("field,value", [("score", 0.9), ("model_id", "other_model")])
def test_conflicting_duplicate_preserves_first_alert(field, value):
    policy = CustomerDayAlertPolicy(config(daily_customer_budget=1))
    event, response = scored(1, 1, 0.8)
    first = policy.handle(event, response)
    with pytest.raises(AlertPolicyError, match="Conflicting"):
        policy.handle(event, response.model_copy(update={field: value}))
    assert policy.handle(event, response) == first
    assert policy.handle(*scored(2, 2, 0.9)).status == "budget_exhausted"


@pytest.mark.parametrize(
    "field,value",
    [("transaction_id", 9), ("model_sha256", "c" * 64), ("serving_release_id", "d" * 32)],
)
def test_wrong_identity_cannot_advance_clock_or_use_budget(field, value):
    policy = CustomerDayAlertPolicy(config(daily_customer_budget=1))
    event, response = scored(1, 1, 0.8, "2018-04-02T10:00:00")
    with pytest.raises(AlertPolicyError, match="differs"):
        policy.handle(event, response.model_copy(update={field: value}))
    assert policy.handle(*scored(2, 2, 0.8)).status == "alert"


@pytest.mark.parametrize("clock,tx_id", [("2018-04-01T09:00:00", 3), ("2018-04-01T10:00:00", 1)])
def test_late_and_reversed_timestamp_peers_do_not_change_budget(clock, tx_id):
    policy = CustomerDayAlertPolicy(config())
    policy.handle(*scored(2, 2, 0.8))
    with pytest.raises(AlertPolicyError, match="order"):
        policy.handle(*scored(tx_id, 1, 0.9, clock))
    assert policy.handle(*scored(4, 4, 0.9)).daily_budget_used == 2


def test_rebuilding_a_complete_prefix_reproduces_alert_ids_and_decisions():
    events = [scored(1, 1, 0.8), scored(2, 2, 0.6), scored(3, 2, 0.9), scored(4, 3, 1.0)]
    uninterrupted = CustomerDayAlertPolicy(config())
    expected = [uninterrupted.handle(*item) for item in events]
    restarted = CustomerDayAlertPolicy(config())
    assert [restarted.handle(*item) for item in events[:2]] == expected[:2]
    assert [restarted.handle(*item) for item in events[2:]] == expected[2:]
    with pytest.raises(FrozenInstanceError):
        expected[0].status = "below_threshold"


def test_future_transactions_cannot_change_an_already_emitted_alert():
    prefix = [scored(1, 1, 0.7), scored(2, 2, 0.8)]
    policy = CustomerDayAlertPolicy(config(daily_customer_budget=1))
    original = [policy.handle(*item) for item in prefix]
    assert policy.handle(*scored(3, 3, 1.0)).status == "budget_exhausted"
    assert [policy.handle(*item) for item in prefix] == original


@pytest.mark.parametrize(
    "changes",
    [
        {"score_threshold": None},
        {"score_threshold": float("nan")},
        {"score_threshold": float("inf")},
        {"score_threshold": -0.1},
        {"score_threshold": 1.1},
        {"daily_customer_budget": True},
        {"daily_customer_budget": 101},
        {"daily_customer_budget": 0},
        {"fraud_label": 1},
        {"model_sha256": "unidentified"},
    ],
)
def test_invalid_or_unfrozen_configuration_is_rejected(changes):
    with pytest.raises(ValidationError):
        config(**changes)


def test_policy_identity_binds_threshold_budget_and_release():
    identities = {
        config().policy_sha256,
        config(score_threshold=0.8).policy_sha256,
        config(daily_customer_budget=1).policy_sha256,
        config(serving_release_id="c" * 32).policy_sha256,
        config(model_sha256="c" * 64).policy_sha256,
    }
    assert len(identities) == 5


def test_feedback_cannot_be_sent_as_a_scored_transaction():
    with pytest.raises(AlertPolicyError, match="no feedback"):
        CustomerDayAlertPolicy(config()).handle({"fraud_label": 1}, scored(1, 1, 0.9)[1])
