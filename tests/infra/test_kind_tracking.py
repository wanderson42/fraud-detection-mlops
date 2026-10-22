"""Ownership, credential boundaries and real Helm rendering without a cluster."""

import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest
from scripts.platform import kind_foundation as f
from scripts.platform import kind_tracking as k
import yaml

from fraud_detection_mlops.storage import managed_paths

ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "infra/helm/fraud-tracking"
OWNER = "a" * 32
PASSWORD = "b" * 48


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.setattr(f, "ROOT", tmp_path)
    monkeypatch.setattr(managed_paths, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(f, "owner", lambda: {"node_uid": "node-owned"})
    monkeypatch.setattr(f, "workspace", lambda: tmp_path / "data/storage/kind-platform")
    k.work().mkdir(parents=True, mode=0o700)
    f.private_json(k.work() / "credentials.json", {"owner_id": OWNER, "password": PASSWORD})
    return tmp_path


@pytest.fixture(scope="module")
def helm():
    binary = shutil.which("helm")
    if binary is None:
        pytest.skip("Helm 4.3.x needed; run kind_tracking validate natively")
    return binary


def render(helm, extra=(), ok=True):
    result = subprocess.run(
        [
            helm,
            "template",
            "fraud-tracking",
            str(CHART),
            "--namespace",
            "fraud-platform",
            "--kube-version",
            "1.35.8",
            *extra,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert (result.returncode == 0) == ok, result.stderr
    return list(yaml.safe_load_all(result.stdout)) if ok else []


def test_real_chart_security_backend_and_proxy(helm):
    subprocess.run(
        [helm, "lint", "--strict", str(CHART), "--kube-version", "1.35.8"],
        check=True,
        capture_output=True,
    )
    service, dep = render(helm)
    assert service["kind"] == "Service" and service["spec"]["type"] == "ClusterIP"
    assert dep["kind"] == "Deployment" and dep["spec"]["replicas"] == 1
    assert dep["spec"]["strategy"]["type"] == "Recreate"
    spec = dep["spec"]["template"]["spec"]
    assert not spec["automountServiceAccountToken"]
    assert spec["securityContext"]["runAsNonRoot"]
    assert spec["securityContext"]["seccompProfile"]["type"] == "RuntimeDefault"
    container = spec["containers"][0]
    assert container["imagePullPolicy"] == "Never"
    assert container["securityContext"] == {
        "allowPrivilegeEscalation": False,
        "readOnlyRootFilesystem": True,
        "capabilities": {"drop": ["ALL"]},
    }
    assert {"startupProbe", "readinessProbe", "livenessProbe"} <= container.keys()
    for probe in ["startupProbe", "readinessProbe", "livenessProbe"]:
        assert container[probe]["httpGet"]["httpHeaders"] == [
            {"name": "Host", "value": "127.0.0.1:5000"}
        ]
    assert {"limits", "requests"} == container["resources"].keys()
    env = {e["name"]: e for e in container["env"]}
    for name in ("MLFLOW_BACKEND_STORE_URI", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        assert "value" not in env[name]
        assert env[name]["valueFrom"]["secretKeyRef"]["name"] == k.SECRET
    assert "--serve-artifacts" in container["args"]
    assert "s3://fraud-kind-mlflow/v1" in container["args"]
    assert spec["volumes"] == [{"name": "tmp", "emptyDir": {"sizeLimit": "512Mi"}}]
    assert "hostPath" not in json.dumps(spec)
    assert "persistentVolumeClaim" not in json.dumps(spec)
    assert env["MLFLOW_S3_ENDPOINT_URL"]["value"] == "http://fraud-platform-rustfs:9000"


@pytest.mark.parametrize(
    "value",
    [
        "image=latest",
        "ownerId=invalid",
        "existingSecret=foreign",
        "unknown=true",
        "resources.limits.password=x",
    ],
)
def test_real_schema_refuses_unsupported_values(helm, value):
    render(helm, ["--set-string", value], ok=False)


def test_initialize_is_idempotent_and_no_rotation(local):
    before = (k.work() / "credentials.json").read_bytes()
    assert k.initialize()["status"] == "tracking_initialized"
    assert (k.work() / "credentials.json").read_bytes() == before


def test_private_credentials_required(local):
    (k.work() / "credentials.json").chmod(0o644)
    with pytest.raises(ValueError, match="mode 600"):
        k.credentials()


@pytest.mark.parametrize(
    "change", [{"owner_id": "x" * 32}, {"password": "x" * 48}, {"foreign": "value"}]
)
def test_credential_shape_and_hex_validation(local, change):
    path = k.work() / "credentials.json"
    data = json.loads(path.read_text())
    data.update(change)
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        k.credentials()


def test_initialize_refuses_incomplete_workspace(local):
    (k.work() / "credentials.json").unlink()
    (k.work() / "foreign.json").write_text("{}")
    with pytest.raises(ValueError, match="Incomplete"):
        k.initialize()


def role():
    return {
        "comment": "fraud-kind-tracking:" + OWNER,
        "superuser": False,
        "createdb": False,
        "createrole": False,
        "replication": False,
        "bypassrls": False,
        "login": True,
    }


def test_database_bootstrap_uses_restricted_separate_role_and_database(local, monkeypatch):
    calls = []

    def sql(text):
        calls.append(text)
        return json.dumps({"role": None, "database": None}) if len(calls) == 1 else ""

    monkeypatch.setattr(k, "sql", sql)
    k.prepare_database()
    assert "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS" in calls[1]
    assert PASSWORD in calls[1]
    assert calls[2].startswith("CREATE DATABASE mlflow_kind OWNER mlflow_kind;\n")
    assert "REVOKE ALL ON DATABASE mlflow_kind FROM PUBLIC" in calls[2]


@pytest.mark.parametrize(
    "field",
    ["comment", "superuser", "createdb", "createrole", "replication", "bypassrls", "login"],
)
def test_database_refuses_foreign_or_privileged_role(local, monkeypatch, field):
    current = role()
    current[field] = "foreign" if field == "comment" else not current[field]
    monkeypatch.setattr(k, "sql", lambda _: json.dumps({"role": current, "database": None}))
    with pytest.raises(ValueError, match="restricted tracking role"):
        k.prepare_database()


def test_database_idempotence_does_not_rotate_password(local, monkeypatch):
    calls = []

    def sql(text):
        calls.append(text)
        return json.dumps(
            {
                "role": role(),
                "database": {"comment": "fraud-kind-tracking:" + OWNER, "owner": k.ROLE},
            }
        )

    monkeypatch.setattr(k, "sql", sql)
    k.prepare_database()
    assert len(calls) == 1 and PASSWORD not in calls[0]


def test_database_refuses_foreign_database(local, monkeypatch):
    monkeypatch.setattr(
        k,
        "sql",
        lambda _: json.dumps(
            {"role": role(), "database": {"comment": "foreign", "owner": "other"}}
        ),
    )
    with pytest.raises(ValueError, match="database is not owned"):
        k.prepare_database()


def test_secret_stdin_only_and_idempotent_collision_refusal(local, monkeypatch):
    monkeypatch.setattr(
        f, "credentials", lambda: {"rustfs-access-key": "c" * 24, "rustfs-secret-key": "d" * 48}
    )
    calls = []
    current = None

    def kube(*args, data=None, **kwargs):
        calls.append((args, data))
        return SimpleNamespace(stdout=json.dumps(current) if args[0] == "get" else "")

    monkeypatch.setattr(f, "kube", kube)
    k.ensure_secret()
    assert calls[1][0] == ("create", "-f", "-")
    current = json.loads(calls[1][1])
    calls.clear()
    k.ensure_secret()
    assert len(calls) == 1
    current["metadata"]["annotations"][k.OWNER_ANNOTATION] = "foreign"
    with pytest.raises(ValueError, match="Secret differs"):
        k.ensure_secret()


def test_client_environment_has_no_direct_credentials_or_proxy(local, monkeypatch):
    for name in [
        "AWS_ACCESS_KEY_ID",
        "PGPASSWORD",
        "PGUSER",
        "MLFLOW_BACKEND_STORE_URI",
        "MLFLOW_TRACKING_TOKEN",
        "HTTPS_PROXY",
    ]:
        monkeypatch.setenv(name, "must-not-cross")
    env = k.client_environment(k.work())
    assert "must-not-cross" not in env.values()
    assert env["HOME"] == env["TMPDIR"] == str(k.work())
    assert env["PYTHONPATH"] == str(local)


@pytest.mark.parametrize(
    "identity", ["sha256:" + "a" * 64, "docker.io/library/name@sha256:" + "b" * 64]
)
def test_image_digest_handles_cri_and_repository_forms(identity):
    assert k.image_digest(identity).startswith("sha256:")


@pytest.mark.parametrize("identity", ["latest", "sha256:short", "sha256:" + "g" * 64])
def test_image_digest_rejects_ambiguous_identities(identity):
    with pytest.raises(ValueError):
        k.image_digest(identity)


def test_client_removes_disposable_workspace_on_invalid_output(local, monkeypatch):
    source = local / "scripts/platform/kind_tracking_client.py"
    source.parent.mkdir(parents=True)
    source.write_text("# isolated fixture client\n")
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **kw: SimpleNamespace(stdout="not json", returncode=0)
    )
    with pytest.raises(ValueError, match="strict JSON"):
        k.client("read", "http://127.0.0.1:1234", {})
    assert not list(k.work().glob("client-*"))


def test_image_source_and_node_identity_must_match(local, monkeypatch):
    monkeypatch.setattr(k, "pins", lambda: {"source": "hash"})
    record = {
        "schema": "fraud_kind_tracking_image_v1",
        "source_pins": {"source": "hash"},
        "node_uid": "node-owned",
        "image": "fraud-kind-tracking:" + OWNER,
        "loaded_identities": ["sha256:" + "c" * 64],
    }
    path = k.work() / "image.json"
    path.write_text(json.dumps(record))
    assert k.image_record() == record
    record["source_pins"] = {}
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="sources changed"):
        k.image_record()
    record["source_pins"] = {"source": "hash"}
    record["node_uid"] = "foreign"
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="another node"):
        k.image_record()


@pytest.mark.parametrize("fail_write", [False, True])
def test_rehearsal_uid_precondition_and_failure_receipt(local, monkeypatch, fail_write):
    from contextlib import contextmanager

    state = {"recreated": False}
    deletes = []
    monkeypatch.setattr(f, "inventory", lambda: {"pvcs": {"same": "volume"}})
    monkeypatch.setattr(k, "sha", lambda _: "source-hash")
    monkeypatch.setattr(
        k,
        "inventory",
        lambda: {
            "pod": "owned-pod",
            "pod_uid": "after" if state["recreated"] else "before",
            "deployment_uid": "same",
        },
    )

    @contextmanager
    def forward():
        yield "http://127.0.0.1:1234"

    monkeypatch.setattr(k, "port_forward", forward)

    def client(action, uri, record=None):
        if fail_write:
            raise ValueError("Synthetic publication failed")
        return {
            "run_id": "a" * 32,
            "model_uri": "models:/m-abc",
            "model_artifact_uri": "mlflow-artifacts:/1/models/m-abc/artifacts",
            "model_sha256": "c" * 64,
            "metric_history_rows": 2,
            "max_absolute_score_error": 0.0,
        }

    monkeypatch.setattr(k, "client", client)
    monkeypatch.setattr(k, "storage_pod", lambda *a: {"status": "s3_model_verified"})

    def kube(*args, data=None, **kwargs):
        if args[0] == "delete":
            deletes.append(json.loads(data))
            state["recreated"] = True
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(f, "kube", kube)
    if fail_write:
        with pytest.raises(ValueError, match="publication failed"):
            k.rehearse()
        assert not deletes
    else:
        result = k.rehearse()
        assert result["status"] == "success" and result["phase"] == "complete"
        assert result["foundation_identities_preserved"]
        assert not result["backup_restore_validated"]
        assert deletes == [
            {"apiVersion": "v1", "kind": "DeleteOptions", "preconditions": {"uid": "before"}}
        ]
    receipts = list(k.work().glob("rehearsal-*.json"))
    assert len(receipts) == 1
    receipt = json.loads(receipts[0].read_text())
    assert receipt["status"] == ("failed" if fail_write else "success")
    assert receipt["phase"] == ("publish_and_independent_reload" if fail_write else "complete")
    assert receipt.get("independent_http_reload_validated", False) is not fail_write
