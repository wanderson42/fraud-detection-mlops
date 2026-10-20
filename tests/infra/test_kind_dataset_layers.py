"""Exercise each Parquet dataset's existing semantic verifier through the new transport."""

from pathlib import Path
import shutil

import pytest
from scripts.platform import kind_datasets as k
from scripts.platform import kind_datasets_worker as worker
from scripts.storage import archive, development_gold_archive, gold_archive, silver_archive
from tests.data.datasets.test_gold_dataset import prepared as prepared_gold_source  # noqa: F401
from tests.data.datasets.test_silver_dataset import source as silver_source  # noqa: F401
from tests.storage.test_bronze_transfer import MemoryClient
from tests.storage.test_development_gold_archive import built_development  # noqa: F401
from tests.storage.test_gold_archive import built_gold  # noqa: F401
from tests.storage.test_silver_archive import source as silver_bundle_source  # noqa: F401


@pytest.mark.parametrize("layer", ["silver", "gold", "development-gold"])
def test_layer_roundtrip_uses_only_restored_archive(layer, request, tmp_path):
    if layer == "silver":
        root, _ = request.getfixturevalue("silver_bundle_source")
        result = silver_archive.prepare_silver(root)
    elif layer == "gold":
        built = request.getfixturevalue("built_gold")
        root = tmp_path / "gold-source"
        shutil.copytree(built, root)
        result = gold_archive.prepare_gold(root)
    else:
        built, relative = request.getfixturevalue("built_development")
        root = tmp_path / "development-source"
        shutil.copytree(built, root)
        result = development_gold_archive.prepare_development(root, relative.name)
    identity = result["bundle_id"]
    schema = k.LAYERS[layer]
    directory = Path(result["manifest"]).parent
    k.verify_directory(directory, identity, schema)
    client = MemoryClient()
    published = worker.execute(
        "publish", identity, schema, client, archive.workspace(root), "a" * 32
    )
    assert published["status"] == "success"
    # Delete every upstream source/reference and the local bundle before download.
    shutil.rmtree(root)
    target = tmp_path / (layer + "-download")
    target.mkdir(parents=True)
    downloaded = worker.execute("download", identity, schema, client, target, "a" * 32)
    document, _, semantic = k.verify_directory(target / downloaded["candidate"], identity, schema)
    assert document["schema"] == schema
    assert semantic["rows"] > 0
