"""Apply a fixed threshold and daily budget to ordered, identified transaction scores."""

from dataclasses import dataclass
import hashlib
import json
from typing import Literal

import pandas as pd

from fraud_detection_mlops.serving.request_schema import ScoreResponse
from fraud_detection_mlops.streaming.contracts.customer_day_alerts import CustomerDayAlertConfig
from fraud_detection_mlops.streaming.contracts.transaction_events import (
    TransactionEvent,
    canonical_event,
)


class AlertPolicyError(ValueError):
    """A score, identity or ordering violation; no policy state was changed."""


@dataclass(frozen=True)
class CustomerDayAlertDecision:
    policy_sha256: str
    transaction_id: int
    customer_id: int
    decision_clock: str
    score: float
    status: Literal["alert", "below_threshold", "already_alerted", "budget_exhausted"]
    daily_budget_used: int
    alert_id: str | None


class CustomerDayAlertPolicy:
    """A deterministic in-memory reducer; no broker publication or durable checkpoint.

    Replay the complete committed score prefix to rebuild this state. A native
    executor must persist alerts and progress atomically before external delivery.
    Already emitted alerts are never replaced by a later, higher-scoring customer.
    """

    def __init__(self, config: CustomerDayAlertConfig):
        self.config = CustomerDayAlertConfig.model_validate(config.model_dump())
        self._seen: dict[int, tuple[str, CustomerDayAlertDecision]] = {}
        self._alerted: dict[str, set[int]] = {}
        self._last_order: tuple[int, int] | None = None

    def handle(
        self, transaction: TransactionEvent, response: ScoreResponse
    ) -> CustomerDayAlertDecision:
        if type(transaction) is not TransactionEvent or type(response) is not ScoreResponse:
            raise AlertPolicyError("Supply a transaction and its identified score; no feedback")
        transaction = TransactionEvent.model_validate(transaction.model_dump())
        response = ScoreResponse.model_validate(response.model_dump())
        if (
            response.transaction_id != transaction.transaction_id
            or response.model_sha256 != self.config.model_sha256
            or response.serving_release_id != self.config.serving_release_id
        ):
            raise AlertPolicyError("Transaction or release differs from the fixed alert policy")
        digest = hashlib.sha256(
            json.dumps(
                {"transaction": canonical_event(transaction), "score": response.model_dump()},
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode()
        ).hexdigest()
        previous = self._seen.get(transaction.transaction_id)
        if previous is not None:
            if previous[0] != digest:
                raise AlertPolicyError(
                    "Conflicting repeated score; preserve the original decision"
                )
            return previous[1]
        order = (transaction.event_clock_ns, transaction.transaction_id)
        if self._last_order is not None and order <= self._last_order:
            raise AlertPolicyError("New scores must follow event-clock and transaction-ID order")
        clock = pd.Timestamp(order[0])
        day = clock.strftime("%Y-%m-%d")
        alerted = self._alerted.get(day, set())
        alert_id = None
        if transaction.customer_id in alerted:
            status = "already_alerted"
        elif response.score < self.config.score_threshold:
            status = "below_threshold"
        elif len(alerted) >= self.config.daily_customer_budget:
            status = "budget_exhausted"
        else:
            status = "alert"
            alert_id = hashlib.sha256(
                f"{self.config.policy_sha256}:{day}:{transaction.customer_id}".encode()
            ).hexdigest()
        decision = CustomerDayAlertDecision(
            policy_sha256=self.config.policy_sha256,
            transaction_id=transaction.transaction_id,
            customer_id=transaction.customer_id,
            decision_clock=clock.isoformat(),
            score=response.score,
            status=status,
            daily_budget_used=len(alerted) + int(status == "alert"),
            alert_id=alert_id,
        )
        if status == "alert":
            self._alerted.setdefault(day, set()).add(transaction.customer_id)
        self._seen[transaction.transaction_id] = (digest, decision)
        self._last_order = order
        return decision
