"""Foundation gates without a Kubernetes cluster or historical state."""

from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest
from scripts.platform import kind_foundation as foundation
import yaml

from fraud_detection_mlops.storage import managed_paths

ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "infra/helm/fraud-platform"


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.setattr(foundation, "ROOT", tmp_path)
    monkeypatch.setattr(managed_paths, "DATA_DIR", tmp_path / "data")
    return tmp_path


@pytest.fixture(scope="module")
def helm_binary():
    binary = shutil.which("helm")
    if not binary:
        pytest.skip("Helm CLI required for template validation; run foundation validate natively")
    version = subprocess.check_output([binary, "version", "--short"], text=True)
    if not version.startswith("v4.3."):
        pytest.skip("This increment targets Helm 4.3.x")
    return binary


def render(helm_binary, *extra, success=True):
    result = subprocess.run(
        [
            helm_binary,
            "template",
            "fraud-platform",
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
    assert (result.returncode == 0) == success, result.stderr
    return list(yaml.safe_load_all(result.stdout)) if success else []


def test_actual_helm_strict_lint_and_rendered_security_persistence(helm_binary):
    subprocess.run(
        [helm_binary, "lint", "--strict", str(CHART), "--kube-version", "1.35.8"],
        check=True,
        capture_output=True,
    )
    objects = render(helm_binary)
    assert len(objects) == 8
    assert sum(o["kind"] == "StatefulSet" for o in objects) == 2
    assert sum(o["kind"] == "PersistentVolumeClaim" for o in objects) == 2
    assert all(
        o["kind"] not in {"Secret", "Namespace", "ClusterRole", "Deployment"} for o in objects
    )
    for item in objects:
        if item["kind"] == "PersistentVolumeClaim":
            assert item["metadata"]["annotations"]["helm.sh/resource-policy"] == "keep"
            assert item["spec"]["storageClassName"] == "standard"
            assert item["spec"]["accessModes"] == ["ReadWriteOnce"]
        elif item["kind"] == "Service":
            assert item["spec"].get("type", "ClusterIP") == "ClusterIP"
            assert "nodePort" not in json.dumps(item)
            assert not item["spec"].get("externalIPs")
        else:
            assert item["spec"]["replicas"] == 1
            spec = item["spec"]["template"]["spec"]
            assert not spec["automountServiceAccountToken"]
            assert spec["securityContext"]["runAsNonRoot"]
            assert spec["securityContext"]["seccompProfile"]["type"] == "RuntimeDefault"
            assert all("hostPath" not in volume for volume in spec["volumes"])
            container = spec["containers"][0]
            assert container["securityContext"]["capabilities"]["drop"] == ["ALL"]
            assert container["securityContext"]["readOnlyRootFilesystem"]
            assert not container["securityContext"]["allowPrivilegeEscalation"]
            assert {"startupProbe", "readinessProbe", "livenessProbe"} <= set(container)
            assert set(container["resources"]) == {"requests", "limits"}
            assert "@sha256:" in container["image"]
            for field in container["env"]:
                if "PASSWORD" in field["name"] or "KEY" in field["name"]:
                    assert field["valueFrom"]["secretKeyRef"]["name"] == foundation.SECRET


@pytest.mark.parametrize(
    "option",
    [
        "postgres.image=postgres:latest",
        "rustfs.image=rustfs/rustfs:latest",
        "postgres.storage=0Gi",
        "existingSecret=BAD_NAME",
        "postgres.user=owner;DROP",
        "unknown=true",
    ],
)
def test_bad_values_rejected_by_actual_helm_schema(helm_binary, option):
    render(helm_binary, "--set-string", option, success=False)


def test_unsupported_kubernetes_rejected(helm_binary):
    result = subprocess.run(
        [helm_binary, "template", "fraud-platform", str(CHART), "--kube-version", "1.37.0"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0


def test_cluster_is_single_pinned_node_loopback_without_historical_mounts():
    config = yaml.safe_load((ROOT / "infra/kind/cluster.yaml").read_text())
    lock = json.loads((ROOT / "infra/kind/images.lock.json").read_text())["images"]
    assert config["nodes"] == [{"role": "control-plane", "image": lock["node"]}]
    assert config["networking"]["apiServerAddress"] == "127.0.0.1"
    assert "extraMounts" not in json.dumps(config)
    values = yaml.safe_load((CHART / "values.yaml").read_text())
    for service in foundation.COMPONENTS:
        assert values[service]["image"] == lock[service]
    assert lock["postgres"] in (ROOT / "infra/tracking/compose.yaml").read_text()
    assert lock["rustfs"] in (ROOT / "infra/observability/compose.yaml").read_text()


def test_initialize_private_random_credentials_and_reapply_preserves_bytes(local):
    result = foundation.initialize()
    assert result["status"] == "initialized"
    path = foundation.workspace() / "credentials.json"
    original = path.read_bytes()
    assert path.stat().st_mode & 0o777 == 0o600
    assert foundation.workspace().stat().st_mode & 0o777 == 0o700
    assert len(foundation.credentials()) == 3
    assert foundation.initialize()["status"] == "already_initialized"
    assert path.read_bytes() == original
    assert not (local / "mlruns").exists()


def test_world_readable_credentials_refused(local):
    foundation.initialize()
    (foundation.workspace() / "credentials.json").chmod(0o644)
    with pytest.raises(ValueError, match="mode 600"):
        foundation.credentials()


def test_initialize_after_only_validation_caches_exist(local):
    (foundation.workspace() / "helm/cache").mkdir(parents=True)
    assert foundation.initialize()["status"] == "initialized"


def test_managed_cache_symlink_refused_before_subprocess(local, monkeypatch):
    foundation.initialize()
    (foundation.workspace() / "helm").symlink_to(local, target_is_directory=True)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("Unexpected process"))
    with pytest.raises(ValueError, match="Symbolic"):
        foundation.run(["helm", "version"])


def test_deploy_refuses_foreign_namespace_before_helm_or_secret_mutation(local, monkeypatch):
    foundation.initialize()
    monkeypatch.setattr(foundation, "validate", dict)
    monkeypatch.setattr(foundation, "owner", dict)
    calls = []

    def fake_kube(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(stdout=json.dumps({"metadata": {"labels": {"owner": "other"}}}))

    monkeypatch.setattr(foundation, "kube", fake_kube)
    monkeypatch.setattr(foundation, "helm", lambda *a, **k: pytest.fail("Unexpected deployment"))
    with pytest.raises(ValueError, match="Namespace"):
        foundation.deploy()
    assert len(calls) == 1 and calls[0][0:2] == ("get", "namespace")


def test_status_can_report_unready_pods_without_returning_environment(local, monkeypatch):
    monkeypatch.setattr(foundation, "owner", dict)
    pod = {
        "kind": "Pod",
        "metadata": {"name": "probe"},
        "spec": {"containers": [{"env": [{"value": "private"}]}]},
        "status": {
            "phase": "Pending",
            "containerStatuses": [{"state": {"waiting": {"reason": "ImagePullBackOff"}}}],
        },
    }
    monkeypatch.setattr(
        foundation, "kube", lambda *a, **k: SimpleNamespace(stdout=json.dumps({"items": [pod]}))
    )
    result = foundation.status()
    assert result["resources"][0]["phase"] == "Pending"
    assert "ImagePullBackOff" in json.dumps(result)
    assert "private" not in json.dumps(result)


def test_catalogue_declares_workspace_producer_and_kind_external_boundary():
    from scripts.storage.persistence_inventory import load_catalogue

    catalogue, _ = load_catalogue(ROOT)
    rule = next(r for r in catalogue["locations"] if r["prefix"] == "data/storage/kind-platform")
    assert "scripts/platform/kind_foundation.py" in rule["producers"]
    assert any(r["id"] == "kind_foundation_local_pvs" for r in catalogue["external_boundaries"])


def test_symlink_workspace_rejected_before_writes(local, tmp_path):
    (local / "data/storage").mkdir(parents=True)
    outside = local / "outside"
    outside.mkdir()
    (local / "data/storage/kind-platform").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="Symbolic"):
        foundation.initialize()
    assert list(outside.iterdir()) == []


def test_private_files_never_overwrite(local):
    foundation.initialize()
    path = foundation.workspace() / "owner.json"
    foundation.private_json(path, {"id": 1})
    with pytest.raises(FileExistsError):
        foundation.private_json(path, {"id": 2})
    assert json.loads(path.read_text()) == {"id": 1}


def test_kubectl_and_helm_always_target_private_kubeconfig(local, monkeypatch):
    calls = []
    monkeypatch.setattr(foundation, "run", lambda args, **kwargs: calls.append((args, kwargs)))
    foundation.kube("get", "pods")
    foundation.helm("status", foundation.RELEASE)
    for args, _ in calls:
        assert str(foundation.workspace() / "kubeconfig") in [str(a) for a in args]
        assert foundation.CONTEXT in args
        assert foundation.NAMESPACE in args


def test_existing_unowned_cluster_refused_without_create(local, monkeypatch):
    foundation.initialize()
    monkeypatch.setattr(foundation, "versions", dict)
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(stdout=foundation.CLUSTER + "\n")

    monkeypatch.setattr(foundation, "run", fake_run)
    with pytest.raises(ValueError, match="ownership"):
        foundation.create()
    assert not any("create" in args for args in calls)


def test_probe_pod_credentials_are_secret_refs_and_no_source_mounts(local, monkeypatch):
    monkeypatch.setattr(foundation, "images", lambda: {"s3_probe": "probe@sha256:" + "a" * 64})
    pod = foundation.probe_pod("a" * 32, write=False)
    assert pod["spec"]["volumes"] == [{"name": "tmp", "emptyDir": {}}]
    container = pod["spec"]["containers"][0]
    assert "create-bucket" not in container["command"][-1]
    assert "put-object" not in container["command"][-1]
    assert "get-object" in container["command"][-1]
    assert "addressing_style = path" in container["command"][-1]
    assert (
        next(e["value"] for e in container["env"] if e["name"] == "AWS_CONFIG_FILE")
        == "/tmp/aws-config"
    )
    assert all(
        "valueFrom" in field
        for field in container["env"]
        if field["name"] in {"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"}
    )
    assert not pod["spec"]["automountServiceAccountToken"]


@pytest.mark.parametrize("identity", ["bad/name", "' OR 1=1", "a" * 31, "a" * 33])
def test_probe_identity_rejected_before_any_command(identity, monkeypatch):
    monkeypatch.setattr(foundation, "kube", lambda *a, **k: pytest.fail("Unexpected command"))
    with pytest.raises(ValueError):
        foundation.postgres_probe(identity, write=True)
    with pytest.raises(ValueError):
        foundation.probe_pod(identity, write=True)


def test_command_failures_hide_output_and_credentials(monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1, stdout="private-key", stderr="private-password"
        ),
    )
    with pytest.raises(ValueError) as error:
        foundation.run(["kubectl", "create", "-f", "-"], data="private-password")
    assert "private" not in str(error.value)


def resources():
    return {
        "pods": {
            s: {"name": foundation.RELEASE + "-" + s + "-0", "uid": s + "-before"}
            for s in foundation.COMPONENTS
        },
        "pvcs": {s: {"uid": s + "-pvc", "volume_name": s + "-pv"} for s in foundation.COMPONENTS},
    }


@pytest.mark.parametrize("failure", [None, "s3_write", "pvc_changed", "pod_not_recreated"])
def test_rehearsal_requires_readbacks_new_pods_same_pvcs_and_preserves_failure_receipt(
    local, monkeypatch, failure
):
    foundation.initialize()
    monkeypatch.setattr(foundation, "owner", dict)
    before = resources()
    after = deepcopy(before)
    for service in foundation.COMPONENTS:
        after["pods"][service]["uid"] += "-after"
    if failure == "pvc_changed":
        after["pvcs"]["postgres"]["uid"] = "different"
    if failure == "pod_not_recreated":
        after["pods"]["postgres"]["uid"] = before["pods"]["postgres"]["uid"]
    inventories = iter([before, after])
    monkeypatch.setattr(foundation, "inventory", lambda: next(inventories))
    calls = []
    monkeypatch.setattr(foundation, "kube", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(
        foundation, "postgres_probe", lambda identity, write: calls.append(("sql", write))
    )

    def s3(identity, write):
        calls.append(("s3", write))
        if failure == "s3_write" and write:
            raise ValueError("S3 failure")

    monkeypatch.setattr(foundation, "s3_probe", s3)
    if failure:
        with pytest.raises(ValueError):
            foundation.rehearse()
    else:
        assert foundation.rehearse()["status"] == "success"
        assert ("sql", False) in calls and ("s3", False) in calls
    receipt = json.loads(next(foundation.workspace().glob("foundation-*.json")).read_text())
    assert receipt["status"] == ("failed" if failure else "success")
    assert not receipt["primary_storage_migrated"]
    assert not receipt["backup_restore_validated"]
    assert all(not ("delete" in c and "pvc" in c) for c in calls)
