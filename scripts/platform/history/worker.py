"""Credential-free HTTP worker; output a single result and retain failure diagnostics."""

from contextlib import redirect_stdout
import json
import os
import sys

from scripts.platform.history import registry, transfer
from scripts.platform.history.source import require


def execute(request):
    from fraud_detection_mlops.integrations.mlflow_tracking import http_tracking

    with http_tracking(request["uri"]):
        return _execute(request)


def _execute(request):
    from importlib.metadata import version

    from mlflow import MlflowClient

    require(version("mlflow") == "3.17.0", "Restore MLflow 3.17.0")
    require(
        not any(name.startswith(("AWS_", "PG")) for name in os.environ),
        "No S3/database environment may enter migration client",
    )
    require("MLFLOW_BACKEND_STORE_URI" not in os.environ, "No backend credentials in worker")
    plan, uri = request["payload"], request["uri"]
    require(uri.startswith("http://127.0.0.1:"), "Owned loopback tracking endpoint required")
    client = MlflowClient(tracking_uri=uri, registry_uri=uri)
    before = protected(client, plan)
    result = transfer.execute(client, plan, uri, create=request["action"] == "import")
    require(
        before == protected(client, plan), "Unrelated destination metadata changed during transfer"
    )
    if request["action"] == "champion":
        result["registry"] = registry.champion(
            client, plan, result["mapping"], uri, request["reference"]
        )
    result["unrelated_destination_metadata_unchanged"] = True
    return result


def protected(client, plan):
    from mlflow.entities import ViewType
    from scripts.platform.history.source import PREFIX, fingerprint

    values = {}
    for experiment in transfer.paged(
        client.search_experiments, view_type=ViewType.ALL, max_results=1000
    ):
        if experiment.tags.get(PREFIX + "bundle") == plan["snapshot_bundle_id"]:
            continue
        values["experiment:" + experiment.experiment_id] = (
            experiment.to_proto().SerializeToString(deterministic=True).hex()
        )
        for run in transfer.run_rows(client, experiment.experiment_id):
            values["run:" + run.info.run_id] = {
                "info": dict(run.info),
                "params": run.data.params,
                "tags": run.data.tags,
                "metrics": {
                    key: sorted(
                        transfer.metric_key(m)
                        for m in client.get_metric_history(run.info.run_id, key)
                    )
                    for key in run.data.metrics
                },
            }
        for model in transfer.model_rows(client, experiment.experiment_id):
            values["model:" + model.model_id] = (
                model.to_proto().SerializeToString(deterministic=True).hex()
            )
    return fingerprint(values)


if __name__ == "__main__":
    try:
        request = json.load(sys.stdin)
        with redirect_stdout(sys.stderr):
            result = execute(request)
        print(json.dumps({"status": "success", **result}, allow_nan=False), flush=True)
    except Exception as exc:  # noqa: BLE001 — subprocess boundary, avoid credential tracebacks
        print(
            json.dumps(
                {"status": "failed", "error_type": type(exc).__name__, "reason": str(exc)[-2500:]}
            ),
            flush=True,
        )
        raise SystemExit(1) from None
