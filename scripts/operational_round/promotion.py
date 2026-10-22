"""Independently verify the NEW round before assigning fraud-detector@champion."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from mlflow import MlflowClient
from mlflow.artifacts import download_artifacts
import numpy as np
from scripts.operational_round import lineage
from scripts.operational_round.policy import decide, digest, require, sha
from threadpoolctl import threadpool_limits

from fraud_detection_mlops.evaluation.ranking_metrics import evaluate_ranking
from fraud_detection_mlops.integrations import mlflow_tracking as tracking
from fraud_detection_mlops.serving.model_release import runtime_environment


def verify(client, summary, uri, validation, training=None, partitions=None):
    from importlib.metadata import version

    require(
        summary["environment"] == {**runtime_environment(), "mlflow": version("mlflow")},
        "Independent verification environment differs from the training runtime",
    )
    policy = summary["policy"]
    require(
        summary["schema"] == "fraud_operational_training_v1"
        and summary["status"] == "qualified"
        and summary["policy_sha256"] == digest(policy)
        and set(summary["candidates"]) == set(policy["candidates"])
        and summary["gate"] == decide(summary["comparison"], policy)
        and summary["gate"]["passed"],
        "Round is not qualified",
    )
    require(
        summary["test_evaluated"] is False and summary["historical_models_used"] is False,
        "Only the authorized new round may be promoted",
    )
    runs = client.search_runs([summary["experiment_id"]], max_results=1000)
    models = client.search_logged_models(
        experiment_ids=[summary["experiment_id"]], max_results=1000
    )
    require(
        not runs.token
        and not models.token
        and {r.info.run_id for r in runs} == {v["run_id"] for v in summary["candidates"].values()}
        and {m.model_id for m in models}
        == {v["logged_model_id"] for v in summary["candidates"].values()},
        "Round candidate run/model set differs",
    )
    experiment = client.get_experiment(summary["experiment_id"])
    require(
        experiment.name == policy["experiment_prefix"] + "-" + summary["round_id"]
        and experiment.tags
        == {
            "fraud.round.id": summary["round_id"],
            "fraud.round.policy_sha256": summary["policy_sha256"],
        },
        "Round experiment identity differs",
    )
    expected_inputs = summary["lineage"]["datasets"]
    if training is not None:
        for context, batch, split in [
            ("training", training, "train"),
            ("validation", validation, "validation"),
        ]:
            checked, _ = lineage.description(
                *batch,
                context=context,
                provenance=summary["provenance"],
                partitions=partitions[split],
            )
            require(checked == expected_inputs[context], "Consumed dataset identity differs")
    X_val, y_val, metadata = validation
    report_bytes = json.dumps(summary, indent=2, allow_nan=False).encode()
    import hashlib

    report_sha = hashlib.sha256(report_bytes).hexdigest()
    checks = {}
    with threadpool_limits(limits=policy["thread_limit"]):
        for name, item in summary["candidates"].items():
            run = client.get_run(item["run_id"])
            require(
                run.info.experiment_id == summary["experiment_id"]
                and run.info.status == "FINISHED"
                and run.data.tags.get("fraud.round.id") == summary["round_id"]
                and run.data.tags.get("model_id") == name
                and run.data.tags.get("fraud.round.policy_sha256") == summary["policy_sha256"]
                and run.data.tags.get("fraud.round.candidate_complete") == "true"
                and run.data.tags.get("fraud.round.training_report_sha256") == report_sha,
                "Incomplete or foreign candidate run",
            )
            lineage.inputs_match(run, expected_inputs)
            logged = client.get_logged_model(item["logged_model_id"])
            require(
                logged.source_run_id == item["run_id"]
                and logged.status.name == "READY"
                and logged.artifact_location == item["model_artifact_uri"],
                "Candidate model lineage differs",
            )
            for key, value in item["metrics"].items():
                require(
                    run.data.metrics.get("validation_" + key) == value,
                    "Tracked validation metrics differ",
                )
            with TemporaryDirectory(prefix="round-independent-") as temporary:
                control = Path(
                    client.download_artifacts(item["run_id"], "lineage.json", temporary)
                )
                require(
                    sha(control) == summary["lineage_artifact_sha256"]
                    and json.loads(control.read_bytes()) == summary["lineage"],
                    "Lineage artifact differs",
                )
                saved_report = Path(
                    client.download_artifacts(item["run_id"], "training-report.json", temporary)
                )
                require(
                    saved_report.read_bytes() == report_bytes, "Training report artifact differs"
                )
                directory = Path(
                    download_artifacts(
                        artifact_uri=logged.artifact_location, tracking_uri=uri, dst_path=temporary
                    )
                )
                require(
                    {
                        p.relative_to(directory).as_posix(): sha(p)
                        for p in directory.rglob("*")
                        if p.is_file()
                    }
                    == item["artifacts"],
                    "Model artifact byte set differs",
                )
                model = tracking.load_tracked_model(
                    directory.as_uri(),
                    Path(temporary) / "reviewed",
                    feature_columns=list(X_val.columns),
                )
                scores = np.asarray(model.predict(X_val))[:, 1]
                measured = evaluate_ranking(y_val, scores, metadata)
                for key in ("average_precision", "daily_customer_precision_at_100"):
                    require(
                        np.isclose(
                            measured[key], summary["comparison"][name][key], rtol=0, atol=1e-12
                        ),
                        "Independent model quality differs",
                    )
                require(
                    abs(float(scores[0]) - item["control_score"]) <= policy["parity_atol"],
                    "Independent control score differs",
                )
                require(
                    sha(directory / item["weight_file"]) == item["model_sha256"], "Weights differ"
                )
                model_metrics = {
                    (m.key, m.dataset_name, m.dataset_digest): m.value
                    for m in logged.metrics or []
                }
                for key in ("average_precision", "daily_customer_precision_at_100"):
                    require(
                        model_metrics.get(
                            (
                                "validation_" + key,
                                expected_inputs["validation"]["name"],
                                expected_inputs["validation"]["digest"],
                            )
                        )
                        == item["metrics"][key],
                        "Model metric is not linked to the validation dataset",
                    )
            checks[name] = {
                "run_id": item["run_id"],
                "model_sha256": item["model_sha256"],
                "native_inputs_verified": True,
                "all_artifact_bytes_verified": True,
                "quality_recomputed_on_validation": True,
                "parity_rows": len(scores),
            }
    return checks


def promote(summary, uri, validation, *, expected_alias, owner_id, training=None, partitions=None):
    with tracking.http_tracking(uri):
        client = MlflowClient(tracking_uri=uri, registry_uri=uri)
        checks = verify(client, summary, uri, validation, training, partitions)
        name = summary["policy"]["registered_model"]
        owned_tags = {"fraud.task": name, "fraud.owner_id": owner_id}
        existing = list(client.search_registered_models(filter_string="name = '" + name + "'"))
        if existing:
            require(len(existing) == 1 and existing[0].tags == owned_tags, "Foreign task Registry")
            current_alias = existing[0].aliases.get("champion")
        else:
            current_alias = None
        versions = list(client.search_model_versions("name = '" + name + "'")) if existing else []
        matches = [v for v in versions if v.tags.get("fraud.round.id") == summary["round_id"]]
        require(len(matches) <= 1, "Duplicate round versions")
        desired = matches[0] if matches else None
        # Idempotent retry is allowed only if champion already resolves to this exact round.
        require(
            current_alias == expected_alias
            or desired is not None
            and str(current_alias) == desired.version,
            "Champion changed since round initialization",
        )
        winner = summary["candidates"][summary["gate"]["selected_model"]]
        tags = {
            "fraud.round.id": summary["round_id"],
            "fraud.round.policy_sha256": summary["policy_sha256"],
            "fraud.model_sha256": winner["model_sha256"],
        }
        if not existing:
            client.create_registered_model(name, tags=owned_tags)
        if desired is None:
            desired = client.create_model_version(
                name,
                source=winner["model_uri"],
                run_id=winner["run_id"],
                tags=tags,
                model_id=winner["logged_model_id"],
            )
        require(
            desired.run_id == winner["run_id"]
            and desired.tags == tags
            and desired.status == "READY",
            "Registered version differs from selected new candidate",
        )
        with TemporaryDirectory(prefix="round-registry-") as temporary:
            path = Path(
                download_artifacts(
                    artifact_uri=client.get_model_version_download_uri(name, desired.version),
                    tracking_uri=uri,
                    registry_uri=uri,
                    dst_path=temporary,
                )
            )
            require(
                {p.relative_to(path).as_posix(): sha(p) for p in path.rglob("*") if p.is_file()}
                == winner["artifacts"],
                "Registry version artifact bytes differ",
            )
        now = client.get_registered_model(name).aliases.get("champion")
        require(now == current_alias, "Concurrent Registry update; preserve version and review")
        client.set_registered_model_alias(name, "champion", desired.version)
        alias = client.get_model_version_by_alias(name, "champion")
        require(
            alias.version == desired.version and alias.run_id == winner["run_id"],
            "Champion readback differs",
        )
        return {
            "schema": "fraud_operational_promotion_v1",
            "status": "promoted",
            "round_id": summary["round_id"],
            "registered_model": name,
            "version": desired.version,
            "alias_uri": "models:/" + name + "@champion",
            "previous_alias": expected_alias,
            "selected_model": summary["gate"]["selected_model"],
            "run_id": winner["run_id"],
            "model_sha256": winner["model_sha256"],
            "policy_sha256": summary["policy_sha256"],
            "verification": checks,
            "historical_models_used": False,
            "fit_executed": False,
            "test_evaluated": False,
            "production_promotion": False,
        }
