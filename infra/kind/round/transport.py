"""Init container only: fetch the exact NEW Gold output using the owned S3 bucket."""

from contextlib import closing
import json
import os
from pathlib import Path

from scripts.platform.kind_datasets_worker import execute
from scripts.platform.kind_pipeline_transport import make_client
from scripts.storage import archive

with closing(make_client()) as client:
    value = execute(
        "download",
        os.environ["ROUND_GOLD_BUNDLE_ID"],
        archive.GOLD_SCHEMA,
        client,
        Path("/work"),
        os.environ["ROUND_DATASET_OWNER"],
    )
with Path("/work/input.json").open("x") as stream:
    json.dump(value, stream, allow_nan=False)
