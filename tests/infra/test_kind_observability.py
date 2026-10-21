"""Completed-state copies and real Helm contracts; no native cluster is accessed."""

from contextlib import contextmanager
import hashlib
from io import BytesIO
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import pytest
from scripts.platform import kind_observability as k
from scripts.platform import kind_observability_state as s
from tests.monitoring.conftest import runtime, setup_job  # noqa: F401
import yaml

from fraud_detection_mlops.storage import managed_paths

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def completed(setup_job):  # noqa: F811
    from scripts.replay.paced_triage_replay import execute

    p = setup_job
    execute(p["factory"], p["output"], p["metadata"], p["runtime"], p["policy"])
    return p["output"]


def packet(directory):
    files = s.hashes(directory)
    line = (json.dumps(files, sort_keys=True, separators=(",", ":")) + "\n").encode()
    return hashlib.sha256(line).hexdigest(), line + b"".join(
        (directory / n).read_bytes() for n in s.FILES
    )


def test_copy_is_consistent_readonly_and_source_bytes_preserved(completed, tmp_path, monkeypatch):
    from scripts.monitoring.collector import snapshot
    from scripts.monitoring.monitoring_exporter import Monitor

    before = s.hashes(completed)
    copied = tmp_path / "copy"
    result = s.capture(completed, copied, True)
    assert result["source_files"] == before == s.hashes(completed)
    assert s.aggregate(snapshot(completed)) == s.aggregate(snapshot(copied))
    assert all((copied / n).stat().st_mode & 0o222 == 0 for n in s.FILES)
    root = tmp_path / "pvc"
    root.mkdir()
    digest, raw = packet(copied)
    assert s.receive(root, digest, BytesIO(raw))["files"] == result["files"]
    monitor = Monitor(root / "snapshot", 0.1)
    monitor.refresh()
    code, _, data = monitor.response("/summary")
    assert code == 200 and s.aggregate(json.loads(data)) == result["expected_summary"]
    assert s.hashes(root / "snapshot") == result["files"]
    assert "fraud_replay_events_total" in k.expected_metrics(result["expected_summary"])
    assert k.expected_metrics(result["expected_summary"])["fraud_feature_comparison_ready"] == 0
    assert b"fraud_feature_jensen_shannon{" not in monitor.response("/metrics")[2]
    with pytest.raises(ValueError, match="overwrite"):
        s.receive(root, digest, BytesIO(raw))


@pytest.mark.parametrize(
    "failure", ["declaration", "running", "sidecar", "symlink", "changed_source"]
)
def test_copy_rejects_unstable_or_unreviewed_source(completed, tmp_path, monkeypatch, failure):
    if failure == "running":
        with sqlite3.connect(completed / "telemetry.sqlite") as con:
            con.execute("UPDATE sessions SET state='running'")
    elif failure == "sidecar":
        (completed / "checkpoint.sqlite-wal").write_bytes(b"not closed")
    elif failure == "symlink":
        p = completed / "metadata.json"
        moved = completed / "original.json"
        p.rename(moved)
        p.symlink_to(moved)
    elif failure == "changed_source":
        original = s.hashes
        count = 0

        def changed(path):
            nonlocal count
            result = original(path)
            if path == completed:
                count += 1
                if count > 1:
                    result["metadata.json"]["sha256"] = "0" * 64
            return result

        monkeypatch.setattr(s, "hashes", changed)
    with pytest.raises(ValueError):
        s.capture(completed, tmp_path / "copy", failure != "declaration")


@pytest.mark.parametrize(
    "failure", ["hash", "truncated", "trailing", "manifest", "paths", "budget"]
)
def test_partial_or_foreign_transfers_never_publish_a_snapshot(completed, tmp_path, failure):
    digest, raw = packet(completed)
    if failure == "hash":
        raw = raw[:-1] + bytes([raw[-1] ^ 1])
    elif failure == "truncated":
        raw = raw[:-32]
    elif failure == "trailing":
        raw += b"x"
    elif failure == "manifest":
        digest = "0" * 64
    else:
        manifest = s.hashes(completed)
        if failure == "paths":
            manifest["../foreign"] = manifest.pop("metadata.json")
        else:
            manifest["metadata.json"]["bytes"] = s.LIMIT + 1
        line = (json.dumps(manifest) + "\n").encode()
        digest = hashlib.sha256(line).hexdigest()
        raw = line
    root = tmp_path / "pvc"
    root.mkdir()
    foreign = root / "preserve.txt"
    foreign.write_bytes(b"untouched")
    with pytest.raises(ValueError):
        s.receive(root, digest, BytesIO(raw))
    assert not (root / "snapshot").exists() and foreign.read_bytes() == b"untouched"


def render(ready):
    helm = shutil.which("helm")
    if not helm:
        pytest.skip("Real Helm required for chart rendering")
    raw = subprocess.check_output(
        [
            helm,
            "template",
            k.RELEASE,
            str(k.CHART),
            "--set",
            "stateReady=" + str(ready).lower(),
            "--namespace",
            "fraud-platform",
            "--kube-version",
            "1.35.8",
        ],
        text=True,
    )
    return [r for r in yaml.safe_load_all(raw) if r]


@pytest.mark.parametrize("ready", [False, True])
def test_real_chart_readonly_state_private_services_security_and_provisioning(ready):
    objects = render(ready)
    claims = [v for v in objects if v["kind"] == "PersistentVolumeClaim"]
    assert len(claims) == 3 and all(
        v["metadata"]["annotations"]["helm.sh/resource-policy"] == "keep" for v in claims
    )
    services = [v for v in objects if v["kind"] == "Service"]
    assert len(services) == 3 and all(v["spec"]["type"] == "ClusterIP" for v in services)
    deps = [v for v in objects if v["kind"] == "Deployment"]
    assert len(deps) == 3
    for dep in deps:
        assert dep["spec"]["replicas"] == int(ready)
        pod = dep["spec"]["template"]["spec"]
        assert not pod["automountServiceAccountToken"] and not pod["enableServiceLinks"]
        assert pod["securityContext"]["runAsNonRoot"]
        assert all("hostPath" not in v for v in pod["volumes"])
        container = pod["containers"][0]
        assert container["imagePullPolicy"] == "Never"
        assert container["securityContext"]["readOnlyRootFilesystem"]
        assert container["securityContext"]["capabilities"]["drop"] == ["ALL"]
        assert (
            "startupProbe" in container
            and "readinessProbe" in container
            and "livenessProbe" in container
        )
        if container["name"] == "exporter":
            assert next(v for v in container["volumeMounts"] if v["name"] == "state")["readOnly"]
            assert next(v for v in pod["volumes"] if v["name"] == "state")[
                "persistentVolumeClaim"
            ]["readOnly"]
            assert "env" not in container
        if container["name"] == "grafana":
            secret = next(v for v in container["env"] if v["name"] == "GF_SECURITY_ADMIN_PASSWORD")
            assert "valueFrom" in secret and "value" not in secret
    config = next(
        v
        for v in objects
        if v["kind"] == "ConfigMap" and v["metadata"]["name"].endswith("-config")
    )["data"]
    assert yaml.safe_load(config["prometheus.yml"])["scrape_configs"][0]["static_configs"][0][
        "targets"
    ] == ["fraud-observability-exporter:19100"]
    assert len(yaml.safe_load(config["alerts.yml"])["groups"][0]["rules"]) == 8
    dashboards = next(
        v
        for v in objects
        if v["kind"] == "ConfigMap" and v["metadata"]["name"].endswith("-dashboards")
    )["data"]
    assert len(dashboards) == 4
    for p in (ROOT / "infra/observability/grafana/dashboards").glob("*.json"):
        assert dashboards[p.name] == p.read_text()
    assert "replay concluído" in json.loads(dashboards["kind-overview.json"])["title"]


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(managed_paths, "DATA_DIR", tmp_path / "data")
    location = tmp_path / "data/storage/kind-platform/observability"
    location.mkdir(parents=True)
    location.chmod(0o700)
    monkeypatch.setattr(k, "work", lambda: location)
    monkeypatch.setattr(k.f, "owner", lambda: {"node_uid": "node"})
    monkeypatch.setattr(k, "credentials", lambda: {"owner_id": "a" * 32, "password": "b" * 48})
    return location


@pytest.mark.parametrize("role", k.ROLES)
def test_image_import_exports_only_selected_platform_and_preserves_identity(
    workspace, monkeypatch, role
):
    calls = []
    tag = "fraud-kind-observability-" + role + ":" + "a" * 32

    def cmd(args, timeout=300):
        calls.append(args)
        if args[:3] == ["docker", "image", "inspect"]:
            return SimpleNamespace(
                stdout=json.dumps(
                    [{"Id": "sha256:" + "1" * 64, "Os": "linux", "Architecture": "amd64"}]
                )
            )
        if args[:3] == ["docker", "image", "save"]:
            Path(args[args.index("--output") + 1]).write_bytes(b"archive")
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(k, "command", cmd)
    monkeypatch.setattr(
        k.delivery, "cri", lambda _: {"id": "sha256:" + "2" * 64, "repoTags": [tag]}
    )
    record = k.import_image(role, tag)
    assert record["docker_id"] == "sha256:" + "1" * 64
    assert record["platform"] == "linux/amd64" and record["temporary_archive_removed"]
    assert calls[1][3:5] == ["--platform", "linux/amd64"]
    assert calls[2][:3] == ["kind", "load", "image-archive"]
    assert not list(workspace.glob("image-import-*"))


def test_foreign_resource_is_rejected_before_mutating_helm(workspace, monkeypatch):
    monkeypatch.setattr(k, "validate", lambda: None)
    monkeypatch.setattr(k, "snapshot_record", dict)
    monkeypatch.setattr(k.f, "inventory", dict)
    monkeypatch.setattr(k, "protected_inventory", list)
    monkeypatch.setattr(
        k, "get", lambda *args: {"metadata": {"annotations": {k.ANNOTATION: "foreign"}}}
    )
    monkeypatch.setattr(k, "helm", lambda *a, **kw: pytest.fail("must not mutate Helm"))
    with pytest.raises(ValueError, match="Foreign"):
        k._up()


def test_gate_recovers_exporter_on_same_pvcs_and_preserves_other_workloads(workspace, monkeypatch):
    count = 0

    def inv():
        nonlocal count
        count += 1
        return {
            "pods": {
                role: {
                    "pod": role + "-pod",
                    "pod_uid": "old" if role != "exporter" or count == 1 else "new",
                    "deployment_uid": role + "-dep",
                }
                for role in k.ROLES
            },
            "pvcs": {"state": {"uid": "state-pvc"}},
        }

    monkeypatch.setattr(k, "inventory", inv)
    monkeypatch.setattr(k, "protected_inventory", lambda: [["Pod", "baseline", "same"]])
    monkeypatch.setattr(k.f, "inventory", lambda: {"postgres": "same"})
    monkeypatch.setattr(
        k,
        "snapshot_record",
        lambda: {
            "source_files": {},
            "files": {},
            "expected_summary": {},
            "source_directory": str(workspace),
        },
    )
    monkeypatch.setattr(k.state, "hashes", lambda _: {})
    monkeypatch.setattr(k, "verify_http", lambda: {"verified": True})
    calls = []
    monkeypatch.setattr(k.f, "kube", lambda *a, **kw: calls.append(a))
    result = k.rehearse()
    assert result["status"] == "success" and result["exporter_pod_recreation_validated"]
    assert calls == [("delete", "pod", "exporter-pod", "--wait=true", "--timeout=60s")]


def test_failed_gate_writes_failed_receipt_without_success(workspace, monkeypatch):
    monkeypatch.setattr(k, "inventory", lambda: (_ for _ in ()).throw(ValueError("foreign image")))
    with pytest.raises(ValueError, match="foreign image"):
        k.rehearse()
    report = json.loads(next(workspace.glob("rehearsal-*.json")).read_text())
    assert report["status"] == "failed" and "foreign image" in report["reason"]
    assert not report.get("exporter_pod_recreation_validated", False)


def test_http_gate_checks_source_gauges_rules_dashboard_panels_and_proxy(
    workspace, completed, tmp_path, monkeypatch
):
    from scripts.monitoring.collector import snapshot

    expected = s.aggregate(snapshot(completed))
    record = {"files": s.hashes(completed), "expected_summary": expected}
    monkeypatch.setattr(k, "snapshot_record", lambda: record)
    monkeypatch.setattr(k, "inventory", lambda: {"pods": {"exporter": {"pod": "owned-exporter"}}})
    monkeypatch.setattr(
        k.f,
        "kube",
        lambda *a, **kw: SimpleNamespace(stdout=json.dumps({"files": record["files"]})),
    )

    @contextmanager
    def forward(role):
        yield "http://" + role

    monkeypatch.setattr(k, "forward", forward)
    seen = []
    corrupted = False

    def http(uri, path, auth=False):
        seen.append((uri, path, auth))
        if path == "/summary":
            return expected
        if path.startswith("/api/v1/format_query?"):
            from urllib.parse import parse_qs, urlsplit

            return {"status": "success", "data": parse_qs(urlsplit(path).query)["query"][0]}
        if path == "/api/v1/targets":
            return {
                "data": {
                    "activeTargets": [
                        {
                            "health": "up",
                            "scrapeUrl": "http://fraud-observability-exporter:19100/metrics",
                        }
                    ]
                }
            }
        if path == "/api/v1/rules":
            rules = yaml.safe_load((k.CHART / "files/alerts.yml").read_text())["groups"][0][
                "rules"
            ]
            return {
                "data": {
                    "groups": [
                        {
                            "rules": [
                                {
                                    "name": row["alert"],
                                    "health": "ok",
                                    "query": row["expr"],
                                    "duration": int(row["for"][:-1])
                                    * (60 if row["for"].endswith("m") else 1),
                                    "labels": row["labels"],
                                    "annotations": row["annotations"],
                                }
                                for row in rules
                            ]
                        }
                    ]
                }
            }
        if path == "/api/datasources/uid/fraud-prometheus":
            assert auth
            return {"type": "prometheus", "url": "http://fraud-observability-prometheus:9090"}
        if path.startswith("/api/datasources/proxy"):
            assert auth
            return {"status": "success", "data": {"result": [{"value": [0, "1"]}]}}
        if path.startswith("/api/dashboards/uid/"):
            assert auth
            for p in (k.CHART / "files/dashboards").glob("*.json"):
                d = json.loads(p.read_text())
                if path.endswith(d["uid"]):
                    if corrupted:
                        d["panels"][0]["title"] = "foreign panel"
                    return {"dashboard": d}
        raise AssertionError(path)

    monkeypatch.setattr(k, "http", http)
    checks = k.expected_metrics(expected)
    monkeypatch.setattr(
        k, "query", lambda uri, expr: {(): 1.0 if expr.startswith("up{") else checks[expr]}
    )
    result = k.verify_http()
    assert result["healthy_alert_rules"] == 8 and len(result["provisioned_dashboard_UIDs"]) == 4
    assert result["grafana_datasource_proxy_verified"]
    assert not result["visual_dashboard_review_validated"] and not result["alert_firing_validated"]
    assert any("proxy/uid" in path and auth for _, path, auth in seen)
    corrupted = True
    with pytest.raises(ValueError, match="dashboard differs"):
        k.verify_http()


@pytest.mark.parametrize("fail_at", ["save", "load"])
def test_failed_image_transfer_cleans_only_owned_temporary_archive(
    workspace, monkeypatch, fail_at
):
    marker = workspace / "preserve.json"
    marker.write_text("preserve")

    def cmd(args, timeout=300):
        if args[:3] == ["docker", "image", "inspect"]:
            return SimpleNamespace(
                stdout=json.dumps(
                    [{"Id": "sha256:" + "1" * 64, "Os": "linux", "Architecture": "amd64"}]
                )
            )
        if args[:3] == ["docker", "image", "save"]:
            if fail_at == "save":
                raise ValueError("native export failure")
            Path(args[args.index("--output") + 1]).write_bytes(b"archive")
        else:
            raise ValueError("native import failure")
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(k, "command", cmd)
    with pytest.raises(ValueError, match="native"):
        k.import_image("exporter", "fraud-kind-observability-exporter:" + "a" * 32)
    assert marker.read_text() == "preserve" and not list(workspace.glob("image-import-*"))


def test_initial_helm_install_does_not_wait_for_consumer_bound_claims(workspace, monkeypatch):
    monkeypatch.setattr(k, "values", lambda ready: {"stateReady": ready})
    calls = []
    monkeypatch.setattr(k, "helm", lambda *a, **kw: calls.append(a))
    k.install(False)
    k.install(True)
    assert "--wait" not in calls[0] and "--wait" in calls[1]


def test_binary_transfer_runs_real_receiver_and_preserves_existing_snapshot(
    workspace, completed, tmp_path, monkeypatch
):
    target = tmp_path / "remote-volume"
    target.mkdir()
    monkeypatch.setattr(
        k,
        "kube_args",
        lambda *args: [
            sys.executable,
            "-m",
            "scripts.platform.kind_observability_state",
            "receive",
            str(target),
            args[-1],
        ],
    )
    record = {"files": s.hashes(completed), "directory": str(completed)}
    k.transfer("controlled-seed", record)
    assert s.hashes(target / "snapshot") == record["files"]
    with pytest.raises(ValueError, match="(rejected|failed)"):
        k.transfer("controlled-seed", record)
    assert s.hashes(target / "snapshot") == record["files"]
    assert not list(workspace.glob("transfer-*"))


@pytest.mark.parametrize("tamper", ["mount", "claim", "name"])
def test_actual_exporter_mount_cannot_be_writable_or_foreign(tamper):
    pod = {
        "spec": {
            "containers": [
                {"volumeMounts": [{"name": "state", "mountPath": "/state", "readOnly": True}]}
            ],
            "volumes": [
                {
                    "name": "state",
                    "persistentVolumeClaim": {"claimName": k.RELEASE + "-state", "readOnly": True},
                }
            ],
        }
    }
    k.validate_exporter_mount(pod)
    if tamper == "mount":
        pod["spec"]["containers"][0]["volumeMounts"][0]["readOnly"] = False
    elif tamper == "claim":
        pod["spec"]["volumes"][0]["persistentVolumeClaim"]["readOnly"] = False
    else:
        pod["spec"]["volumes"][0]["persistentVolumeClaim"]["claimName"] = "historical-source"
    with pytest.raises(ValueError, match="read-only"):
        k.validate_exporter_mount(pod)


def test_helm4_release_query_is_accepted_by_real_cli_parser(workspace, monkeypatch):
    helm = shutil.which("helm")
    if not helm:
        pytest.skip("Real Helm required")
    monkeypatch.setattr(k.f, "workspace", lambda: workspace)
    calls = []

    def cmd(args, timeout=60):
        assert args[0] == "helm"
        calls.append(list(map(str, args)))
        # An explicitly absent config prevents any real cluster contact while
        # exercising Helm 4's actual command/flag parser.
        done = subprocess.run(
            [helm, *map(str, args[1:])], text=True, capture_output=True, check=False, timeout=10
        )
        assert "unknown flag" not in done.stderr
        assert "cluster unreachable" in done.stderr and "kubeconfig" in done.stderr
        return SimpleNamespace(stdout="[]")

    monkeypatch.setattr(k, "command", cmd)
    assert k.releases() == []
    assert calls[0][:3] == ["helm", "list", "--filter"]
    assert "--all" not in calls[0]
    assert calls[0][calls[0].index("--kube-context") + 1] == "kind-fraud-platform"
    assert calls[0][calls[0].index("--namespace") + 1] == "fraud-platform"


def test_helm_failure_keeps_bounded_native_diagnostic(workspace, monkeypatch):
    monkeypatch.setattr(k.f, "workspace", lambda: workspace)
    monkeypatch.setattr(
        k.f,
        "run",
        lambda *a, **kw: SimpleNamespace(returncode=1, stdout="", stderr="native Helm diagnostic"),
    )
    with pytest.raises(ValueError, match="native Helm diagnostic"):
        k.releases()


def test_runtime_image_pins_are_separate_but_legacy_receipt_is_exact(monkeypatch):
    image_runtime = k.runtime_pins()
    assert k.CONTROLLER_SOURCE not in image_runtime
    assert not any(name.startswith("infra/helm/") for name in image_runtime)
    k.verify_image_sources(image_runtime)
    profile = json.loads((k.f.ROOT / k.LEGACY_PROFILE).read_text())
    old = {**profile["source_pins"], k.CONTROLLER_SOURCE: k.PRE_HELM4_FIX_CONTROLLER_SHA256}
    k.verify_image_sources(old)
    for key in (
        k.CONTROLLER_SOURCE,
        "infra/helm/fraud-observability/files/dashboards/feature-health.json",
        "scripts/platform/kind_observability_state.py",
    ):
        with pytest.raises(ValueError, match="source pins changed"):
            k.verify_image_sources({**old, key: "foreign-version"})
    monkeypatch.setattr(
        k,
        "runtime_pins",
        lambda: {**image_runtime, "scripts/platform/kind_observability_state.py": "changed"},
    )
    with pytest.raises(ValueError, match="runtime source pins changed"):
        k.verify_image_sources(old)


def test_legacy_loaded_images_are_reused_without_receipt_rewrite(workspace, monkeypatch):
    profile = json.loads((k.f.ROOT / k.LEGACY_PROFILE).read_text())
    current = profile["source_pins"]
    receipt = {
        "schema": "fraud_kind_observability_images_v1",
        "owner_id": "a" * 32,
        "node_uid": "node",
        "source_pins": {**current, k.CONTROLLER_SOURCE: k.PRE_HELM4_FIX_CONTROLLER_SHA256},
        "images": {
            role: {
                "image": "fraud-kind-observability-" + role + ":" + "a" * 32,
                "loaded_identities": ["sha256:" + "1" * 64],
            }
            for role in k.ROLES
        },
    }
    path = workspace / "images.json"
    path.write_text(json.dumps(receipt))
    before = path.read_bytes()
    monkeypatch.setattr(
        k.delivery, "cri", lambda tag: {"id": "sha256:" + "1" * 64, "repoTags": [tag]}
    )
    assert k.image_record() == receipt and path.read_bytes() == before
    monkeypatch.setattr(k.delivery, "cri", lambda tag: {"id": "foreign", "repoTags": [tag]})
    with pytest.raises(ValueError, match="image identity differs"):
        k.image_record()
    assert path.read_bytes() == before
