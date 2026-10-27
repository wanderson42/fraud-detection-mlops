"""Actual Helm rendering, input boundaries, ownership and owned-resource recovery."""

from contextlib import contextmanager
import json
from pathlib import Path
import shutil
import subprocess
import tomllib
from types import SimpleNamespace

import pytest
from scripts.operational_round import controller as c
from scripts.operational_round import policy, serving
from scripts.operational_round.training import MARKER
import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_helm_training_is_one_bounded_job_with_credentials_only_in_init():
    helm = shutil.which("helm")
    if helm is None:
        pytest.skip("Helm executable required for real rendering")
    subprocess.run([helm, "lint", "--strict", str(c.CHART)], check=True, capture_output=True)
    raw = subprocess.check_output(
        [
            helm,
            "template",
            "fraud-round-validation",
            str(c.CHART),
            "--namespace",
            "fraud-platform",
        ],
        text=True,
    )
    docs = list(yaml.safe_load_all(raw))
    assert [d["kind"] for d in docs] == ["ConfigMap", "Job"]
    job = docs[1]["spec"]
    pod = job["template"]["spec"]
    assert job["backoffLimit"] == 0 and job["activeDeadlineSeconds"] == 1800
    assert pod["automountServiceAccountToken"] is False and pod["enableServiceLinks"] is False
    assert len(pod["containers"]) == 1 and len(pod["initContainers"]) == 1
    assert not pod["containers"][0].get("env")
    assert any(e["name"] == "AWS_SECRET_ACCESS_KEY" for e in pod["initContainers"][0]["env"])
    assert all(
        v["securityContext"]["readOnlyRootFilesystem"]
        for v in pod["containers"] + pod["initContainers"]
    )


def test_training_requirements_use_only_locked_versions_and_hashes():
    lock = {v["name"]: v for v in tomllib.loads((ROOT / "poetry.lock").read_text())["package"]}
    text = (ROOT / "infra/kind/round/requirements.txt").read_text()
    import re

    for match in re.finditer(
        r"^([\w.-]+)==([^\s]+)(.*?)(?=^[\w.-]+==|\Z)", text, re.MULTILINE | re.DOTALL
    ):
        name, version, hashes = match.groups()
        assert version == lock[name]["version"]
        assert set(re.findall(r"--hash=(sha256:[a-f0-9]+)", hashes)) == {
            f["hash"] for f in lock[name]["files"]
        }
    assert not re.search(r"^(boto3|psycopg|pytest|ruff)==", text, re.MULTILINE)


@pytest.mark.parametrize("raw", [MARKER + "{}\n" + MARKER + "{}", "diagnostics only"])
def test_ambiguous_or_missing_worker_result_is_refused(raw):
    with pytest.raises(ValueError, match="Exactly one"):
        c.parse_result(raw)


def test_worker_log_diagnostics_cannot_be_interpreted_as_results():
    assert c.parse_result("INFO: header\n" + MARKER + '{"status":"qualified"}\n') == {
        "status": "qualified"
    }


def test_uncommitted_protocol_refused_before_any_service_access(tmp_path, monkeypatch):
    monkeypatch.setattr(c, "ROOT", tmp_path)
    p = tmp_path / "policy.json"
    p.write_text("{}")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Fixture"], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "fixture@example.invalid"], check=True
    )
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "policy"], check=True)
    monkeypatch.setattr(c, "pins", lambda: {"policy.json": policy.sha(p)})
    before, _ = c.committed()
    assert len(before) == 40
    p.write_text('{"changed":true}')
    with pytest.raises(ValueError, match="Commit the reviewed"):
        c.committed()


def job_fixture():
    owner = "e" * 32
    job = {"metadata": {"uid": "job-uid", "annotations": {"fraud.round/owner-id": owner}}}
    pod = {
        "metadata": {
            "name": "pod",
            "uid": "pod-uid",
            "ownerReferences": [{"kind": "Job", "controller": True, "uid": "job-uid"}],
        },
        "spec": {
            "automountServiceAccountToken": False,
            "enableServiceLinks": False,
            "volumes": [{"emptyDir": {}}],
            "containers": [{"name": "train", "image": "trainer"}],
            "initContainers": [{"name": "download", "image": "transport"}],
        },
        "status": {
            "containerStatuses": [{"name": "train", "imageID": "sha256:" + "a" * 64}],
            "initContainerStatuses": [{"name": "download", "imageID": "sha256:" + "b" * 64}],
        },
    }
    images = {
        "train": {"image": "trainer", "loaded_identities": ["sha256:" + "a" * 64]},
        "download": {"image": "transport", "loaded_identities": ["sha256:" + "b" * 64]},
    }
    return job, pod, owner, images


@pytest.mark.parametrize(
    "change", ["foreign_owner", "pod_owner", "image", "credentials", "host_path", None]
)
def test_job_readback_requires_ownership_images_and_credential_boundary(change):
    job, pod, owner, images = job_fixture()
    if change == "foreign_owner":
        job["metadata"]["annotations"]["fraud.round/owner-id"] = "foreign"
    if change == "pod_owner":
        pod["metadata"]["ownerReferences"][0]["uid"] = "foreign"
    if change == "image":
        pod["status"]["containerStatuses"][0]["imageID"] = "sha256:" + "c" * 64
    if change == "credentials":
        pod["spec"]["containers"][0]["env"] = [{"name": "AWS_SECRET_ACCESS_KEY"}]
    if change == "host_path":
        pod["spec"]["volumes"] = [{"hostPath": {"path": "/"}}]
    if change:
        with pytest.raises(ValueError):
            c.check_job(job, pod, owner, images)
    else:
        assert c.check_job(job, pod, owner, images)["image_identities_verified"]


def test_registry_write_requires_exclusive_writer_declaration(monkeypatch):
    monkeypatch.setattr(c, "read", lambda _: pytest.fail("must refuse before reading"))
    with pytest.raises(ValueError, match="no-other-writers"):
        c.promote("portfolio-v1", False)


def test_rollout_cleanup_failure_cannot_leave_a_success_receipt(monkeypatch):
    value = {"round_id": "fixture", "model_sha256": "a" * 64}
    monkeypatch.setattr(serving, "preparation", lambda _: value)
    monkeypatch.setattr(serving, "owned", lambda _: True)
    calls = []

    def apply(_, **kwargs):
        calls.append(kwargs)
        if kwargs.get("replicas") == 0:
            raise ValueError("injected cleanup failure")

    monkeypatch.setattr(serving, "apply", apply)
    monkeypatch.setattr(
        serving.f,
        "kube",
        lambda *a, **k: SimpleNamespace(
            stdout=json.dumps({"items": [{"metadata": {"name": "green-pod"}}]})
        ),
    )

    @contextmanager
    def forward(_):
        yield "http://fixture"

    monkeypatch.setattr(serving, "forward", forward)
    monkeypatch.setattr(serving, "gateway", lambda _: "gateway-pod")
    monkeypatch.setattr(serving, "check", lambda *a: {"score": "verified"})
    monkeypatch.setattr(serving.checks, "request_json", lambda *a: (502, {}))
    monkeypatch.setattr(c, "save", lambda *a: pytest.fail("must not publish success"))
    with pytest.raises(ValueError, match="cleanup failure"):
        serving.rehearse("fixture")
    assert calls[-1]["active"] == "blue"


def render_serving(tmp_path, **overrides):
    binary = shutil.which("helm")
    if binary is None:
        pytest.skip("Helm executable required for real rendering")
    values = {
        "ownerId": "a" * 32,
        "images": {
            role: "fraud-kind-serving-" + role + ":" + "b" * 32
            for role in ("runtime", "artifacts", "gateway")
        },
        "activeSlot": "blue",
        "greenEnabled": False,
        "greenReplicas": 1,
        "manifests": {slot: "c" * 64 for slot in ("blue", "green")},
        **overrides,
    }
    path = tmp_path / "values.json"
    path.write_text(json.dumps(values))
    command = [binary, "template", "fraud-detector", str(serving.CHART), "--values", str(path)]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    return result, {
        v["kind"] + ":" + v["metadata"]["name"]: v for v in yaml.safe_load_all(result.stdout) if v
    }


def test_shared_serving_chart_keeps_blue_and_gateway_stable_across_routing(tmp_path):
    result, baseline = render_serving(tmp_path)
    assert result.returncode == 0, result.stderr
    _, green = render_serving(tmp_path, greenEnabled=True)
    _, routed = render_serving(tmp_path, greenEnabled=True, activeSlot="green")
    _, outage = render_serving(tmp_path, greenEnabled=True, activeSlot="green", greenReplicas=0)
    for key in (
        "Deployment:fraud-detector-blue",
        "Deployment:fraud-detector-gateway",
        "ConfigMap:fraud-detector-gateway",
        "Service:fraud-detector-gateway",
    ):
        assert baseline[key] == green[key] == routed[key] == outage[key]
    assert (
        routed["Service:fraud-detector-active"]["spec"]["selector"]["app.kubernetes.io/component"]
        == "green"
    )
    assert outage["Deployment:fraud-detector-green"]["spec"]["replicas"] == 0
    for item in green.values():
        assert item["kind"] not in {"Secret", "PersistentVolumeClaim", "Role", "RoleBinding"}
        if item["kind"] != "Deployment":
            continue
        pod = item["spec"]["template"]["spec"]
        assert pod["automountServiceAccountToken"] is False and pod["enableServiceLinks"] is False
        assert pod["securityContext"]["runAsNonRoot"] is True
        assert not any("hostPath" in v or "secret" in v for v in pod["volumes"])
        for container in pod["containers"] + pod.get("initContainers", []):
            assert container["imagePullPolicy"] == "Never"
            assert container["securityContext"]["readOnlyRootFilesystem"] is True
            assert container["securityContext"]["capabilities"]["drop"] == ["ALL"]
            assert not any("valueFrom" in value for value in container.get("env", []))


@pytest.mark.parametrize(
    "overrides",
    [
        {"images": {"runtime": "ubuntu:latest"}},
        {"ownerId": "invalid"},
        {"greenReplicas": 2},
        {"activeSlot": "green"},
    ],
)
def test_shared_serving_chart_rejects_unsafe_or_absent_candidate(tmp_path, overrides):
    result, _ = render_serving(tmp_path, **overrides)
    assert result.returncode != 0
