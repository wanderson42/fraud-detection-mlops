import json
from pathlib import Path
import shutil
import tomllib

import pytest
from tests.serving_validation_fixtures import local_service, prepared_request

from fraud_detection_mlops.operations import serving_validation as operations
from fraud_detection_mlops.operations.contracts.serving_validation_contract import (
    ServingValidationError,
    TransientServiceError,
)
from fraud_detection_mlops.orchestration import serving_validation_flow as orchestration


@pytest.fixture(scope="module")
def prefect_backend():
    # A real isolated Prefect API/SQLite database; not calls through task.fn.
    # Defer server ORM imports until this fixture, away from MLflow test collection.
    from prefect.testing.utilities import prefect_test_harness

    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("PREFECT_SERVER_ANALYTICS_ENABLED", "false")
        patch.setenv("PREFECT_LOGGING_LEVEL", "ERROR")
        # This isolated loopback test must not depend on workstation proxy settings.
        for key in (
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "http_proxy",
            "https_proxy",
            "all_proxy",
        ):
            patch.delenv(key, raising=False)
        with prefect_test_harness():
            yield


@pytest.fixture(scope="module")
def generated_request(tmp_path_factory):
    return prepared_request(tmp_path_factory.mktemp("flow-control"))


@pytest.fixture
def validation_request(generated_request, tmp_path, monkeypatch):
    shutil.copytree(generated_request.release_path, tmp_path / "release")
    monkeypatch.setattr(
        orchestration,
        "probe_validation",
        orchestration.probe_validation.with_options(retry_delay_seconds=0),
    )
    return generated_request.model_copy(
        update={"release_path": tmp_path / "release", "output_root": tmp_path / "receipts"}
    )


def test_real_flow_retries_only_transient_probe_and_resumes(prefect_backend, validation_request):
    with local_service(validation_request) as service:
        service["failures"].append((503, {"detail": "model_not_ready"}))
        state = orchestration.serving_validation_flow(service["request"], return_state=True)
        assert state.is_completed()
        assert state.result()["reused"] is False
        assert service["calls"]["/health"] == 2
        assert service["calls"]["/score"] == 1
        assert orchestration.serving_validation_flow(service["request"])["reused"] is True
        assert service["calls"]["/health"] == 2


def test_real_flow_exhausts_three_attempts_without_receipt(prefect_backend, validation_request):
    with local_service(validation_request) as service:
        service["failures"].extend([(503, {})] * 4)
        state = orchestration.serving_validation_flow(service["request"], return_state=True)
        assert state.is_failed()
        with pytest.raises(TransientServiceError):
            state.result()
        assert service["calls"] == {"/health": 3}
        assert not validation_request.output_root.exists()


def test_real_flow_does_not_retry_contract_failure(prefect_backend, validation_request):
    with local_service(validation_request) as service:
        service["failures"].append((503, {"detail": "model_contract_failure"}))
        state = orchestration.serving_validation_flow(service["request"], return_state=True)
        assert state.is_failed()
        with pytest.raises(ServingValidationError):
            state.result()
        assert service["calls"] == {"/health": 1}
        assert not validation_request.output_root.exists()


def test_real_flow_does_not_retry_unexpected_code_error(
    prefect_backend, validation_request, monkeypatch
):
    attempts = []

    def broken_probe(release):
        attempts.append(release)
        raise RuntimeError("unexpected implementation failure")

    monkeypatch.setattr(operations, "probe_service", broken_probe)
    state = orchestration.serving_validation_flow(validation_request, return_state=True)
    assert state.is_failed()
    with pytest.raises(RuntimeError, match="implementation failure"):
        state.result()
    assert len(attempts) == 1
    assert not validation_request.output_root.exists()


def test_real_flow_refuses_modified_release_before_probe(prefect_backend, validation_request):
    with local_service(validation_request) as service:
        (validation_request.release_path / "pipeline.skops").write_bytes(b"changed")
        state = orchestration.serving_validation_flow(service["request"], return_state=True)
        assert state.is_failed()
        assert not service["calls"]
        assert not validation_request.output_root.exists()


def test_real_flow_resumes_after_publish_task_interruption(
    prefect_backend, validation_request, monkeypatch
):
    original = operations.publish_receipt

    def publish_then_fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("process interrupted after receipt publication")

    with local_service(validation_request) as service:
        with monkeypatch.context() as patch:
            patch.setattr(operations, "publish_receipt", publish_then_fail)
            state = orchestration.serving_validation_flow(service["request"], return_state=True)
            assert state.is_failed()
        result = orchestration.serving_validation_flow(service["request"])
        assert result["reused"] is True
        assert service["calls"]["/score"] == 1
        assert json.loads(Path(result["receipt_path"]).read_text())["status"] == "success"


@pytest.mark.parametrize(
    "url",
    [
        "",
        "https://api.prefect.cloud/api",
        "http://127.0.0.1:bad/api",
        "http://localhost:4200/wrong",
        "http://localhost:0/api",
    ],
)
def test_cli_requires_explicit_local_prefect_server(monkeypatch, url):
    monkeypatch.setenv("PREFECT_API_URL", url)
    with pytest.raises(ServingValidationError):
        orchestration.require_local_prefect_api()


def test_cli_accepts_explicit_local_server(monkeypatch):
    monkeypatch.setenv("PREFECT_API_URL", "http://127.0.0.1:4200/api")
    orchestration.require_local_prefect_api()


def test_prefect_is_outside_serving_dependency_group():
    root = Path(__file__).resolve().parents[2]
    project = tomllib.loads((root / "pyproject.toml").read_text())
    assert project["dependency-groups"]["orchestration"] == ["prefect (==3.8.8)"]
    assert not any("prefect" in dep for dep in project["project"]["dependencies"])
