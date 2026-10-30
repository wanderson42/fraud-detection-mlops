"""Offline archive, recovery and isolation contracts; no native acceptance implied."""

from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path

import pytest
from scripts.platform.tracking_backup import archive as a
from scripts.platform.tracking_backup import controller as c
from scripts.platform.tracking_backup import worker as w
from scripts.tracking import capture_worker as shared
import yaml


def files(folder):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "objects").mkdir()
    (folder / "objects/0000.bin").write_bytes(b"payload")
    (folder / "postgres.dump").write_bytes(b"PGDMP-offline-fixture")
    shared.save(
        folder / "metadata.json",
        {
            "database": a.DATABASE,
            "role": a.ROLE,
            "tables": {},
            "locations": [
                {"table": "runs", "id": "run", "uri": "mlflow-artifacts:/1/run/artifacts"}
            ],
        },
    )
    shared.save(
        folder / "objects.json",
        [
            {
                "key": "v1/1/run/artifacts/item.json",
                "file": "objects/0000.bin",
                "bytes": 7,
                "sha256": a.sha(folder / "objects/0000.bin"),
            }
        ],
    )
    shared.save(
        folder / "control.json", {"model_uri": "models:/m-abc123", "model_sha256": "f" * 64}
    )
    shared.save(folder / "owner-marker.json", {"owner_id": "owner"})


@pytest.fixture
def sealed(tmp_path):
    folder = tmp_path / ("a" * 32)
    files(folder)
    digest = a.seal(
        folder,
        {
            "schema": a.SCHEMA,
            "status": "captured_verified",
            "capture_id": folder.name,
            "source_recovered": True,
            "database": a.DATABASE,
            "bucket": a.BUCKET,
            "tracking_owner_id": "owner",
            "model_sha256": "f" * 64,
        },
    )
    return folder, digest


def test_closed_archive_requires_external_pin_and_exact_members(sealed):
    folder, digest = sealed
    assert a.verify(folder, digest)["capture_id"] == folder.name
    with pytest.raises(ValueError, match="digest"):
        a.verify(folder, "0" * 64)
    (folder / "extra").write_bytes(b"unreviewed")
    with pytest.raises(ValueError, match="members"):
        a.verify(folder, digest)


@pytest.mark.parametrize(
    "name", ["../postgres.dump", "/tmp/test", "objects/../control.json", "objects/key.json"]
)
def test_object_names_cannot_escape_archive(name):
    with pytest.raises(ValueError, match="member"):
        a.member(name)


@pytest.mark.parametrize("member", ["postgres.dump", "objects/0000.bin", "control.json"])
def test_byte_tampering_rejected(sealed, member):
    folder, digest = sealed
    (folder / member).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="changed"):
        a.verify(folder, digest)


def test_symlink_rejected_even_when_contents_match(sealed, tmp_path):
    folder, digest = sealed
    payload = folder / "objects/0000.bin"
    other = tmp_path / "outside"
    payload.rename(other)
    payload.symlink_to(other)
    with pytest.raises(ValueError, match="path"):
        a.verify(folder, digest)


@pytest.mark.parametrize("mutation", ["duplicate", "role", "owner", "uncovered"])
def test_semantic_integrity_after_manifest_repin(sealed, mutation):
    folder, _ = sealed
    manifest = a.document(folder / "manifest.json")
    (folder / "manifest.json").unlink()
    if mutation == "duplicate":
        manifest["files"].append(manifest["files"][0])
        shared.save(folder / "manifest.json", manifest)
        with pytest.raises(ValueError, match="Duplicate"):
            a.verify(folder, a.sha(folder / "manifest.json"))
        return
    name = {"role": "metadata.json", "owner": "owner-marker.json", "uncovered": "objects.json"}[
        mutation
    ]
    value = a.document(folder / name)
    if mutation == "role":
        value["role"] = "other"
    elif mutation == "owner":
        value["owner_id"] = "other"
    else:
        value[0]["key"] = "v1/foreign/item"
    (folder / name).unlink()
    shared.save(folder / name, value)
    with pytest.raises(ValueError):
        a.seal(folder, manifest)


def test_environment_is_raw_for_docker_and_private(tmp_path):
    path = tmp_path / "private.env"
    c.write_environment(path, {"PASSWORD": "abc123", "ENDPOINT": "http://127.0.0.1:9000"})
    assert path.read_text() == "PASSWORD=abc123\nENDPOINT=http://127.0.0.1:9000\n"
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        c.write_environment(path, {"X": "y"})


@pytest.mark.parametrize("bad", ["x\ny", "x$HOME", "x,y", "space here", '"quoted"'])
def test_environment_rejects_interpolation_or_mount_delimiters(tmp_path, bad):
    with pytest.raises(ValueError, match="environment"):
        c.write_environment(tmp_path / "private.env", {"X": bad})


@pytest.fixture
def capture_harness(tmp_path, monkeypatch):
    calls = []
    resource = {"replicas": 1, "uid": "source"}
    before = {
        "resources": {"Deployment/fraud-tracking": resource},
        "owner": {"container_id": "native"},
    }
    monkeypatch.setattr(c, "ROOT", tmp_path)
    monkeypatch.setattr(c, "work", lambda: tmp_path)
    monkeypatch.setattr(c, "committed", dict)
    monkeypatch.setattr(c, "source_pins", dict)
    monkeypatch.setattr(c, "preflight", lambda session: calls.append("preflight"))
    monkeypatch.setattr(c, "snapshot", lambda session: deepcopy(before))
    monkeypatch.setattr(c, "model_control", lambda: {"model_sha256": "f" * 64})
    monkeypatch.setattr(c.tracking, "credentials", lambda: {"owner_id": "owner"})
    monkeypatch.setattr(c.tracking, "inventory", lambda: calls.append("healthy"))
    monkeypatch.setattr(c.f, "kube", lambda *args, **kw: calls.append("rollout"))
    monkeypatch.setattr(
        c.operations,
        "change_replica",
        lambda plan, kind, name, count: calls.append(("scale", name, count)),
    )

    @contextmanager
    def environment():
        yield tmp_path / "env"

    monkeypatch.setattr(c, "source_environment", environment)

    def command(args, **kw):
        if "pg_dump" in args:
            kw["destination"].write_bytes(b"PGDMP-fixture")
        return b"TABLE DATA public alembic_version"

    monkeypatch.setattr(c, "command", command)

    def worker(action, env, output=None):
        calls.append(action)
        return {"status": "objects_captured"}

    monkeypatch.setattr(c, "source_worker", worker)
    monkeypatch.setattr(a, "seal", lambda output, value: "e" * 64)
    return calls


def test_capture_only_scales_tracking_and_recovers_once(capture_harness, tmp_path):
    result = c.capture(True, "session")
    assert result["status"] == "captured_verified" and result["source_recovered"]
    assert not result["postgres_backup_restore_validated"]
    scales = [call for call in capture_harness if isinstance(call, tuple)]
    assert scales == [("scale", "fraud-tracking", 0), ("scale", "fraud-tracking", 1)]
    assert a.document(tmp_path / ("capture-" + result["capture_id"] + ".json")) == result


@pytest.mark.parametrize("stage", ["inventory", "objects", "dump", "scale", "interrupt"])
def test_capture_failure_always_attempts_resume_and_saves_receipt(
    capture_harness, tmp_path, monkeypatch, stage
):
    if stage in {"inventory", "objects", "interrupt"}:

        def fail(action, *args):
            if action == ("inventory" if stage == "interrupt" else stage):
                raise KeyboardInterrupt() if stage == "interrupt" else ValueError("failure")
            return {}

        monkeypatch.setattr(c, "source_worker", fail)
    elif stage == "dump":
        monkeypatch.setattr(
            c, "command", lambda *args, **kw: (_ for _ in ()).throw(ValueError("dump"))
        )
    else:

        def scale(plan, kind, name, count):
            capture_harness.append(("scale", name, count))
            if count == 0:
                raise ValueError("scale interrupted")

        monkeypatch.setattr(c.operations, "change_replica", scale)
    with pytest.raises((ValueError, KeyboardInterrupt)):
        c.capture(True)
    assert ("scale", "fraud-tracking", 1) in capture_harness
    receipt = a.document(
        next(p for p in tmp_path.glob("capture-*.json") if not p.name.endswith("-plan.json"))
    )
    assert receipt["status"] == "failed" and receipt["source_recovered"]


def test_failed_resume_still_records_failure(capture_harness, tmp_path, monkeypatch):
    monkeypatch.setattr(
        c.operations,
        "change_replica",
        lambda *args: (_ for _ in ()).throw(ValueError("unavailable")),
    )
    with pytest.raises(ValueError):
        c.capture(True)
    receipt = a.document(
        next(p for p in tmp_path.glob("capture-*.json") if not p.name.endswith("-plan.json"))
    )
    assert not receipt["source_recovered"] and receipt["status"] == "failed"


def test_no_declaration_means_no_preflight_or_scaling(capture_harness):
    with pytest.raises(ValueError, match="no-other-writers"):
        c.capture(False)
    assert capture_harness == []


def test_target_topology_has_no_host_ports_external_storage_or_client_credentials():
    config = yaml.safe_load((c.INFRA / "restore.compose.yaml").read_text())
    assert config["networks"] == {"default": {"internal": True}}
    assert config["volumes"] == {"postgres-data": None, "rustfs-data": None}
    assert set(config["services"]) == {"postgres", "rustfs", "mlflow", "seed", "client"}
    for service in config["services"].values():
        assert not any(
            k in service
            for k in ("ports", "network_mode", "privileged", "container_name", "networks")
        )
        assert service["pull_policy"] == "never"
        assert service["environment"]["MLFLOW_DISABLE_TELEMETRY"] == "true"
    client = config["services"]["client"]
    assert not any(
        k.startswith(("AWS_", "BACKUP_PG", "TARGET_PG", "POSTGRES_"))
        for k in client["environment"]
    )
    assert len(client["volumes"]) == 1 and client["volumes"][0]["read_only"]
    assert client["volumes"][0]["source"] == "${PROOF_DIRECTORY}"
    seed = config["services"]["seed"]
    assert (
        seed["volumes"][0]["read_only"] and seed["volumes"][0]["source"] == "${CAPTURE_DIRECTORY}"
    )
    assert config["services"]["postgres"]["image"] == c.f.images()["postgres"]
    assert config["services"]["rustfs"]["image"] == c.f.images()["rustfs"]


def test_client_refuses_storage_credentials_before_any_api(monkeypatch, tmp_path):
    monkeypatch.setenv("BACKUP_PG_PASSWORD", "private")
    with pytest.raises(ValueError, match="credentials"):
        w.client_check(tmp_path)


def test_target_project_validation_prevents_source_cleanup():
    with pytest.raises(ValueError, match="Unmanaged"):
        c.target_command("fraud-platform", Path("/tmp/env"), "down", "--volumes")


def test_worker_reports_require_reviewed_code_hash():
    with pytest.raises(ValueError, match="code differs"):
        c.worker_result(json.dumps({"status": "runtime_ready", "worker_sha256": "0" * 64}))


@pytest.fixture
def restore_harness(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(c, "committed", dict)
    monkeypatch.setattr(c, "directory", lambda identity: tmp_path / identity)
    monkeypatch.setattr(c, "work", lambda: tmp_path)
    monkeypatch.setattr(a, "verify", lambda folder, pin: {"session_id": "session"})
    monkeypatch.setattr(c, "image_record", lambda: {"image_id": "sha256:reviewed"})
    monkeypatch.setattr(c, "snapshot", lambda session: {"owner": {"container_id": "original"}})
    monkeypatch.setattr(
        c, "target_identity", lambda project, env, service: {"container_id": "new-" + service}
    )

    def command(args, **kw):
        calls.append(args)
        if "compose" in args and "run" in args:
            action = args[-1]
            return json.dumps(
                {
                    "worker_sha256": a.sha(Path(w.__file__)),
                    "status": {
                        "runtime-check": "runtime_ready",
                        "seed": "restored_data_verified",
                        "client": "target_client_verified",
                        "verify": "restored_data_verified",
                    }[action],
                    "model_reload_validated": True,
                }
            ).encode()
        return b""

    monkeypatch.setattr(c, "command", command)
    return calls, command


def test_restore_never_executes_dump_on_source_and_cleans_new_project(restore_harness, tmp_path):
    calls, _ = restore_harness
    result = c.restore("a" * 32, "e" * 64, True)
    assert result["status"] == "verified" and result["temporary_resources_removed"]
    assert (
        result["postgres_backup_restore_validated"]
        and not result["platform_backup_restore_validated"]
    )
    sql = next(args for args in calls if "pg_restore" in args)
    assert sql[:4] == ["docker", "exec", "-i", "new-postgres"]
    assert "--single-transaction" in sql and "--clean" not in sql
    assert not any("kubectl" in args or "scale" in args for args in calls)
    down = next(args for args in calls if "down" in args)
    assert result["project"] in down and "--volumes" in down
    assert not list(tmp_path.rglob("*.env"))
    receipt = a.document(tmp_path / (result["project"] + ".json"))
    assert receipt == result


@pytest.mark.parametrize("failure", ["pg_restore", "seed", "client", "verify", "down"])
def test_target_failures_remove_credentials_and_never_record_acceptance(
    restore_harness, tmp_path, monkeypatch, failure
):
    calls, command = restore_harness

    def fail(args, **kw):
        if (
            failure == "pg_restore"
            and "pg_restore" in args
            or failure == "down"
            and "down" in args
            or "run" in args
            and args[-1] == failure
        ):
            calls.append(args)
            raise ValueError("target unavailable")
        return command(args, **kw)

    monkeypatch.setattr(c, "command", fail)
    with pytest.raises(ValueError):
        c.restore("a" * 32, "e" * 64, True)
    receipt = a.document(next(tmp_path.glob("fraud-kind-restore-*.json")))
    assert receipt["status"] == "failed" and not receipt["postgres_backup_restore_validated"]
    assert not list(tmp_path.rglob("*.env"))
    assert any("down" in args for args in calls)


def test_preexisting_target_labels_prevent_adoption_or_cleanup(
    restore_harness, tmp_path, monkeypatch
):
    calls, command = restore_harness

    def existing(args, **kw):
        if "--filter" in args:
            return b"existing-volume"
        return command(args, **kw)

    monkeypatch.setattr(c, "command", existing)
    with pytest.raises(ValueError, match="already exists"):
        c.restore("a" * 32, "e" * 64, True)
    assert not any("down" in args or "up" in args or "pg_restore" in args for args in calls)
    assert not list(tmp_path.rglob("*.env"))


def test_owner_marker_must_match_before_listing(monkeypatch):
    import io

    class Client:
        def get_object(self, **kw):
            assert kw == {"Bucket": a.BUCKET, "Key": "platform-owner.json"}
            return {"Body": io.BytesIO(shared.encoded({"owner_id": "wrong"}))}

    monkeypatch.setattr(
        shared, "object_listing", lambda *args, **kw: pytest.fail("listed foreign bucket")
    )
    with pytest.raises(ValueError, match="owner"):
        w.owned_listing(Client(), "expected")


def test_source_bucket_listing_rejects_objects_outside_tracking(monkeypatch):
    import io

    class Client:
        def get_object(self, **kw):
            return {"Body": io.BytesIO(shared.encoded({"owner_id": "owner"}))}

    monkeypatch.setattr(shared, "object_listing", lambda *args, **kw: [{"key": "other/data"}])
    with pytest.raises(ValueError, match="outside"):
        w.owned_listing(Client(), "owner")


def test_interrupted_capture_has_durable_plan_before_pause(capture_harness, tmp_path, monkeypatch):
    def scale(plan, kind, name, count):
        assert list(tmp_path.glob("capture-*-plan.json"))
        if count == 0:
            raise KeyboardInterrupt()

    monkeypatch.setattr(c.operations, "change_replica", scale)
    with pytest.raises(KeyboardInterrupt):
        c.capture(True, "session")
    plan = a.document(next(tmp_path.glob("capture-*-plan.json")))
    assert plan["before"]["resources"]["Deployment/fraud-tracking"]["replicas"] == 1
    assert plan["session_id"] == "session"


@pytest.mark.parametrize("foreign", [False, True])
def test_emergency_recovery_rejects_changed_source(
    capture_harness, tmp_path, monkeypatch, foreign
):
    result = c.capture(True, "session")
    identity = result["capture_id"]
    before = a.document(tmp_path / ("capture-" + identity + "-plan.json"))["before"]
    current = deepcopy(before)
    current["resources"]["Deployment/fraud-tracking"]["replicas"] = 0
    if foreign:
        current["resources"]["Deployment/fraud-tracking"]["uid"] = "foreign"

    def snapshot(session):
        return deepcopy(current)

    def scale(plan, kind, name, count):
        current["resources"]["Deployment/fraud-tracking"]["replicas"] = count

    monkeypatch.setattr(c, "snapshot", snapshot)
    monkeypatch.setattr(c.operations, "change_replica", scale)
    if foreign:
        with pytest.raises(ValueError, match="Protected"):
            c.recover(identity)
        assert current["resources"]["Deployment/fraud-tracking"]["replicas"] == 0
    else:
        recovered = c.recover(identity)
        assert recovered["source_recovered"] and not recovered["backup_accepted"]
        assert current == before


def test_source_change_during_restore_is_reported_as_failure(
    restore_harness, tmp_path, monkeypatch
):
    snapshots = iter(
        [{"owner": {"container_id": "original"}}, {"owner": {"container_id": "changed"}}]
    )
    monkeypatch.setattr(c, "snapshot", lambda session: next(snapshots))
    with pytest.raises(ValueError, match="Original source changed"):
        c.restore("a" * 32, "e" * 64, True)
    receipt = a.document(next(tmp_path.glob("fraud-kind-restore-*.json")))
    assert receipt["status"] == "failed" and receipt["source_modified"] is True
    assert (
        receipt["temporary_resources_removed"] and not receipt["postgres_backup_restore_validated"]
    )


@pytest.mark.parametrize("action", ["seed", "verify"])
def test_restore_worker_uses_uuid_mount_and_archive_keeps_strict_identity(
    sealed, tmp_path, monkeypatch, action
):
    import shutil

    folder, digest = sealed
    config = yaml.safe_load((c.INFRA / "restore.compose.yaml").read_text())
    seed = config["services"]["seed"]
    mount = seed["volumes"][0]["target"]
    env = seed["environment"]["BACKUP_CAPTURE_PATH"]
    assert mount == env == "/capture/${BACKUP_CAPTURE_ID:?Explicit capture ID required}"
    rendered = env.replace("${BACKUP_CAPTURE_ID:?Explicit capture ID required}", folder.name)
    monkeypatch.setenv("BACKUP_CAPTURE_PATH", rendered)
    path = w.capture_path(action)
    assert path == Path("/capture") / folder.name
    mounted = tmp_path / "container" / path.relative_to("/")
    shutil.copytree(folder, mounted)
    assert a.verify(mounted, digest)["capture_id"] == folder.name
    monkeypatch.setattr(w, "s3", lambda: object())
    monkeypatch.setattr(
        w, "verify_data", lambda directory, client: {"status": "restored_data_verified"}
    )
    assert w.execute("verify", mounted, tmp_path, digest)["status"] == "restored_data_verified"
    renamed = mounted.with_name("b" * 32)
    mounted.rename(renamed)
    with pytest.raises(ValueError, match="identity"):
        a.verify(renamed, digest)


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "/capture",
        "/capture/../" + "a" * 32,
        "/elsewhere/" + "a" * 32,
        "/capture/" + "a" * 31,
        "/capture/" + "A" * 32,
        "/capture/" + "a" * 32 + "/",
    ],
)
def test_worker_rejects_missing_or_unreviewed_capture_mount(monkeypatch, bad):
    monkeypatch.setenv("BACKUP_CAPTURE_PATH", bad)
    with pytest.raises(ValueError, match="UUID capture mount"):
        w.capture_path("seed")
    with pytest.raises(ValueError, match="UUID capture mount"):
        w.capture_path("verify")
    assert w.capture_path("inventory") == Path("/capture")


def test_controller_supplies_explicit_capture_id(restore_harness, monkeypatch):
    original = c.write_environment

    def check(path, values):
        assert values["BACKUP_CAPTURE_ID"] == "a" * 32
        return original(path, values)

    monkeypatch.setattr(c, "write_environment", check)
    assert c.restore("a" * 32, "e" * 64, True)["status"] == "verified"


def test_failure_receipt_identifies_stage_without_private_output(
    restore_harness, tmp_path, monkeypatch
):
    _, command = restore_harness

    def fail(args, **kwargs):
        if "run" in args and args[-1] == "seed":
            raise ValueError("must-not-enter-public-receipt")
        return command(args, **kwargs)

    monkeypatch.setattr(c, "command", fail)
    with pytest.raises(ValueError):
        c.restore("a" * 32, "e" * 64, True)
    receipt = a.document(next(tmp_path.glob("fraud-kind-restore-*.json")))
    assert receipt["failed_phase"] == "s3_seed_and_data_verification"
    assert receipt["error_type"] == "ValueError"
    assert "must-not-enter-public-receipt" not in json.dumps(receipt)
