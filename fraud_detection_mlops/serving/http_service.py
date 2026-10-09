"""HTTP adapter for one identified release and precomputed feature requests."""

from contextlib import asynccontextmanager
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from threadpoolctl import threadpool_limits

from fraud_detection_mlops.modeling.contracts.model_interface import ModelContractError
from fraud_detection_mlops.serving.model_release import ServingReleaseError, load_release
from fraud_detection_mlops.serving.request_schema import (
    CONTRACT_VERSION,
    ScoreRequest,
    ScoreResponse,
)


def create_app(release_path: Path | None = None, manifest_sha256: str | None = None) -> FastAPI:
    """Uvicorn factory; importing the module does not load data, models or tracking."""
    path = release_path or os.environ.get("FRAUD_SERVING_RELEASE")
    digest = manifest_sha256 or os.environ.get("FRAUD_SERVING_MANIFEST_SHA256")
    if not path or not digest:
        raise ServingReleaseError("Set FRAUD_SERVING_RELEASE and FRAUD_SERVING_MANIFEST_SHA256")
    directory = Path(path).expanduser().resolve()

    @asynccontextmanager
    async def lifespan(app):
        with threadpool_limits(limits=4):
            app.state.runtime = load_release(directory, digest)
            try:
                yield
            finally:
                app.state.runtime = None

    app = FastAPI(title="Fraud laboratory scoring", version=CONTRACT_VERSION, lifespan=lifespan)
    app.state.runtime = None

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        # Do not echo input values: NaN errors must also produce valid JSON.
        return JSONResponse(
            status_code=422,
            content={
                "error": "invalid_request",
                "details": [
                    {"field": ".".join(map(str, item["loc"])), "type": item["type"]}
                    for item in exc.errors()
                ],
            },
        )

    def require_runtime():
        if app.state.runtime is None:
            raise HTTPException(status_code=503, detail="model_not_ready")
        return app.state.runtime

    @app.get("/health")
    def health():
        return {"status": "alive"}

    @app.get("/ready")
    def ready():
        runtime = require_runtime()
        return {"status": "ready", "serving_release_id": runtime.manifest.serving_release_id}

    @app.get("/info")
    def information():
        return require_runtime().information()

    @app.post("/score", response_model=ScoreResponse)
    def score(payload: ScoreRequest):
        try:
            return require_runtime().score(payload)
        except ModelContractError as exc:
            raise HTTPException(status_code=503, detail="model_contract_failure") from exc

    return app
