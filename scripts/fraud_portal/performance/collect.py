"""Read MLflow evidence and score only its identified validation data; never fit or promote."""

from datetime import UTC, datetime
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from mlflow import MlflowClient
from mlflow.artifacts import download_artifacts
import numpy as np
import pandas as pd
from scripts.fraud_portal import data
from scripts.fraud_portal.performance import metrics, reader
from scripts.operational_round import lineage
from scripts.platform import kind_foundation as f
from scripts.platform import kind_tracking
from scripts.product_flow import controller as flow
from threadpoolctl import threadpool_limits

from fraud_detection_mlops.integrations import mlflow_tracking as tracking
from fraud_detection_mlops.modeling.experiments.candidate_training import load_split


def validation_batch(summary, directory):
    """Authenticate only the Gold manifest and validation partitions; no train/test row read."""
    directory = data.checked_path(directory)
    outer = data.checked_path(directory / "manifest.json")
    data.require(
        data.sha(outer) == summary["provenance"]["gold_bundle_id"], "Bundle Gold divergente"
    )
    archived = json.loads(outer.read_bytes())
    expected = summary["provenance"]["gold_dataset_manifest_sha256"]
    matches = [
        v
        for v in archived["files"]
        if v["sha256"] == expected and v["path"].endswith("/manifest.json")
    ]
    data.require(len(matches) == 1, "Manifest do dataset ausente ou ambíguo")
    manifest = data.checked_path(directory / matches[0]["path"])
    data.require(
        manifest.is_relative_to(directory) and data.sha(manifest) == expected,
        "Manifest Gold alterado",
    )
    document = json.loads(manifest.read_bytes())
    parts = [v for v in document["files"] if v["split"] == "validation"]
    data.require(
        parts == summary["lineage"]["datasets"]["validation"]["partitions"],
        "Partições de validação divergentes",
    )
    hashes = {str(manifest): expected, str(outer): data.sha(outer)}
    for part in parts:
        path = data.checked_path(manifest.parent / part["path"])
        data.require(
            path.is_relative_to(manifest.parent) and data.sha(path) == part["sha256"],
            "Partição de validação alterada",
        )
        hashes[str(path)] = part["sha256"]
    return load_split(manifest.parent, document, "validation"), hashes


def collect(client, uri, version, snapshot, portal_sha, batch_provider):
    data.require(
        version.tags.get("fraud.model_sha256") == snapshot["model_sha256"],
        "Registry identifica outros pesos",
    )
    run = client.get_run(version.run_id)
    with TemporaryDirectory(prefix="portal-validation-") as temp:
        report_path = Path(client.download_artifacts(version.run_id, "training-report.json", temp))
        report_sha = data.sha(report_path)
        data.require(
            run.data.tags.get("fraud.round.training_report_sha256") == report_sha,
            "Relatório MLflow divergente",
        )
        summary = json.loads(report_path.read_bytes())
        data.require(
            summary["schema"] == "fraud_operational_training_v1"
            and summary["test_evaluated"] is False,
            "Protocolo de avaliação não suportado",
        )
        batch, files = batch_provider(summary)
        X, labels, metadata = batch
        expected = summary["lineage"]["datasets"]
        checked, _ = lineage.description(
            *batch,
            context="validation",
            provenance=summary["provenance"],
            partitions=expected["validation"]["partitions"],
        )
        data.require(checked == expected["validation"], "Conteúdo da validação diverge dos Inputs")
        data.require(
            pd.Timestamp(expected["training"]["latest_label_available_at"])
            < metadata.TX_DATETIME.min(),
            "Feedback de treino invade a validação",
        )
        data.require(
            metadata.LABEL_AVAILABLE_AT.max()
            == pd.Timestamp(expected["validation"]["latest_label_available_at"]),
            "Maturidade dos rótulos divergente",
        )
        candidates, selected_scores = [], None
        for name, item in summary["candidates"].items():
            candidate_run = client.get_run(item["run_id"])
            data.require(
                candidate_run.info.status == "FINISHED"
                and candidate_run.info.experiment_id == summary["experiment_id"]
                and candidate_run.data.tags.get("fraud.round.training_report_sha256")
                == report_sha,
                "Candidato incompleto ou de outro experimento",
            )
            lineage.inputs_match(candidate_run, expected)
            logged = client.get_logged_model(item["logged_model_id"])
            data.require(
                logged.source_run_id == item["run_id"]
                and logged.artifact_location == item["model_artifact_uri"],
                "Origem do modelo divergente",
            )
            with TemporaryDirectory(prefix="candidate-", dir=temp) as local:
                path = Path(
                    download_artifacts(
                        artifact_uri=logged.artifact_location, tracking_uri=uri, dst_path=local
                    )
                )
                actual = {
                    p.relative_to(path).as_posix(): data.sha(p)
                    for p in path.rglob("*")
                    if p.is_file()
                }
                data.require(
                    actual == item["artifacts"]
                    and data.sha(path / item["weight_file"]) == item["model_sha256"],
                    "Bytes do modelo divergentes",
                )
                estimator = tracking.load_tracked_model(
                    path.as_uri(), Path(local) / "checked", feature_columns=list(X.columns)
                )
                with threadpool_limits(limits=2):
                    scores = metrics.scores(estimator.predict(X), len(X))
                measured = metrics.measure(
                    labels, scores, metadata, snapshot["alert_policy"]["score_threshold"]
                )
                for key in ("average_precision", "roc_auc", "daily_customer_precision_at_100"):
                    data.require(
                        np.isclose(
                            measured["metrics"][key], item["metrics"][key], atol=1e-12, rtol=0
                        )
                        and candidate_run.data.metrics.get("validation_" + key)
                        == item["metrics"][key],
                        "Métricas recalculadas divergem do MLflow",
                    )
                selected = item["run_id"] == version.run_id
                if selected:
                    data.require(
                        item["model_sha256"] == snapshot["model_sha256"]
                        and name == snapshot["model_id"],
                        "Snapshot e candidato avaliado divergem",
                    )
                    selected_scores = scores
                candidates.append(
                    {
                        "model_id": name,
                        "run_id": item["run_id"],
                        "model_sha256": item["model_sha256"],
                        "is_snapshot_model": selected,
                        **measured,
                    }
                )
        data.require(selected_scores is not None, "Modelo do snapshot não consta no relatório")
        queue = metrics.queue_policy(
            X, metadata, selected_scores, snapshot["alert_policy"], snapshot["model_id"]
        )
        data.require(
            all(data.sha(Path(path)) == sha for path, sha in files.items()),
            "Fonte alterada durante avaliação",
        )
        return {
            "schema": "fraud_portal_performance_v1",
            "model_id": snapshot["model_id"],
            "model_sha256": snapshot["model_sha256"],
            "serving_release_id": snapshot["serving_release_id"],
            "portal_manifest_sha256": portal_sha,
            "registry": {
                "name": version.name,
                "version": version.version,
                "run_id": version.run_id,
            },
            "dataset": expected["validation"],
            "training_dataset": expected["training"],
            "labels_mature_as_of": expected["validation"]["latest_label_available_at"],
            "source_report_sha256": report_sha,
            "source_commit": summary["provenance"]["implementation_commit"],
            "candidates": candidates,
            "queue_policy": queue,
            "selection_data_reused": True,
            "independent_holdout_confirmation": False,
            "model_fit_executed": False,
            "features_recomputed": False,
            "champion_modified": False,
        }


def prepare(root, portal_id, key, round_id, no_other_writers, gold_directory=None):
    from scripts.fraud_portal import controller

    data.require(no_other_writers, "Declare PRODUCT_NO_OTHER_WRITERS=1")
    receipt, snapshot, _ = controller.config(portal_id)
    value = flow.read(snapshot["flow_id"])
    data.require(
        value["release_manifest"]["model_sha256"] == snapshot["model_sha256"],
        "Fluxo do snapshot divergente",
    )
    if gold_directory is None:
        chain = data.checked_path(f.workspace() / "rounds" / round_id / "data.json")
        source = json.loads(chain.read_bytes())
        gold_directory = Path(
            source["stages"]["gold"]["independent_restore"]["restored_directory"]
        )
    gold_directory = f.managed_destination(Path(gold_directory), "storage")
    with flow.target(value), kind_tracking.port_forward() as uri, tracking.http_tracking(uri):
        client = MlflowClient(tracking_uri=uri, registry_uri=uri)
        task = json.loads((root / "references/operational_round_policy_v1.json").read_bytes())[
            "registered_model"
        ]
        version = client.get_model_version(task, str(snapshot["registry_version_at_capture"]))
        data.require(
            client.get_model_version_by_alias(task, "champion").version == version.version,
            "Champion mudou desde a captura",
        )
        report = collect(
            client,
            uri,
            version,
            snapshot,
            receipt["manifest_sha256"],
            lambda summary: validation_batch(summary, gold_directory),
        )
        data.require(
            client.get_model_version_by_alias(task, "champion").version == version.version,
            "Champion mudou durante a avaliação",
        )
    directory = f.managed_destination(
        root / "data/reports/fraud-portal/performance" / key, "reports"
    )
    directory.parent.mkdir(parents=True, exist_ok=True)
    if directory.exists():
        old, _ = reader.load(
            directory, data.sha(directory / "manifest.json"), receipt["manifest_sha256"], snapshot
        )
        data.require(old == report, "Avaliação existente diverge; preserve e revise")
    else:
        with TemporaryDirectory(prefix=".quality-", dir=directory.parent) as temp:
            stage = Path(temp) / "report"
            stage.mkdir(mode=0o700)
            (stage / "performance.json").write_bytes(data.encoded(report))
            manifest = {
                "schema": "fraud_portal_performance_manifest_v1",
                "created_at": datetime.now(UTC).isoformat(),
                "report_sha256": data.sha(stage / "performance.json"),
            }
            (stage / "manifest.json").write_bytes(data.encoded(manifest))
            os.rename(stage, directory)
    pointer = f.managed_destination(
        root / "data/storage/kind-platform/portal" / (portal_id + "-performance.json"), "storage"
    )
    link = {
        "schema": "fraud_portal_performance_access_v1",
        "directory": str(directory),
        "manifest_sha256": data.sha(directory / "manifest.json"),
        "portal_manifest_sha256": receipt["manifest_sha256"],
    }
    if pointer.exists():
        data.require(
            json.loads(pointer.read_bytes()) == link,
            "Avaliação já vinculada diverge; preserve recibo",
        )
    else:
        f.private_json(pointer, link)
    return {
        "status": "portal_performance_prepared",
        **link,
        "validation_rows": report["dataset"]["rows"],
        "model_sha256": report["model_sha256"],
        "champion_modified": False,
    }
