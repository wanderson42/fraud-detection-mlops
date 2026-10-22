"""Bind the historical approved HGB to the Registry only after full transfer readback."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from mlflow import MlflowClient
from mlflow.artifacts import download_artifacts
import numpy as np
from scripts.platform.history.source import PREFIX, fingerprint, require
from scripts.storage import archive
from scripts.storage.mlflow_recovery_worker import signature_frame


def references(root, plan):
    baseline_path = root / "references/evidence/mlflow_execution_2026-10-07.json"
    gate_path = root / "references/evidence/final_evaluation_execution_2026-10-08.json"
    frozen_path = root / "references/frozen_candidate_v1.json"
    baseline, gate, frozen = (
        json.loads(p.read_bytes()) for p in (baseline_path, gate_path, frozen_path)
    )
    require(
        gate["gate"]["passed"] is True
        and gate["gate"]["decision"] == "eligible_for_laboratory_serving_review"
        and gate["gate"]["scope"] == "offline_candidate_for_laboratory_serving",
        "Historical quality gate did not authorize laboratory review",
    )
    require(
        gate["freeze_sha256"] == archive.digest(frozen_path), "Frozen quality-gate binding differs"
    )
    require(
        frozen["model"]["model_sha256"] == plan["control"]["model_sha256"]
        and frozen["model_uri"] == plan["control"]["model_uri"] == gate["model_uri"],
        "Approved reference/release/model identities differ",
    )
    require(
        {m["model_id"] for m in baseline["models"]}
        == {"dummy_prior", "logistic_regression", "hist_gradient_boosting"},
        "Expected all three historical baselines",
    )
    runs = {r["id"]: r for store in plan["stores"] for r in store["runs"]}
    criteria = gate["gate"]["criteria"]
    acceptance = frozen["policy"]["acceptance"]
    for key, policy_key in (
        ("average_precision", "minimum_average_precision"),
        ("daily_customer_precision_at_100", "minimum_daily_customer_precision_at_100"),
    ):
        rule = criteria[key]
        require(
            rule["passed"] is True
            and rule["minimum"] == acceptance[policy_key]
            and rule["value"] >= rule["minimum"],
            "Historical quality criterion differs from frozen policy",
        )
    evaluation = runs.get(gate["tracking"]["run_id"])
    require(
        evaluation is not None and evaluation["metadata"]["status"] == "FINISHED",
        "Quality-gate tracking run missing",
    )
    reports = [
        v
        for v in evaluation["artifacts"].values()
        if v["sha256"] == gate["tracking"]["report_sha256"]
    ]
    require(len(reports) == 1, "Pinned quality-gate report absent from snapshot")
    saved_report = json.loads(
        (Path(plan["snapshot_directory"]) / reports[0]["source"]).read_bytes()
    )
    require(
        saved_report["gate"] == gate["gate"]
        and saved_report["freeze_sha256"] == gate["freeze_sha256"]
        and saved_report["model_uri"] == gate["model_uri"]
        and saved_report["refit"] is False,
        "Archived evaluation report differs from approved gate",
    )
    for model in baseline["models"]:
        run = runs.get(model["mlflow_run_id"])
        require(
            run is not None and run["metadata"]["status"] == "FINISHED",
            "Historical baseline run missing or not finished",
        )
        require(
            model["model_uri"] == "runs:/" + run["id"] + "/model",
            "Unsupported historical baseline URI",
        )
        require("model/MLmodel" in run["artifacts"], "Missing baseline model metadata")
        if model["model_id"] == "hist_gradient_boosting":
            require(
                model["mlflow_run_id"] == frozen["model"]["mlflow_run_id"],
                "Frozen run differs from historical comparison",
            )
    return {
        "models": baseline["models"],
        "gate": gate["gate"],
        "frozen_sha256": archive.digest(frozen_path),
        "baseline_evidence_sha256": archive.digest(baseline_path),
        "gate_evidence_sha256": archive.digest(gate_path),
    }


def reload_baselines(plan, mapping, uri, reference):
    import mlflow.pyfunc
    from threadpoolctl import threadpool_limits

    from fraud_detection_mlops.integrations import mlflow_tracking as tracking
    from fraud_detection_mlops.serving.model_release import runtime_environment
    from fraud_detection_mlops.serving.request_schema import ScoreRequest

    require(
        runtime_environment() == plan["control"]["runtime_environment"],
        "Restore the pinned historical release environment before model reload",
    )
    result = {}
    request = ScoreRequest.model_validate(plan["control"]["request"])
    runs = {r["id"]: r for s in plan["stores"] for r in s["runs"]}
    for item in reference["models"]:
        origin = item["mlflow_run_id"]
        target_uri = "runs:/" + mapping["runs"][origin] + "/model"
        with TemporaryDirectory(prefix="history-baseline-") as temporary:
            directory = Path(
                download_artifacts(
                    artifact_uri=target_uri, tracking_uri=uri, registry_uri=uri, dst_path=temporary
                )
            )
            _, reviewed = tracking.download_pipeline(
                directory.as_uri(), Path(temporary) / "reviewed"
            )
            metadata = mlflow.models.Model.load(str(reviewed / "MLmodel"))
            expected = {
                n.removeprefix("model/"): v["sha256"]
                for n, v in runs[origin]["artifacts"].items()
                if n.startswith("model/")
            }
            actual = {
                p.relative_to(directory).as_posix(): archive.digest(p)
                for p in directory.rglob("*")
                if p.is_file()
            }
            require(actual == expected, "Baseline downloaded bytes differ")
            frame, changes = signature_frame(request.feature_frame(), metadata.signature.inputs)
            with threadpool_limits(limits=1):
                values = np.asarray(mlflow.pyfunc.load_model(str(directory)).predict(frame))
            require(
                values.shape == (1, 2)
                and np.isfinite(values).all()
                and (values >= 0).all()
                and (values <= 1).all(),
                "Unexpected baseline probability output",
            )
            score = float(values[0, 1])
            if item["model_id"] == "hist_gradient_boosting":
                flavor = metadata.flavors["sklearn"]
                require(
                    archive.digest(directory / flavor["pickled_model"])
                    == plan["control"]["model_sha256"],
                    "Frozen HGB bytes differ",
                )
                require(
                    abs(score - plan["control"]["expected_score"]) <= 1e-12,
                    "Historical HGB control score differs",
                )
        result[item["model_id"]] = {
            "origin_run_id": origin,
            "target_run_id": mapping["runs"][origin],
            "model_uri": target_uri,
            "artifact_bytes_verified": True,
            "reload_verified": True,
            "control_score": score,
            "signature_input_adaptations": changes,
        }
    return result


def champion(client: MlflowClient, plan, mapping, uri, reference, name="fraud-hgb-reference"):
    checks = reload_baselines(plan, mapping, uri, reference)
    hgb = checks["hist_gradient_boosting"]
    tags = {
        PREFIX + "bundle": plan["snapshot_bundle_id"],
        PREFIX + "model_sha256": plan["control"]["model_sha256"],
        PREFIX + "gate_sha256": reference["gate_evidence_sha256"],
    }
    models = list(client.search_registered_models(filter_string="name = '" + name + "'"))
    if not models:
        client.create_registered_model(
            name, tags=tags, description="Approved historical HGB reference for laboratory serving"
        )
    else:
        require(
            len(models) == 1 and models[0].tags == tags,
            "Registered model name belongs to another provenance",
        )
    versions = list(client.search_model_versions("name = '" + name + "'"))
    require(len(versions) <= 1, "Ambiguous reference versions; preserve and review")
    if versions:
        version = versions[0]
        require(
            version.run_id == hgb["target_run_id"] and version.tags == tags,
            "Existing version differs from approved source",
        )
    else:
        version = client.create_model_version(
            name, source=hgb["model_uri"], run_id=hgb["target_run_id"], tags=tags
        )
    require(version.status == "READY", "Registered version not ready")
    # Read the numbered version before assigning the alias, so a failure cannot promote it.
    with TemporaryDirectory(prefix="history-version-") as temporary:
        directory = Path(
            download_artifacts(
                artifact_uri=client.get_model_version_download_uri(name, version.version),
                tracking_uri=uri,
                registry_uri=uri,
                dst_path=temporary,
            )
        )
        import mlflow.models

        metadata = mlflow.models.Model.load(str(directory / "MLmodel"))
        origin = next(
            r for s in plan["stores"] for r in s["runs"] if r["id"] == hgb["origin_run_id"]
        )
        expected = {
            n.removeprefix("model/"): v["sha256"]
            for n, v in origin["artifacts"].items()
            if n.startswith("model/")
        }
        actual = {
            p.relative_to(directory).as_posix(): archive.digest(p)
            for p in directory.rglob("*")
            if p.is_file()
        }
        require(actual == expected, "Registry version artifact bytes differ")
        require(
            archive.digest(directory / metadata.flavors["sklearn"]["pickled_model"])
            == plan["control"]["model_sha256"],
            "Registry version resolves to other weights",
        )
    registered = client.get_registered_model(name)
    prior = registered.aliases.get("champion")
    require(
        prior is None or str(prior) == str(version.version),
        "Champion already references another version",
    )
    client.set_registered_model_alias(name, "champion", version.version)
    resolved = client.get_model_version_by_alias(name, "champion")
    require(
        resolved.version == version.version and resolved.run_id == hgb["target_run_id"],
        "Champion alias readback differs",
    )
    return {
        "registered_model": name,
        "version": version.version,
        "alias": "champion",
        "alias_uri": f"models:/{name}@champion",
        "previous_alias_version": prior,
        "model_sha256": plan["control"]["model_sha256"],
        "baseline_models": checks,
        "quality_gate": reference["gate"],
        "reference_fingerprint": fingerprint(reference),
        "historical_release_deployed": False,
        "refit": False,
        "production_promotion": False,
    }
