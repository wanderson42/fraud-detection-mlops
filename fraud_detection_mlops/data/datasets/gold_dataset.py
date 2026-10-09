"""Publish versioned modeling tables from verified Silver, without fitting a model."""

from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from typing import Annotated
from uuid import uuid4

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import typer

from fraud_detection_mlops.artifacts import sha256, source_fingerprint, write_json
from fraud_detection_mlops.config import PROJECT_ROOT
from fraud_detection_mlops.data.contracts.dataset_errors import GoldError
from fraud_detection_mlops.data.contracts.gold_contract import (
    DEFAULT_GOLD_CONTRACT,
    load_gold_contract,
)
from fraud_detection_mlops.data.contracts.silver_contract import DEFAULT_CONTRACT
from fraud_detection_mlops.data.contracts.temporal_protocol import DEFAULT_PROTOCOL, load_protocol
from fraud_detection_mlops.data.datasets.silver_dataset import DEFAULT_OUTPUT, verify_silver
from fraud_detection_mlops.data.ingestion.handbook_inventory import (
    DEFAULT_INVENTORY,
    load_inventory,
)
from fraud_detection_mlops.features.causal_history import compute_features
from fraud_detection_mlops.features.feature_schema import (
    DTYPES,
    FEATURE_COLUMNS,
    METADATA_DTYPES,
)
from fraud_detection_mlops.features.feature_schema import FEATURE_ARROW_SCHEMA as ARROW_SCHEMA
from fraud_detection_mlops.features.feature_validation import check_feature_values

DEFAULT_GOLD = PROJECT_ROOT / "data/processed/handbook"
SPLITS = ("train", "validation", "test")
app = typer.Typer(no_args_is_help=True)


def _days(protocol: dict) -> list[tuple[str, str]]:
    result = []
    for split in SPLITS:
        window = protocol["windows"][split]
        start, end = (
            date.fromisoformat(window["start"]),
            date.fromisoformat(window["end_exclusive"]),
        )
        result.extend((split, str(start + timedelta(days=i))) for i in range((end - start).days))
    return result


def _relative_path(split: str, day: str) -> str:
    return f"transactions/split={split}/tx_date={day}/part-00000.parquet"


def _context_records(manifest: dict, protocol: dict) -> list[dict]:
    first = date.fromisoformat(protocol["windows"]["train"]["start"]) - timedelta(days=14)
    end = date.fromisoformat(protocol["windows"]["test"]["end_exclusive"])
    available = [date.fromisoformat(item["input_filename"][:10]) for item in manifest["files"]]
    first = max(first, min(available))
    records = [
        item for item in manifest["files"] if str(first) <= item["input_filename"][:10] < str(end)
    ]
    if [item["input_filename"][:10] for item in records] != [
        str(first + timedelta(days=i)) for i in range((end - first).days)
    ]:
        raise GoldError("Incomplete Silver context for causal history")
    return records


def _verify_directory(directory: Path, inventory: dict, protocol: dict, identities: dict) -> dict:
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if (
        manifest.get("schema_version") != 1
        or manifest.get("layer") != "gold"
        or manifest.get("version") != "gold_v1"
        or manifest.get("source") != inventory["source"]
        or manifest.get("protocol") != protocol
        or manifest.get("feature_columns") != FEATURE_COLUMNS
        or any(manifest.get(name) != value for name, value in identities.items())
    ):
        raise GoldError("Gold manifest contract, protocol or source mismatch")
    records = manifest.get("files", [])
    expected = [_relative_path(split, day) for split, day in _days(protocol)]
    if (
        len(records) != len(expected)
        or [item.get("path") for item in records] != expected
        or {str(p.relative_to(directory)) for p in directory.rglob("*.parquet")} != set(expected)
    ):
        raise GoldError("Incomplete or untracked Gold partitions")
    rows = {split: 0 for split in SPLITS}
    with duckdb.connect() as connection:
        all_paths = []
        for (split, day), item in zip(_days(protocol), records, strict=True):
            path = directory / item["path"]
            if path.stat().st_size != item.get("size_bytes") or sha256(path) != item.get("sha256"):
                raise GoldError(f"Gold integrity mismatch: {item['path']}")
            parquet = pq.ParquetFile(path)
            if (
                not parquet.schema_arrow.equals(ARROW_SCHEMA, check_metadata=False)
                or parquet.metadata.num_rows != item.get("rows")
                or item["rows"] <= 0
                or item.get("split") != split
                or item.get("date") != day
            ):
                raise GoldError(f"Gold schema or partition metadata mismatch: {item['path']}")
            connection.read_parquet(str(path), hive_partitioning=False).create_view(
                "part", replace=True
            )
            wrong_day = connection.execute(
                "SELECT count(*) FROM part WHERE CAST(TX_DATETIME AS DATE) != ?::DATE", [day]
            ).fetchone()[0]
            if wrong_day or check_feature_values(connection, "part"):
                raise GoldError(f"Gold semantic checks failed: {item['path']}")
            rows[split] += item["rows"]
            all_paths.append(str(path))
        connection.read_parquet(all_paths, hive_partitioning=False).create_view("all_rows")
        distinct = connection.execute(
            "SELECT count(DISTINCT TRANSACTION_ID) FROM all_rows"
        ).fetchone()[0]
        if distinct != sum(rows.values()) or rows != manifest.get("split_rows"):
            raise GoldError("Gold IDs or split counts do not reconcile")
    return {
        "gold_path": str(directory),
        "verified_partitions": len(records),
        "feature_count": len(FEATURE_COLUMNS),
        "split_rows": rows,
        "rows": sum(rows.values()),
    }


def _inputs(inventory_path: Path, silver_contract: Path, protocol_path: Path, gold_contract: Path):
    load_gold_contract(gold_contract)
    inventory = load_inventory(inventory_path)
    protocol = load_protocol(protocol_path, inventory)
    identities = {
        "inventory_sha256": sha256(inventory_path),
        "silver_contract_sha256": sha256(silver_contract),
        "protocol_sha256": sha256(protocol_path),
        "gold_contract_sha256": sha256(gold_contract),
    }
    return inventory, protocol, identities


def verify_gold(
    output_root: Path = DEFAULT_GOLD,
    *,
    inventory_path: Path = DEFAULT_INVENTORY,
    silver_contract: Path = DEFAULT_CONTRACT,
    protocol_path: Path = DEFAULT_PROTOCOL,
    gold_contract: Path = DEFAULT_GOLD_CONTRACT,
) -> dict:
    """Validate output bytes, schema, feature invariants, date coverage and IDs offline."""
    inventory, protocol, identities = _inputs(
        inventory_path, silver_contract, protocol_path, gold_contract
    )
    return _verify_directory(
        output_root / inventory["source"]["commit"] / "gold_v1", inventory, protocol, identities
    )


@contextmanager
def _lock(parent: Path):
    parent.mkdir(parents=True, exist_ok=True)
    path = parent / ".gold_v1.lock"
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(datetime.now(UTC).isoformat())
    except FileExistsError as exc:
        raise GoldError(f"Gold lock exists: {path}") from exc
    try:
        yield
    finally:
        path.unlink(missing_ok=True)


def build_gold(
    silver_root: Path = DEFAULT_OUTPUT,
    output_root: Path = DEFAULT_GOLD,
    *,
    inventory_path: Path = DEFAULT_INVENTORY,
    silver_contract: Path = DEFAULT_CONTRACT,
    protocol_path: Path = DEFAULT_PROTOCOL,
    gold_contract: Path = DEFAULT_GOLD_CONTRACT,
) -> dict:
    """Build features over complete context, then publish only the protocol's split rows."""
    inventory, protocol, identities = _inputs(
        inventory_path, silver_contract, protocol_path, gold_contract
    )
    parent = output_root / inventory["source"]["commit"]
    destination = parent / "gold_v1"
    with _lock(parent):
        run_id = uuid4().hex
        audit_path = parent / "runs" / f"gold_{run_id}.json"
        audit = {
            "schema_version": 1,
            "run_id": run_id,
            "status": "running",
            "started_at_utc": datetime.now(UTC).isoformat(),
            "source": inventory["source"],
            **identities,
            "builder_sha256": source_fingerprint(
                PROJECT_ROOT,
                [
                    Path(__file__),
                    PROJECT_ROOT / "fraud_detection_mlops/data/contracts/gold_contract.py",
                ],
            ),
            "features_sha256": source_fingerprint(
                PROJECT_ROOT, (PROJECT_ROOT / "fraud_detection_mlops/features").glob("*.py")
            ),
            "environment": {
                "python": sys.version.split()[0],
                "duckdb": duckdb.__version__,
                "pandas": pd.__version__,
                "pyarrow": pa.__version__,
            },
        }
        write_json(audit_path, audit)
        try:
            verified = verify_silver(
                silver_root, inventory_path=inventory_path, contract_path=silver_contract
            )
            source_dir = Path(verified["silver_path"])
            manifest_path = source_dir / "manifest.json"
            source_hash = sha256(manifest_path)
            source_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            context = _context_records(source_manifest, protocol)
            audit["silver_manifest_sha256"] = source_hash
            audit["context_partitions"] = len(context)
            if destination.exists():
                result = _verify_directory(destination, inventory, protocol, identities)
                previous = json.loads((destination / "manifest.json").read_text())
                if (
                    previous.get("silver_manifest_sha256") != source_hash
                    or previous.get("context_inputs") != context
                    or previous.get("features_sha256") != audit["features_sha256"]
                ):
                    raise GoldError("Existing Gold does not match current Silver inputs")
                audit["status"] = "reused"
            else:
                with TemporaryDirectory(prefix=".gold-staging-", dir=parent) as temporary:
                    staging = Path(temporary) / "gold_v1"
                    staging.mkdir()
                    records, split_rows = [], {split: 0 for split in SPLITS}
                    with duckdb.connect(
                        config={
                            "threads": 4,
                            "temp_directory": str(Path(temporary) / "duckdb-temp"),
                        }
                    ) as connection:
                        connection.read_parquet(
                            [str(source_dir / item["path"]) for item in context],
                            hive_partitioning=False,
                        ).create_view("transactions")
                        compute_features(connection)
                        for split, day in _days(protocol):
                            frame = connection.execute(
                                "SELECT * FROM gold_features WHERE CAST(TX_DATETIME AS DATE) = ?::DATE ORDER BY TX_DATETIME, TRANSACTION_ID",
                                [day],
                            ).fetchdf()
                            frame = frame.astype(DTYPES)
                            if not len(frame):
                                raise GoldError(f"Empty partition: {split}/{day}")
                            source_count = connection.execute(
                                "SELECT count(*) FROM transactions WHERE CAST(TX_DATETIME AS DATE) = ?::DATE",
                                [day],
                            ).fetchone()[0]
                            if len(frame) != source_count:
                                raise GoldError(f"Source row reconciliation failed: {day}")
                            # Exact metadata/label/amount reconciliation against the source.
                            names = "TRANSACTION_ID, TX_DATETIME, CUSTOMER_ID, TERMINAL_ID, TX_FRAUD, TX_AMOUNT"
                            mismatch = connection.execute(
                                f"SELECT count(*) FROM ((SELECT {names} FROM gold_features WHERE CAST(TX_DATETIME AS DATE) = ?::DATE) EXCEPT ALL (SELECT {names} FROM transactions WHERE CAST(TX_DATETIME AS DATE) = ?::DATE))",
                                [day, day],
                            ).fetchone()[0]
                            if mismatch:
                                raise GoldError(f"Source value reconciliation failed: {day}")
                            path = staging / _relative_path(split, day)
                            path.parent.mkdir(parents=True, exist_ok=True)
                            frame.to_parquet(
                                path,
                                engine="pyarrow",
                                compression="zstd",
                                index=False,
                                version="2.6",
                            )
                            pd.testing.assert_frame_equal(
                                frame, pq.ParquetFile(path).read().to_pandas(), check_exact=True
                            )
                            records.append(
                                {
                                    "path": str(path.relative_to(staging)),
                                    "split": split,
                                    "date": day,
                                    "rows": len(frame),
                                    "size_bytes": path.stat().st_size,
                                    "sha256": sha256(path),
                                }
                            )
                            split_rows[split] += len(frame)
                    if (
                        sha256(manifest_path) != source_hash
                        or any(
                            sha256(source_dir / item["path"]) != item["sha256"] for item in context
                        )
                        or any(
                            sha256(path) != identities[name]
                            for name, path in {
                                "inventory_sha256": inventory_path,
                                "silver_contract_sha256": silver_contract,
                                "protocol_sha256": protocol_path,
                                "gold_contract_sha256": gold_contract,
                            }.items()
                        )
                    ):
                        raise GoldError("Inputs changed during Gold build")
                    write_json(
                        staging / "manifest.json",
                        {
                            "schema_version": 1,
                            "layer": "gold",
                            "version": "gold_v1",
                            "source": inventory["source"],
                            **identities,
                            "protocol": protocol,
                            "feature_columns": FEATURE_COLUMNS,
                            "silver_manifest_sha256": source_hash,
                            "context_inputs": context,
                            "files": records,
                            "split_rows": split_rows,
                            "run_id": run_id,
                            "builder_sha256": audit["builder_sha256"],
                            "features_sha256": audit["features_sha256"],
                            "created_at_utc": datetime.now(UTC).isoformat(),
                        },
                    )
                    result = _verify_directory(staging, inventory, protocol, identities)
                    staging.rename(destination)
                result["gold_path"] = str(destination)
                audit["status"] = "success"
            audit["result"] = result
        except BaseException as exc:
            audit["status"] = "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
            audit["error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            audit["finished_at_utc"] = datetime.now(UTC).isoformat()
            write_json(audit_path, audit)
    return {**result, "status": audit["status"], "audit_path": str(audit_path)}


def load_gold_split(split: str, output_root: Path = DEFAULT_GOLD, **kwargs) -> tuple:
    """Return X, y, metadata using the feature allowlist; test is for frozen final evaluation."""
    if split not in SPLITS:
        raise GoldError(f"Unknown split: {split}")
    verified = verify_gold(output_root, **kwargs)
    directory = Path(verified["gold_path"])
    paths = sorted((directory / "transactions" / f"split={split}").rglob("*.parquet"))
    with duckdb.connect() as connection:
        connection.read_parquet([str(p) for p in paths], hive_partitioning=False).create_view(
            "selected"
        )
        frame = (
            connection.execute("SELECT * FROM selected ORDER BY TX_DATETIME, TRANSACTION_ID")
            .fetchdf()
            .astype(DTYPES)
        )
    return (
        frame[FEATURE_COLUMNS].copy(),
        frame["TX_FRAUD"].copy(),
        frame[list(METADATA_DTYPES)].copy(),
    )


@app.command()
def build(
    silver_root: Annotated[Path, typer.Option()] = DEFAULT_OUTPUT,
    output_root: Annotated[Path, typer.Option()] = DEFAULT_GOLD,
    inventory: Annotated[Path, typer.Option()] = DEFAULT_INVENTORY,
    silver_contract: Annotated[Path, typer.Option()] = DEFAULT_CONTRACT,
    protocol: Annotated[Path, typer.Option()] = DEFAULT_PROTOCOL,
    contract: Annotated[Path, typer.Option()] = DEFAULT_GOLD_CONTRACT,
):
    """Verify Silver, generate causal features and publish the temporal modeling splits."""
    try:
        result = build_gold(
            silver_root,
            output_root,
            inventory_path=inventory,
            silver_contract=silver_contract,
            protocol_path=protocol,
            gold_contract=contract,
        )
    except Exception as exc:
        typer.echo(f"Gold build failed: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(json.dumps(result, indent=2))


@app.command()
def verify(
    output_root: Annotated[Path, typer.Option()] = DEFAULT_GOLD,
    inventory: Annotated[Path, typer.Option()] = DEFAULT_INVENTORY,
    silver_contract: Annotated[Path, typer.Option()] = DEFAULT_CONTRACT,
    protocol: Annotated[Path, typer.Option()] = DEFAULT_PROTOCOL,
    contract: Annotated[Path, typer.Option()] = DEFAULT_GOLD_CONTRACT,
):
    """Verify Gold output integrity, schema, split coverage and feature invariants."""
    try:
        result = verify_gold(
            output_root,
            inventory_path=inventory,
            silver_contract=silver_contract,
            protocol_path=protocol,
            gold_contract=contract,
        )
    except Exception as exc:
        typer.echo(f"Gold verification failed: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(json.dumps(result, indent=2))


if __name__ == "__main__":
    app()
