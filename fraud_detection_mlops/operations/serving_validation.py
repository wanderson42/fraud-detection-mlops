"""Inspect a release, probe its local service and publish one immutable receipt."""

import argparse
from datetime import UTC, datetime
import hashlib
from http.client import HTTPException
import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from pydantic import ValidationError

from fraud_detection_mlops.artifacts import sha256, write_json
from fraud_detection_mlops.features.feature_schema import FEATURE_COLUMNS
from fraud_detection_mlops.modeling.contracts.model_interface import VERSION as INTERFACE_VERSION
from fraud_detection_mlops.operations.contracts import serving_validation_contract
from fraud_detection_mlops.operations.contracts.serving_validation_contract import (
    InspectedServingRelease,
    ServingValidationError,
    ServingValidationReceipt,
    ServingValidationRequest,
    TransientServiceError,
)
from fraud_detection_mlops.serving.model_release import ReleaseManifest, SmokeCase, checked_bytes
from fraud_detection_mlops.serving.request_schema import ScoreResponse

PARITY_TOLERANCE = 1e-12
HTTP_TIMEOUT_SECONDS = 5
MAX_RESPONSE_BYTES = 1024 * 1024
TRANSIENT_HTTP_STATUSES = {408, 429, 500, 502, 503, 504}


def implementation_sha256() -> str:
    paths = [Path(__file__), Path(serving_validation_contract.__file__)]
    return hashlib.sha256(b"".join(path.read_bytes() for path in paths)).hexdigest()


def inspect_release(request: ServingValidationRequest) -> InspectedServingRelease:
    """Check contract and all three files without deserializing or fitting the model."""
    directory = request.release_path
    try:
        for name in ("manifest.json", "pipeline.skops", "smoke.json"):
            path = directory / name
            if not path.is_file() or path.is_symlink():
                raise ServingValidationError(f"Release requires a regular file: {name}")
        manifest = ReleaseManifest.model_validate_json(
            checked_bytes(directory / "manifest.json", request.manifest_sha256)
        )
        if (
            manifest.feature_columns != FEATURE_COLUMNS
            or manifest.model_interface_version != INTERFACE_VERSION
        ):
            raise ServingValidationError("Release feature/interface contract differs")
        if sha256(directory / manifest.model_file) != manifest.model_sha256:
            raise ServingValidationError("Release model bytes changed")
        smoke = SmokeCase.model_validate_json(
            checked_bytes(directory / manifest.smoke_file, manifest.smoke_sha256)
        )
        return InspectedServingRelease(request, manifest, smoke, implementation_sha256())
    except (ValueError, OSError) as exc:
        raise ServingValidationError(f"Cannot inspect serving release: {exc}") from exc


def validate_score(release: InspectedServingRelease, response: dict) -> ScoreResponse:
    try:
        score = ScoreResponse.model_validate(response)
    except ValidationError as exc:
        raise ServingValidationError("Service response violates the score contract") from exc
    manifest = release.manifest
    if (
        score.transaction_id != release.smoke.request.transaction_id
        or score.serving_release_id != manifest.serving_release_id
        or score.model_sha256 != manifest.model_sha256
        or score.model_id != manifest.model_id
        or abs(score.score - release.smoke.expected_score) > PARITY_TOLERANCE
    ):
        raise ServingValidationError("Service score or release identity differs from control")
    return score


def existing_receipt(release: InspectedServingRelease) -> ServingValidationReceipt | None:
    path = release.receipt_path
    if path.is_symlink():
        raise ServingValidationError("Receipt must not be a symbolic link")
    if not path.exists():
        return None
    try:
        receipt = ServingValidationReceipt.model_validate_json(path.read_bytes())
    except (ValueError, OSError) as exc:
        raise ServingValidationError(
            "Existing receipt is invalid; preserve it for review"
        ) from exc
    if (
        receipt.operation_id != release.request.operation_id
        or receipt.manifest_sha256 != release.request.manifest_sha256
        or receipt.smoke_sha256 != release.manifest.smoke_sha256
        or receipt.implementation_sha256 != release.implementation_sha256
        or receipt.service_url != release.request.service_url
        or receipt.serving_release_id != release.manifest.serving_release_id
        or receipt.evidence_kind != release.manifest.evidence_kind
    ):
        raise ServingValidationError("Operation ID already identifies another validation")
    validate_score(release, receipt.score.model_dump())
    return receipt


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_json(base_url: str, route: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload, allow_nan=False).encode()
    request = Request(base_url + route, data=data, headers={"Content-Type": "application/json"})
    opener = build_opener(ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        # A model contract failure is permanent even though the API reports HTTP 503.
        try:
            body = exc.read(MAX_RESPONSE_BYTES + 1)
        except (TimeoutError, HTTPException, ConnectionError) as transport:
            raise TransientServiceError("Serving error response was interrupted") from transport
        finally:
            exc.close()
        try:
            detail = json.loads(body).get("detail")
        except ValueError, AttributeError:
            detail = None
        if exc.code in TRANSIENT_HTTP_STATUSES and detail != "model_contract_failure":
            raise TransientServiceError(f"Temporary serving HTTP {exc.code}") from exc
        raise ServingValidationError(f"Serving request rejected: HTTP {exc.code}") from exc
    except (URLError, TimeoutError, HTTPException, ConnectionError) as exc:
        raise TransientServiceError("Serving service is temporarily unavailable") from exc
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ServingValidationError("Serving response exceeds the bounded JSON size")
    try:
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise TypeError("Expected a JSON object")
        return result
    except (ValueError, TypeError) as exc:
        raise ServingValidationError("Serving response is not a valid JSON object") from exc


def probe_service(release: InspectedServingRelease) -> ScoreResponse:
    base = release.request.service_url
    if request_json(base, "/health") != {"status": "alive"}:
        raise ServingValidationError("Unexpected service health contract")
    ready = request_json(base, "/ready")
    if ready != {"status": "ready", "serving_release_id": release.manifest.serving_release_id}:
        raise ServingValidationError("Service readiness identifies another release")
    expected_info = release.manifest.model_dump(
        include={
            "serving_release_id",
            "model_id",
            "model_sha256",
            "feature_contract_version",
            "model_interface_version",
            "score_semantics",
            "usage_scope",
            "evidence_kind",
            "runtime_environment",
            "feature_columns",
        }
    )
    if request_json(base, "/info") != expected_info:
        raise ServingValidationError("Service information differs from the pinned release")
    return validate_score(
        release,
        request_json(base, "/score", release.smoke.request.model_dump(mode="json")),
    )


def result_summary(release: InspectedServingRelease, *, reused: bool) -> dict:
    return {
        "version": "serving_validation_v1",
        "status": "success",
        "operation_id": release.request.operation_id,
        "reused": reused,
        "receipt_path": str(release.receipt_path.resolve()),
        "serving_release_id": release.manifest.serving_release_id,
        "refit": False,
        "production_promotion": False,
    }


def publish_receipt(release: InspectedServingRelease, score: ScoreResponse) -> dict:
    # Refuse changes to release bytes between inspection and publication.
    current = inspect_release(release.request)
    validate_score(current, score.model_dump())
    prior = existing_receipt(current)
    if prior is not None:
        return result_summary(current, reused=True)
    receipt = ServingValidationReceipt(
        schema_version=1,
        version="serving_validation_v1",
        operation_id=current.request.operation_id,
        manifest_sha256=current.request.manifest_sha256,
        smoke_sha256=current.manifest.smoke_sha256,
        implementation_sha256=current.implementation_sha256,
        service_url=current.request.service_url,
        serving_release_id=current.manifest.serving_release_id,
        evidence_kind=current.manifest.evidence_kind,
        checked_at_utc=datetime.now(UTC),
        score=score,
        parity_tolerance=PARITY_TOLERANCE,
        status="success",
        refit=False,
        production_promotion=False,
    )
    try:
        write_json(current.receipt_path, receipt.model_dump(mode="json"), overwrite=False)
    except FileExistsError:
        # Another concurrent publisher won; accept only a complete compatible receipt.
        if existing_receipt(current) is None:
            raise ServingValidationError("Concurrent receipt disappeared before verification")
        return result_summary(current, reused=True)
    except OSError as exc:
        raise ServingValidationError(f"Cannot publish validation receipt: {exc}") from exc
    return result_summary(current, reused=False)


def validate_serving(request: ServingValidationRequest) -> dict:
    release = inspect_release(request)
    if existing_receipt(release) is not None:
        return result_summary(release, reused=True)
    return publish_receipt(release, probe_service(release))


def argument_parser(description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--service-url", required=True)
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--output-root", type=Path, default=Path("data/orchestration/serving"))
    return parser


def request_from_arguments(args) -> ServingValidationRequest:
    return ServingValidationRequest(
        operation_id=args.operation_id,
        release_path=args.release.resolve(),
        manifest_sha256=args.manifest_sha256,
        service_url=args.service_url,
        output_root=args.output_root.resolve(),
    )


def main():
    args = argument_parser(__doc__).parse_args()
    try:
        print(json.dumps(validate_serving(request_from_arguments(args)), indent=2))
    except (ServingValidationError, TransientServiceError, ValidationError) as exc:
        raise SystemExit(f"Serving validation failed: {exc}") from exc


if __name__ == "__main__":
    main()
