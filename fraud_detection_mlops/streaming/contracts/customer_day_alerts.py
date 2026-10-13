"""Identified, immutable configuration for first-crossing customer-day alerts."""

import hashlib
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, StrictStr

from fraud_detection_mlops.serving.request_schema import SchemaVersion

ALERT_POLICY_VERSION = "first_crossing_customer_day_v1"
Sha256 = Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]
ReleaseId = Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{32}$")]


class CustomerDayAlertConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)

    schema_version: SchemaVersion
    version: Literal["first_crossing_customer_day_v1"]
    model_sha256: Sha256
    serving_release_id: ReleaseId
    score_threshold: Annotated[StrictFloat, Field(ge=0, le=1)]
    daily_customer_budget: Annotated[StrictInt, Field(ge=1, le=100)] = 100

    @property
    def policy_sha256(self) -> str:
        payload = json.dumps(self.model_dump(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()
