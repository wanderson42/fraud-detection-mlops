"""Real Helm rendering, image pin boundaries and controller failure handling."""

import json
from pathlib import Path
import re
import shutil
import subprocess
import tomllib
from types import SimpleNamespace

import pytest
from scripts.platform import kind_pipeline as k
from tests.infra.test_kind_pipeline_workers import (  # noqa: F401
    OWNER,
    RUN,
    chain,
    data_source,
    prepared,
)
import yaml

from fraud_detection_mlops.storage import managed_paths

ROOT = Path(__file__).resolve().parents[2]


def render(tmp_path, jobs):
    helm = shutil.which("helm")
    if not helm:
        pytest.skip("Helm 4.3.x required for real chart rendering")
    path = tmp_path / "values.json"
    path.write_text(json.dumps({"jobs": jobs}))
    result = subprocess.run(
        [
            helm,
            "template",
            "fraud-chain-test",
            str(ROOT / "infra/helm/fraud-data-jobs"),
            "--values",
            str(path),
            "--namespace",
            "fraud-platform",
            "--kube-version",
            "1.35.8",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return list(yaml.safe_load_all(result.stdout))


def test_real_helm_jobs_have_credential_free_compute_and_ephemeral_outputs(tmp_path):
    jobs = render(
        tmp_path,
        [
            {"stage": "silver", "inputBundleId": "a" * 64},
            {"stage": "gold", "inputBundleId": "b" * 64},
        ],
    )
    assert len(jobs) == 2 and all(j["kind"] == "Job" for j in jobs)
    for job in jobs:
        assert job["spec"]["backoffLimit"] == 0
        assert job["spec"]["activeDeadlineSeconds"] == 1800
        pod = job["spec"]["template"]["spec"]
        assert (
            pod["restartPolicy"] == "Never"
            and not pod["automountServiceAccountToken"]
            and not pod["enableServiceLinks"]
        )
        assert (
            pod["securityContext"]["runAsNonRoot"] and pod["securityContext"]["fsGroup"] == 10001
        )
        assert pod["nodeSelector"] == {"kubernetes.io/hostname": "fraud-platform-control-plane"}
        assert [c["name"] for c in pod["initContainers"]] == ["download", "compute"]
        assert [c["name"] for c in pod["containers"]] == ["publish"]
        compute_container = pod["initContainers"][1]
        assert "env" not in compute_container and "envFrom" not in compute_container
        assert compute_container["resources"]["limits"] == {"cpu": "2", "memory": "4Gi"}
        for container in pod["initContainers"] + pod["containers"]:
            assert container["imagePullPolicy"] == "Never"
            assert (
                container["securityContext"]["readOnlyRootFilesystem"]
                and not container["securityContext"]["allowPrivilegeEscalation"]
            )
            assert container["securityContext"]["capabilities"] == {"drop": ["ALL"]}
        for container in [pod["initContainers"][0], pod["containers"][0]]:
            env = {e["name"]: e for e in container["env"]}
            assert env["AWS_SECRET_ACCESS_KEY"]["valueFrom"]["secretKeyRef"] == {
                "name": "fraud-platform-credentials",
                "key": "rustfs-secret-key",
            }
        assert all(set(v) == {"name", "emptyDir"} for v in pod["volumes"])
        assert "hostPath" not in json.dumps(pod) and "persistentVolumeClaim" not in json.dumps(pod)


def test_adding_gold_does_not_change_immutable_silver_job(tmp_path):
    silver = {"stage": "silver", "inputBundleId": "a" * 64}
    first = render(tmp_path, [silver])[0]
    upgraded = render(tmp_path, [silver, {"stage": "gold", "inputBundleId": "b" * 64}])
    assert first == upgraded[0]


def test_science_requirements_are_exact_linux_subset_of_existing_lock():
    packages = {p["name"]: p for p in tomllib.loads((ROOT / "poetry.lock").read_text())["package"]}
    raw = (ROOT / "infra/kind/pipeline/requirements-science.txt").read_text()
    lines = [line for line in raw.splitlines() if not line.startswith("#")]
    blocks = "\n".join(lines).replace("\\\n", " ").splitlines()
    names = set()
    for block in blocks:
        name, version = block.split()[0].split("==")
        names.add(name)
        assert version == packages[name]["version"]
        assert set(re.findall(r"--hash=(sha256:[a-f0-9]{64})", block)) == {
            f["hash"] for f in packages[name]["files"]
        }
    assert {"pandas", "numpy", "pyarrow", "duckdb", "typer"} <= names
    assert not names & {"scikit-learn", "mlflow", "optuna", "shap"}


@pytest.fixture
def local(chain, tmp_path, monkeypatch):  # noqa: F811 — imported worker fixture
    root, values, _client = chain
    monkeypatch.setattr(k.f, "ROOT", root)
    monkeypatch.setattr(managed_paths, "DATA_DIR", root / "data")
    (root / "scripts/platform").mkdir(parents=True)
    shutil.copyfile(
        ROOT / "scripts/platform/kind_pipeline_compute.py",
        root / "scripts/platform/kind_pipeline_compute.py",
    )
    monkeypatch.setattr(k, "uuid4", lambda: SimpleNamespace(hex=RUN))
    monkeypatch.setattr(k, "initialize", lambda: {"owner_id": OWNER})
    monkeypatch.setattr(
        k,
        "images",
        lambda: {"images": {"science": {"image": "science"}, "transport": {"image": "transport"}}},
    )
    monkeypatch.setattr(k.f, "inventory", lambda: {"pods": {"rustfs": "owned"}, "pvcs": "same"})
    monkeypatch.setattr(
        k.datasets,
        "available",
        lambda: {
            "bundles": [{"layer": "bronze", "bundle_id": values["silver"]["input_bundle_id"]}]
        },
    )
    monkeypatch.setattr(k, "validate", lambda: None)
    k.work().mkdir(parents=True)
    calls = []

    def helm(*args, **kw):
        calls.append(args)

    monkeypatch.setattr(k.f, "helm", helm)

    def kube(*args, **kw):
        if args[0] == "logs":
            stage = "gold" if "gold" in args[1] else "silver"
            return SimpleNamespace(stdout=json.dumps(values[stage]))
        return SimpleNamespace(stdout='{"items": []}')

    monkeypatch.setattr(k.f, "kube", kube)
    monkeypatch.setattr(k, "await_job", lambda name: None)
    monkeypatch.setattr(
        k,
        "job_inventory",
        lambda release, stage, *a: {"pod": release + "-" + stage, "job_uid": "uid-" + stage},
    )

    def restore(action, identity, stage):
        assert action == "restore" and identity == values[stage]["output_bundle_id"]
        semantic = values[stage]["computation"]["semantic_verification"].copy()
        semantic[stage + "_path"] = "separate-host-copy"
        return {"semantic_verification": semantic, "status": "restored_verified"}

    monkeypatch.setattr(k.datasets, "transfer", restore)
    return values, calls


def test_controller_links_jobs_and_independent_restores_then_removes_only_release(local):
    _values, calls = local
    result = k.rehearse()
    assert result["status"] == "success" and result["temporary_jobs_removed"]
    assert result["foundation_identities_preserved"] and result["features_recomputed"]
    assert not result["fit_executed"] and not result["primary_storage_migrated"]
    assert (
        result["stages"]["gold"]["result"]["input_bundle_id"]
        == result["stages"]["silver"]["result"]["output_bundle_id"]
    )
    assert [c[0] for c in calls] == ["upgrade", "upgrade", "uninstall"]
    assert calls[-1][1] == result["release"]
    assert json.loads(Path(result["report"]).read_text()) == result


def test_first_job_failure_preserves_resources_and_never_starts_gold(local, monkeypatch):
    _values, calls = local
    monkeypatch.setattr(
        k, "await_job", lambda name: (_ for _ in ()).throw(ValueError("injected Job failure"))
    )
    monkeypatch.setattr(
        k, "diagnostic", lambda release: [{"pod": release + "-silver", "error": "fixture"}]
    )
    with pytest.raises(ValueError, match="injected Job failure"):
        k.rehearse()
    assert len(calls) == 1 and calls[0][0] == "upgrade"
    receipt = json.loads(next(k.work().glob("rehearsal-*.json")).read_text())
    assert receipt["status"] == "failed" and not receipt["temporary_jobs_removed"]
    assert receipt["stages"] == {} and receipt["diagnostics"]


@pytest.mark.parametrize("fault", ["run", "worker", "reference", "envelope", "input"])
def test_controller_rejects_wrong_pins_or_completion_binding(local, fault):
    values, _calls = local
    value = json.loads(json.dumps(values["silver"]))
    if fault == "run":
        value["run_id"] = "f" * 32
    elif fault == "worker":
        value["computation"]["source_worker_sha256"] = "0" * 64
    elif fault == "reference":
        value["computation"]["reference_hashes"] = {"../../outside": "0" * 64}
    elif fault == "input":
        value["computation"]["input_bundle_unchanged"] = False
    else:
        value["remote_receipt_sha256"] = "0" * 64
    with pytest.raises(ValueError):
        k.validate_stage(value, "silver", RUN, values["silver"]["input_bundle_id"], OWNER)


def test_loaded_images_are_bound_to_source_node_and_cri_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(k.f, "ROOT", tmp_path)
    monkeypatch.setattr(managed_paths, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(k.f, "owner", lambda: {"node_uid": "owned-node"})
    monkeypatch.setattr(k, "pins", lambda: {"source": "a" * 64})
    k.work().mkdir(parents=True)
    record = {
        "schema": "fraud_kind_pipeline_images_v1",
        "node_uid": "owned-node",
        "source_pins": k.pins(),
        "images": {
            role: {
                "image": "fraud-kind-pipeline-" + role + ":" + "a" * 32,
                "loaded_identities": ["sha256:" + "b" * 64],
            }
            for role in ["science", "transport"]
        },
    }
    path = k.work() / "images.json"
    path.write_text(json.dumps(record))

    def run(args, **kw):
        return SimpleNamespace(
            stdout=json.dumps({"status": {"repoTags": [args[-1]], "id": "sha256:" + "b" * 64}})
        )

    monkeypatch.setattr(k.f, "run", run)
    assert k.images() == record
    monkeypatch.setattr(k.f, "owner", lambda: {"node_uid": "foreign-node"})
    with pytest.raises(ValueError, match="another node"):
        k.images()
    monkeypatch.setattr(k.f, "owner", lambda: {"node_uid": "owned-node"})
    monkeypatch.setattr(k, "pins", lambda: {"source": "c" * 64})
    with pytest.raises(ValueError, match="inputs changed"):
        k.images()


@pytest.mark.parametrize("fault", [None, "image", "owner", "exit"])
def test_job_acceptance_checks_owner_exit_codes_and_actual_images(tmp_path, monkeypatch, fault):
    job = {
        "metadata": {
            "name": "run-silver",
            "uid": "job-uid",
            "annotations": {"fraud.pipeline/run-id": RUN, "fraud.pipeline/owner-id": OWNER},
            "labels": {"app.kubernetes.io/instance": "run"},
        }
    }
    states = [
        {
            "name": name,
            "imageID": "docker.io/library/import@sha256:" + "b" * 64,
            "state": {"terminated": {"exitCode": 0}},
        }
        for name in ["download", "compute", "publish"]
    ]
    pod = {
        "metadata": {
            "name": "pod",
            "uid": "pod-uid",
            "ownerReferences": [{"uid": "job-uid", "controller": True}],
        },
        "spec": {
            "initContainers": [
                {"name": "download", "image": "transport"},
                {"name": "compute", "image": "science"},
            ],
            "containers": [{"name": "publish", "image": "transport"}],
        },
        "status": {
            "phase": "Succeeded",
            "initContainerStatuses": states[:2],
            "containerStatuses": states[2:],
        },
    }
    record = {
        "images": {
            role: {"image": role, "loaded_identities": ["sha256:" + "b" * 64]}
            for role in ["science", "transport"]
        }
    }
    if fault == "image":
        states[1]["imageID"] = "sha256:" + "c" * 64
    elif fault == "owner":
        pod["metadata"]["ownerReferences"][0]["uid"] = "foreign"
    elif fault == "exit":
        states[1]["state"]["terminated"]["exitCode"] = 1
    monkeypatch.setattr(
        k.f,
        "kube",
        lambda *a, **kw: SimpleNamespace(
            stdout=json.dumps(job if a[1] == "job" else {"items": [pod]})
        ),
    )
    if fault:
        with pytest.raises(ValueError):
            k.job_inventory("run", "silver", RUN, OWNER, record)
    else:
        assert k.job_inventory("run", "silver", RUN, OWNER, record)["pod_uid"] == "pod-uid"


def test_independent_restore_failure_prevents_gold_job(local, monkeypatch):
    _values, calls = local
    monkeypatch.setattr(
        k.datasets, "transfer", lambda *a: (_ for _ in ()).throw(ValueError("restore gate"))
    )
    monkeypatch.setattr(k, "diagnostic", lambda release: [])
    with pytest.raises(ValueError, match="restore gate"):
        k.rehearse()
    assert len(calls) == 1 and calls[0][0] == "upgrade"
    assert not json.loads(next(k.work().glob("rehearsal-*.json")).read_text())[
        "temporary_jobs_removed"
    ]
