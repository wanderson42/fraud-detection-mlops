"""Strict wire format for precomputed Gold features; no label or scoring policy."""

import re
from typing import Annotated, Literal, Self

import pandas as pd
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    StrictInt,
    StrictStr,
    create_model,
    model_validator,
)

from fraud_detection_mlops.features.feature_schema import FEATURE_COLUMNS, FEATURE_DTYPES
from fraud_detection_mlops.features.feature_validation import validate_feature_record

CONTRACT_VERSION = "precomputed_features_v1"
FEATURE_CONTRACT_VERSION = "gold_v1"
SCORE_SEMANTICS = "uncalibrated_ranking"
Identifier = Annotated[StrictInt, Field(ge=0, le=2**63 - 1)]
SchemaVersion = Annotated[StrictInt, Field(ge=1, le=1)]
CLOCK_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?\Z")


class FeatureRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False, frozen=True)

    @model_validator(mode="after")
    def check_gold_values(self) -> Self:
        validate_feature_record(self.model_dump())
        return self


GoldFeatures = create_model(
    "GoldFeatures",
    __base__=FeatureRecord,
    **{
        name: (
            Annotated[
                StrictInt if dtype.startswith("int") else StrictFloat,
                Field(
                    ge=0,
                    **(
                        {"le": 127 if dtype == "int8" else 2**63 - 1}
                        if dtype.startswith("int")
                        else {}
                    ),
                ),
            ],
            ...,
        )
        for name, dtype in FEATURE_DTYPES.items()
    },
)


class ScoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: SchemaVersion
    feature_contract_version: Literal["gold_v1"]
    transaction_id: Identifier
    customer_id: Identifier
    terminal_id: Identifier
    tx_datetime: StrictStr
    feature_as_of: StrictStr
    features: GoldFeatures

    @model_validator(mode="after")
    def check_clock(self) -> Self:
        stamps = []
        for text in (self.tx_datetime, self.feature_as_of):
            if not CLOCK_PATTERN.fullmatch(text):
                raise ValueError("Use ISO 8601 without offset and at most nanosecond precision")
            try:
                stamp = pd.Timestamp(text)
                _ = stamp.value
            except (ValueError, OverflowError) as exc:
                raise ValueError("Clock must fit the Gold nanosecond timestamp type") from exc
            stamps.append(stamp)
        if (
            stamps[0] != stamps[1]
            or self.features.TX_HOUR != stamps[0].hour
            or self.features.TX_WEEKDAY != stamps[0].isoweekday()
        ):
            raise ValueError("Event clock, feature clock, hour and weekday must agree")
        return self

    def feature_frame(self) -> pd.DataFrame:
        return pd.DataFrame([self.features.model_dump()], columns=FEATURE_COLUMNS).astype(
            FEATURE_DTYPES
        )


class ScoreResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    schema_version: SchemaVersion
    transaction_id: Identifier
    score: Annotated[StrictFloat, Field(ge=0, le=1)]
    score_semantics: Literal["uncalibrated_ranking"]
    model_id: StrictStr
    model_sha256: StrictStr
    feature_contract_version: Literal["gold_v1"]
    serving_release_id: StrictStr
