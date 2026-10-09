"""Portable skops release, pinned manifest and startup score parity."""

from dataclasses import dataclass
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import platform
from threading import Lock
from typing import Annotated, Literal
from uuid import uuid4

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr
from sklearn.pipeline import Pipeline

from fraud_detection_mlops.artifacts import write_json
from fraud_detection_mlops.features.feature_schema import FEATURE_COLUMNS
from fraud_detection_mlops.integrations.skops_persistence import load_pipeline_bytes
from fraud_detection_mlops.modeling.contracts.model_interface import (
    VERSION as MODEL_INTERFACE_VERSION,
)
from fraud_detection_mlops.modeling.contracts.model_interface import predict_scores
from fraud_detection_mlops.serving.request_schema import ScoreRequest, ScoreResponse

RUNTIME_PACKAGES = ("numpy", "pandas", "scipy", "scikit-learn", "skops", "pyarrow")
Digest = Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]


class ServingReleaseError(ValueError):
    """A distribution is incomplete, unpinned or incompatible with the runtime."""


class ReleaseManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False, frozen=True)

    schema_version: Annotated[StrictInt, Field(ge=1, le=1)]
    version: Literal["serving_release_v1"]
    serving_release_id: Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{32}$")]
    usage_scope: Literal["laboratory_only"]
    evidence_kind: Literal["verified_reference", "synthetic_smoke"]
    model_id: Annotated[StrictStr, Field(pattern=r"^[a-z][a-z0-9_]*$")]
    model_file: Literal["pipeline.skops"]
    model_sha256: Digest
    smoke_file: Literal["smoke.json"]
    smoke_sha256: Digest
    feature_contract_version: Literal["gold_v1"]
    model_interface_version: Literal["model_interface_v1"]
    feature_columns: list[StrictStr]
    score_semantics: Literal["uncalibrated_ranking"]
    runtime_environment: dict[StrictStr, StrictStr]
    source: dict[StrictStr, StrictStr]
    parity_rows: Annotated[StrictInt, Field(ge=1)]


class SmokeCase(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    request: ScoreRequest
    expected_score: Annotated[float, Field(ge=0, le=1, strict=True)]


def runtime_environment() -> dict[str, str]:
    return {"python": platform.python_version(), **{p: version(p) for p in RUNTIME_PACKAGES}}


def checked_bytes(path: Path, expected: str) -> bytes:
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected:
        raise ServingReleaseError(f"Release bytes changed: {path.name}")
    return data


@dataclass
class ScoringRuntime:
    pipeline: Pipeline
    manifest: ReleaseManifest
    _lock: Lock

    def score(self, request: ScoreRequest) -> ScoreResponse:
        with self._lock:
            score = float(predict_scores(self.pipeline, request.feature_frame())[0])
        return ScoreResponse(
            schema_version=1,
            transaction_id=request.transaction_id,
            score=score,
            score_semantics="uncalibrated_ranking",
            model_id=self.manifest.model_id,
            model_sha256=self.manifest.model_sha256,
            feature_contract_version="gold_v1",
            serving_release_id=self.manifest.serving_release_id,
        )

    def information(self) -> dict:
        return self.manifest.model_dump(
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


def load_release(directory: Path, manifest_sha256: str) -> ScoringRuntime:
    """Require an externally pinned manifest, then validate bytes before loading."""
    if not isinstance(manifest_sha256, str) or len(manifest_sha256) != 64:
        raise ServingReleaseError("Pin the manifest SHA-256 printed by the exporter")
    try:
        manifest = ReleaseManifest.model_validate_json(
            checked_bytes(directory / "manifest.json", manifest_sha256)
        )
        if (
            manifest.feature_columns != FEATURE_COLUMNS
            or manifest.model_interface_version != MODEL_INTERFACE_VERSION
            or manifest.runtime_environment != runtime_environment()
        ):
            raise ServingReleaseError("Restore the release environment and feature contract")
        data = checked_bytes(directory / manifest.model_file, manifest.model_sha256)
        smoke = SmokeCase.model_validate_json(
            checked_bytes(directory / manifest.smoke_file, manifest.smoke_sha256)
        )
        pipeline = load_pipeline_bytes(data)
        runtime = ScoringRuntime(pipeline, manifest, Lock())
        if not np.isclose(
            runtime.score(smoke.request).score, smoke.expected_score, rtol=1e-12, atol=1e-12
        ):
            raise ServingReleaseError("Release startup scores differ from the recorded model")
        return runtime
    except (ValueError, OSError, KeyError) as exc:
        raise ServingReleaseError(f"Cannot load the identified release: {exc}") from exc


def write_release(
    directory: Path,
    model_bytes: bytes,
    smoke: SmokeCase,
    *,
    model_id: str,
    source: dict[str, str],
    parity_rows: int,
    evidence_kind: str,
) -> dict:
    """Write a new immutable distribution; approval belongs to the exporting workflow."""
    # The directory is reserved exclusively; manifest.json is published last.
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "pipeline.skops").write_bytes(model_bytes)
    write_json(directory / "smoke.json", json.loads(smoke.model_dump_json()), overwrite=False)
    manifest = ReleaseManifest(
        schema_version=1,
        version="serving_release_v1",
        serving_release_id=uuid4().hex,
        usage_scope="laboratory_only",
        evidence_kind=evidence_kind,
        model_id=model_id,
        model_file="pipeline.skops",
        model_sha256=hashlib.sha256(model_bytes).hexdigest(),
        smoke_file="smoke.json",
        smoke_sha256=hashlib.sha256((directory / "smoke.json").read_bytes()).hexdigest(),
        feature_contract_version="gold_v1",
        model_interface_version=MODEL_INTERFACE_VERSION,
        feature_columns=FEATURE_COLUMNS,
        score_semantics="uncalibrated_ranking",
        runtime_environment=runtime_environment(),
        source=source,
        parity_rows=parity_rows,
    )
    write_json(directory / "manifest.json", manifest.model_dump(), overwrite=False)
    digest = hashlib.sha256((directory / "manifest.json").read_bytes()).hexdigest()
    load_release(directory, digest)
    return {
        "release_path": str(directory.resolve()),
        "manifest_sha256": digest,
        "serving_release_id": manifest.serving_release_id,
        "model_sha256": manifest.model_sha256,
        "parity_rows": parity_rows,
        "evidence_kind": evidence_kind,
        "refit": False,
        "production_promotion": False,
        "status": "success",
    }
