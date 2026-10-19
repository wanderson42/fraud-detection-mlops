"""Control-plane gates, failure compensation and managed receipts, without Docker."""

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts.serving import blue_green_rehearsal as rollout
from scripts.serving import check_docker_runtime as checks

from fraud_detection_mlops.serving.model_release import load_release


def test_same_weights_repackage_loads_but_corrupt_candidate_is_blocked(serving_release, tmp_path):
    source, digest = serving_release
    before = checks.hashes(source)
    blue, green, invalid = rollout.stage_releases(source, tmp_path)
    green_pin = checks.hashes(green)["manifest.json"]
    a, b = load_release(blue, digest), load_release(green, green_pin)
    assert a.manifest.serving_release_id != b.manifest.serving_release_id
    assert a.manifest.model_sha256 == b.manifest.model_sha256
    assert checks.hashes(blue) == before == checks.hashes(source)
    assert (blue / "pipeline.skops").read_bytes() == (green / "pipeline.skops").read_bytes()
    assert b.manifest.source["purpose"] == "same_weights_blue_green_rollback_drill"
    with pytest.raises(checks.CheckFailed, match="Hash do modelo diverge"):
        checks.read_release(invalid, digest)


@pytest.mark.parametrize("name", ["blue;stop", "x\nserver {}", "../blue", "127.0.0.1", "blue"])
def test_nginx_backend_must_be_owned_generated_name(name):
    with pytest.raises(checks.CheckFailed):
        rollout.configuration(name)


def test_nginx_config_disables_implicit_failover_and_preserves_error_protocol(tmp_path):
    backend = "fraud-serving-check-" + "a" * 32 + "-blue"
    rollout.write_configuration(tmp_path, backend)
    body = (tmp_path / "nginx.conf").read_text()
    assert "proxy_next_upstream off;" in body
    assert "listen 8080;" in body and "access_log off;" in body
    assert 'return 502 \'{"error":"upstream_unavailable"}\';' in body
    assert not (tmp_path / "nginx.pending").exists()
    assert "proxy_pass http://" + backend + ":8000;" in body


@pytest.fixture
def routing(monkeypatch):
    gateway = SimpleNamespace(url="gateway", selected=[])
    gateway.select = lambda name: gateway.selected.append(name)
    old = ("blue", "blue-url", {"serving_release_id": "A"}, {})
    new = ("green", "green-url", {"serving_release_id": "B"}, {})
    calls = []
    monkeypatch.setattr(rollout, "gate", lambda url, *a: calls.append(url))
    monkeypatch.setattr(rollout, "await_gate", lambda url, manifest, smoke: manifest)
    return gateway, new, old, calls


def test_successful_switch_checks_both_backends_before_changing_route(routing):
    gateway, new, old, calls = routing
    assert rollout.switch_or_rollback(gateway, new, old)["serving_release_id"] == "B"
    assert calls == ["green-url", "blue-url"] and gateway.selected == ["green"]


def test_failed_candidate_direct_gate_never_switches(routing, monkeypatch):
    gateway, new, old, _ = routing
    monkeypatch.setattr(
        rollout, "gate", lambda *a: (_ for _ in ()).throw(checks.CheckFailed("candidate failed"))
    )
    with pytest.raises(checks.CheckFailed):
        rollout.switch_or_rollback(gateway, new, old)
    assert gateway.selected == []


@pytest.mark.parametrize("failure", ["reload", "parity"])
def test_failed_switch_compensates_with_baseline_and_still_fails(routing, monkeypatch, failure):
    gateway, new, old, _ = routing
    if failure == "reload":

        def select(name):
            gateway.selected.append(name)
            if name == "green":
                raise checks.CheckFailed("reload failed")

        gateway.select = select
    else:

        def wait(url, manifest, smoke):
            if manifest["serving_release_id"] == "B":
                raise checks.CheckFailed("candidate parity failed")

        monkeypatch.setattr(rollout, "await_gate", wait)
    with pytest.raises(checks.CheckFailed):
        rollout.switch_or_rollback(gateway, new, old)
    assert gateway.selected == ["green", "blue"]


def test_healthy_release_does_not_trigger_rollback(routing):
    gateway, new, old, _ = routing
    assert rollout.recover_if_unhealthy(gateway, new, old) is False
    assert gateway.selected == []


def test_candidate_outage_triggers_rollback_after_previous_health_gate(routing, monkeypatch):
    gateway, new, old, calls = routing

    def gate(url, *args):
        calls.append(url)
        if url == "gateway":
            raise checks.CheckFailed("outage")

    monkeypatch.setattr(rollout, "gate", gate)
    assert rollout.recover_if_unhealthy(gateway, new, old) is True
    assert calls == ["gateway", "blue-url"] and gateway.selected == ["blue"]


def test_broken_previous_release_cannot_be_reported_as_recovered(routing, monkeypatch):
    gateway, new, old, _ = routing
    monkeypatch.setattr(
        rollout, "gate", lambda *a: (_ for _ in ()).throw(checks.CheckFailed("all down"))
    )
    with pytest.raises(checks.CheckFailed):
        rollout.recover_if_unhealthy(gateway, new, old)
    assert gateway.selected == []


class NetworkDocker:
    def __init__(self, cleanup_failure=False):
        self.containers = []
        self.calls = []
        self.networks = {}
        self.cleanup_failure = cleanup_failure

    @property
    def network(self):
        return next(iter(self.networks), None)

    def run(self, *args, **kwargs):
        self.calls.append(args)
        if args[:2] == ("network", "create"):
            self.networks[args[-1]] = "--internal" in args
        if args[:2] == ("network", "inspect"):
            name = args[-1]
            owner = name.removesuffix("-edge")
            return json.dumps(
                [{"Internal": self.networks[name], "Labels": {"fraud.rollout": owner}}]
            )
        if args[:2] == ("network", "ls"):
            return "\n".join(self.networks)
        if args[:2] == ("network", "rm"):
            del self.networks[args[-1]]
        return ""

    def cleanup(self):
        if self.cleanup_failure:
            raise checks.CheckFailed("cleanup failed")
        self.containers.clear()


def test_owned_network_cleanup_on_exception_preserves_failure():
    docker = NetworkDocker()
    report = {}
    with (
        pytest.raises(checks.CheckFailed, match="injected"),
        rollout.private_network(docker, "owned-test", report),
    ):
        raise checks.CheckFailed("injected")
    assert report["temporary_resources_removed"] and docker.network is None
    assert ("network", "rm", "owned-test") in docker.calls


def test_network_not_removed_when_container_cleanup_fails():
    docker = NetworkDocker(cleanup_failure=True)
    report = {}
    with (
        pytest.raises(checks.CheckFailed, match="cleanup"),
        rollout.private_network(docker, "owned-test", report),
    ):
        pass
    assert not report.get("temporary_resources_removed")
    assert not any(args[:2] == ("network", "rm") for args in docker.calls)


@pytest.mark.parametrize("failure", ["none", "outage", "incomplete", "cleanup"])
def test_rehearsal_receipt_success_requires_all_gates_and_cleanup(
    serving_release, tmp_path, monkeypatch, failure
):
    source, digest = serving_release
    before = checks.hashes(source)
    monkeypatch.setattr(rollout, "managed_destination", lambda path, area: path)
    monkeypatch.setattr(rollout, "approved_inputs", lambda root: (source, digest, "image"))
    monkeypatch.setattr(rollout, "local_images", lambda *a: [{"Id": "image"}, {"Id": "gateway"}])
    docker = NetworkDocker(cleanup_failure=failure == "cleanup")
    monkeypatch.setattr(checks, "Docker", lambda: docker)

    def exercise(docker, image, gateway, network, stage, source, digest, report):
        (stage / "keep-on-cleanup-failure").write_text("control")
        report.update(
            events=deepcopy(rollout.EXPECTED_EVENTS),
            rollback_verified=True,
            release_bytes_unchanged=True,
        )
        if failure == "outage":
            raise checks.CheckFailed("gate failed")
        if failure == "incomplete":
            report["events"].pop()

    monkeypatch.setattr(rollout, "exercise", exercise)
    if failure == "none":
        assert rollout.rehearse(tmp_path)["status"] == "success"
    else:
        with pytest.raises(checks.CheckFailed):
            rollout.rehearse(tmp_path)
    receipt = next(tmp_path.glob("data/serving/rollouts/*/result.json"))
    result = json.loads(receipt.read_bytes())
    assert (result["status"] == "success") == (failure == "none")
    assert bool(list(receipt.parent.glob("stage-*"))) == (failure == "cleanup")
    assert checks.hashes(source) == before
    assert receipt.stat().st_mode & 0o777 == 0o600


def test_image_lock_matches_runtime_constant():
    lock = json.loads((Path(rollout.ROOT) / "infra/serving/images.lock.json").read_bytes())
    assert lock["gateway"] == rollout.GATEWAY_IMAGE


def test_gateway_container_is_pinned_readonly_private_and_reload_is_tested(tmp_path):
    class Docker:
        def __init__(self):
            self.containers = []
            self.calls = []

        def run(self, *args, **kwargs):
            self.calls.append(args)

        def inspect(self, name):
            return {
                "Image": "pinned",
                "HostConfig": {"ReadonlyRootfs": True},
                "NetworkSettings": {
                    "Networks": {"own-network": {}, "own-network-edge": {}},
                    "Ports": {"8080/tcp": [{"HostIp": "127.0.0.1", "HostPort": "12345"}]},
                },
                "Mounts": [{"Destination": "/rollout", "RW": False}],
            }

    docker = Docker()
    blue = "fraud-serving-check-" + "a" * 32 + "-blue"
    green = "fraud-serving-check-" + "b" * 32 + "-green"
    gateway = rollout.Gateway(docker, "pinned", "own-network", tmp_path / "gateway", blue)
    assert gateway.url == "http://127.0.0.1:12345"
    create = docker.calls[0]
    assert [create[i + 1] for i, arg in enumerate(create) if arg == "--network"] == [
        "name=own-network-edge,gw-priority=1",
        "own-network",
    ]
    assert create[create.index("--user") + 1] == "101:101"
    assert create[create.index("--pull") + 1] == "never"
    assert create[create.index("--publish") + 1] == "127.0.0.1::8080"
    gateway.select(green)
    assert docker.calls[-2][-6:] == ("nginx", "-t", "-e", "stderr", "-c", "/rollout/nginx.conf")
    assert docker.calls[-1][-7:] == (
        "nginx",
        "-s",
        "reload",
        "-e",
        "stderr",
        "-c",
        "/rollout/nginx.conf",
    )
    assert f"http://{green}:8000" in (tmp_path / "gateway/nginx.conf").read_text()


def test_existing_checker_network_option_is_explicit_and_default_compatible(tmp_path, monkeypatch):
    docker = checks.Docker()
    calls = []
    monkeypatch.setattr(docker, "run", lambda *a, **k: calls.append(a))
    docker.start("pinned-image", tmp_path, "a" * 64, "blue", network="own-network")
    assert calls[0][calls[0].index("--network") + 1] == "own-network"
    calls.clear()
    docker.start("pinned-image", tmp_path, "a" * 64, "valid")
    assert "--network" not in calls[0]


@pytest.mark.parametrize("release", ["approved", "other"])
def test_healthy_private_backend_with_no_published_ports_requires_identity(monkeypatch, release):
    class Docker:
        def __init__(self):
            self.calls = []

        def inspect(self, name):
            return {
                "State": {"Running": True, "Health": {"Status": "healthy"}},
                "NetworkSettings": {"Ports": {"8000/tcp": None}},
                "HostConfig": {"PortBindings": {}},
            }

        def run(self, *args):
            self.calls.append(args)
            return json.dumps([200, {"serving_release_id": release}])

    docker = Docker()
    monkeypatch.setattr(
        checks, "request_json", lambda *a: pytest.fail("private backend accessed from host")
    )
    if release == "approved":
        client = rollout.wait_backend(docker, "owned-blue", "approved")
        assert isinstance(client, rollout.BackendHTTP)
    else:
        with pytest.raises(checks.CheckFailed, match="identity"):
            rollout.wait_backend(docker, "owned-blue", "approved")
    assert docker.calls[0][:3] == ("exec", "owned-blue", "/opt/runtime/bin/python")


def test_private_backend_start_has_no_publish_option(tmp_path, monkeypatch):
    docker = checks.Docker()
    calls = []
    monkeypatch.setattr(docker, "run", lambda *a, **k: calls.append(a))
    docker.start("image", tmp_path, "a" * 64, "blue", network="private", publish=False)
    assert "--publish" not in calls[0]
    assert calls[0][calls[0].index("--network") + 1] == "private"


def test_two_network_roles_and_label_scoped_cleanup():
    docker = NetworkDocker()
    report = {}
    with rollout.private_network(docker, "owned-test", report):
        assert docker.networks == {"owned-test": True, "owned-test-edge": False}
    assert not docker.networks and report["temporary_resources_removed"]
    assert {args[-1] for args in docker.calls if args[:2] == ("network", "rm")} == {
        "owned-test",
        "owned-test-edge",
    }


def test_backend_http_client_and_host_probe_ignore_proxy_environment(serving_release, monkeypatch):
    import subprocess
    import sys

    from tests.serving.test_docker_runtime_checks import http_runtime

    source, pin = serving_release
    manifest, smoke, _ = checks.read_release(source, pin)
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("no_proxy", "")
    with http_runtime(source, pin) as url:

        class Docker:
            def run(self, *args):
                assert args[:3] == ("exec", "owned", "/opt/runtime/bin/python")
                code = args[5].replace("http://127.0.0.1:8000", url)
                return subprocess.check_output(
                    [sys.executable, "-I", "-c", code, *args[6:]], text=True, timeout=10
                )

        assert (
            rollout.gate(rollout.BackendHTTP(Docker(), "owned"), manifest, smoke)["score"]
            == smoke["expected_score"]
        )
        assert rollout.gate(url, manifest, smoke)["score"] == smoke["expected_score"]
