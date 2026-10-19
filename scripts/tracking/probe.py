"""Small operational probe: metadata and one JSON artifact, without model fitting."""

import argparse
from contextlib import redirect_stdout
import hashlib
from importlib.metadata import version
import json
from pathlib import Path, PurePosixPath
import re
import sys
from tempfile import TemporaryDirectory
from urllib.parse import urlparse
from uuid import uuid4

TRACKING_URI = "http://mlflow:5000"
BUCKET = "fraud-tracking-lab"
SCOPE = "isolated_tracking_metadata_and_json_artifact_not_model_reload_or_migration"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def payload(probe_id):
    require(bool(re.fullmatch(r"[0-9a-f]{32}", probe_id)), "Invalid probe identity")
    return (json.dumps({"probe_id": probe_id, "scope": SCOPE}, sort_keys=True) + "\n").encode()


def s3_client():
    import boto3
    from botocore.config import Config

    client = boto3.client(
        "s3",
        endpoint_url="http://rustfs:9000",
        region_name="us-east-1",
        config=Config(
            connect_timeout=5,
            read_timeout=15,
            retries={"max_attempts": 2},
            s3={"addressing_style": "path"},
        ),
    )
    return client


def prepare_bucket():
    from botocore.exceptions import ClientError

    client = s3_client()
    created = False
    try:
        client.head_bucket(Bucket=BUCKET)
    except ClientError as exc:
        if str(exc.response["Error"]["Code"]) not in {"404", "NoSuchBucket", "NotFound"}:
            raise
        try:
            client.create_bucket(Bucket=BUCKET)
            created = True
        except ClientError as create_error:
            if create_error.response["Error"]["Code"] != "BucketAlreadyOwnedByYou":
                raise
    client.head_bucket(Bucket=BUCKET)
    return {"status": "ready", "bucket": BUCKET, "created": created}


def read_probe(client, record):
    require(
        record.get("worker_code_sha256")
        == hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "Probe implementation differs",
    )
    require(
        record.get("mlflow_version") == version("mlflow") == "3.17.0", "MLflow version differs"
    )
    probe_id = record["probe_id"]
    content = payload(probe_id)
    require(record.get("schema") == "fraud_tracking_probe_v1", "Unexpected probe schema")
    require(record["scope"] == SCOPE, "Unexpected rehearsal scope")
    require(
        record["artifact_sha256"] == hashlib.sha256(content).hexdigest(), "Invalid payload pin"
    )
    require(bool(re.fullmatch(r"[0-9a-f]{32}", record["run_id"])), "Invalid run identity")
    experiment = client.get_experiment(record["experiment_id"])
    run = client.get_run(record["run_id"])
    require(experiment.name == "fraud-tracking-lab-" + probe_id, "Experiment identity differs")
    require(
        experiment.artifact_location.startswith("mlflow-artifacts:/"), "Artifact proxy is required"
    )
    require(
        run.info.experiment_id == experiment.experiment_id, "Run belongs to another experiment"
    )
    require(run.info.status == "FINISHED", "Probe run was not finished")
    require(
        run.info.artifact_uri == record["artifact_uri"]
        and run.info.artifact_uri.startswith("mlflow-artifacts:/"),
        "Run artifact destination differs",
    )
    require(run.data.params.get("probe_id") == probe_id, "Parameter differs")
    require(run.data.tags.get("scope") == SCOPE, "Scope tag differs")
    history = sorted(
        client.get_metric_history(record["run_id"], "probe_value"), key=lambda m: m.step
    )
    require(
        [(m.step, m.value) for m in history] == [(0, 0.25), (1, 0.5)], "Metric history differs"
    )
    with TemporaryDirectory(prefix="fraud-tracking-read-") as temporary:
        path = Path(client.download_artifacts(record["run_id"], "probe.json", temporary))
        require(
            path.resolve().is_relative_to(Path(temporary).resolve()),
            "Download escaped scratch directory",
        )
        require(
            not path.is_symlink() and path.read_bytes() == content, "Downloaded artifact differs"
        )
    return {
        **record,
        "metadata_verified": True,
        "metric_history_rows": len(history),
        "artifact_verified": True,
    }


def write_probe(client):
    probe_id = uuid4().hex
    experiment_id = client.create_experiment("fraud-tracking-lab-" + probe_id)
    experiment = client.get_experiment(experiment_id)
    require(
        experiment.artifact_location.startswith("mlflow-artifacts:/"), "Artifact proxy is required"
    )
    run = client.create_run(
        experiment_id, tags={"scope": SCOPE, "mlflow.runName": "persistence-probe"}
    )
    run_id = run.info.run_id
    try:
        client.log_param(run_id, "probe_id", probe_id)
        client.log_metric(run_id, "probe_value", 0.25, timestamp=1_000, step=0)
        client.log_metric(run_id, "probe_value", 0.5, timestamp=2_000, step=1)
        content = payload(probe_id)
        with TemporaryDirectory(prefix="fraud-tracking-write-") as temporary:
            path = Path(temporary) / "probe.json"
            path.write_bytes(content)
            client.log_artifact(run_id, str(path))
        client.set_terminated(run_id, status="FINISHED")
    except BaseException:
        client.set_terminated(run_id, status="FAILED")
        raise
    record = {
        "worker_code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "mlflow_version": version("mlflow"),
        "schema": "fraud_tracking_probe_v1",
        "scope": SCOPE,
        "probe_id": probe_id,
        "run_id": run_id,
        "experiment_id": experiment_id,
        "artifact_uri": client.get_run(run_id).info.artifact_uri,
        "artifact_sha256": hashlib.sha256(content).hexdigest(),
    }
    return read_probe(client, record)


def verify_object(record):
    content = payload(record["probe_id"])
    require(
        record["artifact_sha256"] == hashlib.sha256(content).hexdigest(), "Invalid payload pin"
    )
    uri = urlparse(record["artifact_uri"])
    require(
        uri.scheme == "mlflow-artifacts" and not any([uri.netloc, uri.query, uri.fragment]),
        "Expected local proxy artifact URI",
    )
    parts = PurePosixPath(uri.path).parts
    require(
        ".." not in parts and "%" not in uri.path and uri.path.startswith("/"),
        "Invalid artifact key",
    )
    key = "v1/" + uri.path.lstrip("/") + "/probe.json"
    response = s3_client().get_object(Bucket=BUCKET, Key=key)
    with response["Body"] as stream:
        body = stream.read(len(content) + 1)
    require(body == content, "Independent S3 readback differs")
    return {
        "bucket": BUCKET,
        "object_key": key,
        "artifact_sha256": hashlib.sha256(body).hexdigest(),
        "s3_object_verified": True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "write", "read", "verify-object"])
    parser.add_argument("--record", type=Path)
    args = parser.parse_args()
    # Keep SDK notices separate from the machine-readable result.
    with redirect_stdout(sys.stderr):
        if args.action == "prepare":
            result = prepare_bucket()
        elif args.action == "verify-object":
            if args.record is None:
                parser.error("verify-object requires --record")
            result = verify_object(json.loads(args.record.read_text()))
        else:
            from mlflow import MlflowClient

            client = MlflowClient(tracking_uri=TRACKING_URI)
            if args.action == "write":
                result = write_probe(client)
            else:
                if args.record is None:
                    parser.error("read requires --record")
                result = read_probe(client, json.loads(args.record.read_text()))
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
