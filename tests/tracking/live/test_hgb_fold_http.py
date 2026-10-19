"""One synthetic HGB fold through the native lab; no historical study or Optuna search."""

import json
import os
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from uuid import uuid4

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("FRAUD_TRACKING_HGB_LIVE") != "1",
    reason="Explicit tracking-hgb opt-in required",
)
ROOT = Path(__file__).resolve().parents[3]
URI = "http://127.0.0.1:15001"


def test_synthetic_hgb_fold_proxy_reload_and_no_duplicate_fit(prepared_data, monkeypatch):
    import mlflow
    from mlflow import MlflowClient
    import numpy as np
    import pandas as pd
    from scripts.tracking import local_stack as stack

    from fraud_detection_mlops.artifacts import sha256, write_json
    from fraud_detection_mlops.integrations import mlflow_tracking as tracking
    from fraud_detection_mlops.modeling.experiments import candidate_training
    from fraud_detection_mlops.modeling.experiments import hgb_optimization as hgb
    from fraud_detection_mlops.modeling.experiments import temporal_development_data as development

    with stack.exclusive(ROOT):
        workspace = stack.workspace(ROOT)
        identity = uuid4().hex
        output = stack.checked(workspace / ("hgb-fold-" + identity))
        receipt = workspace / "receipts" / ("hgb-fold-" + identity + ".json")
        result = {
            "schema": "fraud_hgb_fold_tracking_validation_v1",
            "status": "failed",
            "scope": "one_synthetic_temporal_HGB_fold_http_reload_and_receipt_reuse_not_full_Optuna_study",
            "tracking_uri": URI,
            "producer_code_sha256": sha256(Path(hgb.__file__)),
            "validation_code_sha256": sha256(Path(__file__)),
            "tracking_code_sha256": sha256(Path(tracking.__file__)),
            "output": str(output),
            "report": str(receipt),
            "historical_study_read": False,
            "historical_runs_migrated": False,
            "full_optuna_search_validated": False,
            "production_promotion": False,
            "direct_s3_model_readback_validated": False,
            "postgres_backup_restore_validated": False,
        }
        try:
            before = {name: stack.inspect_service(ROOT, name) for name in ["postgres", "mlflow"]}
            for name in list(os.environ):
                if name.startswith("AWS_"):
                    monkeypatch.delenv(name)
            monkeypatch.setenv("MLFLOW_DISABLE_TELEMETRY", "true")
            monkeypatch.setenv("MLFLOW_DISABLE_AGENT_HINT", "1")
            monkeypatch.setattr(hgb, "HTTP_EXPERIMENT", "fraud-hgb-fold-smoke-http-v1")
            source, policy_path = prepared_data
            data = output / "dataset"
            shutil.copytree(source, data)
            policy = development.load_policy(policy_path)
            manifest = json.loads((data / "manifest.json").read_text())
            directory = data / "study"
            write_json(directory / "protocol.json", policy)
            fit_identity = {
                "policy_sha256": sha256(policy_path),
                "data_sha256": sha256(data / "manifest.json"),
                "max_fit_attempts": 1,
                **hgb.hgb_tracking_target(None, URI),
            }
            fold = policy["folds"][0]
            args = (directory, "reference", fold, {}, data, manifest, fit_identity, None)
            fitted = hgb.fit_hgb_fold(*args, http_uri=URI)
            record = fitted["tracking"]
            client = MlflowClient(tracking_uri=URI)
            run = client.get_run(record["run_id"])
            assert run.info.status == "FINISHED" and record["model_reload_validated"]
            assert client.get_experiment(run.info.experiment_id).name == hgb.HTTP_EXPERIMENT
            assert run.data.tags["fold_id"] == fold["id"]
            assert run.data.tags["test_evaluated"] == "false"
            assert run.data.params["early_stopping"] == "False"
            assert run.data.metrics["average_precision"] == fitted["metrics"]["average_precision"]
            _, (features, _, _) = development.load_fold(data, manifest, fold)
            part = directory / fitted["path"]
            scores = pd.read_parquet(part / "validation_predictions.parquet").SCORE.to_numpy()
            hidden = part.with_name(part.name + "-hidden")
            previous = mlflow.get_tracking_uri()
            part.rename(hidden)
            try:
                with tracking.http_tracking(URI), TemporaryDirectory(dir=output) as temporary:
                    model, downloaded = tracking.download_pipeline(
                        record["model_uri"], Path(temporary)
                    )
                    metadata = mlflow.models.Model.load(downloaded / "MLmodel")
                    model_sha = sha256(downloaded / metadata.flavors["sklearn"]["pickled_model"])
                    probabilities = model.predict_proba(features)[:, 1]
                    np.testing.assert_allclose(probabilities, scores, rtol=1e-12, atol=1e-12)
            finally:
                hidden.rename(part)
            assert mlflow.get_tracking_uri() == previous
            monkeypatch.setattr(
                candidate_training,
                "fit_candidate",
                lambda *a, **k: pytest.fail("Unexpected refit"),
            )
            monkeypatch.setattr(
                tracking, "log_candidate", lambda *a, **k: pytest.fail("Unexpected republish")
            )
            assert hgb.fit_hgb_fold(*args, http_uri=URI) == fitted
            assert len(list((directory / "attempts").rglob("*.json"))) == 1
            assert not list(output.rglob("*.db"))
            after = {name: stack.inspect_service(ROOT, name) for name in ["postgres", "mlflow"]}
            assert before == after
            result.update(
                status="success",
                tracking=record,
                model_sha256=model_sha,
                training_rows=fitted["training_rows"],
                validation_rows=fitted["validation_rows"],
                independent_http_reload_validated=True,
                local_model_unavailable_during_reload=True,
                max_absolute_score_error=float(np.max(np.abs(probabilities - scores))),
                completed_fold_reused_without_fit_or_publication=True,
                fit_attempts=1,
                producer_aws_environment_removed=True,
                local_tracking_database_created=False,
                containers_unchanged=True,
                containers_before=before,
                containers_after=after,
            )
        except (Exception, KeyboardInterrupt) as exc:
            result["error_type"] = type(exc).__name__
            raise
        finally:
            stack.save_json(receipt, result)
            print("Relatório: " + str(receipt), flush=True)
        print(json.dumps(result, indent=2, allow_nan=False))
