"""Separate observed transactions from labels released seven event-clock days later."""

import hashlib
import json
from typing import Annotated, Literal, Self

import pandas as pd
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    StrictInt,
    StrictStr,
    model_validator,
)

from fraud_detection_mlops.features.feature_schema import DAY_NS, LABEL_DELAY_DAYS
from fraud_detection_mlops.serving.request_schema import CLOCK_PATTERN, Identifier, SchemaVersion

EVENT_CONTRACT_VERSION = "transaction_feedback_v1"
LABEL_DELAY_NS = LABEL_DELAY_DAYS * DAY_NS


def clock_ns(text: str) -> int:
    """Preserve Gold's timezone-naive source clock without conversion or rounding."""
    if not CLOCK_PATTERN.fullmatch(text):
        raise ValueError("Use the Gold ISO clock without offset, at nanosecond precision")
    try:
        value = pd.Timestamp(text).value
    except (ValueError, OverflowError) as exc:
        raise ValueError("Event clock must fit the Gold nanosecond timestamp") from exc
    if value < 0 or value > 2**63 - 1 - LABEL_DELAY_NS:
        raise ValueError("Event clock must be nonnegative and allow the seven-day feedback")
    return value


class TransactionEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)

    schema_version: SchemaVersion
    event_type: Literal["transaction"]
    transaction_id: Identifier
    tx_datetime: StrictStr
    customer_id: Identifier
    terminal_id: Identifier
    amount: Annotated[StrictFloat, Field(ge=0)]

    @model_validator(mode="after")
    def validate_clock(self) -> Self:
        clock_ns(self.tx_datetime)
        return self

    @property
    def event_id(self) -> str:
        return f"transaction:{self.transaction_id}"

    @property
    def event_clock_ns(self) -> int:
        return clock_ns(self.tx_datetime)


class FeedbackEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)

    schema_version: SchemaVersion
    event_type: Literal["feedback"]
    transaction_id: Identifier
    available_at: StrictStr
    fraud_label: Annotated[StrictInt, Field(ge=0, le=1)]

    @model_validator(mode="after")
    def validate_clock(self) -> Self:
        clock_ns(self.available_at)
        return self

    @property
    def event_id(self) -> str:
        return f"feedback:{self.transaction_id}"

    @property
    def event_clock_ns(self) -> int:
        return clock_ns(self.available_at)


ReplayEvent = TransactionEvent | FeedbackEvent


def canonical_event(event: ReplayEvent) -> str:
    """Equivalent ISO fractions identify the same logical event."""
    record = event.model_dump()
    field = "tx_datetime" if isinstance(event, TransactionEvent) else "available_at"
    record[field] = pd.Timestamp(event.event_clock_ns).isoformat()
    return json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False)


def event_digest(event: ReplayEvent) -> str:
    return hashlib.sha256(canonical_event(event).encode()).hexdigest()


def parse_event(record: dict) -> ReplayEvent:
    if record.get("event_type") == "transaction":
        return TransactionEvent.model_validate(record)
    if record.get("event_type") == "feedback":
        return FeedbackEvent.model_validate(record)
    raise ValueError("Expected a transaction or feedback event")
