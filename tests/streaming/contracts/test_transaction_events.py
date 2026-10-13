import pytest

from fraud_detection_mlops.streaming.contracts.transaction_events import (
    FeedbackEvent,
    TransactionEvent,
    event_digest,
    parse_event,
)


def transaction(**changes):
    return {
        "schema_version": 1,
        "event_type": "transaction",
        "transaction_id": 10,
        "tx_datetime": "2018-04-01T00:00:00.000000001",
        "customer_id": 1,
        "terminal_id": 2,
        "amount": 10.0,
        **changes,
    }


@pytest.mark.parametrize("label_field", ["TX_FRAUD", "fraud_label", "label", "TX_FRAUD_SCENARIO"])
def test_observed_transaction_cannot_carry_a_target(label_field):
    with pytest.raises(ValueError):
        parse_event(transaction(**{label_field: 1}))


@pytest.mark.parametrize(
    "changes",
    [
        {"transaction_id": True},
        {"customer_id": -1},
        {"terminal_id": 2**63},
        {"amount": -1.0},
        {"amount": float("inf")},
        {"amount": float("nan")},
        {"amount": True},
        {"schema_version": 2},
        {"tx_datetime": "2018-04-01T00:00:00Z"},
        {"tx_datetime": "2018-04-01T00:00:00+00:00"},
        {"tx_datetime": "2018-04-01T00:00:00.0000000001"},
        {"tx_datetime": "2018-02-30T00:00:00"},
        {"tx_datetime": "2500-04-01T00:00:00"},
    ],
)
def test_invalid_event_domains_are_rejected(changes):
    with pytest.raises(ValueError):
        parse_event(transaction(**changes))


def test_equivalent_fraction_texts_share_identity_without_losing_nanoseconds():
    left = parse_event(transaction(tx_datetime="2018-04-01T00:00:00.1"))
    right = parse_event(transaction(tx_datetime="2018-04-01T00:00:00.100000000"))
    adjacent = parse_event(transaction(tx_datetime="2018-04-01T00:00:00.100000001"))
    assert left.event_id == right.event_id == adjacent.event_id == "transaction:10"
    assert event_digest(left) == event_digest(right)
    assert event_digest(left) != event_digest(adjacent)
    assert adjacent.event_clock_ns - left.event_clock_ns == 1
    assert isinstance(left, TransactionEvent)


@pytest.mark.parametrize("label", [True, -1, 2, "1", 1.0])
def test_feedback_target_is_binary_and_strict(label):
    with pytest.raises(ValueError):
        FeedbackEvent(
            schema_version=1,
            event_type="feedback",
            transaction_id=10,
            available_at="2018-04-08T00:00:00",
            fraud_label=label,
        )


def test_feedback_is_a_separate_identity_and_unknown_event_type_is_rejected():
    event = FeedbackEvent(
        schema_version=1,
        event_type="feedback",
        transaction_id=10,
        available_at="2018-04-08T00:00:00",
        fraud_label=1,
    )
    assert event.event_id == "feedback:10"
    with pytest.raises(ValueError):
        parse_event({"event_type": "correction"})
