"""Owned KinD dataset transport and semantic restore boundaries without services."""

from contextlib import contextmanager
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest
from scripts.platform import kind_datasets as k
from scripts.platform import kind_datasets_worker as worker
from scripts.platform import kind_foundation as f
from scripts.storage import archive, bronze_archive
from tests.storage.test_bronze_archive import source as bronze_source  # noqa: F401
from tests.storage.test_bronze_transfer import MemoryClient, ServiceError

from fraud_detection_mlops.storage import managed_paths

OWNER = "a" * 32


@pytest.fixture
def prepared(bronze_source, monkeypatch):  # noqa: F811 — shared opaque Bronze fixture
    root, snapshot, inventory = bronze_source
    identity = bronze_archive.prepare_bronze(root)["bundle_id"]
    monkeypatch.setattr(f, "ROOT", root)
    monkeypatch.setattr(managed_paths, "DATA_DIR", root / "data")
    monkeypatch.setattr(f, "owner", lambda: {"node_uid": "node-owned"})
    monkeypatch.setattr(f, "inventory", lambda: {"pods": {"rustfs": "same"}, "pvcs": "same"})
    monkeypatch.setattr(
        k, "image_record", lambda: {"image_id": "sha256:" + "b" * 64, "source_pins": {}}
    )
    f.workspace().mkdir(mode=0o700)
    k.initialize()
    client = MemoryClient()
    own = k.initialize()["owner_id"]

    def job(action, bundle, schema, attempt, source=None):
        if source is not None:
            (attempt / "bundles").mkdir()
            shutil.copytree(source, attempt / "bundles" / bundle)
        return worker.execute(action, bundle, schema, client, attempt, own)

    return SimpleNamespace(
        root=root,
        snapshot=snapshot,
        inventory=inventory,
        identity=identity,
        client=client,
        job=job,
    )


def test_roundtrip_idempotence_and_restore_independent_of_all_originals(prepared):
    p = prepared
    source = p.root / "data/storage/bundles" / p.identity
    before = {q.relative_to(source): q.read_bytes() for q in source.rglob("*") if q.is_file()}
    first = k.transfer("publish", p.identity, "bronze", p.job)
    assert first["status"] == "published_verified"
    assert first["source_bundle_unchanged"] and first["foundation_identities_preserved"]
    assert first["transport"]["uploaded_objects"] == 74
    assert p.client.puts[0] == worker.MARKER
    assert p.client.puts[-1].endswith("/manifest.json")
    second = k.transfer("publish", p.identity, "bronze", p.job)
    assert second["transport"]["uploaded_objects"] == 0
    assert second["transport"]["reused_objects"] == 74
    assert before == {
        q.relative_to(source): q.read_bytes() for q in source.rglob("*") if q.is_file()
    }
    shutil.rmtree(p.snapshot)
    p.inventory.unlink()
    shutil.rmtree(p.root / "data/storage/bundles")
    restored = k.transfer("restore", p.identity, "bronze", p.job)
    assert restored["independent_restored_bundle_verified"]
    assert restored["semantic_verification"]["verified_file_count"] == 70
    assert restored["status"] == "restored_verified"
    assert not restored["primary_storage_migrated"] and not restored["pickle_deserialized"]
    directory = Path(restored["restored_directory"])
    assert before == {
        q.relative_to(directory): q.read_bytes() for q in directory.rglob("*") if q.is_file()
    }
    assert json.loads(Path(restored["report"]).read_text()) == restored


@pytest.mark.parametrize(
    "fault", ["unmarked_bucket", "foreign_marker", "hash", "extra_object", "interrupted"]
)
def test_publication_failure_is_preserved_without_success(prepared, fault):
    p = prepared
    if fault == "unmarked_bucket":
        p.client.bucket = True
    elif fault == "foreign_marker":
        p.client.bucket = True
        p.client.objects[worker.MARKER] = b"foreign"
    elif fault == "hash":
        worker.owned_bucket(p.client, k.initialize()["owner_id"], create=True)
        manifest = json.loads(
            (p.root / "data/storage/bundles" / p.identity / "manifest.json").read_bytes()
        )
        item = manifest["files"][0]
        p.client.objects["bronze/v1/" + p.identity + "/" + item["path"]] = b"x" * item["bytes"]
    elif fault == "extra_object":
        p.client.objects["bronze/v1/" + p.identity + "/extra"] = b"extra"
    else:
        p.client.fail_at = 2
    with pytest.raises((ValueError, OSError, ServiceError)):
        k.transfer("publish", p.identity, "bronze", p.job)
    receipts = list(k.work().glob("publish-*/result.json"))
    assert len(receipts) == 1 and json.loads(receipts[0].read_text())["status"] == "failed"
    assert "bronze/v1/" + p.identity + "/manifest.json" not in p.client.objects
    assert not list(k.work().glob("*/restored"))


@pytest.mark.parametrize("fault", ["truncated", "semantic", "foundation"])
def test_bad_restore_is_not_finalized(prepared, monkeypatch, fault):
    p = prepared
    k.transfer("publish", p.identity, "bronze", p.job)
    if fault == "truncated":
        key = next(key for key in p.client.objects if key.endswith(".pkl"))
        p.client.objects[key] = p.client.objects[key][:-1]
    elif fault == "semantic":
        monkeypatch.setattr(
            bronze_archive,
            "verify_directory",
            lambda *a: (_ for _ in ()).throw(ValueError("provenance")),
        )
    else:
        calls = iter([{"identity": "before"}, {"identity": "after"}])
        monkeypatch.setattr(f, "inventory", lambda: next(calls))
    with pytest.raises(ValueError):
        k.transfer("restore", p.identity, "bronze", p.job)
    receipt = next(k.work().glob("restore-*/result.json"))
    assert json.loads(receipt.read_text())["status"] == "failed"
    if fault != "foundation":
        assert not list(k.work().glob("restore-*/restored"))


def test_metadata_inventory_rejects_unknown_and_symlink_paths(prepared):
    assert k.available()["bundles"][0]["bundle_id"] == prepared.identity
    assert not k.available()["payloads_verified"]
    manifest = prepared.root / "data/storage/bundles" / prepared.identity / "manifest.json"
    data = manifest.read_bytes()
    manifest.unlink()
    other = prepared.root / "foreign.json"
    other.write_bytes(data)
    manifest.symlink_to(other)
    with pytest.raises(ValueError, match="link"):
        k.available()


def test_owner_cannot_be_reused_on_another_node(prepared, monkeypatch):
    monkeypatch.setattr(f, "owner", lambda: {"node_uid": "foreign"})
    with pytest.raises(ValueError, match="owner differs"):
        k.initialize()


@pytest.mark.parametrize(
    "schema", [archive.SCHEMA, archive.HGB_STUDY_SCHEMA, "fraud_mlflow_tracking_bundle_v1"]
)
def test_non_dataset_profiles_rejected_before_s3(schema, tmp_path):
    class NoClient:
        def __getattr__(self, name):
            pytest.fail("Unsupported profile contacted S3")

    with pytest.raises(ValueError, match="Only Bronze"):
        worker.execute("publish", "b" * 64, schema, NoClient(), tmp_path, OWNER)


def test_worker_failure_removes_credential_file_and_owned_container(prepared, monkeypatch):
    p = prepared
    monkeypatch.setattr(
        f,
        "credentials",
        lambda: {"rustfs-access-key": "private-key", "rustfs-secret-key": "private-secret"},
    )

    @contextmanager
    def forward():
        yield "http://127.0.0.1:12345"

    monkeypatch.setattr(k, "port_forward", forward)
    commands = []

    def run(args, **kw):
        commands.append(args)
        if args[1] == "run":
            envfile = Path(args[args.index("--env-file") + 1])
            assert envfile.stat().st_mode & 0o777 == 0o600
            assert "private-secret" in envfile.read_text()
            assert "private-secret" not in repr(args)
            assert args[args.index("--network") + 1] == "host"
            assert "--read-only" in args and "--cap-drop" in args
            raise TimeoutError("injected timeout")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(f, "run", run)
    attempt = k.work() / "worker-test"
    attempt.mkdir()
    source = p.root / "data/storage/bundles" / p.identity
    with pytest.raises(TimeoutError):
        k.run_worker("publish", p.identity, archive.BRONZE_SCHEMA, attempt, source)
    assert not (attempt / "worker.env").exists()
    assert commands[-1][1:3] == ["rm", "--force"]


def test_forward_drains_connection_logs_and_uses_only_owned_loopback(prepared, monkeypatch):
    from io import BytesIO

    raw = (
        b"Forwarding from 127.0.0.1:34567 -> 9000\n" + b"Handling connection for 34567\n" * 100000
    )
    commands = []

    class Process:
        stdout = BytesIO(raw)
        terminated = False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

        def wait(self, **kw):
            return 0

    process = Process()

    def popen(args, **kw):
        commands.append(args)
        return process

    monkeypatch.setattr(k.subprocess, "Popen", popen)
    with k.port_forward() as endpoint:
        assert endpoint == "http://127.0.0.1:34567"
    assert process.terminated and process.stdout.tell() == len(raw)
    args = commands[0]
    assert args[args.index("--context") + 1] == "kind-fraud-platform"
    assert args[args.index("--address") + 1] == "127.0.0.1"
    assert "service/fraud-platform-rustfs" in args


def test_rehearsal_refuses_missing_or_ambiguous_layers_before_transfer(prepared, monkeypatch):
    monkeypatch.setattr(k, "transfer", lambda *a: pytest.fail("Unexpected transfer"))
    with pytest.raises(ValueError, match="exactly one prepared silver"):
        k.rehearse()
    row = k.available()["bundles"][0]
    monkeypatch.setattr(k, "available", lambda: {"bundles": [row, row]})
    with pytest.raises(ValueError, match="exactly one prepared bronze"):
        k.rehearse()


def test_rehearsal_collects_four_separate_receipt_chains(prepared, monkeypatch):
    monkeypatch.setattr(
        k,
        "available",
        lambda: {"bundles": [{"layer": layer, "bundle_id": "b" * 64} for layer in k.LAYERS]},
    )
    calls = []

    def transfer(action, identity, layer):
        calls.append((action, layer))
        return {
            "files": 2,
            "bytes": 10,
            "manifest_sha256": identity,
            "transport": {"uploaded_objects": 0, "reused_objects": 3, "verified_objects": 3},
            "semantic_verification": {"rows": 1},
            "report": "receipt-" + str(len(calls)),
            "restored_directory": "restore-" + layer,
            "independent_restored_bundle_verified": True,
            "foundation_identities_preserved": True,
        }

    monkeypatch.setattr(k, "transfer", transfer)
    result = k.rehearse()
    assert result["status"] == "success" and set(result["datasets"]) == set(k.LAYERS)
    assert calls == [
        (action, layer) for layer in k.LAYERS for action in ["publish", "publish", "restore"]
    ]
    assert json.loads(Path(result["report"]).read_text()) == result
