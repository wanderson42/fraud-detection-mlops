"""Real producer chain with synthetic pickles/Parquet and an in-memory object store."""

import json
import shutil

import pytest
from scripts.platform import kind_datasets_worker
from scripts.platform import kind_pipeline_compute as compute
from scripts.platform import kind_pipeline_transport as worker
from scripts.storage import archive, bronze_archive, gold_archive, silver_archive
from scripts.storage import bronze_s3_worker as transport
from tests.data.datasets.test_gold_dataset import prepared as data_source  # noqa: F401
from tests.storage.test_bronze_transfer import MemoryClient

RUN = "a" * 32
OWNER = "b" * 32


@pytest.fixture
def prepared(data_source, tmp_path, monkeypatch):  # noqa: F811 — reuse real synthetic input generator
    source, protocol = data_source
    root = tmp_path / "project"
    (root / "references").mkdir(parents=True)
    for name in compute.REFERENCES:
        target = root / name
        if name == archive.INVENTORY_PATH:
            shutil.copyfile(source / "inventory.json", target)
        elif name == archive.TEMPORAL_PROTOCOL_PATH:
            target.write_text(json.dumps(protocol))
        else:
            shutil.copyfile(compute.ROOT / name, target)
    for name in list(__import__("os").environ):
        if name.startswith(("AWS_", "PG", "MLFLOW_")):
            monkeypatch.delenv(name)
    shutil.copytree(source / "bronze", root / "data/raw/handbook")
    result = bronze_archive.prepare_bronze(root)
    client = MemoryClient()
    kind_datasets_worker.owned_bucket(client, OWNER, create=True)
    transport.publish(archive.workspace(root), result["bundle_id"], client)
    return root, result["bundle_id"], client


@pytest.fixture
def chain(prepared, tmp_path):
    root, identity, client = prepared
    values = {}
    for stage in ["silver", "gold"]:
        work = tmp_path / stage
        work.mkdir()
        incoming = worker.execute("download", stage, identity, RUN, OWNER, client, work)
        assert incoming["input_bundle_id"] == identity
        result = compute.execute(stage, identity, RUN, work, root)
        assert result["status"] == "computed_verified"
        assert result["input_bundle_unchanged"] and not result["fit_executed"]
        published = worker.execute("publish", stage, identity, RUN, OWNER, client, work)
        assert published["status"] == "published_verified"
        assert client.puts[-1] == f"platform-jobs/v1/{RUN}/{stage}/receipt.json"
        assert json.loads(client.objects[published["remote_receipt_key"]]) == {
            k: v for k, v in published.items() if not k.startswith("remote_receipt_")
        }
        values[stage] = published
        identity = result["output_bundle_id"]
    return root, values, client


def test_real_bronze_silver_gold_chain_and_independent_outputs(chain, tmp_path):
    root, values, client = chain
    assert values["silver"]["computation"]["semantic_verification"]["rows"] == 63
    assert values["gold"]["computation"]["semantic_verification"]["rows"] == 12
    assert values["gold"]["computation"]["semantic_verification"]["feature_count"] == 19
    assert values["gold"]["input_bundle_id"] == values["silver"]["output_bundle_id"]
    assert values["silver"]["computation"]["pickle_deserialized"]
    assert values["gold"]["computation"]["features_recomputed"]
    # Original project, both Job workspaces, generated bundles and local manifests disappear.
    for stage in ["silver", "gold"]:
        shutil.rmtree(tmp_path / stage)
    shutil.rmtree(root)
    for stage, module in [("silver", silver_archive), ("gold", gold_archive)]:
        target = tmp_path / ("verify-" + stage)
        target.mkdir()
        identity = values[stage]["output_bundle_id"]
        raw = transport.download(target, identity, client, compute.OUTPUT_SCHEMAS[stage])
        candidate = target / raw["candidate"]
        document, _ = transport.manifest_at(candidate, identity, compute.OUTPUT_SCHEMAS[stage])
        verified = module.verify_directory(candidate, document)
        assert verified["rows"] == values[stage]["computation"]["semantic_verification"]["rows"]


def test_untrusted_inventory_rejected_before_pickle_read(prepared, tmp_path, monkeypatch):
    import pandas as pd

    root, identity, client = prepared
    work = tmp_path / "untrusted"
    work.mkdir()
    worker.execute("download", "silver", identity, RUN, OWNER, client, work)
    (root / archive.INVENTORY_PATH).write_bytes(b"foreign trusted inventory")
    monkeypatch.setattr(pd, "read_pickle", lambda *a, **kw: pytest.fail("Untrusted pickle read"))
    with pytest.raises(ValueError, match="frozen producer input"):
        compute.execute("silver", identity, RUN, work, root)
    assert not (work / "project").exists()
    assert not (work / "compute.json").exists()


def test_scientific_process_refuses_credentials(prepared, tmp_path, monkeypatch):
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "do-not-log")
    with pytest.raises(ValueError, match="must not receive"):
        compute.execute("silver", prepared[1], RUN, tmp_path, prepared[0])


@pytest.mark.parametrize(
    "field,value",
    [
        ("input_bundle_id", "0" * 64),
        ("stage", "gold"),
        ("bundle_relative", "../escape"),
        ("run_id", "c" * 32),
    ],
)
def test_forged_compute_receipt_not_published(chain, tmp_path, field, value):
    _root, values, client = chain
    work = tmp_path / "silver"
    path = work / "compute.json"
    content = json.loads(path.read_text())
    content[field] = value
    path.write_text(json.dumps(content))
    before = dict(client.objects)
    with pytest.raises(ValueError):
        worker.execute(
            "publish", "silver", values["silver"]["input_bundle_id"], RUN, OWNER, client, work
        )
    assert before == client.objects


def test_corrupt_input_does_not_reach_producer(prepared, tmp_path, monkeypatch):
    _root, identity, client = prepared
    key = next(k for k in client.objects if k.endswith(".pkl"))
    client.objects[key] = b"damaged"
    work = tmp_path / "corrupt"
    work.mkdir()
    with pytest.raises(ValueError):
        worker.execute("download", "silver", identity, RUN, OWNER, client, work)
    assert not (work / "input.json").exists()
    assert not (work / "project").exists()


def test_producers_run_in_fresh_process_without_training_or_sdk_imports(chain, tmp_path):
    import os
    import subprocess
    import sys

    root, values, client = chain
    code = """import importlib.abc, json, sys
from pathlib import Path
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in {"sklearn", "mlflow", "optuna", "shap", "boto3", "botocore"}:
            raise AssertionError("Forbidden producer import: " + fullname)
sys.meta_path.insert(0, Guard())
from scripts.platform.kind_pipeline_compute import execute
result = execute(sys.argv[1], sys.argv[2], sys.argv[3], Path(sys.argv[4]), Path(sys.argv[5]))
print(json.dumps({"status": result["status"], "stage": result["stage"]}))
"""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("AWS_", "PG", "MLFLOW_"))}
    for stage in ["silver", "gold"]:
        work = tmp_path / ("fresh-" + stage)
        work.mkdir()
        input_id = values[stage]["input_bundle_id"]
        worker.execute("download", stage, input_id, RUN, OWNER, client, work)
        result = subprocess.run(
            [sys.executable, "-c", code, stage, input_id, RUN, str(work), str(root)],
            cwd=compute.ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr + result.stdout
        assert json.loads(result.stdout)["status"] == "computed_verified"


def test_transport_cli_keeps_kubernetes_logs_as_one_json_document(monkeypatch, capsys):
    import sys
    from types import SimpleNamespace

    def client():
        print("SDK diagnostic on stderr", file=sys.stderr)
        return SimpleNamespace(close=lambda: None)

    def execute(*args):
        print("Silver: 25/186 payloads verified", file=sys.stderr)
        return {"status": "published_verified", "stage": "silver"}

    monkeypatch.setattr(worker, "make_client", client)
    monkeypatch.setattr(worker, "execute", execute)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "worker",
            "publish",
            "--stage",
            "silver",
            "--input-bundle-id",
            "a" * 64,
            "--run-id",
            RUN,
            "--owner-id",
            OWNER,
        ],
    )
    worker.main()
    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out) == {"status": "published_verified", "stage": "silver"}
