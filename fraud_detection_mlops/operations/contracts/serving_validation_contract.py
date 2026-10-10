"""One explicit validation operation against an identified local serving release."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator

from fraud_detection_mlops.serving.model_release import Digest, ReleaseManifest, SmokeCase
from fraud_detection_mlops.serving.request_schema import ScoreResponse

VERSION = "serving_validation_v1"
OperationId = Annotated[StrictStr, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,79}$")]


class ServingValidationError(ValueError):
    """Permanent contract, release or publication failure; do not retry."""


class TransientServiceError(ConnectionError):
    """Temporary service unavailability eligible for a bounded retry."""


def local_service_url(value: str) -> str:
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError as exc:
        raise ValueError("Use an HTTP loopback URL with an explicit port") from exc
    if (
        parts.scheme != "http"
        or parts.hostname not in {"127.0.0.1", "localhost", "::1"}
        or port is None
        or not 1 <= port <= 65535
        or parts.username is not None
        or parts.password is not None
        or parts.path not in {"", "/"}
        or parts.query
        or parts.fragment
    ):
        raise ValueError("Use an HTTP loopback URL with an explicit port and no credentials/path")
    return value.rstrip("/")


class ServingValidationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    operation_id: OperationId
    release_path: Path
    manifest_sha256: Digest
    service_url: StrictStr
    output_root: Path

    _service_url = field_validator("service_url")(local_service_url)


class ServingValidationReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False, frozen=True)

    schema_version: Literal[1]
    version: Literal["serving_validation_v1"]
    operation_id: OperationId
    manifest_sha256: Digest
    smoke_sha256: Digest
    implementation_sha256: Digest
    service_url: StrictStr
    serving_release_id: StrictStr
    evidence_kind: Literal["verified_reference", "synthetic_smoke"]
    checked_at_utc: datetime
    score: ScoreResponse
    parity_tolerance: Literal[1e-12]
    status: Literal["success"]
    refit: Literal[False]
    production_promotion: Literal[False]

    _service_url = field_validator("service_url")(local_service_url)

    @field_validator("checked_at_utc")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        if value.utcoffset() is None or value.utcoffset().total_seconds() != 0:
            raise ValueError("Receipt timestamps must use UTC")
        return value


@dataclass(frozen=True)
class InspectedServingRelease:
    request: ServingValidationRequest
    manifest: ReleaseManifest
    smoke: SmokeCase
    implementation_sha256: str

    @property
    def receipt_path(self) -> Path:
        return self.request.output_root / self.request.operation_id / "receipt.json"
