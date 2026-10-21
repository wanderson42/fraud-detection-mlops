"""Real Helm rendering, copied releases and HTTP; controlled Kubernetes switch/rollback failures."""

from contextlib import contextmanager
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

from examples.serving_smoke import synthetic_release
from fastapi.testclient import TestClient
import pytest
from scripts.platform import kind_serving as k
from scripts.platform import kind_serving_delivery as d
from scripts.platform import kind_serving_seed as seed
from threadpoolctl import threadpool_limits
import yaml

from fraud_detection_mlops.serving.http_service import create_app

ROOT = Path(__file__).resolve().parents[2]
ID = "a" * 32


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    release = tmp_path_factory.mktemp("kind-serving-generated") / "release"
    with threadpool_limits(limits=2):
        synthetic_release(release)
    return release


@pytest.fixture
def staged(tmp_path, generated):
    result = d.stage(generated, tmp_path)
    return {
        **result,
        "directory": str(tmp_path),
        "owner_id": ID,
        "source_commit": "b" * 40,
        "source_pins": {},
        "node_uid": "node-uid",
        "gateway_requested": "pinned-gateway",
        "artifact_carrier_built": True,
        "descriptor_sha256": "c" * 64,
        "qualified_runtime_image_id": "sha256:" + "d" * 64,
        "images": {
            role: {
                "image": "fraud-kind-serving-" + role + ":" + ID,
                "docker_id": "sha256:" + "d" * 64,
                "loaded_identities": ["sha256:" + "d" * 64],
            }
            for role in d.ROLES
        },
    }


@pytest.fixture
def clients(staged, tmp_path):
    with (
        TestClient(
            create_app(
                Path(staged["directory"]) / "blue", staged["releases"]["blue"]["manifest_sha256"]
            )
        ) as blue,
        TestClient(
            create_app(
                Path(staged["directory"]) / "green", staged["releases"]["green"]["manifest_sha256"]
            )
        ) as green,
    ):
        yield {"blue": blue, "green": green}


def request(client, route, payload=None):
    response = client.get(route) if payload is None else client.post(route, json=payload)
    return response.status_code, response.json()


def test_same_weights_new_identity_seed_and_real_HTTP_gates(staged, clients, tmp_path):
    blue, green = staged["releases"]["blue"], staged["releases"]["green"]
    assert blue["manifest"]["model_sha256"] == green["manifest"]["model_sha256"]
    assert blue["manifest"]["serving_release_id"] != green["manifest"]["serving_release_id"]
    assert staged["invalid_rejected"]
    for slot in ["blue", "green"]:
        out = tmp_path / (slot + "-pod-model")
        seeded = seed.seed(
            Path(staged["directory"]) / slot, out, staged["releases"][slot]["manifest_sha256"]
        )
        assert seeded["status"] == "release_seeded"
        assert all((out / name).stat().st_mode & 0o222 == 0 for name in seed.FILES)
        report = k.check_api(
            lambda route, payload=None, client=clients[slot]: request(client, route, payload),
            staged,
            slot,
        )
        assert report["invalid_input_http_status"] == 422
        assert (
            report["score"]["serving_release_id"]
            == staged["releases"][slot]["manifest"]["serving_release_id"]
        )


@pytest.mark.parametrize("mutation", ["manifest_pin", "model", "extra", "link", "preexisting"])
def test_seed_rejects_corruption_links_or_existing_model_before_replacing_bytes(
    staged, tmp_path, mutation
):
    source = tmp_path / "copy"
    shutil.copytree(Path(staged["directory"]) / "blue", source)
    target = tmp_path / "target"
    pin = staged["releases"]["blue"]["manifest_sha256"]
    if mutation == "manifest_pin":
        pin = "f" * 64
    elif mutation == "model":
        (source / "pipeline.skops").write_bytes(b"corrupted")
    elif mutation == "extra":
        (source / "unreviewed").write_text("extra")
    elif mutation == "link":
        (source / "pipeline.skops").unlink()
        (source / "pipeline.skops").symlink_to(Path(staged["directory"]) / "blue/pipeline.skops")
    else:
        target.mkdir()
        (target / "keep").write_text("keep")
    with pytest.raises(ValueError):
        seed.seed(source, target, pin)
    assert not (target / "pipeline.skops").exists()
    if mutation == "preexisting":
        assert (target / "keep").read_text() == "keep"


def helm(*args):
    binary = shutil.which("helm")
    assert binary, "Helm required for actual template validation"
    return subprocess.run([binary, *map(str, args)], capture_output=True, text=True, check=False)


def render(staged, tmp_path, **kwargs):
    path = tmp_path / "values.json"
    path.write_text(json.dumps(k.values(staged, **kwargs)))
    done = helm("template", k.RELEASE, d.CHART, "--values", path)
    assert done.returncode == 0, done.stderr
    return {
        doc["kind"] + ":" + doc["metadata"]["name"]: doc for doc in yaml.safe_load_all(done.stdout)
    }


def test_real_Helm_changes_only_Service_route_and_candidate_and_keeps_baseline_gateway(
    staged, tmp_path
):
    assert helm("lint", "--strict", d.CHART).returncode == 0
    baseline = render(staged, tmp_path)
    candidate = render(staged, tmp_path, green=True)
    routed = render(staged, tmp_path, green=True, active="green")
    outage = render(staged, tmp_path, green=True, active="green", replicas=0)
    for key in [
        "Deployment:fraud-serving-blue",
        "Deployment:fraud-serving-gateway",
        "ConfigMap:fraud-serving-gateway",
        "Service:fraud-serving-gateway",
    ]:
        assert baseline[key] == candidate[key] == routed[key] == outage[key]
    assert (
        routed["Service:fraud-serving-active"]["spec"]["selector"]["app.kubernetes.io/component"]
        == "green"
    )
    assert candidate["Deployment:fraud-serving-green"] == routed["Deployment:fraud-serving-green"]
    assert outage["Deployment:fraud-serving-green"]["spec"]["replicas"] == 0
    for document in candidate.values():
        assert document["kind"] not in {"Secret", "PersistentVolumeClaim", "Role", "RoleBinding"}
        if document["kind"] != "Deployment":
            continue
        pod = document["spec"]["template"]["spec"]
        assert not pod["automountServiceAccountToken"] and not pod["enableServiceLinks"]
        assert pod["securityContext"]["runAsNonRoot"]
        assert not any("hostPath" in volume or "secret" in volume for volume in pod["volumes"])
        for container in [*pod.get("initContainers", []), *pod["containers"]]:
            assert container["imagePullPolicy"] == "Never"
            assert container["securityContext"]["readOnlyRootFilesystem"]
            assert container["securityContext"]["capabilities"]["drop"] == ["ALL"]
            assert not any("valueFrom" in env for env in container.get("env", []))
    api = candidate["Deployment:fraud-serving-blue"]["spec"]["template"]["spec"]
    assert api["containers"][0]["image"] == staged["images"]["runtime"]["image"]
    assert api["containers"][0]["volumeMounts"][0]["readOnly"] is True
    assert "emptyDir" in api["volumes"][0]


@pytest.mark.parametrize(
    "value",
    ["images.runtime=ubuntu:latest", "ownerId=invalid", "greenReplicas=2", "activeSlot=green"],
)
def test_real_Helm_rejects_unsafe_or_absent_candidate(value):
    assert helm("template", k.RELEASE, d.CHART, "--set", value).returncode != 0


def test_external_descriptor_pin_is_checked_before_Docker_or_cluster(tmp_path, monkeypatch):
    monkeypatch.setattr(d.f, "managed_destination", lambda p, _: p)

    def reject(directory, commit, pin):
        assert directory == tmp_path and commit == "a" * 40 and pin == "b" * 64
        raise d.checks.CheckFailed("external pin differs")

    monkeypatch.setattr(d.delivery, "verify", reject)
    monkeypatch.setattr(
        d.f, "owner", lambda: pytest.fail("cluster contacted before pin verification")
    )
    monkeypatch.setattr(
        d.f, "run", lambda *a, **kw: pytest.fail("Docker contacted before verification")
    )
    with pytest.raises(d.checks.CheckFailed, match="external pin"):
        d.prepare(tmp_path, "a" * 40, "b" * 64)


@pytest.fixture
def cluster(staged, clients, tmp_path, monkeypatch):
    state = {"values": k.values(staged), "calls": [], "revision": 7}

    def snapshot():
        pods = {name: {"name": name, "uid": name + "-uid"} for name in ["blue", "gateway"]}
        if state["values"]["greenEnabled"] and state["values"]["greenReplicas"]:
            pods["green"] = {"name": "green", "uid": "green-uid"}
        deployments = {name: name + "-dep" for name in ["blue", "gateway"]}
        if state["values"]["greenEnabled"]:
            deployments["green"] = "green-dep"
        return {
            "pods": pods,
            "deployments": deployments,
            "services": {
                "fraud-serving-active": {
                    "uid": "service-uid",
                    "cluster_ip": "10.96.1.2",
                    "selector": {"slot": state["values"]["activeSlot"]},
                }
            },
        }

    monkeypatch.setattr(d, "plan", lambda: staged)
    monkeypatch.setattr(d, "work", lambda: tmp_path)
    monkeypatch.setattr(k, "observe", lambda _: snapshot())
    monkeypatch.setattr(k, "owned", lambda _: None)
    monkeypatch.setattr(k, "current_values", lambda: state["values"])
    monkeypatch.setattr(k, "revision", lambda: state["revision"])
    monkeypatch.setattr(k.f, "inventory", lambda: {"foundation": "unchanged"})
    monkeypatch.setattr(k.f, "private_json", lambda p, value: p.write_text(json.dumps(value)))

    def apply(_, desired):
        state["calls"].append(("upgrade", desired))
        state["values"] = desired
        state["revision"] += 1

    monkeypatch.setattr(k, "apply", apply)

    def helm(*args, **kwargs):
        assert args[:2] == ("rollback", "fraud-serving") and args[2] == "7"
        state["calls"].append(args)
        state["values"] = k.values(staged)
        state["revision"] += 1
        return SimpleNamespace(stdout="", stderr="")

    monkeypatch.setattr(k.f, "helm", helm)
    monkeypatch.setattr(k.f, "kube", lambda *a, **kw: SimpleNamespace(stdout="", stderr=""))

    @contextmanager
    def forward(_):
        yield "http://127.0.0.1:12345"

    monkeypatch.setattr(d, "forward", forward)

    def endpoints(expected):
        slot = state["values"]["activeSlot"]
        current = {slot + "-uid"} if slot == "blue" or state["values"]["greenReplicas"] else set()
        assert expected == current
        return sorted(current)

    monkeypatch.setattr(k, "wait_endpoints", endpoints)
    monkeypatch.setattr(
        k, "direct", lambda pod, route, payload=None: request(clients[pod], route, payload)
    )

    def gateway(url, route, payload=None):
        assert url == "http://127.0.0.1:12345"
        slot = state["values"]["activeSlot"]
        if slot == "green" and state["values"]["greenReplicas"] == 0:
            return 502, {"error": "upstream_unavailable"}
        return request(clients[slot], route, payload)

    monkeypatch.setattr(k.checks, "request_json", gateway)
    return state, snapshot


def test_full_controller_path_uses_real_HTTP_checks_and_native_Helm_rollback_contract(cluster):
    state, _ = cluster
    result = k.rehearse()
    assert result["status"] == "success" and result["events"] == k.EVENTS
    assert result["observed_failure_http_status"] == 502
    assert result["native_Helm_rollback_validated"]
    assert result["before"] == result["after"]
    assert result["candidate_deployment_removed"] and result["baseline_and_gateway_pods_unchanged"]
    assert state["calls"][-1][:3] == ("rollback", "fraud-serving", "7")
    assert result["runtime_rebuilt"] is False and result["fit_executed"] is False


def test_failed_candidate_HTTP_never_switches_and_recovers_baseline(
    cluster, monkeypatch, tmp_path
):
    state, _ = cluster
    real = k.direct

    def failed(pod, route, payload=None):
        if pod == "green":
            return 503, {}
        return real(pod, route, payload)

    monkeypatch.setattr(k, "direct", failed)
    with pytest.raises(ValueError, match="Readiness"):
        k.rehearse()
    receipt = json.loads(next(tmp_path.glob("rehearsal-*.json")).read_text())
    assert receipt["status"] == "failed" and receipt["failure_recovery_baseline_restored"]
    assert all(c[1]["activeSlot"] == "blue" for c in state["calls"] if c[0] == "upgrade")
    assert receipt["events"] == k.EVENTS[:2]


def test_endpoint_gate_does_not_trust_count_only(monkeypatch):
    slices = {
        "items": [
            {
                "endpoints": [
                    {
                        "targetRef": {"kind": "Pod", "uid": "expected"},
                        "conditions": {"ready": True},
                    },
                    {
                        "targetRef": {"kind": "Pod", "uid": "not-ready"},
                        "conditions": {"ready": False},
                    },
                ]
            }
        ]
    }
    monkeypatch.setattr(k.f, "kube", lambda *a, **kw: SimpleNamespace(stdout=json.dumps(slices)))
    assert k.endpoint_uids() == {"expected"}
    assert k.wait_endpoints({"expected"}, timeout=0.1) == ["expected"]
    with pytest.raises(ValueError):
        k.wait_endpoints({"different-pod"}, timeout=0.01)


@pytest.fixture
def importer(tmp_path, generated, monkeypatch):
    source = tmp_path / "delivery"
    source.mkdir()
    shutil.copytree(generated, source / "release")
    image_id = "sha256:" + "d" * 64
    archive_tag = "fraud-serving-delivery:" + ID
    descriptor = {"image": {"id": image_id}, "exported_local_tag": archive_tag}
    (source / "delivery.json").write_text(json.dumps(descriptor))
    (source / "image.tar").write_bytes(b"controlled archive bytes")
    destination = tmp_path / "workspace"
    trace = {
        "calls": [],
        "loaded": False,
        "existing": False,
        "id": image_id,
        "revision": "b" * 40,
        "verifications": 0,
    }
    monkeypatch.setattr(d.f, "managed_destination", lambda p, _: Path(p))
    monkeypatch.setattr(d, "work", lambda: destination)
    monkeypatch.setattr(d.f, "owner", lambda: {"node_uid": "owned-node"})

    def verify(directory, commit, pin):
        assert directory == source and commit == "b" * 40 and pin == "c" * 64
        trace["verifications"] += 1

    monkeypatch.setattr(d.delivery, "verify", verify)

    def command(args, **kwargs):
        trace["calls"].append(args)
        if args[:3] == ["docker", "image", "inspect"]:
            if args[-1] == archive_tag and not (trace["existing"] or trace["loaded"]):
                return SimpleNamespace(returncode=1, stdout="", stderr="missing")
            document = {
                "Id": trace["id"],
                "Os": "linux",
                "Architecture": "amd64",
                "Config": {"Labels": {"org.opencontainers.image.revision": trace["revision"]}},
                "RepoDigests": ["nginx@" + d.rollout.GATEWAY_IMAGE.split("@", 1)[1]],
            }
            return SimpleNamespace(returncode=0, stdout=json.dumps([document]), stderr="")
        if args[:3] == ["docker", "image", "load"]:
            trace["loaded"] = True
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(d.f, "run", command)
    monkeypatch.setattr(
        d,
        "load",
        lambda tag: {"image": tag, "docker_id": image_id, "loaded_identities": [image_id]},
    )
    return source, destination, trace, archive_tag


def test_prepare_reuses_exact_runtime_builds_only_release_carrier_and_preserves_source(importer):
    source, work, trace, archive_tag = importer
    before = d.checks.hashes(source / "release")
    result = d.prepare(source, "b" * 40, "c" * 64)
    record = json.loads((work / "prepared.json").read_text())
    assert result["status"] == "qualified_runtime_and_release_carrier_loaded"
    assert not record["runtime_rebuilt"] and not record["fit_executed"]
    assert record["artifact_carrier_built"]
    assert trace["verifications"] == 2
    builds = [c for c in trace["calls"] if c[:2] == ["docker", "build"]]
    assert (
        len(builds) == 1
        and "fraud-kind-serving-artifacts:" in builds[0][builds[0].index("--tag") + 1]
    )
    assert ["docker", "image", "rm", archive_tag] in trace["calls"]
    assert d.checks.hashes(source / "release") == before


@pytest.mark.parametrize("bad", ["id", "revision", "foreign_tag"])
def test_bad_runtime_stops_before_carrier_build_or_Kubernetes_and_preserves_preexisting_tag(
    importer, bad
):
    source, work, trace, archive_tag = importer
    if bad in {"id", "foreign_tag"}:
        trace["id"] = "sha256:" + "e" * 64
    if bad == "revision":
        trace["revision"] = "e" * 40
    if bad == "foreign_tag":
        trace["existing"] = True
    with pytest.raises(ValueError):
        d.prepare(source, "b" * 40, "c" * 64)
    assert not (work / "prepared.json").exists()
    assert not any(c[:2] == ["docker", "build"] for c in trace["calls"])
    assert json.loads(next(work.glob("prepare-*/failure.json")).read_text())["reason"]
    removed = [c for c in trace["calls"] if c[:3] == ["docker", "image", "rm"]]
    assert removed == ([] if bad == "foreign_tag" else [["docker", "image", "rm", archive_tag]])


def test_prepare_keeps_preexisting_matching_archive_tag(importer):
    source, _, trace, archive_tag = importer
    trace["existing"] = True
    d.prepare(source, "b" * 40, "c" * 64)
    assert not any(c[:3] == ["docker", "image", "load"] for c in trace["calls"])
    assert ["docker", "image", "rm", archive_tag] not in trace["calls"]


def test_installation_failure_records_diagnostics_without_removing_owned_resources(
    tmp_path, monkeypatch
):
    destination = tmp_path / "serving"
    monkeypatch.setattr(d, "work", lambda: destination)
    monkeypatch.setattr(k.f, "owner", lambda: {"node_uid": "owned"})
    monkeypatch.setattr(k.f, "private_json", lambda p, value: p.write_text(json.dumps(value)))
    monkeypatch.setattr(
        k, "install_baseline", lambda: (_ for _ in ()).throw(ValueError("startup failed"))
    )
    monkeypatch.setattr(k, "diagnostics", lambda: {"worker_logs": "reason preserved"})
    with pytest.raises(ValueError, match="startup failed"):
        k.up()
    report = json.loads(next(destination.glob("installation-*.json")).read_text())
    assert (
        report["status"] == "failed" and report["diagnostics"]["worker_logs"] == "reason preserved"
    )


@pytest.mark.parametrize("wrong", ["restart", "image", "owner"])
def test_live_resource_verifier_rejects_wrong_images_restarts_or_foreign_owner(
    staged, monkeypatch, wrong
):
    tag = staged["images"]["runtime"]["image"]
    dep = {
        "metadata": {
            "name": "fraud-serving-blue",
            "uid": "deployment",
            "labels": {"app.kubernetes.io/component": "blue"},
            "annotations": {k.OWNER: ID},
        },
        "spec": {"replicas": 1},
    }
    rs = {
        "metadata": {
            "uid": "replicaset",
            "ownerReferences": [{"uid": "deployment", "controller": True}],
        }
    }
    pod = {
        "metadata": {
            "name": "blue",
            "uid": "pod",
            "labels": {"app.kubernetes.io/component": "blue"},
            "annotations": {k.OWNER: ID},
            "ownerReferences": [{"uid": "replicaset", "controller": True}],
        },
        "spec": {
            "nodeName": k.f.CLUSTER + "-control-plane",
            "containers": [{"name": "api", "image": tag}],
            "initContainers": [
                {"name": "release", "image": staged["images"]["artifacts"]["image"]}
            ],
        },
        "status": {
            "conditions": [{"type": "Ready", "status": "True"}],
            "containerStatuses": [
                {"name": "api", "ready": True, "restartCount": 0, "imageID": "sha256:" + "d" * 64}
            ],
            "initContainerStatuses": [
                {
                    "name": "release",
                    "restartCount": 0,
                    "imageID": "sha256:" + "d" * 64,
                    "state": {"terminated": {"exitCode": 0}},
                }
            ],
        },
    }
    if wrong == "restart":
        pod["status"]["containerStatuses"][0]["restartCount"] = 1
    elif wrong == "image":
        pod["status"]["containerStatuses"][0]["imageID"] = "sha256:" + "e" * 64
    else:
        pod["metadata"]["annotations"][k.OWNER] = "foreign"
    monkeypatch.setattr(k, "owned", lambda _: [dep])
    monkeypatch.setattr(k, "objects", lambda kind: {"replicasets": [rs], "pods": [pod]}[kind])
    with pytest.raises(ValueError, match="ownership|Image|digest"):
        k.observe(staged)


@pytest.mark.parametrize("role", ["runtime", "artifacts", "gateway"])
def test_image_import_exports_one_platform_preserves_digest_and_cleans_owned_archive(
    tmp_path, monkeypatch, role
):
    tag = "fraud-kind-serving-" + role + ":" + ID
    identity = "sha256:" + "d" * 64
    calls = []
    archives = []
    monkeypatch.setattr(d, "work", lambda: tmp_path)
    monkeypatch.setattr(d.f, "managed_destination", lambda p, _: p)

    def run(args, **kwargs):
        calls.append(args)
        if args[:3] == ["docker", "image", "inspect"]:
            return SimpleNamespace(
                stdout=json.dumps([{"Id": identity, "Os": "linux", "Architecture": "amd64"}])
            )
        if args[:3] == ["docker", "image", "save"]:
            assert args[3:5] == ["--platform", "linux/amd64"] and args[-1] == tag
            path = Path(args[args.index("--output") + 1])
            archives.append(path)
            path.write_bytes(b"controlled single-platform image archive")
        elif args[:3] == ["kind", "load", "image-archive"]:
            assert args[3:5] == ["--name", "fraud-platform"]
            assert Path(args[-1]) == archives[0] and archives[0].exists()
            assert archives[0].stat().st_mode & 0o077 == 0
        else:
            pytest.fail("Unexpected image mutation: " + repr(args))
        assert kwargs["check"] is False
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(d.f, "run", run)
    monkeypatch.setattr(d, "cri", lambda _: {"repoTags": [tag], "id": identity})
    result = d.load(tag)
    assert result["docker_id"] == identity and result["loaded_identities"] == [identity]
    assert result["platform"] == "linux/amd64" and result["import_method"] == "platform_archive"
    assert result["archive_bytes"] > 0 and len(result["archive_sha256"]) == 64
    assert result["temporary_archive_removed"] and not archives[0].exists()
    assert not any("docker-image" in args for args in calls)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("failure", ["save", "import", "identity"])
def test_import_failure_surfaces_native_error_and_cleans_only_temporary_archive(
    tmp_path, monkeypatch, failure
):
    tag = "fraud-kind-serving-gateway:" + ID
    marker = tmp_path / "keep.json"
    marker.write_text("preserve")
    monkeypatch.setattr(d, "work", lambda: tmp_path)
    monkeypatch.setattr(d.f, "managed_destination", lambda p, _: p)
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if args[:3] == ["docker", "image", "inspect"]:
            return SimpleNamespace(
                stdout=json.dumps(
                    [{"Id": "sha256:" + "d" * 64, "Os": "linux", "Architecture": "amd64"}]
                )
            )
        if args[:3] == ["docker", "image", "save"]:
            Path(args[args.index("--output") + 1]).write_bytes(b"partial or complete export")
            if failure == "save":
                return SimpleNamespace(
                    returncode=1, stdout="export stdout", stderr="no suitable export target"
                )
        elif args[:3] == ["kind", "load", "image-archive"]:
            if failure == "import":
                return SimpleNamespace(
                    returncode=1, stdout="kind output", stderr="ctr: content digest missing"
                )
        else:
            pytest.fail("Unexpected command: " + repr(args))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(d.f, "run", run)
    monkeypatch.setattr(d, "cri", lambda _: {"repoTags": ["foreign"], "id": "sha256:" + "d" * 64})
    with pytest.raises(
        ValueError,
        match={
            "save": "no suitable export target",
            "import": "content digest missing",
            "identity": "Loaded tag differs",
        }[failure],
    ):
        d.load(tag)
    assert marker.read_text() == "preserve" and list(tmp_path.iterdir()) == [marker]
    assert not any("prune" in args or "rm" in args for args in calls)
    if failure == "save":
        assert not any(args[0] == "kind" for args in calls)
