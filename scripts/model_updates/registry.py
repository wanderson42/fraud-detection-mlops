"""Compare all new candidates against the pinned incumbent; never move champion here."""

from pathlib import Path
from tempfile import TemporaryDirectory

from mlflow import MlflowClient
from mlflow.artifacts import download_artifacts
import numpy as np
import pandas as pd
from scripts.model_updates.policy import compare, mature
from scripts.operational_round import lineage, promotion
from scripts.operational_round.policy import digest, require, sha
from threadpoolctl import threadpool_limits

from fraud_detection_mlops.evaluation.ranking_metrics import evaluate_ranking
from fraud_detection_mlops.integrations import mlflow_tracking as tracking


def assess(summary, baseline, uri, training, validation, partitions):
    mature(validation[2], summary["policy"]["comparison_as_of"])
    with tracking.http_tracking(uri), threadpool_limits(limits=summary["policy"]["thread_limit"]):
        client = MlflowClient(tracking_uri=uri, registry_uri=uri)
        candidate_checks = promotion.verify(client, summary, uri, validation, training, partitions)
        version = client.get_model_version("fraud-detector", baseline["version"])
        require(
            version.run_id == baseline["run_id"]
            and version.tags.get("fraud.model_sha256") == baseline["model_sha256"],
            "Pinned incumbent Registry identity differs",
        )
        with TemporaryDirectory(prefix="update-incumbent-") as tmp:
            lineage_path = Path(client.download_artifacts(version.run_id, "lineage.json", tmp))
            import json

            incumbent_lineage = json.loads(lineage_path.read_bytes())
            lineage.inputs_match(client.get_run(version.run_id), incumbent_lineage["datasets"])
            require(
                pd.Timestamp(
                    incumbent_lineage["datasets"]["training"]["latest_label_available_at"]
                )
                < validation[2].TX_DATETIME.min(),
                "Incumbent training overlaps the comparison window",
            )
            path = Path(
                download_artifacts(
                    artifact_uri=client.get_model_version_download_uri(
                        "fraud-detector", baseline["version"]
                    ),
                    tracking_uri=uri,
                    registry_uri=uri,
                    dst_path=tmp,
                )
            )
            require(
                len(list(path.glob("*.skops"))) == 1
                and sha(next(path.glob("*.skops"))) == baseline["model_sha256"],
                "Pinned incumbent weights differ",
            )
            model = tracking.load_tracked_model(
                "models:/fraud-detector/" + baseline["version"],
                Path(tmp) / "model",
                feature_columns=list(validation[0]),
            )
            scores = np.asarray(model.predict(validation[0]))[:, 1]
            measured = evaluate_ranking(validation[1], scores, validation[2])
        decision = compare(summary["comparison"], measured, summary["policy"])
        return {
            "schema": "fraud_model_update_assessment_v1",
            "update_id": summary["round_id"],
            "policy_sha256": summary["policy_sha256"],
            "training_report_sha256": digest(summary),
            "baseline": baseline,
            "gate": decision,
            "candidates_verified": candidate_checks,
            "comparison_dataset": summary["lineage"]["datasets"]["validation"]["identity_sha256"],
            "comparison_labels_mature": True,
            "champion_modified": False,
        }


def register(summary, assessment, baseline, uri, owner, *, no_other_writers):
    require(no_other_writers, "Declare no other Registry writers")
    require(
        assessment["training_report_sha256"] == digest(summary)
        and assessment["baseline"] == baseline
        and assessment["gate"]["passed"],
        "Only the independently qualified challenger can be registered",
    )
    selected = summary["candidates"][assessment["gate"]["selected_model"]]
    client = MlflowClient(tracking_uri=uri, registry_uri=uri)
    task = client.get_registered_model("fraud-detector")
    require(
        task.tags == {"fraud.task": "fraud-detector", "fraud.owner_id": owner},
        "Foreign Registry owner",
    )
    require(
        task.aliases.get("champion") == baseline["version"], "Champion changed since update init"
    )
    tags = {
        "fraud.update.id": summary["round_id"],
        "fraud.update.policy_sha256": summary["policy_sha256"],
        "fraud.model_sha256": selected["model_sha256"],
        "validation_status": "approved",
        "fraud.update.baseline_version": baseline["version"],
        "fraud.update.assessment_sha256": digest(assessment),
    }
    versions = client.search_model_versions("name = 'fraud-detector'", max_results=1000)
    require(not versions.token, "Registry version pagination requires review")
    matches = [v for v in versions if v.tags.get("fraud.update.id") == summary["round_id"]]
    require(len(matches) <= 1, "Duplicate update versions")
    existing = matches[0] if matches else None
    require(
        task.aliases.get("challenger")
        in (None, baseline["version"], existing.version if existing else None),
        "Another challenger occupies the task; preserve and review",
    )
    if existing is None:
        existing = client.create_model_version(
            "fraud-detector",
            source=selected["model_uri"],
            model_id=selected["logged_model_id"],
            run_id=selected["run_id"],
            tags=tags,
        )
    require(
        existing.tags == tags
        and existing.run_id == selected["run_id"]
        and existing.status == "READY",
        "Registered challenger differs",
    )
    with tracking.http_tracking(uri), TemporaryDirectory(prefix="update-registry-") as tmp:
        path = Path(
            download_artifacts(
                artifact_uri=client.get_model_version_download_uri(
                    "fraud-detector", existing.version
                ),
                tracking_uri=uri,
                registry_uri=uri,
                dst_path=tmp,
            )
        )
        require(
            {p.relative_to(path).as_posix(): sha(p) for p in path.rglob("*") if p.is_file()}
            == selected["artifacts"],
            "Registered challenger artifact set differs",
        )
    require(
        client.get_registered_model("fraud-detector").aliases.get("champion")
        == baseline["version"],
        "Concurrent champion update",
    )
    client.set_registered_model_alias("fraud-detector", "challenger", existing.version)
    require(
        client.get_model_version_by_alias("fraud-detector", "challenger").version
        == existing.version,
        "Challenger readback differs",
    )
    return {
        "status": "challenger_registered",
        "version": existing.version,
        "run_id": selected["run_id"],
        "model_sha256": selected["model_sha256"],
        "model_id": assessment["gate"]["selected_model"],
        "assessment_sha256": digest(assessment),
        "champion_modified": False,
    }
