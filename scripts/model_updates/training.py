"""Train repeatable candidates; optional tuning uses only inner training windows."""

from importlib.metadata import version
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

import mlflow
from mlflow import MlflowClient
from mlflow.entities import Metric
from mlflow.models import infer_signature
import mlflow.sklearn
import numpy as np
from scripts.model_updates.policy import validate
from scripts.model_updates.search import choose
from scripts.operational_round import lineage
from scripts.operational_round.policy import decide, digest, require, sha
from scripts.operational_round.training import request_for, run_worker
from scripts.storage import archive, gold_archive
from threadpoolctl import threadpool_limits

from fraud_detection_mlops.integrations import mlflow_tracking as tracking
from fraud_detection_mlops.integrations.skops_persistence import TRUSTED_TYPES
from fraud_detection_mlops.modeling.algorithms.model_catalog import MODEL_CATALOG
from fraud_detection_mlops.modeling.experiments.candidate_training import fit_candidate, load_split
from fraud_detection_mlops.serving.model_release import runtime_environment

MARKER = "FRAUD_OPERATIONAL_ROUND_RESULT="


def candidates(train, validation, provenance, partitions, *, round_key, uri, policy, work):
    X_train, y_train, meta_train = train
    X_val, y_val, meta_val = validation
    require(
        set(meta_train.TRANSACTION_ID).isdisjoint(meta_val.TRANSACTION_ID)
        and meta_train.LABEL_AVAILABLE_AT.max() < meta_val.TX_DATETIME.min(),
        "Training feedback or IDs overlap validation",
    )
    descriptions, datasets = {}, {}
    for context, batch, split in [
        ("training", train, "train"),
        ("validation", validation, "validation"),
    ]:
        descriptions[context], datasets[context] = lineage.description(
            *batch, context=context, provenance=provenance, partitions=partitions[split]
        )
    lineage_document = {
        "schema": "fraud_round_dataset_lineage_v1",
        "round_id": round_key,
        "datasets": descriptions,
        "policy_sha256": digest(policy),
    }
    lineage_path = work / "lineage.json"
    lineage_path.write_text(json.dumps(lineage_document, sort_keys=True, indent=2))
    lineage_hash = sha(lineage_path)
    experiment_name = policy["experiment_prefix"] + "-" + round_key
    results, comparison, optimization = {}, {}, {}
    validate(policy)
    from scripts.model_updates.policy import mature

    mature(validation[2], policy["comparison_as_of"])
    with tracking.http_tracking(uri), threadpool_limits(limits=policy["thread_limit"]):
        client = MlflowClient(tracking_uri=uri, registry_uri=uri)
        expected_tags = {"fraud.round.id": round_key, "fraud.round.policy_sha256": digest(policy)}
        experiment = client.get_experiment_by_name(experiment_name)
        if experiment is None:
            eid = client.create_experiment(experiment_name, tags=expected_tags)
        else:
            require(experiment.tags == expected_tags, "Foreign operational experiment")
            eid = experiment.experiment_id
        tracking.require_proxy_artifacts(client.get_experiment(eid).artifact_location)
        # An interrupted publication cannot silently create a second set of candidates.
        require(
            not client.search_runs([eid], max_results=1),
            "Round already has candidate runs; preserve and inspect its Job/receipt",
        )
        for name in policy["candidates"]:
            with mlflow.start_run(experiment_id=eid, run_name=name) as run:
                mlflow.set_tags(
                    {
                        **expected_tags,
                        "model_id": name,
                        "fraud.round.lineage_sha256": lineage_hash,
                        "fraud.round.source_commit": provenance["implementation_commit"],
                        "mlflow.source.git.commit": provenance["implementation_commit"],
                        "mlflow.source.name": "scripts/model_updates/training.py",
                        "test_evaluated": "false",
                        "historical_models_used": "false",
                    }
                )
                for context, dataset in datasets.items():
                    d = descriptions[context]
                    mlflow.log_input(
                        dataset,
                        context=context,
                        tags={
                            "fraud.dataset.identity_sha256": d["identity_sha256"],
                            "fraud.dataset.content_rows_sha256": d["content_rows_sha256"],
                            "fraud.dataset.gold_bundle_id": provenance["gold_bundle_id"],
                        },
                    )
                lineage.inputs_match(client.get_run(run.info.run_id), descriptions)
                mlflow.log_artifact(str(lineage_path))
                parameters, optimization[name] = choose(
                    name,
                    train,
                    provenance,
                    partitions["train"],
                    policy,
                    uri,
                    round_key,
                    run.info.run_id,
                    work,
                )
                mlflow.set_tags({"fraud.optimization.mode": policy["optimization"][name]["mode"]})
                model, scores, measured, timings = fit_candidate(
                    name,
                    X_train,
                    y_train,
                    X_val,
                    y_val,
                    meta_val,
                    model_spec=MODEL_CATALOG[name],
                    parameters=parameters,
                )
                comparison[name] = {
                    k: float(v)
                    for k, v in measured.items()
                    if type(v) in (int, float) and v is not None
                }
                mlflow.log_params(
                    {
                        "candidate": name,
                        "training_rows": len(y_train),
                        "validation_rows": len(y_val),
                        "feature_count": len(X_train.columns),
                        "gold_bundle_id": provenance["gold_bundle_id"],
                    }
                )
                # Include fitted estimator/scaler parameters, not only a display name.
                mlflow.log_params({k: str(v) for k, v in model.get_params(deep=True).items()})
                logged = mlflow.sklearn.log_model(
                    model,
                    name="model",
                    serialization_format="skops",
                    skops_trusted_types=sorted(TRUSTED_TYPES),
                    pyfunc_predict_fn="predict_proba",
                    signature=infer_signature(X_val.head(5), model.predict_proba(X_val.head(5))),
                    pip_requirements=[
                        f"{package}=={version(package)}"
                        for package in (
                            "mlflow",
                            "numpy",
                            "pandas",
                            "scipy",
                            "scikit-learn",
                            "skops",
                            "pyarrow",
                        )
                    ],
                )
                metrics = {
                    **{"validation_" + k: v for k, v in comparison[name].items()},
                    **timings,
                }
                import time

                now = int(time.time() * 1000)
                client.log_batch(
                    run.info.run_id,
                    metrics=[
                        Metric(
                            k,
                            float(v),
                            now,
                            0,
                            model_id=logged.model_id,
                            dataset_name=datasets["validation"].name,
                            dataset_digest=datasets["validation"].digest,
                        )
                        for k, v in metrics.items()
                    ],
                )
                with TemporaryDirectory(prefix="reload-", dir=work) as temporary:
                    restored = tracking.load_tracked_model(
                        logged.model_uri, Path(temporary), feature_columns=list(X_val.columns)
                    )
                    reloaded = np.asarray(restored.predict(X_val))[:, 1]
                    require(
                        np.allclose(
                            reloaded,
                            scores,
                            rtol=policy["parity_rtol"],
                            atol=policy["parity_atol"],
                        ),
                        "Full validation reload differs",
                    )
                    local = Path(
                        mlflow.artifacts.download_artifacts(
                            artifact_uri=client.get_logged_model(
                                logged.model_id
                            ).artifact_location,
                            tracking_uri=uri,
                            dst_path=str(Path(temporary) / "bytes"),
                        )
                    )
                    artifacts = {
                        p.relative_to(local).as_posix(): sha(p)
                        for p in local.rglob("*")
                        if p.is_file()
                    }
                    metadata = mlflow.models.Model.load(str(local / "MLmodel"))
                    weight = metadata.flavors["sklearn"]["pickled_model"]
                    require(weight in artifacts, "Tracked weights missing")
                lineage.inputs_match(client.get_run(run.info.run_id), descriptions)
                mlflow.set_tag("fraud.round.candidate_complete", "true")
                results[name] = {
                    "run_id": run.info.run_id,
                    "logged_model_id": logged.model_id,
                    "model_uri": logged.model_uri,
                    "model_artifact_uri": client.get_logged_model(
                        logged.model_id
                    ).artifact_location,
                    "artifacts": artifacts,
                    "model_sha256": artifacts[weight],
                    "weight_file": weight,
                    "metrics": comparison[name],
                    "timings": timings,
                    "full_validation_reload_verified": True,
                    "parity_rows": len(scores),
                    "max_absolute_score_error": float(np.max(np.abs(reloaded - scores))),
                    "control_score": float(scores[0]),
                }
        gate = decide(comparison, policy)
        summary = {
            "schema": "fraud_operational_training_v1",
            "status": "qualified" if gate["passed"] else "rejected",
            "round_id": round_key,
            "experiment_id": eid,
            "experiment_name": experiment_name,
            "policy": policy,
            "policy_sha256": digest(policy),
            "environment": {**runtime_environment(), "mlflow": version("mlflow")},
            "provenance": provenance,
            "lineage": lineage_document,
            "lineage_artifact_sha256": lineage_hash,
            "candidates": results,
            "comparison": comparison,
            "optimization": optimization,
            "gate": gate,
            "control_request": request_for(X_val, meta_val),
            "test_evaluated": False,
            "historical_models_used": False,
            "fit_executed": True,
            "registered_model_promoted": False,
            "production_promotion": False,
        }
        report = work / "training-report.json"
        report.write_text(json.dumps(summary, indent=2, allow_nan=False))
        for item in results.values():
            client.log_artifact(item["run_id"], str(report))
            client.set_tag(item["run_id"], "fraud.round.training_report_sha256", sha(report))
        return summary


def execute(plan, incoming, work):
    require(
        not any(k.startswith(("AWS_", "PG")) for k in os.environ),
        "Training container must not receive storage/database credentials",
    )
    policy = validate(plan["policy"])
    require(
        plan["policy_sha256"] == digest(policy) and plan["policy"] == policy,
        "Training plan policy changed",
    )
    require(incoming["bundle_id"] == plan["provenance"]["gold_bundle_id"], "Wrong Gold bundle")
    candidate = archive.checked_path(work, Path(incoming["candidate"]))
    manifest = json.loads((candidate / "manifest.json").read_bytes())
    require(
        archive.digest(candidate / "manifest.json") == incoming["bundle_id"],
        "Gold manifest identity differs",
    )
    verified = gold_archive.verify_directory(candidate, manifest)
    gold = Path(verified["gold_path"])
    dataset_manifest = json.loads((gold / "manifest.json").read_bytes())
    provenance = {
        **plan["provenance"],
        "gold_dataset_manifest_sha256": sha(gold / "manifest.json"),
    }
    partitions = {
        split: [f for f in dataset_manifest["files"] if f["split"] == split]
        for split in ("train", "validation")
    }
    return candidates(
        load_split(gold, dataset_manifest, "train"),
        load_split(gold, dataset_manifest, "validation"),
        provenance,
        partitions,
        round_key=plan["round_id"],
        uri="http://fraud-tracking:5000",
        policy=policy,
        work=work,
    )


def main():
    run_worker(execute, __doc__, MARKER)


if __name__ == "__main__":
    main()
