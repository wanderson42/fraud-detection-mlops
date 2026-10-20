"""Run existing data producers on verified inputs in an ephemeral, credential-free Job volume."""

import argparse
import json
import os
from pathlib import Path
import re
import shutil

from scripts.storage import archive
from scripts.storage import bronze_s3_worker as transport

ROOT = Path(__file__).resolve().parents[2]
REFERENCES = (
    archive.INVENTORY_PATH,
    archive.SILVER_CONTRACT_PATH,
    archive.GOLD_CONTRACT_PATH,
    archive.TEMPORAL_PROTOCOL_PATH,
)
INPUT_SCHEMAS = {"silver": archive.BRONZE_SCHEMA, "gold": archive.SILVER_SCHEMA}
OUTPUT_SCHEMAS = {"silver": archive.SILVER_SCHEMA, "gold": archive.GOLD_SCHEMA}


def require(value, message):
    if not value:
        raise ValueError(message)


def identity(run_id, stage, input_id):
    require(bool(re.fullmatch(r"[a-f0-9]{32}", run_id)), "Invalid Job run ID")
    require(stage in INPUT_SCHEMAS, "Unknown producer stage")
    archive.bundle_id(input_id)


def execute(stage, input_id, run_id, work=Path("/work"), references=ROOT):
    identity(run_id, stage, input_id)
    require(
        not any(k.startswith(("AWS_", "PG", "MLFLOW_")) for k in os.environ),
        "Scientific container must not receive storage or tracking credentials",
    )
    require(not (work / "compute.json").exists(), "Existing computation receipt; preserve it")
    incoming = json.loads((work / "input.json").read_text())
    require(
        incoming["run_id"] == run_id
        and incoming["stage"] == stage
        and incoming["input_bundle_id"] == input_id,
        "Downloaded input does not match this Job",
    )
    candidate = archive.checked_path(work, Path(incoming["candidate"]))
    document, _ = transport.manifest_at(candidate, input_id, INPUT_SCHEMAS[stage])
    # Pin the trusted inventory before any pickle is deserialized. The archived
    # inventory cannot substitute another repository, commit or set of Git blobs.
    for name in (
        (archive.INVENTORY_PATH,)
        if stage == "silver"
        else (archive.INVENTORY_PATH, archive.SILVER_CONTRACT_PATH)
    ):
        require(
            archive.digest(archive.checked_path(candidate, Path(name)))
            == archive.digest(references / name),
            "Archived reference differs from the frozen producer input: " + name,
        )
    if stage == "silver":
        from scripts.storage import bronze_archive

        bronze_archive.verify_directory(candidate, document)
    else:
        from scripts.storage import silver_archive

        silver_archive.verify_directory(candidate, document)
    project = work / "project"
    project.mkdir()
    (project / "references").mkdir()
    for name in REFERENCES:
        shutil.copyfile(references / name, project / name)
    if stage == "silver":
        from scripts.storage import silver_archive as output

        from fraud_detection_mlops.data.datasets import silver_dataset as producer

        produced = producer.build_silver(
            candidate / "data/raw/handbook",
            project / "data/interim/handbook",
            inventory_path=project / archive.INVENTORY_PATH,
            contract_path=project / archive.SILVER_CONTRACT_PATH,
        )
        bundle = output.prepare_silver(project)
    else:
        from scripts.storage import gold_archive as output

        from fraud_detection_mlops.data.datasets import gold_dataset as producer

        produced = producer.build_gold(
            candidate / "data/interim/handbook",
            project / "data/processed/handbook",
            inventory_path=project / archive.INVENTORY_PATH,
            silver_contract=project / archive.SILVER_CONTRACT_PATH,
            protocol_path=project / archive.TEMPORAL_PROTOCOL_PATH,
            gold_contract=project / archive.GOLD_CONTRACT_PATH,
        )
        bundle = output.prepare_gold(project)
    bundle_id = archive.bundle_id(bundle["bundle_id"])
    directory = archive.workspace(project) / "bundles" / bundle_id
    manifest, _ = transport.manifest_at(directory, bundle_id, OUTPUT_SCHEMAS[stage])
    verified = output.verify_directory(directory, manifest)
    transport.verify_payload(candidate, document)
    transport.manifest_at(candidate, input_id, INPUT_SCHEMAS[stage])
    result = {
        "input_bundle_unchanged": True,
        "schema": "fraud_kind_pipeline_computation_v1",
        "status": "computed_verified",
        "stage": stage,
        "run_id": run_id,
        "input_bundle_id": input_id,
        "output_bundle_id": bundle_id,
        "bundle_relative": directory.relative_to(work).as_posix(),
        "producer_result": produced,
        "semantic_verification": verified,
        "files": len(manifest["files"]),
        "bytes": sum(r["bytes"] for r in manifest["files"]),
        "reference_hashes": {n: archive.digest(project / n) for n in REFERENCES},
        "source_worker_sha256": archive.digest(Path(__file__)),
        "fit_executed": False,
        "features_recomputed": stage == "gold",
        "pickle_deserialized": stage == "silver",
        "primary_storage_migrated": False,
        "scope": "isolated_platform_producer_reexecution_not_new_model_quality_evidence",
    }
    with (work / "compute.json").open("x") as stream:
        json.dump(result, stream, allow_nan=False)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", required=True, choices=INPUT_SCHEMAS)
    parser.add_argument("--input-bundle-id", required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(execute(args.stage, args.input_bundle_id, args.run_id), allow_nan=False))
    except Exception as exc:  # noqa: BLE001 — bounded worker output
        print(
            json.dumps(
                {
                    "status": "failed",
                    "step": "compute",
                    "error_type": type(exc).__name__,
                    "reason": str(exc)[:1000],
                }
            )
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
