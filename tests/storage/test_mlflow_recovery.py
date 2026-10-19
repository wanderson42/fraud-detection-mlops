"""Real SDK/model recovery with removed original stores and isolated audit guards."""

import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

from examples.serving_smoke import synthetic_release
import mlflow
import mlflow.sklearn
import pandas as pd
import pytest
from scripts.storage import archive
from scripts.storage import mlflow_backup as backup
from scripts.storage import mlflow_recovery as recovery
from scripts.storage.mlflow_recovery_worker import signature_frame
from tests.storage import test_mlflow_backup as fixtures

from fraud_detection_mlops.integrations import mlflow_tracking as tracking
from fraud_detection_mlops.integrations.skops_persistence import TRUSTED_TYPES, load_pipeline
from fraud_detection_mlops.serving.model_release import SmokeCase, write_release

tracking_store = fixtures.tracking_store
two_stores = fixtures.two_stores


@pytest.fixture
def recovered(two_stores, tmp_path, request):
    root, *_ = two_stores
    generated = tmp_path / "generated"
    synthetic_release(generated)
    smoke = SmokeCase.model_validate_json((generated / "smoke.json").read_bytes())
    model = load_pipeline(generated / "pipeline.skops")
    mode = getattr(request, "param", "runs")
    signature_input = smoke.request.feature_frame()
    if mode.endswith("_double"):
        signature_input = signature_input.astype("float64")
    with tracking.local_tracking(root / "data/tracking"):
        client = mlflow.MlflowClient()
        experiment = client.get_experiment_by_name("inventory-fixture")
        with mlflow.start_run(experiment_id=experiment.experiment_id) as run:
            mlflow.log_param("check", "preserved")
            mlflow.log_metric("history", 0.25, step=1)
            mlflow.log_metric("history", 0.75, step=2)
            logged = mlflow.sklearn.log_model(
                model,
                name="model",
                serialization_format="skops",
                skops_trusted_types=sorted(TRUSTED_TYPES),
                pyfunc_predict_fn="predict_proba",
                signature=mlflow.models.infer_signature(
                    signature_input,
                    model.predict_proba(signature_input),
                ),
                pip_requirements=[],
            )
            native = Path(
                client.get_logged_model(logged.model_id).artifact_location.removeprefix("file://")
            )
            metadata = mlflow.models.Model.load(native / "MLmodel")
            raw = (native / metadata.flavors["sklearn"]["pickled_model"]).read_bytes()
            shutil.copytree(native, Path(run.info.artifact_uri.removeprefix("file://")) / "model")
            uri = (
                logged.model_uri
                if mode.startswith("models")
                else "runs:/" + run.info.run_id + "/model"
            )
    release = root / recovery.RELEASE_PATH
    result = write_release(
        release,
        raw,
        smoke,
        model_id="hist_gradient_boosting",
        source={"model_uri": uri},
        parity_rows=1,
        evidence_kind="synthetic_smoke",
    )
    (root / "data/serving-export.json").write_text(json.dumps(result))
    reference = root / "references/evidence/control.json"
    reference.parent.mkdir(parents=True)
    reference.write_text(
        json.dumps(
            {
                "release": result,
                "wheel_check": {
                    "http_score": {
                        "model_sha256": result["model_sha256"],
                        "serving_release_id": result["serving_release_id"],
                        "transaction_id": smoke.request.transaction_id,
                        "score": smoke.expected_score,
                    }
                },
            }
        )
    )
    snapshot = backup.prepare(root, writers_stopped=True)
    release_id = archive.prepare(
        root, [recovery.RELEASE_PATH.as_posix(), "data/serving-export.json"]
    )
    work = archive.workspace(root)
    for identity in (snapshot["bundle_id"], release_id):
        target = work / "restored" / identity
        target.parent.mkdir(exist_ok=True)
        shutil.copytree(work / "bundles" / identity, target)
    shutil.rmtree(root / "data/tracking")
    shutil.rmtree(root / "data/serving")
    shutil.rmtree(work / "bundles")
    return root, snapshot["bundle_id"], release_id, reference.relative_to(root)


@pytest.mark.parametrize(
    "recovered", ["runs", "models", "runs_double", "models_double"], indirect=True
)
def test_model_and_full_history_recover_without_originals_and_preserve_restores(
    recovered, request
):
    root, identity, release_id, reference = recovered
    report = recovery.rehearse(root, identity, release_id, reference)
    assert report["status"] == "success"
    assert report["total_runs"] == 4 and report["total_logged_models"] == 3
    assert report["metric_history_rows"] == 5
    assert report["full_API_history_verified"] and report["model_reload_validated"]
    assert report["absolute_score_error"] <= 1e-12
    # Historical native signatures are all-double; keep mixed signatures supported.
    if request.node.callspec.params["recovered"].endswith("_double"):
        assert {row["column"] for row in report["signature_input_adaptations"]} >= {
            "CUSTOMER_TX_COUNT_1D",
            "TERMINAL_TX_COUNT_7D",
            "TX_HOUR",
        }
        assert all(row["to"] == "float64" for row in report["signature_input_adaptations"])
    else:
        assert report["signature_input_adaptations"] == []
    assert report["data_access_violations"] == report["network_connections_allowed"] == 0
    assert report["restored_snapshot_unchanged"] and report["restored_release_unchanged"]
    assert report["disposable_copy_removed"] and not report["original_source_read"]
    assert not report["migration_implemented"] and not report["s3_accessed"]
    assert all("/rehearsals/" in row["new"] for row in report["uri_map"])
    assert Path(report["report"]).stat().st_mode & 0o777 == 0o600
    assert not list((root / "data/storage/rehearsals").glob("mlflow-recovery-*"))


@pytest.mark.parametrize("problem", ["snapshot", "external_pin", "historical_score"])
def test_invalid_inputs_rejected_before_worker(recovered, monkeypatch, problem):
    root, identity, release_id, reference = recovered
    if problem == "snapshot":
        (root / "data/storage/restored" / identity / backup.AUXILIARY / "mlflow.db").write_bytes(
            b"corrupt"
        )
    else:
        path = root / reference
        data = json.loads(path.read_bytes())
        if problem == "external_pin":
            data["release"]["manifest_sha256"] = "0" * 64
        else:
            data["wheel_check"]["http_score"]["score"] += 0.1
        path.write_text(json.dumps(data))
    monkeypatch.setattr(
        recovery.subprocess, "run", lambda *a, **k: pytest.fail("Unverified data reached worker")
    )
    with pytest.raises(ValueError):
        recovery.rehearse(root, identity, release_id, reference)
    assert not list((root / "data/storage/receipts").glob("mlflow-recovery-*"))


def test_mapping_detects_side_effects_outside_reviewed_uri_cells(recovered, tmp_path):
    root, identity, *_ = recovered
    source, _document, _ = recovery.restored_snapshot(root, identity)
    candidate = tmp_path / "disposable"
    shutil.copytree(source, candidate)
    with sqlite3.connect(candidate / backup.DATABASE) as connection:
        connection.execute(
            "CREATE TRIGGER unexpected AFTER UPDATE ON runs BEGIN UPDATE params SET value='changed'; END"
        )
    inventory = json.loads((candidate / backup.INVENTORY).read_bytes())
    with pytest.raises(ValueError, match="outside declared URI"):
        recovery.map_locations(candidate, inventory)


@pytest.mark.parametrize("failure", ["exit", "timeout", "model_hash"])
def test_worker_failure_does_not_leave_success_receipt_or_disposable_copy(
    recovered, monkeypatch, failure
):
    root, identity, release_id, reference = recovered
    if failure == "model_hash":
        original = recovery.release_control
        monkeypatch.setattr(
            recovery, "release_control", lambda *a: original(*a) | {"model_sha256": "0" * 64}
        )
    else:

        def fail(*args, **kwargs):
            if failure == "timeout":
                raise subprocess.TimeoutExpired("rehearsal", 180)
            return subprocess.CompletedProcess(args, 1, "", "injected failure")

        monkeypatch.setattr(recovery.subprocess, "run", fail)
    with pytest.raises((ValueError, subprocess.TimeoutExpired)):
        recovery.rehearse(root, identity, release_id, reference)
    assert not list((root / "data/storage/receipts").glob("mlflow-recovery-*"))
    assert not list((root / "data/storage/rehearsals").glob("mlflow-recovery-*"))


@pytest.mark.parametrize("operation", ["open", "database", "write", "network", "process"])
def test_worker_guard_blocks_source_reads_and_unmanaged_io(tmp_path, operation):
    candidate = tmp_path / "data/storage/rehearsals/candidate"
    candidate.mkdir(parents=True)
    original = tmp_path / "data/tracking/mlflow.db"
    original.parent.mkdir(parents=True)
    original.write_bytes(b"never read")
    expressions = {
        "open": "original.read_bytes()",
        "database": "sqlite3.connect(original)",
        "write": "(root/'outside').write_text('bad')",
        "network": "socket.create_connection(('127.0.0.1',9))",
        "process": "subprocess.run(['true'])",
    }
    code = """import sys,json,sqlite3,socket,subprocess
from pathlib import Path
from scripts.storage.mlflow_recovery_worker import install_guard
root=Path(sys.argv[1]); candidate=Path(sys.argv[2]);original=root/'data/tracking/mlflow.db'
state=install_guard(candidate,[root],set())
try:
    EXPR
except PermissionError:
    print(json.dumps(state));raise SystemExit(0)
raise SystemExit('Guard failed')
""".replace("EXPR", expressions[operation])
    result = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path), str(candidate)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert any(json.loads(result.stdout).values())


@pytest.mark.parametrize("value", [2**53 + 1, 2**63 - 1])
def test_signature_adaptation_rejects_integer_rounding(value):
    frame = pd.DataFrame({"count": pd.Series([value], dtype="int64")})
    schema = mlflow.types.Schema([mlflow.types.ColSpec("double", "count")])
    with pytest.raises(ValueError, match="represented exactly"):
        signature_frame(frame, schema)
    assert int(frame.iloc[0, 0]) == value


@pytest.mark.parametrize("value", [0, 2**53, 2**53 + 2])
def test_signature_adaptation_preserves_exactly_representable_integer_values(value):
    frame = pd.DataFrame({"count": pd.Series([value], dtype="int64")})
    schema = mlflow.types.Schema([mlflow.types.ColSpec("double", "count")])
    adapted, changes = signature_frame(frame, schema)
    assert adapted["count"].dtype == "float64"
    assert int(adapted.iloc[0, 0]) == value
    assert frame["count"].dtype == "int64"
    assert changes == [{"column": "count", "from": "int64", "to": "float64"}]


def test_signature_adaptation_requires_the_same_input_columns():
    frame = pd.DataFrame({"unexpected": [1]})
    schema = mlflow.types.Schema([mlflow.types.ColSpec("double", "count")])
    with pytest.raises(ValueError, match="input columns"):
        signature_frame(frame, schema)
