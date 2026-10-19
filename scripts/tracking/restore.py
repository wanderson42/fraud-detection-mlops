"""Opt-in restore into disposable, isolated Compose resources; never restore into the source."""

import argparse
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
import time
from uuid import uuid4

from scripts.tracking import capture
from scripts.tracking import capture_worker as archive
from scripts.tracking import local_stack as stack

ROOT = Path(__file__).resolve().parents[2]
ACCEPTED = ROOT / "references/evidence/tracking_capture_author_validation_2026-10-19.json"
MODEL_EVIDENCE = "references/evidence/model_contribution_http_author_validation_2026-10-17.json"
IMAGE = "fraud-tracking-restore:lab-v1"


def build(root=ROOT):
    stack.command(
        [
            "docker",
            "build",
            "--file",
            str(root / "infra/tracking/Dockerfile.restore"),
            "--tag",
            IMAGE,
            str(root),
        ],
        timeout=900,
    )
    return {"status": "built", "image": IMAGE}


def control(root, directory, proof):
    """Freeze scores from the previously accepted producer output, before creating any target."""
    from examples import model_contribution as example
    import numpy as np
    import pandas as pd

    pin = json.loads((root / MODEL_EVIDENCE).read_bytes())
    archive.require(
        capture.sha(root / "examples/model_contribution.py")
        == pin["source_sha256"]["examples/model_contribution.py"],
        "Synthetic control producer code changed",
    )
    receipt = stack.checked(root / pin["receipt"])
    archive.require(
        receipt.is_file() and receipt.stat().st_size < 1024**2,
        "Accepted producer receipt unavailable",
    )
    native = json.loads(receipt.read_bytes())
    archive.require(
        native["status"] == "success"
        and native["model_sha256"] == pin["model_sha256"]
        and native["producer"]["tracking"]["model_uri"] == pin["model_uri"]
        and native["producer"]["tracking"]["run_id"] == pin["mlflow_run_id"],
        "Producer receipt differs from accepted pin",
    )
    output = stack.checked(
        root / "data/examples/contribution-http-50d616d89641404cbf25eb50e438515b"
    )
    predictions_path = stack.checked(output / "validation_predictions.parquet")
    metrics_path = stack.checked(output / "metrics.json")
    archive.require(
        predictions_path.stat().st_size <= 1024**2 and metrics_path.stat().st_size <= 1024**2,
        "Synthetic control files exceed budget",
    )
    objects = json.loads((directory / "objects.json").read_bytes())
    index = {row["key"]: row for row in objects}
    model_key = (
        "v1/3/models/" + pin["model_uri"].removeprefix("models:/") + "/artifacts/model.skops"
    )
    metrics_key = "v1/3/" + pin["mlflow_run_id"] + "/artifacts/metrics.json"
    archive.require(
        index[model_key]["sha256"] == pin["model_sha256"],
        "Captured model differs from accepted pin",
    )
    archive.require(
        index[metrics_key]["sha256"] == capture.sha(metrics_path),
        "Producer metrics differ from archived run",
    )
    features, labels, metadata = example.synthetic_batches()[1]
    predictions = pd.read_parquet(predictions_path)
    archive.require(
        predictions.TRANSACTION_ID.tolist() == metadata.TRANSACTION_ID.tolist()
        and predictions.TX_FRAUD.tolist() == labels.tolist(),
        "Synthetic control row identities differ",
    )
    scores = predictions.SCORE.to_numpy()
    archive.require(
        len(scores) == pin["evaluation_rows"]
        and np.isfinite(scores).all()
        and ((scores >= 0) & (scores <= 1)).all(),
        "Invalid saved control scores",
    )
    result = {
        "schema": "fraud_restore_model_control_v1",
        "model_uri": pin["model_uri"],
        "run_id": pin["mlflow_run_id"],
        "model_sha256": pin["model_sha256"],
        "columns": list(features.columns),
        "rows": features.to_numpy().tolist(),
        "scores": scores.tolist(),
        "native_receipt_sha256": capture.sha(receipt),
        "predictions_sha256": capture.sha(predictions_path),
        "expected_scores_source": "saved_predictions_from_author_accepted_producer_before_target_creation",
        "refit": False,
    }
    archive.save(proof / "control.json", result)
    return capture.sha(proof / "control.json")


def target_command(root, project, environment, *args):
    archive.require(
        bool(re.fullmatch(r"fraud-restore-[0-9a-f]{12}", project)), "Unmanaged restore project"
    )
    return [
        "docker",
        "compose",
        "--project-name",
        project,
        "--env-file",
        str(environment),
        "-f",
        str(root / "infra/tracking/restore.compose.yaml"),
        *args,
    ]


def inspect_target(root, project, environment, service, deadline):
    identities = (
        capture.run(
            target_command(root, project, environment, "ps", "-a", "-q", service), deadline
        )
        .decode()
        .strip()
    )
    archive.require(
        bool(re.fullmatch(r"[0-9a-f]{12,64}", identities)),
        "One target container per service required",
    )
    row = json.loads(capture.run(["docker", "inspect", identities], deadline))[0]
    labels = row["Config"]["Labels"]
    archive.require(
        labels.get("com.docker.compose.project") == project
        and labels.get("com.docker.compose.service") == service,
        "Refuse operation on another project/service",
    )
    archive.require(
        row["State"]["Running"] and row["State"].get("Health", {}).get("Status") == "healthy",
        "Target service is not healthy",
    )
    networks = set(row["NetworkSettings"]["Networks"])
    archive.require(
        networks == {project + "_default"}, "Target attached to an unexpected/source network"
    )
    mounts = [m for m in row["Mounts"] if m["Type"] != "tmpfs"]
    volume = project + ("_postgres-data" if service == "postgres" else "_rustfs-data")
    if service in {"postgres", "rustfs"}:
        volumes = [m for m in mounts if m["Type"] == "volume"]
        archive.require(
            len(volumes) == 1 and volumes[0]["Name"] == volume, "Unexpected target volume"
        )
        if service == "postgres":
            archive.require(row["Image"] == capture.PG_IMAGE, "Target PostgreSQL image differs")
            binds = [m for m in mounts if m["Type"] == "bind"]
            archive.require(
                len(mounts) == 2
                and len(binds) == 1
                and binds[0]["Source"] == str(root / "infra/tracking/init-postgres.sh")
                and binds[0]["Destination"] == "/docker-entrypoint-initdb.d/10-mlflow.sh"
                and not binds[0]["RW"]
                and volumes[0]["Destination"] == "/var/lib/postgresql/data",
                "Unexpected target PostgreSQL bind or volume destination",
            )
        else:
            archive.require(len(mounts) == 1, "Unexpected RustFS mount")
    elif service == "mlflow":
        archive.require(not mounts, "Target MLflow mounted local artifacts/state")
    return {
        "container_id": row["Id"],
        "image_id": row["Image"],
        "project": project,
        "networks": sorted(networks),
        "volume": volume if service in {"postgres", "rustfs"} else None,
    }


def configuration(root, directory, proof, environment, capture_id, manifest_hash):
    values = {
        "TARGET_PG_PASSWORD": secrets.token_hex(24),
        "TARGET_PG_ADMIN_PASSWORD": secrets.token_hex(24),
        "TARGET_S3_ACCESS_KEY": secrets.token_hex(10).upper(),
        "TARGET_S3_SECRET_KEY": secrets.token_hex(20),
        "CAPTURE_ID": capture_id,
        "CAPTURE_MANIFEST_SHA256": manifest_hash,
        "RESTORE_CAPTURE_DIRECTORY": str(directory),
        "RESTORE_PROOF_DIRECTORY": str(proof),
        "RESTORE_UID": str(os.getuid()),
        "RESTORE_GID": str(os.getgid()),
    }
    archive.require(
        all(
            '"' not in v and "\n" not in v and "$" not in v and "`" not in v
            for v in values.values()
        ),
        "Unsafe restore environment value",
    )
    with environment.open("x") as stream:
        os.chmod(environment, 0o600)
        stream.write("".join(k + '="' + v + '"\n' for k, v in values.items()))


def review_topology(root):
    import yaml

    config = yaml.safe_load((root / "infra/tracking/restore.compose.yaml").read_bytes())
    archive.require(
        config["networks"] == {"default": {"internal": True}}, "Private target network required"
    )
    archive.require(
        set(config["volumes"]) == {"postgres-data", "rustfs-data"}, "Only target volumes permitted"
    )
    archive.require(
        all(not definition for definition in config["volumes"].values()),
        "External/named volumes forbidden",
    )
    archive.require(
        set(config["services"]) == {"postgres", "rustfs", "mlflow", "seed", "client"},
        "Unexpected restore services",
    )
    for service in config["services"].values():
        archive.require(
            not any(
                k in service
                for k in ["networks", "network_mode", "extra_hosts", "env_file", "privileged"]
            ),
            "Unexpected service isolation override",
        )
    client = config["services"]["client"]
    archive.require(
        not any(k.startswith(("AWS_", "TARGET_PG", "POSTGRES_")) for k in client["environment"]),
        "Client credentials forbidden",
    )
    archive.require(
        len(client["volumes"]) == 1
        and client["volumes"][0]["read_only"]
        and client["volumes"][0]["source"] == "${RESTORE_PROOF_DIRECTORY}",
        "Client may mount only proof controls",
    )
    archive.require(
        config["services"]["mlflow"]["ports"] == ["127.0.0.1:15002:5000"],
        "Target endpoint must be isolated loopback",
    )


def run_restore(root=ROOT):
    accepted = json.loads((root / ACCEPTED.relative_to(ROOT)).read_bytes())
    identity, manifest_hash = accepted["capture_id"], accepted["manifest_sha256"]
    directory = capture.capture_directory(root, identity)
    capture.verify(directory, manifest_hash)
    review_topology(root)
    with stack.exclusive(root), capture.recover_on_term():
        run_id = uuid4().hex
        project = "fraud-restore-" + run_id[:12]
        workspace = stack.checked(stack.workspace(root) / "restores" / run_id)
        workspace.mkdir(parents=True, mode=0o700)
        proof = workspace / "proof"
        proof.mkdir(mode=0o700)
        environment = workspace / ".env"
        receipt = stack.workspace(root) / "receipts" / ("restore-" + run_id + ".json")
        result = {
            "schema": "fraud_tracking_restore_v1",
            "status": "failed",
            "project": project,
            "capture_id": identity,
            "manifest_sha256": manifest_hash,
            "report": str(receipt),
            "phase": "preflight",
            "source_services_modified": False,
            "refit": False,
            "primary_storage_migrated": False,
            "historical_sqlite_migrated": False,
            "project_wide_storage_inventory_complete": False,
            "temporary_resources_removed": False,
            "postgres_backup_restore_validated": False,
            "controller_code_sha256": capture.sha(Path(__file__)),
        }
        created = False
        origin = time.monotonic()
        deadline = origin + 600

        def command(*args, **kwargs):
            return capture.run(
                target_command(root, project, environment, *args), deadline, **kwargs
            )

        def worker(service, action):
            raw = command("run", "--rm", "--no-deps", service, action)
            value = json.loads(raw)
            archive.require(
                value["worker_code_sha256"]
                == capture.sha(root / "scripts/tracking/restore_worker.py"),
                "Rebuild restore image; worker code differs",
            )
            if value.get("status") == "failed":
                result["worker_failure"] = value
                print(
                    json.dumps({"worker_failure": value}, allow_nan=False),
                    file=sys.stderr,
                    flush=True,
                )
                archive.require(
                    False,
                    "Restore worker failed at "
                    + value.get("step", "unknown")
                    + ": "
                    + value.get("reason", "See preserved worker diagnosis"),
                )
            return value

        try:
            before = {s: stack.inspect_service(root, s) for s in ["postgres", "mlflow"]}
            result["source_before"] = before
            result["control_sha256"] = control(root, directory, proof)
            configuration(root, directory, proof, environment, identity, manifest_hash)
            with socket.socket() as port:
                port.bind(("127.0.0.1", 15002))
            archive.require(not command("ps", "-a", "-q").strip(), "Target project already exists")
            created = True  # The one-off check also creates a private network.
            worker("client", "check")
            result["phase"] = "create_target"
            command("up", "-d", "--wait", "--wait-timeout", "120", "postgres", "rustfs")
            resources = {
                s: inspect_target(root, project, environment, s, deadline)
                for s in ["postgres", "rustfs"]
            }
            result["target_resources"] = resources
            archive.require(
                resources["postgres"]["container_id"] != before["postgres"]["container_id"],
                "Target/source database identities overlap",
            )
            result["phase"] = "restore_postgres"
            # --clean is restricted to the newly created, inspected target container.
            capture.run(
                [
                    "docker",
                    "exec",
                    "-i",
                    resources["postgres"]["container_id"],
                    "pg_restore",
                    "--exit-on-error",
                    "--single-transaction",
                    "--clean",
                    "--if-exists",
                    "-U",
                    "mlflow",
                    "-d",
                    "mlflow_lab",
                    "--no-password",
                ],
                deadline,
                input_path=directory / "postgres.dump",
                limit=4 * 1024**2,
            )
            result["phase"] = "restore_objects_and_verify_database"
            result["restore"] = worker("seed", "seed")
            archive.require(
                result["restore"]["status"] == "restored_verified",
                "Restore worker did not verify target",
            )
            result["phase"] = "model_and_api_readback"
            command("up", "-d", "--wait", "--wait-timeout", "120", "mlflow")
            resources["mlflow"] = inspect_target(root, project, environment, "mlflow", deadline)
            result["client"] = worker("client", "client")
            archive.require(
                result["client"]["status"] == "client_verified"
                and result["client"]["model_reload_validated"],
                "Independent model client failed",
            )
            command("stop", "--timeout", "10", "mlflow")
            result["after_api_readback"] = worker("seed", "verify")
            archive.require(
                result["after_api_readback"]["status"] == "target_unchanged",
                "API reads changed restored data",
            )
            result["source_after"] = {s: stack.inspect_service(root, s) for s in before}
            archive.require(
                result["source_after"] == before, "Source container identity/health changed"
            )
            capture.verify(directory, manifest_hash)
            result["capture_unchanged"] = True
            result["postgres_backup_restore_validated"] = True
            result["phase"] = "verified"
        except BaseException as exc:
            result["error_type"] = type(exc).__name__
            result["reason"] = (
                str(exc)
                if isinstance(exc, archive.CaptureError)
                else "Inspect the phase; private command details withheld"
            )
            raise
        finally:
            try:
                if created:
                    capture.run(
                        target_command(
                            root, project, environment, "down", "--volumes", "--remove-orphans"
                        ),
                        time.monotonic() + 120,
                    )
                    remaining = capture.run(
                        [
                            "docker",
                            "ps",
                            "-aq",
                            "--filter",
                            "label=com.docker.compose.project=" + project,
                        ],
                        time.monotonic() + 20,
                    )
                    volumes = capture.run(
                        [
                            "docker",
                            "volume",
                            "ls",
                            "-q",
                            "--filter",
                            "label=com.docker.compose.project=" + project,
                        ],
                        time.monotonic() + 20,
                    )
                    networks = capture.run(
                        [
                            "docker",
                            "network",
                            "ls",
                            "-q",
                            "--filter",
                            "label=com.docker.compose.project=" + project,
                        ],
                        time.monotonic() + 20,
                    )
                    archive.require(
                        not remaining.strip() and not volumes.strip() and not networks.strip(),
                        "Temporary resources remain; inspect dedicated project",
                    )
                    result["temporary_resources_removed"] = True
                if result["phase"] == "verified" and result["temporary_resources_removed"]:
                    result["status"] = "success"
            except BaseException as exc:
                result["cleanup_error_type"] = type(exc).__name__
                result["postgres_backup_restore_validated"] = False
                raise
            finally:
                if result["temporary_resources_removed"] or not created:
                    environment.unlink(missing_ok=True)
                result["temporary_credentials_removed"] = not environment.exists()
                result["elapsed_seconds"] = time.monotonic() - origin
                stack.save_json(receipt, result)
                print("Relatório: " + str(receipt), file=sys.stderr, flush=True)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["build", "run"])
    args = parser.parse_args()
    try:
        result = build() if args.action == "build" else run_restore()
    except (OSError, ValueError, RuntimeError, KeyError, subprocess.SubprocessError) as exc:
        print(
            "Tracking restore stopped (" + type(exc).__name__ + "); inspect preserved receipt",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
