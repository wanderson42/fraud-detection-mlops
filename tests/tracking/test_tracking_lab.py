"""Offline lab control tests: protect existing state and reject incomplete persistence proofs."""

from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
from scripts.tracking import local_stack as stack
from scripts.tracking import probe
import yaml


@pytest.fixture
def lab(tmp_path):
    for folder in ["infra/observability", "infra/tracking"]:
        (tmp_path / folder).mkdir(parents=True)
    (tmp_path / "infra/observability/.env").write_text("SECRET=do-not-read\n")
    stack.initialize(tmp_path)
    return tmp_path


def test_private_init_preserves_existing_credentials_and_missing_base(tmp_path, lab):
    env = lab / "infra/tracking/.env"
    original = env.read_bytes()
    assert env.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        stack.initialize(lab)
    assert env.read_bytes() == original
    missing = tmp_path / "missing"
    with pytest.raises(ValueError, match="observability"):
        stack.initialize(missing)
    assert not missing.exists()


def test_workspace_rejects_redirected_directory(tmp_path):
    (tmp_path / "data/storage").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "data/storage/tracking-lab").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="Symlinks"):
        stack.workspace(tmp_path)
    assert list(outside.iterdir()) == []


def test_compose_project_is_explicit_and_down_preserves_volumes(lab):
    args = stack.compose(lab, "down")
    assert args[args.index("--project-name") + 1] == stack.PROJECT
    assert "--volumes" not in args and "-v" not in args
    assert args.count("--env-file") == 2


def test_command_error_does_not_include_captured_credentials(monkeypatch):
    def fail(*a, **k):
        raise subprocess.CalledProcessError(
            1, ["docker"], stderr="password-secret", output="secret"
        )

    monkeypatch.setattr(stack.subprocess, "run", fail)
    with pytest.raises(RuntimeError) as result:
        stack.command(["docker"], capture=True)
    assert "secret" not in str(result.value)


def snapshot(service, identity="a" * 64):
    env = (
        ["POSTGRES_DB=mlflow_lab"]
        if service == "postgres"
        else [
            "MLFLOW_BACKEND_STORE_URI=postgresql+psycopg://mlflow:private@postgres:5432/mlflow_lab",
            "MLFLOW_S3_ENDPOINT_URL=http://rustfs:9000",
            "MLFLOW_SERVER_ENABLE_JOB_EXECUTION=false",
        ]
    )
    return {
        "Config": {
            "Labels": {
                "com.docker.compose.project": stack.PROJECT,
                "com.docker.compose.service": service,
            },
            "Env": env,
            "Cmd": ["mlflow", "server", "--artifacts-destination", "s3://fraud-tracking-lab/v1"],
        },
        "State": {"Running": True, "Health": {"Status": "healthy"}},
        "Mounts": [
            {"Type": "volume", "Name": stack.VOLUME, "Destination": "/var/lib/postgresql/data"}
        ]
        if service == "postgres"
        else [],
        "Id": identity,
        "Image": "sha256:" + "c" * 64,
    }


@pytest.mark.parametrize(
    "bad", ["project", "unhealthy", "volume", "backend", "local_bind", "bucket"]
)
def test_preflight_rejects_unsafe_or_wrong_services(lab, monkeypatch, bad):
    service = "postgres" if bad == "volume" else "mlflow"
    item = snapshot(service)
    if bad == "project":
        item["Config"]["Labels"]["com.docker.compose.project"] = "historical"
    if bad == "unhealthy":
        item["State"]["Health"]["Status"] = "unhealthy"
    if bad == "volume":
        item["Mounts"][0]["Name"] = "historical-data"
    if bad == "backend":
        item["Config"]["Env"][0] = "MLFLOW_BACKEND_STORE_URI=sqlite:///mlflow.db"
    if bad == "local_bind":
        item["Mounts"] = [{"Type": "bind", "Source": "/old/tracking", "Destination": "/tracking"}]
    if bad == "bucket":
        item["Config"]["Cmd"][-1] = "s3://fraud-artifacts"
    calls = []

    def command(args, **kwargs):
        calls.append(args)
        return json.dumps([item]) if args[:2] == ["docker", "inspect"] else "a" * 64

    monkeypatch.setattr(stack, "command", command)
    with pytest.raises(ValueError):
        stack.inspect_service(lab, service)
    assert not any("up" in args or "stop" in args for args in calls)


@pytest.mark.parametrize("service", ["postgres", "mlflow"])
def test_preflight_accepts_only_the_declared_lab_mounts(lab, monkeypatch, service):
    item = snapshot(service)
    if service == "postgres":
        item["Mounts"].append(
            {
                "Type": "bind",
                "Source": str(lab / "infra/tracking/init-postgres.sh"),
                "Destination": "/docker-entrypoint-initdb.d/10-mlflow.sh",
                "RW": False,
            }
        )
    monkeypatch.setattr(
        stack,
        "command",
        lambda args, **kwargs: (
            json.dumps([item]) if args[:2] == ["docker", "inspect"] else "a" * 64
        ),
    )
    result = stack.inspect_service(lab, service)
    assert result["container_id"] == "a" * 64
    assert result["image_id"] == item["Image"]


def record():
    identifier = "1" * 32
    return {
        "worker_code_sha256": hashlib.sha256(Path(probe.__file__).read_bytes()).hexdigest(),
        "mlflow_version": "3.17.0",
        "schema": "fraud_tracking_probe_v1",
        "scope": probe.SCOPE,
        "probe_id": identifier,
        "run_id": "2" * 32,
        "experiment_id": "1",
        "artifact_uri": "mlflow-artifacts:/1/" + "2" * 32 + "/artifacts",
        "artifact_sha256": hashlib.sha256(probe.payload(identifier)).hexdigest(),
        "metadata_verified": True,
        "metric_history_rows": 2,
        "artifact_verified": True,
    }


@pytest.mark.parametrize(
    "failure", [None, "write", "sql", "s3", "recreation", "read", "same_container"]
)
def test_rehearsal_needs_all_proofs_and_preserves_failure_evidence(lab, monkeypatch, failure):
    current = record()
    counter = {"inspections": 0}
    calls = []

    def inspect(root, service):
        counter["inspections"] += 1
        after = counter["inspections"] > 2
        return {
            "image_id": "sha256:" + "c" * 64,
            "container_id": ("b" if after and failure != "same_container" else "a") * 64,
            "postgres_volume": stack.VOLUME if service == "postgres" else None,
        }

    monkeypatch.setattr(stack, "inspect_service", inspect)

    def worker(root, service, action, path=None):
        if action == failure:
            raise ValueError("injected")
        if action == "verify-object":
            return {
                "s3_object_verified": failure != "s3",
                "artifact_sha256": current["artifact_sha256"],
            }
        return deepcopy(current)

    monkeypatch.setattr(stack, "worker", worker)

    def sql(*a):
        if failure == "sql":
            raise ValueError("SQL differs")
        return {"verified": True}

    monkeypatch.setattr(stack, "sql_readback", sql)

    def command(args, **k):
        calls.append(args)
        if failure == "recreation" and "--force-recreate" in args:
            raise RuntimeError("injected")
        return ""

    monkeypatch.setattr(stack, "command", command)
    if failure:
        with pytest.raises((RuntimeError, ValueError)):
            stack.rehearse(lab)
    else:
        result = stack.rehearse(lab)
        assert (
            result["status"] == "success" and result["postgres_verified"] and result["s3_verified"]
        )
    receipts = list((stack.workspace(lab) / "receipts").glob("*.json"))
    assert len(receipts) == 1 and receipts[0].stat().st_mode & 0o777 == 0o600
    saved = json.loads(receipts[0].read_text())
    assert saved["status"] == ("failed" if failure else "success")
    assert not list((stack.workspace(lab) / "rehearsals").glob("probe-*"))
    assert "private" not in receipts[0].read_text()
    assert all("--volumes" not in args for args in calls)
    if failure in {"write", "sql", "s3"}:
        assert not any("--force-recreate" in args for args in calls)


def test_independent_s3_pin_and_key_are_verified(monkeypatch):
    data = record()
    requests = []

    def get_object(**kwargs):
        requests.append(kwargs)
        return {"Body": io.BytesIO(probe.payload(data["probe_id"]))}

    monkeypatch.setattr(probe, "s3_client", lambda: SimpleNamespace(get_object=get_object))
    result = probe.verify_object(data)
    assert result["s3_object_verified"]
    assert requests == [
        {"Bucket": "fraud-tracking-lab", "Key": "v1/1/" + "2" * 32 + "/artifacts/probe.json"}
    ]
    data["artifact_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="pin"):
        probe.verify_object(data)
    assert len(requests) == 1


@pytest.mark.parametrize(
    "uri", ["file:///old", "mlflow-artifacts://elsewhere/key", "mlflow-artifacts:/../old"]
)
def test_s3_invalid_locations_fail_before_contact(monkeypatch, uri):
    data = record()
    data["artifact_uri"] = uri
    monkeypatch.setattr(probe, "s3_client", lambda: pytest.fail("Storage contacted"))
    with pytest.raises(ValueError):
        probe.verify_object(data)


def test_sql_is_read_only_scoped_and_rejects_untrusted_ids(lab, monkeypatch):
    data = record()
    calls = []
    expected = {
        "role_is_superuser": False,
        "runs": 1,
        "params": 1,
        "metrics": 2,
        "artifact_uri": data["artifact_uri"],
        "probe_id": data["probe_id"],
    }
    monkeypatch.setattr(
        stack, "command", lambda args, **k: calls.append(args) or json.dumps(expected)
    )
    assert stack.sql_readback(lab, data) == expected
    assert "BEGIN READ ONLY;" in calls[0][-1] and "-q" in calls[0]
    data["run_id"] = "x'; DELETE FROM runs;"
    with pytest.raises(ValueError):
        stack.sql_readback(lab, data)
    assert len(calls) == 1


def test_exclusive_rehearsal_rejects_overlap(lab):
    with stack.exclusive(lab), pytest.raises(ValueError, match="Another"), stack.exclusive(lab):
        pytest.fail("Overlapping rehearsal entered")


def test_compose_contract_has_no_historical_mounts_or_client_credentials():
    root = Path(__file__).resolve().parents[2]
    config = yaml.safe_load((root / "infra/tracking/compose.yaml").read_text())
    assert config["name"] == stack.PROJECT
    assert set(config["volumes"]) == {"postgres-data"}
    services = config["services"]
    assert "ports" not in services["postgres"]
    assert services["mlflow"]["ports"] == ["127.0.0.1:15001:5000"]
    assert not any(
        k.startswith(("AWS_", "POSTGRES_", "MLFLOW_BACKEND"))
        for k in services["probe"].get("environment", {})
    )
    for service in ["probe", "storage-check"]:
        mount = services[service]["volumes"][0]
        assert mount["source"] == "../../data/storage/tracking-lab" and mount["read_only"]
    assert config["networks"]["objects"]["external"]
    lock = (root / "infra/tracking/requirements.txt").read_text()
    assert "mlflow==3.17.0" in lock and "--hash=sha256:" in lock


@pytest.mark.parametrize("failed", [False, True])
def test_probe_cli_separates_sdk_notices_from_json(monkeypatch, capsys, failed):
    def client(**kwargs):
        print("SDK client banner")
        return object()

    def write(instance):
        sys.stdout.write("🏃 View run persistence-probe at: http://mlflow:5000/run\n")
        print("🧪 View experiment at: http://mlflow:5000/experiment")
        if failed:
            raise ValueError("Injected SDK failure")
        return {"status": "success"}

    monkeypatch.setitem(sys.modules, "mlflow", SimpleNamespace(MlflowClient=client))
    monkeypatch.setattr(probe, "write_probe", write)
    monkeypatch.setattr(sys, "argv", ["probe", "write"])
    if failed:
        with pytest.raises(ValueError, match="Injected"):
            probe.main()
    else:
        probe.main()
    captured = capsys.readouterr()
    assert "SDK client banner" in captured.err
    assert "View run" in captured.err
    assert "View experiment" in captured.err
    if failed:
        assert captured.out == ""
    else:
        assert json.loads(captured.out) == {"status": "success"}


@pytest.mark.parametrize("raw", ["banner\n{}", "{}\n{}", ""])
def test_worker_keeps_rejecting_contaminated_or_incomplete_stdout(lab, monkeypatch, raw):
    monkeypatch.setattr(stack, "command", lambda *args, **kwargs: raw)
    with pytest.raises(ValueError, match="JSON result"):
        stack.worker(lab, "probe", "write")
