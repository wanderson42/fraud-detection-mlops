"""Three synthetic baseline pipelines through native tracking; no historical rerun."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from uuid import uuid4

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("FRAUD_TRACKING_BASELINE_LIVE") != "1",
    reason="Explicit tracking-baseline opt-in required",
)
ROOT = Path(__file__).resolve().parents[3]
URI = "http://127.0.0.1:15001"
EXPERIMENT = "fraud-baseline-smoke-http-v1"


def test_three_synthetic_baseline_models_reload_without_local_outputs(baseline_source):
    import mlflow
    from mlflow import MlflowClient
    import numpy as np
    import pandas as pd
    from scripts.tracking import local_stack as stack

    from fraud_detection_mlops.artifacts import sha256
    from fraud_detection_mlops.integrations import mlflow_tracking as tracking
    from fraud_detection_mlops.modeling.experiments import baseline_experiment as baseline

    with stack.exclusive(ROOT):
        workspace = stack.workspace(ROOT)
        identity = uuid4().hex
        output = stack.checked(workspace / ("baseline-" + identity))
        receipt = workspace / "receipts" / ("baseline-" + identity + ".json")
        result = {
            "schema": "fraud_baseline_tracking_validation_v1",
            "status": "failed",
            "scope": "three_synthetic_baseline_models_HTTP_proxy_reload_not_historical_rerun_or_migration",
            "tracking_uri": URI,
            "experiment": EXPERIMENT,
            "producer_code_sha256": sha256(Path(baseline.__file__)),
            "validation_code_sha256": sha256(Path(__file__)),
            "tracking_code_sha256": sha256(Path(tracking.__file__)),
            "output": str(output),
            "report": str(receipt),
            "historical_baseline_read": False,
            "historical_runs_migrated": False,
            "production_promotion": False,
            "direct_s3_model_readback_validated": False,
            "postgres_backup_restore_validated": False,
        }
        try:
            before = {name: stack.inspect_service(ROOT, name) for name in ["postgres", "mlflow"]}
            source = output / "inputs"
            shutil.copytree(baseline_source / "gold", source / "gold")
            for name in ["inventory.json", "protocol.json"]:
                shutil.copy2(baseline_source / name, source / name)
            environment = {k: v for k, v in os.environ.items() if not k.startswith("AWS_")}
            environment.update(MLFLOW_DISABLE_TELEMETRY="true", MLFLOW_DISABLE_AGENT_HINT="1")
            # The public CLI is exercised; only the experiment name is scoped to this fixture.
            cli = (
                "from fraud_detection_mlops.modeling.experiments import baseline_experiment as runner; "
                f"runner.HTTP_EXPERIMENT={EXPERIMENT!r}; runner.app()"
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    cli,
                    "run",
                    "--gold-root",
                    str(source / "gold"),
                    "--inventory",
                    str(source / "inventory.json"),
                    "--protocol",
                    str(source / "protocol.json"),
                    "--output-root",
                    str(output / "experiments"),
                    "--tracking-uri",
                    URI,
                ],
                cwd=Path(baseline.__file__).resolve().parents[3],
                env=environment,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            assert completed.returncode == 0, completed.stderr[-3500:]
            producer = json.loads(completed.stdout)
            assert producer["status"] == "success" and not producer["test_evaluated"]
            assert set(producer["tracking"]) == set(baseline.CLASSIFIERS)
            directory = Path(producer["baseline_path"])
            manifest = json.loads((directory / "manifest.json").read_text())
            audit = json.loads(Path(producer["audit_path"]).read_text())
            assert manifest["tracking"] == audit["tracking"] == producer["tracking"]
            assert manifest["tracking_target"] == {
                "mode": "http",
                "uri": URI,
                "experiment": EXPERIMENT,
            }
            gold_dir = source / "gold" / ("b" * 40) / "gold_v1"
            gold_manifest = json.loads((gold_dir / "manifest.json").read_text())
            features, _, _ = baseline.load_split(gold_dir, gold_manifest, "validation")
            expected = {
                name: pd.read_parquet(
                    directory / "models" / name / "validation_predictions.parquet"
                ).SCORE.to_numpy()
                for name in baseline.CLASSIFIERS
            }
            client = MlflowClient(tracking_uri=URI)
            hidden = directory.with_name(directory.name + "-hidden")
            previous = mlflow.get_tracking_uri()
            checked = {}
            directory.rename(hidden)
            try:
                for name, record in producer["tracking"].items():
                    run = client.get_run(record["run_id"])
                    assert run.info.status == "FINISHED" and record["model_reload_validated"]
                    assert run.data.tags["model_id"] == name
                    assert run.data.tags["baseline_run_id"] == producer["run_id"]
                    assert run.data.tags["test_evaluated"] == "false"
                    assert run.data.tags["promotion_status"] == "not_promoted"
                    assert "mlflow.parentRunId" not in run.data.tags
                    assert client.get_experiment(run.info.experiment_id).name == EXPERIMENT
                    assert (
                        run.data.metrics["validation_average_precision"]
                        == producer["comparison"][name]["average_precision"]
                    )
                    assert (
                        run.data.tags["selected_on_validation"]
                        == str(name == producer["selected_model"]).lower()
                    )
                    with tracking.http_tracking(URI), TemporaryDirectory(dir=output) as temporary:
                        _, downloaded = tracking.download_pipeline(
                            record["model_uri"], Path(temporary)
                        )
                        metadata = mlflow.models.Model.load(downloaded / "MLmodel")
                        digest = sha256(downloaded / metadata.flavors["sklearn"]["pickled_model"])
                        restored = mlflow.pyfunc.load_model(str(downloaded))
                        scores = np.asarray(restored.predict(features))[:, 1]
                        np.testing.assert_allclose(scores, expected[name], rtol=1e-12, atol=1e-12)
                    checked[name] = {
                        **record,
                        "model_sha256": digest,
                        "independent_http_reload_validated": True,
                        "max_absolute_score_error": float(np.max(np.abs(scores - expected[name]))),
                    }
            finally:
                hidden.rename(directory)
            assert mlflow.get_tracking_uri() == previous
            runs = client.search_runs(
                [run.info.experiment_id],
                filter_string=f"tags.baseline_run_id = '{producer['run_id']}'",
            )
            assert len(runs) == 3
            assert not list(output.rglob("*.db"))
            after = {name: stack.inspect_service(ROOT, name) for name in ["postgres", "mlflow"]}
            assert before == after
            result.update(
                status="success",
                baseline_run_id=producer["run_id"],
                baseline_path=producer["baseline_path"],
                audit_path=producer["audit_path"],
                training_rows=manifest["split_rows"]["train"],
                validation_rows=manifest["split_rows"]["validation"],
                models=checked,
                independent_http_reload_validated=True,
                local_models_unavailable_during_reload=True,
                independent_main_runs=3,
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
