"""Generated release and a real loopback HTTP transport for orchestration tests."""

from collections import Counter, deque
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading

from examples.serving_smoke import synthetic_release

from fraud_detection_mlops.operations.contracts.serving_validation_contract import (
    ServingValidationRequest,
)
from fraud_detection_mlops.operations.serving_validation import inspect_release


def prepared_request(root: Path) -> ServingValidationRequest:
    exported = synthetic_release(root / "release")
    return ServingValidationRequest(
        operation_id="synthetic-control-001",
        release_path=root / "release",
        manifest_sha256=exported["manifest_sha256"],
        service_url="http://127.0.0.1:8000",
        output_root=root / "receipts",
    )


@contextmanager
def local_service(request):
    release = inspect_release(request)
    info = release.manifest.model_dump(
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
    score = {
        "schema_version": 1,
        "transaction_id": release.smoke.request.transaction_id,
        "score": release.smoke.expected_score,
        "score_semantics": "uncalibrated_ranking",
        "model_id": release.manifest.model_id,
        "model_sha256": release.manifest.model_sha256,
        "feature_contract_version": "gold_v1",
        "serving_release_id": release.manifest.serving_release_id,
    }
    responses = {
        "/health": {"status": "alive"},
        "/ready": {"status": "ready", "serving_release_id": release.manifest.serving_release_id},
        "/info": info,
        "/score": score,
    }
    controller = {"calls": Counter(), "failures": deque(), "responses": responses}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            controller["calls"][self.path] += 1
            if controller["failures"]:
                status, value = controller["failures"].popleft()
            else:
                status, value = 200, responses[self.path]
            body = value if isinstance(value, bytes) else json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            if status == 302:
                self.send_header("Location", "/score")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            controller["payload"] = json.loads(body)
            self.do_GET()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    controller["request"] = request.model_copy(
        update={"service_url": f"http://127.0.0.1:{server.server_port}"}
    )
    try:
        yield controller
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
