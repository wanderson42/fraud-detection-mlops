"""Offline isolation/failure/cleanup contracts for the disposable restore controller."""

from copy import deepcopy
import json
from pathlib import Path

import pytest
from scripts.tracking import capture
from scripts.tracking import capture_worker as archive
from scripts.tracking import local_stack as stack
from scripts.tracking import restore as job
import yaml

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def harness(tmp_path, monkeypatch):
    evidence = tmp_path / job.ACCEPTED.relative_to(job.ROOT)
    evidence.parent.mkdir(parents=True)
    evidence.write_text(json.dumps({"capture_id": "a" * 32, "manifest_sha256": "b" * 64}))
    (tmp_path / "scripts/tracking").mkdir(parents=True)
    (tmp_path / "scripts/tracking/restore_worker.py").write_text("fixture")
    calls = []
    monkeypatch.setattr(capture, "verify", lambda *a: {"status": "verified_locally"})
    monkeypatch.setattr(job, "review_topology", lambda *a: None)
    monkeypatch.setattr(job, "control", lambda root, directory, proof: "c" * 64)

    class Port:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def bind(self, address):
            assert address == ("127.0.0.1", 15002)

    monkeypatch.setattr(job.socket, "socket", Port)
    source = {
        name: {
            "container_id": name + "-original",
            "image_id": "pinned",
            "postgres_volume": "original-volume",
        }
        for name in ["postgres", "mlflow"]
    }
    monkeypatch.setattr(stack, "inspect_service", lambda root, service: deepcopy(source[service]))
    monkeypatch.setattr(
        job,
        "inspect_target",
        lambda root, project, env, service, deadline: {
            "container_id": "new-" + service,
            "project": project,
        },
    )

    def run(args, deadline, **kwargs):
        calls.append((args, kwargs))
        if "run" in args:
            action = args[-1]
            statuses = {
                "check": "ready",
                "seed": "restored_verified",
                "client": "client_verified",
                "verify": "target_unchanged",
            }
            return archive.encoded(
                {
                    "status": statuses[action],
                    "model_reload_validated": action == "client",
                    "worker_code_sha256": capture.sha(
                        tmp_path / "scripts/tracking/restore_worker.py"
                    ),
                }
            )
        return b""

    monkeypatch.setattr(capture, "run", run)
    return tmp_path, calls


def test_restore_success_is_isolated_cleans_target_and_preserves_source(harness):
    root, calls = harness
    result = job.run_restore(root)
    assert result["status"] == "success" and result["postgres_backup_restore_validated"]
    assert result["source_before"] == result["source_after"]
    assert result["temporary_resources_removed"] and result["temporary_credentials_removed"]
    assert result["capture_unchanged"] and not result["primary_storage_migrated"]
    for args, _ in calls:
        if "--project-name" in args:
            assert args[args.index("--project-name") + 1] == result["project"]
            assert "infra/observability/.env" not in " ".join(args)
    restore = next(args for args, _ in calls if "pg_restore" in args)
    assert restore[3] == "new-postgres" and "--single-transaction" in restore
    assert any("down" in args and "--volumes" in args for args, _ in calls)
    receipt = Path(result["report"])
    assert receipt.stat().st_mode & 0o777 == 0o600
    assert not list((stack.workspace(root) / "restores").rglob(".env"))


@pytest.mark.parametrize(
    "stage",
    ["check", "up", "pg_restore", "seed", "client", "verify", "source", "cleanup", "keyboard"],
)
def test_failures_have_no_success_and_only_clean_own_project(harness, monkeypatch, stage):
    root, calls = harness
    original = capture.run

    def run(args, *a, **kw):
        fail = (
            stage == "source"
            and False
            or stage == "keyboard"
            and "pg_restore" in args
            or stage == "cleanup"
            and "down" in args
            or stage in {"up", "pg_restore"}
            and stage in args
            or stage in {"check", "seed", "client", "verify"}
            and "run" in args
            and args[-1] == stage
        )
        if fail:
            calls.append((args, kw))
            raise KeyboardInterrupt() if stage == "keyboard" else RuntimeError("secret-sdk-url")
        return original(args, *a, **kw)

    monkeypatch.setattr(capture, "run", run)
    if stage == "source":
        n = [0]

        def inspect(root, name):
            n[0] += 1
            return {"container_id": "original" if n[0] <= 2 else "changed"}

        monkeypatch.setattr(stack, "inspect_service", inspect)
    with pytest.raises((RuntimeError, ValueError, KeyboardInterrupt)):
        job.run_restore(root)
    result = json.loads(
        next((stack.workspace(root) / "receipts").glob("restore-*.json")).read_bytes()
    )
    assert result["status"] == "failed"
    assert "secret-sdk-url" not in json.dumps(result)
    assert result["temporary_credentials_removed"] == (stage != "cleanup")
    assert any(
        "down" in args and args[args.index("--project-name") + 1] == result["project"]
        for args, _ in calls
    )
    if stage == "cleanup":
        assert not result["postgres_backup_restore_validated"]


@pytest.mark.parametrize(
    "name", ["fraud-tracking-lab", "", "../x", "fraud-restore-ABC", "fraud-restore-" + "a" * 13]
)
def test_target_names_cannot_address_source(tmp_path, name):
    with pytest.raises(ValueError):
        job.target_command(tmp_path, name, tmp_path / ".env", "down", "--volumes")


def test_real_topology_has_no_source_network_volume_or_client_credentials(tmp_path):
    job.review_topology(ROOT)
    path = tmp_path / "infra/tracking/restore.compose.yaml"
    path.parent.mkdir(parents=True)
    original = yaml.safe_load((ROOT / "infra/tracking/restore.compose.yaml").read_text())
    for case in [
        "external_network",
        "external_volume",
        "source_network",
        "client_credentials",
        "client_mount",
        "port",
    ]:
        doc = deepcopy(original)
        if case == "external_network":
            doc["networks"]["objects"] = {"external": True}
        if case == "external_volume":
            doc["volumes"]["postgres-data"] = {"name": stack.VOLUME}
        if case == "source_network":
            doc["services"]["seed"]["network_mode"] = "host"
        if case == "client_credentials":
            doc["services"]["client"]["environment"]["AWS_ACCESS_KEY_ID"] = "secret"
        if case == "client_mount":
            doc["services"]["client"]["volumes"][0]["source"] = "../../data/tracking"
        if case == "port":
            doc["services"]["mlflow"]["ports"] = ["15001:5000"]
        path.write_text(yaml.safe_dump(doc))
        with pytest.raises(ValueError):
            job.review_topology(tmp_path)


def test_credentials_are_private_not_host_environment(tmp_path):
    path = tmp_path / ".env"
    job.configuration(tmp_path, tmp_path / "capture", tmp_path / "proof", path, "a" * 32, "b" * 64)
    assert path.stat().st_mode & 0o777 == 0o600
    assert "TARGET_PG_PASSWORD" in path.read_text()
    values = dict(line.split("=", 1) for line in path.read_text().splitlines())
    assert len(values["TARGET_S3_ACCESS_KEY"].strip('"')) == 20
    assert len(values["TARGET_S3_SECRET_KEY"].strip('"')) == 40
    with pytest.raises(FileExistsError):
        job.configuration(tmp_path, tmp_path, tmp_path, path, "a", "b")


def test_control_freezes_accepted_predictions_before_target_and_rejects_mismatch(
    tmp_path, monkeypatch
):
    from examples import model_contribution as example
    import numpy as np
    from sklearn.pipeline import Pipeline

    pin = json.loads((ROOT / job.MODEL_EVIDENCE).read_bytes())
    path = tmp_path / job.MODEL_EVIDENCE
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(pin))
    source = tmp_path / "examples/model_contribution.py"
    source.parent.mkdir()
    source.write_bytes((ROOT / "examples/model_contribution.py").read_bytes())
    receipt = tmp_path / pin["receipt"]
    receipt.parent.mkdir(parents=True)
    receipt.write_text(
        json.dumps(
            {
                "status": "success",
                "model_sha256": pin["model_sha256"],
                "producer": {
                    "tracking": {"model_uri": pin["model_uri"], "run_id": pin["mlflow_run_id"]}
                },
            }
        )
    )
    output = tmp_path / "data/examples/contribution-http-50d616d89641404cbf25eb50e438515b"
    output.mkdir(parents=True)
    _, labels, metadata = example.synthetic_batches()[1]
    metadata.assign(SCORE=np.where(labels, 0.9, 0.1)).to_parquet(
        output / "validation_predictions.parquet"
    )
    (output / "metrics.json").write_text('{"scope":"synthetic"}')
    directory = tmp_path / "capture"
    directory.mkdir()
    archive.save(
        directory / "objects.json",
        [
            {
                "key": "v1/3/models/"
                + pin["model_uri"].removeprefix("models:/")
                + "/artifacts/model.skops",
                "sha256": pin["model_sha256"],
            },
            {
                "key": "v1/3/" + pin["mlflow_run_id"] + "/artifacts/metrics.json",
                "sha256": capture.sha(output / "metrics.json"),
            },
        ],
    )
    proof = tmp_path / "proof"
    proof.mkdir()
    monkeypatch.setattr(
        Pipeline, "fit", lambda *a, **kw: pytest.fail("No refit for recovery control")
    )
    digest = job.control(tmp_path, directory, proof)
    frozen = json.loads((proof / "control.json").read_bytes())
    assert digest == capture.sha(proof / "control.json")
    assert frozen["model_sha256"] == pin["model_sha256"] and len(frozen["scores"]) == 20
    assert not frozen["refit"]
    (output / "metrics.json").write_text("changed")
    with pytest.raises(ValueError, match="metrics differ"):
        job.control(tmp_path, directory, proof)


@pytest.mark.parametrize("bad", ["label", "network", "volume", "image"])
def test_target_inspection_blocks_original_or_unreviewed_resources(tmp_path, monkeypatch, bad):
    project = "fraud-restore-" + "a" * 12
    row = {
        "Id": "a" * 64,
        "State": {"Running": True, "Health": {"Status": "healthy"}},
        "Image": capture.PG_IMAGE,
        "Config": {
            "Labels": {
                "com.docker.compose.project": project,
                "com.docker.compose.service": "postgres",
            }
        },
        "NetworkSettings": {"Networks": {project + "_default": {}}},
        "Mounts": [
            {
                "Type": "volume",
                "Name": project + "_postgres-data",
                "Destination": "/var/lib/postgresql/data",
            },
            {
                "Type": "bind",
                "Source": str(tmp_path / "infra/tracking/init-postgres.sh"),
                "Destination": "/docker-entrypoint-initdb.d/10-mlflow.sh",
                "RW": False,
            },
        ],
    }
    if bad == "label":
        row["Config"]["Labels"]["com.docker.compose.project"] = stack.PROJECT
    if bad == "network":
        row["NetworkSettings"]["Networks"][stack.PROJECT + "_default"] = {}
    if bad == "volume":
        row["Mounts"][0]["Name"] = stack.VOLUME
    if bad == "image":
        row["Image"] = "unexpected"
    monkeypatch.setattr(
        capture,
        "run",
        lambda args, *a, **kw: (
            json.dumps([row]).encode() if args[:2] == ["docker", "inspect"] else b"aaaaaaaaaaaa"
        ),
    )
    with pytest.raises(ValueError):
        job.inspect_target(tmp_path, project, tmp_path / ".env", "postgres", 999999999)


def test_structured_worker_failure_is_preserved_in_host_receipt(harness, monkeypatch):
    root, _calls = harness
    original = capture.run

    def run(args, *a, **kw):
        if "run" in args and args[-1] == "seed":
            return archive.encoded(
                {
                    "status": "failed",
                    "step": "postgres_comparison",
                    "reason": "Restored metadata differs",
                    "error_type": "CaptureError",
                    "details": {"metadata_difference": {"tables": {"changed_names": ["metrics"]}}},
                    "worker_code_sha256": capture.sha(root / "scripts/tracking/restore_worker.py"),
                }
            )
        return original(args, *a, **kw)

    monkeypatch.setattr(capture, "run", run)
    with pytest.raises(ValueError, match="postgres_comparison"):
        job.run_restore(root)
    result = json.loads(
        next((stack.workspace(root) / "receipts").glob("restore-*.json")).read_bytes()
    )
    assert result["status"] == "failed" and not result["postgres_backup_restore_validated"]
    assert result["worker_failure"]["details"]["metadata_difference"]["tables"][
        "changed_names"
    ] == ["metrics"]
    assert result["temporary_resources_removed"] and result["temporary_credentials_removed"]
