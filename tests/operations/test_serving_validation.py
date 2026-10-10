from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import shutil
import socket
import threading
import time
from urllib.error import URLError

from pydantic import ValidationError
import pytest
from tests.serving_validation_fixtures import local_service, prepared_request
import uvicorn

from fraud_detection_mlops.artifacts import sha256
from fraud_detection_mlops.operations import serving_validation as operations
from fraud_detection_mlops.operations.contracts.serving_validation_contract import (
    ServingValidationError,
    ServingValidationReceipt,
    TransientServiceError,
)
from fraud_detection_mlops.serving.http_service import create_app


@pytest.fixture(scope="module")
def generated_request(tmp_path_factory):
    return prepared_request(tmp_path_factory.mktemp("operations-control"))


@pytest.fixture
def validation_request(generated_request, tmp_path):
    shutil.copytree(generated_request.release_path, tmp_path / "release")
    return generated_request.model_copy(
        update={"release_path": tmp_path / "release", "output_root": tmp_path / "receipts"}
    )


def test_control_uses_http_and_reuses_complete_receipt_without_reprobing(validation_request):
    with local_service(validation_request) as service:
        identified = service["request"]
        files = {p.name: sha256(p) for p in identified.release_path.iterdir()}
        first = operations.validate_serving(identified)
        assert first["reused"] is False
        assert service["calls"] == {"/health": 1, "/ready": 1, "/info": 1, "/score": 1}
        assert service["payload"] == operations.inspect_release(
            identified
        ).smoke.request.model_dump(mode="json")
        receipt = operations.existing_receipt(operations.inspect_release(identified))
        assert receipt.evidence_kind == "synthetic_smoke"
        assert receipt.refit is receipt.production_promotion is False
        assert receipt.checked_at_utc.tzinfo is not None
        assert operations.validate_serving(identified)["reused"] is True
        assert service["calls"]["/score"] == 1
        fresh = identified.model_copy(update={"operation_id": "synthetic-control-002"})
        assert operations.validate_serving(fresh)["reused"] is False
        assert service["calls"]["/score"] == 2
        assert files == {p.name: sha256(p) for p in identified.release_path.iterdir()}


def test_probe_against_actual_fastapi_runtime(validation_request):
    # The other HTTP tests inject failures; this one exercises the real app/model.
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        identified = validation_request.model_copy(
            update={"service_url": f"http://127.0.0.1:{listener.getsockname()[1]}"}
        )
        app = create_app(identified.release_path, identified.manifest_sha256)
        server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 20
            while not server.started and time.monotonic() < deadline and thread.is_alive():
                time.sleep(0.05)
            assert server.started
            assert operations.validate_serving(identified)["reused"] is False
            assert operations.existing_receipt(operations.inspect_release(identified)) is not None
        finally:
            server.should_exit = True
            thread.join(timeout=10)
        assert not thread.is_alive()


@pytest.mark.parametrize("filename", ["manifest.json", "pipeline.skops", "smoke.json"])
def test_changed_release_rejected_before_any_http(validation_request, filename):
    with local_service(validation_request) as service:
        path = validation_request.release_path / filename
        path.write_bytes(path.read_bytes() + b" changed")
        with pytest.raises(ServingValidationError):
            operations.validate_serving(service["request"])
        assert not service["calls"]
        assert not validation_request.output_root.exists()


@pytest.mark.parametrize("filename", ["manifest.json", "pipeline.skops", "smoke.json"])
def test_missing_or_symlink_release_file_rejected(validation_request, filename):
    path = validation_request.release_path / filename
    target = path.with_suffix(".saved")
    path.rename(target)
    with pytest.raises(ServingValidationError, match="regular file"):
        operations.inspect_release(validation_request)
    path.symlink_to(target)
    with pytest.raises(ServingValidationError, match="regular file"):
        operations.inspect_release(validation_request)


@pytest.mark.parametrize(
    ("status", "body", "exception"),
    [
        (503, {"detail": "model_not_ready"}, TransientServiceError),
        (429, {"detail": "rate_limited"}, TransientServiceError),
        (503, {"detail": "model_contract_failure"}, ServingValidationError),
        (422, {"detail": "invalid"}, ServingValidationError),
        (302, {}, ServingValidationError),
        (200, b"not json", ServingValidationError),
        (200, [], ServingValidationError),
    ],
)
def test_http_failure_classification(validation_request, status, body, exception):
    with local_service(validation_request) as service:
        service["failures"].append((status, body))
        with pytest.raises(exception):
            operations.validate_serving(service["request"])
        assert service["calls"] == {"/health": 1}
        assert not validation_request.output_root.exists()


@pytest.mark.parametrize("route", ["/health", "/ready", "/info", "/score"])
def test_semantic_failure_never_publishes_success(validation_request, route):
    with local_service(validation_request) as service:
        service["responses"][route] = {"status": "wrong_release"}
        with pytest.raises(ServingValidationError):
            operations.validate_serving(service["request"])
        assert not validation_request.output_root.exists()


def test_wrong_score_and_model_identity_rejected(validation_request):
    with local_service(validation_request) as service:
        score = service["responses"]["/score"]
        score["score"] += 0.001
        with pytest.raises(ServingValidationError, match="differs from control"):
            operations.validate_serving(service["request"])
        score["score"] = operations.inspect_release(validation_request).smoke.expected_score
        score["model_sha256"] = "0" * 64
        with pytest.raises(ServingValidationError, match="identity differs"):
            operations.validate_serving(service["request"])


def test_transport_timeout_is_transient(validation_request, monkeypatch):
    class Opener:
        def open(self, *args, **kwargs):
            assert kwargs["timeout"] == 5
            raise URLError(TimeoutError("timed out"))

    monkeypatch.setattr(operations, "build_opener", lambda *args: Opener())
    with pytest.raises(TransientServiceError):
        operations.validate_serving(validation_request)


def test_response_size_is_bounded(validation_request, monkeypatch):
    monkeypatch.setattr(operations, "MAX_RESPONSE_BYTES", 10)
    with (
        local_service(validation_request) as service,
        pytest.raises(ServingValidationError, match="bounded JSON size"),
    ):
        operations.validate_serving(service["request"])


def test_conflicting_or_tampered_receipt_preserved(validation_request):
    with local_service(validation_request) as service:
        identified = service["request"]
        operations.validate_serving(identified)
        release = operations.inspect_release(identified)
        before = release.receipt_path.read_bytes()
        conflict = identified.model_copy(update={"service_url": "http://127.0.0.1:1"})
        with pytest.raises(ServingValidationError, match="another validation"):
            operations.validate_serving(conflict)
        assert release.receipt_path.read_bytes() == before
        receipt = json.loads(before)
        receipt["score"]["score"] += 0.001
        release.receipt_path.write_text(json.dumps(receipt))
        with pytest.raises(ServingValidationError, match="differs from control"):
            operations.validate_serving(identified)
        release.receipt_path.write_bytes(b"interrupted external write")
        with pytest.raises(ServingValidationError, match="preserve it"):
            operations.validate_serving(identified)
        assert service["calls"]["/score"] == 1


def test_receipt_symlink_rejected(validation_request):
    release = operations.inspect_release(validation_request)
    release.receipt_path.parent.mkdir(parents=True)
    release.receipt_path.symlink_to(validation_request.release_path / "smoke.json")
    with pytest.raises(ServingValidationError, match="symbolic link"):
        operations.validate_serving(validation_request)


def test_changed_operation_implementation_does_not_reuse_receipt(validation_request, monkeypatch):
    with local_service(validation_request) as service:
        operations.validate_serving(service["request"])
        release = operations.inspect_release(service["request"])
        before = release.receipt_path.read_bytes()
        monkeypatch.setattr(operations, "implementation_sha256", lambda: "0" * 64)
        with pytest.raises(ServingValidationError, match="another validation"):
            operations.validate_serving(service["request"])
        assert release.receipt_path.read_bytes() == before
        assert service["calls"]["/score"] == 1


def test_release_change_between_probe_and_publish_rejected(validation_request):
    with local_service(validation_request) as service:
        release = operations.inspect_release(service["request"])
        score = operations.probe_service(release)
        (validation_request.release_path / "pipeline.skops").write_bytes(b"modified")
        with pytest.raises(ServingValidationError, match="model bytes changed"):
            operations.publish_receipt(release, score)
        assert not release.receipt_path.exists()


def test_interruption_after_atomic_publish_can_resume_without_http(
    validation_request, monkeypatch
):
    writer = operations.write_json

    def publish_then_stop(*args, **kwargs):
        writer(*args, **kwargs)
        raise RuntimeError("interrupted after publishing")

    with local_service(validation_request) as service:
        with monkeypatch.context() as patch:
            patch.setattr(operations, "write_json", publish_then_stop)
            with pytest.raises(RuntimeError, match="interrupted"):
                operations.validate_serving(service["request"])
        assert operations.validate_serving(service["request"])["reused"] is True
        assert service["calls"]["/score"] == 1


def test_interrupted_publication_before_link_can_retry(validation_request, monkeypatch):
    with local_service(validation_request) as service:

        def disk_full(*args, **kwargs):
            raise OSError("disk full")

        with monkeypatch.context() as patch:
            patch.setattr(operations, "write_json", disk_full)
            with pytest.raises(ServingValidationError, match="disk full"):
                operations.validate_serving(service["request"])
        assert operations.validate_serving(service["request"])["reused"] is False
        assert service["calls"]["/score"] == 2


def test_competing_publications_produce_one_complete_receipt(validation_request, monkeypatch):
    with local_service(validation_request) as service:
        release = operations.inspect_release(service["request"])
        score = operations.probe_service(release)
        writer = operations.write_json
        barrier = threading.Barrier(4)

        def concurrent_writer(*args, **kwargs):
            barrier.wait(timeout=10)
            writer(*args, **kwargs)

        monkeypatch.setattr(operations, "write_json", concurrent_writer)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(
                pool.map(lambda _: operations.publish_receipt(release, score), range(4))
            )
        assert sum(not result["reused"] for result in results) == 1
        assert operations.existing_receipt(release) is not None
        assert list(validation_request.output_root.rglob("*.tmp")) == []
        assert len(list(validation_request.output_root.rglob("receipt.json"))) == 1


@pytest.mark.parametrize("operation_id", ["../outside", "a/b", "", "A", "a" * 81])
def test_operation_id_cannot_escape_output_root(validation_request, operation_id):
    with pytest.raises(ValidationError):
        type(validation_request).model_validate(
            {**validation_request.model_dump(), "operation_id": operation_id}
        )


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com:8000",
        "http://127.0.0.1",
        "http://localhost:0",
        "http://localhost:bad",
        "http://u:p@localhost:8000",
        "http://localhost:8000/score",
    ],
)
def test_service_url_is_explicit_and_loopback_only(validation_request, url):
    with pytest.raises(ValidationError):
        type(validation_request).model_validate(
            {**validation_request.model_dump(), "service_url": url}
        )


def test_receipt_rejects_naive_timestamp(validation_request):
    with local_service(validation_request) as service:
        operations.validate_serving(service["request"])
        receipt = operations.existing_receipt(operations.inspect_release(service["request"]))
        with pytest.raises(ValidationError, match="must use UTC"):
            ServingValidationReceipt.model_validate(
                {**receipt.model_dump(), "checked_at_utc": datetime(2026, 10, 10)}  # noqa: DTZ001 - invalid fixture
            )
