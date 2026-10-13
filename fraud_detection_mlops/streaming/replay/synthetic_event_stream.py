"""Ten generated transactions covering timestamp peers and exact history boundaries."""

import pandas as pd

from fraud_detection_mlops.streaming.contracts.transaction_events import (
    LABEL_DELAY_NS,
    FeedbackEvent,
    ReplayEvent,
    TransactionEvent,
)


def synthetic_events() -> list[ReplayEvent]:
    clocks = (
        "2018-04-01T00:00:00",
        "2018-04-01T00:00:00",
        "2018-04-01T00:00:00.000000001",
        "2018-04-02T00:00:00",
        "2018-04-02T00:00:00.000000001",
        "2018-04-08T00:00:00",
        "2018-04-08T00:00:00.000000001",
        "2018-04-09T00:00:00",
        "2018-04-15T00:00:00",
        "2018-04-15T00:00:00.000000001",
    )
    amounts = (0.0, 0.0, 10.0, 20.0, 1000.0, 30.0, 40.0, 50.0, 60.0, 70.0)
    labels = (0, 1, 1, 0, 1, 0, 1, 0, 1, 0)
    events = []
    for index, clock in enumerate(clocks):
        transaction = TransactionEvent(
            schema_version=1,
            event_type="transaction",
            transaction_id=index,
            tx_datetime=clock,
            customer_id=2 if index == 4 else 1,
            terminal_id=10,
            amount=amounts[index],
        )
        events.extend(
            (
                transaction,
                FeedbackEvent(
                    schema_version=1,
                    event_type="feedback",
                    transaction_id=index,
                    available_at=pd.Timestamp(
                        transaction.event_clock_ns + LABEL_DELAY_NS
                    ).isoformat(),
                    fraud_label=labels[index],
                ),
            )
        )
    events.sort(key=lambda e: (e.event_clock_ns, e.event_type, e.transaction_id))
    return [*events, events[0], events[-1]]
