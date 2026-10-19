"""Reload controls plus explicit native check; the default suite uses generated artifacts."""

import json
import math
import os
from pathlib import Path
import shutil
import tempfile

from examples.serving_smoke import synthetic_release
from fastapi.testclient import TestClient
import pytest
from scripts.storage import archive
import skops.io as sio

from fraud_detection_mlops.serving.http_service import create_app
from fraud_detection_mlops.serving.model_release import ServingReleaseError

RELEASE_PATH = Path("data/serving/reference-v1")
REFERENCE_PATH = Path("references/evidence/laboratory_serving_native_validation_2026-10-09.json")


def assess_restored_release(root, identity, reference_path):
    """Load only restored bytes, with a manifest pin obtained outside the restored bundle."""
    identity = archive.bundle_id(identity)
    work = archive.workspace(root)
    restored = archive.checked_path(work, Path("restored") / identity)
    document = json.loads(archive.checked_path(restored, Path("manifest.json")).read_bytes())
    archive.validate_manifest(document, identity)
    expected_paths = {"data/serving-export.json"} | {
        (RELEASE_PATH / name).as_posix()
        for name in ("manifest.json", "pipeline.skops", "smoke.json")
    }
    assert {row["path"] for row in document["files"]} == expected_paths
    archive.verify_files(restored, document)
    reference = json.loads(reference_path.read_bytes())
    expected_release = reference["release"]
    expected_score = reference["wheel_check"]["http_score"]
    release = archive.checked_path(restored, RELEASE_PATH)
    # create_app calls the existing pinned loader during lifespan startup.
    with TestClient(create_app(release, expected_release["manifest_sha256"])) as client:
        ready = client.get("/ready")
        assert ready.status_code == 200
        assert ready.json() == {
            "status": "ready",
            "serving_release_id": expected_release["serving_release_id"],
        }
        information = client.get("/info")
        assert information.status_code == 200
        for key in ("serving_release_id", "model_sha256", "evidence_kind"):
            assert information.json()[key] == expected_release[key]
        smoke = json.loads((release / "smoke.json").read_bytes())
        response = client.post("/score", json=smoke["request"])
        assert response.status_code == 200
        score = response.json()
        for key, value in expected_score.items():
            if key != "score":
                assert score[key] == value
        assert math.isclose(score["score"], expected_score["score"], rel_tol=0, abs_tol=1e-12)
        assert math.isclose(score["score"], smoke["expected_score"], rel_tol=0, abs_tol=1e-12)
        invalid = client.post("/score", json={**smoke["request"], "transaction_id": True})
        assert invalid.status_code == 422
    report = {
        "status": "success",
        "bundle_id": identity,
        "manifest_sha256": expected_release["manifest_sha256"],
        "serving_release_id": score["serving_release_id"],
        "model_sha256": score["model_sha256"],
        "score": score["score"],
        "expected_score": expected_score["score"],
        "absolute_score_error": abs(score["score"] - expected_score["score"]),
        "parity_tolerance": 1e-12,
        "readiness_http_status": 200,
        "invalid_input_http_status": 422,
        "verified_archive_files": len(document["files"]),
        "reference_path": str(reference_path),
        "restored_release_path": str(release),
        "scope": "restored_release_in_process_fastapi_testclient_no_tcp_or_container_deployment",
        "original_release_read": False,
        "s3_accessed_by_this_check": False,
        "refit": False,
        "production_promotion": False,
    }
    receipts = archive.checked_path(work, Path("receipts"))
    receipts.mkdir(exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix="release-reload-", suffix=".json", dir=receipts, delete=False
    ) as f:
        f.write(archive.encoded(report))
        report["report"] = f.name
    return report


@pytest.fixture
def restored_fixture(tmp_path):
    release = tmp_path / RELEASE_PATH
    result = synthetic_release(release)
    (tmp_path / "data/serving-export.json").write_text(json.dumps(result))
    smoke = json.loads((release / "smoke.json").read_bytes())
    manifest = json.loads((release / "manifest.json").read_bytes())
    reference = tmp_path / "reference.json"
    expected = {
        "schema_version": 1,
        "transaction_id": smoke["request"]["transaction_id"],
        "score": smoke["expected_score"],
        "score_semantics": manifest["score_semantics"],
        "model_id": manifest["model_id"],
        "model_sha256": manifest["model_sha256"],
        "feature_contract_version": manifest["feature_contract_version"],
        "serving_release_id": manifest["serving_release_id"],
    }
    reference.write_text(
        json.dumps(
            {
                "release": {**result, "evidence_kind": manifest["evidence_kind"]},
                "wheel_check": {"http_score": expected},
            }
        )
    )
    identity = archive.prepare(tmp_path, [RELEASE_PATH.as_posix(), "data/serving-export.json"])
    bundle = tmp_path / "data/storage/bundles" / identity
    restored = tmp_path / "data/storage/restored" / identity
    shutil.copytree(bundle, restored)
    # Prove the check does not depend on the original release or the staging bundle.
    shutil.rmtree(release.parent)
    (tmp_path / "data/serving-export.json").unlink()
    shutil.rmtree(bundle)
    return tmp_path, identity, reference, restored


def test_generated_restored_release_is_loadable_without_originals(restored_fixture):
    root, identity, reference, _ = restored_fixture
    report = assess_restored_release(root, identity, reference)
    assert report["status"] == "success" and report["absolute_score_error"] <= 1e-12
    assert report["original_release_read"] is False
    assert json.loads(Path(report["report"]).read_bytes())["status"] == "success"


@pytest.mark.parametrize(
    "mutation",
    ["pipeline", "archive_manifest", "external_pin", "historical_score", "release_identity"],
)
def test_corrupt_or_different_restored_release_is_rejected(
    restored_fixture, monkeypatch, mutation
):
    root, identity, reference, restored = restored_fixture
    if mutation == "pipeline":
        (restored / RELEASE_PATH / "pipeline.skops").write_bytes(b"corrupt")
    elif mutation == "archive_manifest":
        document = json.loads((restored / "manifest.json").read_bytes())
        document["files"][0]["sha256"] = "0" * 64
        (restored / "manifest.json").write_bytes(archive.encoded(document))
    else:
        recorded = json.loads(reference.read_bytes())
        if mutation == "external_pin":
            recorded["release"]["manifest_sha256"] = "0" * 64
        elif mutation == "historical_score":
            recorded["wheel_check"]["http_score"]["score"] += 0.1
        else:
            recorded["release"]["serving_release_id"] = "0" * 32
        reference.write_text(json.dumps(recorded))
    if mutation in {"pipeline", "archive_manifest", "external_pin"}:
        monkeypatch.setattr(
            sio, "loads", lambda *a, **k: pytest.fail("Unverified bytes were loaded")
        )
    with pytest.raises((AssertionError, ValueError, ServingReleaseError)):
        assess_restored_release(root, identity, reference)
    assert not list((root / "data/storage").glob("receipts/release-reload-*.json"))


@pytest.mark.skipif(
    os.environ.get("FRAUD_STORAGE_RELEASE_LIVE") != "1",
    reason="Explicit native restored-release check; enable FRAUD_STORAGE_RELEASE_LIVE=1.",
)
def test_native_restored_release():
    root = Path(
        os.environ.get("FRAUD_PROJECT_ROOT", Path(__file__).resolve().parents[2])
    ).resolve()
    identity = os.environ.get("FRAUD_STORAGE_RELEASE_BUNDLE", "")
    report = assess_restored_release(root, identity, root / REFERENCE_PATH)
    print(json.dumps(report, indent=2, allow_nan=False))
