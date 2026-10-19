"""Opt-in model producer through the existing lab; no container recreation or migration."""

import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from uuid import uuid4

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("FRAUD_TRACKING_CONTRIBUTION_LIVE") != "1",
    reason="Explicit tracking-contribution opt-in required",
)
ROOT = Path(__file__).resolve().parents[3]
URI = "http://127.0.0.1:15001"


def test_model_contribution_http_reload_and_explicit_local_routing_rollback():
    from scripts.tracking import local_stack as stack

    with stack.exclusive(ROOT):
        validate_model_contribution()


def validate_model_contribution():
    from examples import model_contribution as example
    import mlflow
    from mlflow import MlflowClient
    import numpy as np
    import pandas as pd
    from scripts.tracking import local_stack as stack

    from fraud_detection_mlops.artifacts import sha256
    from fraud_detection_mlops.integrations import mlflow_tracking as tracking
    from fraud_detection_mlops.storage.managed_paths import managed_destination

    identity = uuid4().hex
    output = managed_destination(
        ROOT / "data/examples" / ("contribution-http-" + identity), "examples"
    )
    workspace = stack.workspace(ROOT)
    receipt = workspace / "receipts" / ("contribution-" + identity + ".json")
    result = {
        "schema": "fraud_tracking_contribution_validation_v1",
        "status": "failed",
        "scope": "synthetic_model_producer_http_proxy_and_local_routing_rollback_not_historical_migration",
        "tracking_uri": URI,
        "producer_code_sha256": sha256(ROOT / "examples/model_contribution.py"),
        "validation_code_sha256": sha256(Path(__file__)),
        "tracking_code_sha256": sha256(
            ROOT / "fraud_detection_mlops/integrations/mlflow_tracking.py"
        ),
        "output": str(output),
        "report": str(receipt),
        "historical_runs_migrated": False,
        "production_promotion": False,
        "direct_s3_model_readback_validated": False,
        "postgres_backup_restore_validated": False,
    }
    try:
        before = {name: stack.inspect_service(ROOT, name) for name in ["postgres", "mlflow"]}
        environment = {k: v for k, v in os.environ.items() if not k.startswith("AWS_")}
        environment.update(MLFLOW_DISABLE_TELEMETRY="true", MLFLOW_DISABLE_AGENT_HINT="1")
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "examples.model_contribution",
                "--output",
                str(output),
                "--tracking-uri",
                URI,
            ],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr[-3000:]
        producer = json.loads(completed.stdout)
        recorded = producer["tracking"]
        assert producer["status"] == "success" and producer["promotion_status"] == "not_promoted"
        assert producer["training_rows"] == 80 and producer["evaluation_rows"] == 20
        assert recorded["tracking_uri"] == URI and recorded["model_reload_validated"]
        assert recorded["max_absolute_score_error"] <= 1e-12
        client = MlflowClient(tracking_uri=URI)
        run = client.get_run(recorded["run_id"])
        assert run.info.status == "FINISHED"
        assert client.get_experiment(run.info.experiment_id).name == example.HTTP_EXPERIMENT
        assert run.data.tags["scope"] == "synthetic_integration_smoke"
        assert run.data.tags["promotion_status"] == "not_promoted"
        assert run.data.tags["example_code_sha256"] == result["producer_code_sha256"]
        measurement = json.loads((output / "metrics.json").read_text())
        for name, value in measurement["configuration"].items():
            assert run.data.params[name] == str(value)
        for name, value in measurement["metrics"].items():
            if name != "daily_customer_metrics":
                assert run.data.metrics[name] == value
        features = example.synthetic_batches()[1][0]
        scores = pd.read_parquet(output / "validation_predictions.parquet").SCORE.to_numpy()
        previous = mlflow.get_tracking_uri()
        hidden = output.with_name(output.name + "-hidden")
        output.rename(hidden)
        try:
            with (
                tracking.http_tracking(URI),
                TemporaryDirectory(prefix=".model-download-", dir=workspace) as temporary,
            ):
                _, downloaded = tracking.download_pipeline(recorded["model_uri"], Path(temporary))
                metadata = mlflow.models.Model.load(downloaded / "MLmodel")
                model_file = downloaded / metadata.flavors["sklearn"]["pickled_model"]
                reloaded = mlflow.pyfunc.load_model(str(downloaded))
                probabilities = np.asarray(reloaded.predict(features))
                np.testing.assert_allclose(probabilities[:, 1], scores, rtol=1e-12, atol=1e-12)
                model_sha = sha256(model_file)
        finally:
            hidden.rename(output)
        assert mlflow.get_tracking_uri() == previous
        local_parent = stack.checked(ROOT / "data/tracking")
        local_parent.mkdir(parents=True, exist_ok=True)
        with (
            TemporaryDirectory(prefix=".contribution-rollback-", dir=local_parent) as local,
            TemporaryDirectory(prefix=".contribution-rollback-", dir=output.parent) as temporary,
        ):
            rolled_back = example.run(Path(temporary) / "example", Path(local) / "store")
            assert rolled_back["status"] == "success" and rolled_back["tracking"]
            assert (Path(local) / "store/mlflow.db").is_file()
            local_client = MlflowClient(tracking_uri=tracking.tracking_uri(Path(local) / "store"))
            assert (
                local_client.get_run(rolled_back["tracking"]["run_id"]).info.status == "FINISHED"
            )
        assert mlflow.get_tracking_uri() == previous
        after = {name: stack.inspect_service(ROOT, name) for name in ["postgres", "mlflow"]}
        assert before == after
        result.update(
            status="success",
            producer=producer,
            model_sha256=model_sha,
            independent_http_reload_validated=True,
            local_output_unavailable_during_reload=True,
            max_absolute_score_error=float(np.max(np.abs(probabilities[:, 1] - scores))),
            local_routing_rollback_validated=True,
            disposable_local_store_removed=not Path(local).exists(),
            producer_aws_environment_removed=True,
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
