"""Prefect adapter for local release validation and immutable operational receipts."""

import json
import os
from urllib.parse import urlsplit

from prefect import flow, task
from prefect.cache_policies import NO_CACHE
from pydantic import ValidationError

from fraud_detection_mlops.operations import serving_validation as operations
from fraud_detection_mlops.operations.contracts.serving_validation_contract import (
    ServingValidationError,
    ServingValidationRequest,
    TransientServiceError,
)


def retry_transient_service(task, task_run, state) -> bool:
    try:
        state.result()
    except TransientServiceError:
        return True
    except Exception:  # noqa: BLE001 - unexpected task errors must never receive a retry
        return False
    return False


@task(name="inspect-serving-release", retries=0, persist_result=False, cache_policy=NO_CACHE)
def inspect_validation(request: ServingValidationRequest):
    release = operations.inspect_release(request)
    return release, operations.existing_receipt(release)


@task(
    name="probe-serving-control",
    retries=2,
    retry_delay_seconds=[1, 2],
    retry_condition_fn=retry_transient_service,
    persist_result=False,
    cache_policy=NO_CACHE,
)
def probe_validation(release):
    return operations.probe_service(release)


@task(name="publish-serving-receipt", retries=0, persist_result=False, cache_policy=NO_CACHE)
def publish_validation(release, score):
    return operations.publish_receipt(release, score)


@flow(
    name="serving-reference-validation",
    version="serving_validation_v1",
    retries=0,
    persist_result=False,
)
def serving_validation_flow(request: ServingValidationRequest) -> dict:
    release, prior = inspect_validation(request)
    if prior is not None:
        return operations.result_summary(release, reused=True)
    score = probe_validation(release)
    return publish_validation(release, score)


def require_local_prefect_api():
    # CLI runs use an explicit server rather than silently starting an ephemeral API.
    value = os.environ.get("PREFECT_API_URL", "")
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError as exc:
        raise ServingValidationError("PREFECT_API_URL contains an invalid URL/port") from exc
    if (
        parts.scheme != "http"
        or parts.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parts.path.rstrip("/") != "/api"
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
        or port is None
        or not 1 <= port <= 65535
    ):
        raise ServingValidationError(
            "Set PREFECT_API_URL to your explicit local server, e.g. http://127.0.0.1:4200/api"
        )


def main():
    args = operations.argument_parser(__doc__).parse_args()
    try:
        require_local_prefect_api()
        result = serving_validation_flow(operations.request_from_arguments(args))
        print(json.dumps(result, indent=2))
    except (ServingValidationError, TransientServiceError, ValidationError) as exc:
        raise SystemExit(f"Prefect serving validation failed: {exc}") from exc


if __name__ == "__main__":
    main()
