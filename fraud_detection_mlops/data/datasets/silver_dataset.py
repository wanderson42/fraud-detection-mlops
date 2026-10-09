"""Validate Bronze and publish a complete, versioned Parquet dataset offline."""

from contextlib import contextmanager
from datetime import UTC, datetime
import hashlib
from io import BytesIO
import json
from pathlib import Path
import re
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
from fraud_detection_mlops.data.contracts.dataset_errors import SilverError
from fraud_detection_mlops.data.contracts.silver_contract import (
    DEFAULT_CONTRACT,
    accept_bronze_profile,
    load_silver_contract,
)
from fraud_detection_mlops.data.contracts.transaction_schema import (
    ARROW_SCHEMA,
    DTYPES,
    INTEGER_COLUMNS,
    is_int64,
)
from fraud_detection_mlops.data.ingestion.handbook_bronze import DEFAULT_BRONZE
from fraud_detection_mlops.data.ingestion.handbook_inventory import (
    DEFAULT_INVENTORY,
    load_inventory,
)
from fraud_detection_mlops.data.quality.transaction_profile import profile_bronze

DEFAULT_OUTPUT = PROJECT_ROOT / "data/interim/handbook"
app = typer.Typer(no_args_is_help=True)


def normalize_dataframe(frame: pd.DataFrame) -> pd.DataFrame:
    """Change representation only; acceptance is checked before this conversion."""
    result = frame.loc[:, list(DTYPES)].copy().reset_index(drop=True)
    # Older pickles retain an object column Index; pandas 3/Arrow infer strings.
    result.columns = pd.Index(list(DTYPES))
    for name in INTEGER_COLUMNS:
        if not result[name].map(is_int64).all():
            raise SilverError(f"Lossy integer conversion: {name}")
        values = result[name].map(int)
        if name in ("TX_FRAUD", "TX_FRAUD_SCENARIO") and not values.between(0, 3).all():
            raise SilverError(f"Invalid label conversion: {name}")
        result[name] = values.astype(DTYPES[name])
    result["TX_DATETIME"] = pd.to_datetime(result["TX_DATETIME"]).astype("datetime64[ns]")
    result["TX_AMOUNT"] = result["TX_AMOUNT"].astype("float64")
    return result


def _connection(paths: list[Path]) -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect()
    try:
        connection.read_parquet(
            [str(path.resolve()) for path in paths], hive_partitioning=True
        ).create_view("transactions")
    except BaseException:
        connection.close()
        raise
    return connection


def _counts(paths: list[Path]) -> dict:
    with _connection(paths) as connection:
        values = connection.execute("""
            SELECT count(*), count(DISTINCT TRANSACTION_ID),
                   count(*) FILTER (WHERE TX_FRAUD = 1),
                   count(*) FILTER (WHERE TX_FRAUD = 0),
                   count(*) FILTER (WHERE TX_AMOUNT = 0)
            FROM transactions
        """).fetchone()
    return dict(
        zip(
            ("rows", "distinct_transaction_ids", "fraud_count", "genuine_count", "zero_amounts"),
            values,
            strict=True,
        )
    )


def _relative_path(day: str) -> str:
    return f"transactions/tx_date={day}/part-00000.parquet"


def _verify_directory(
    directory: Path, inventory: dict, inventory_path: Path, contract_path: Path
) -> dict:
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if (
        manifest.get("schema_version") != 1
        or manifest.get("layer") != "silver"
        or manifest.get("source") != inventory["source"]
        or manifest.get("contract_version") != "silver_v1"
        or manifest.get("contract_sha256") != sha256(contract_path)
        or manifest.get("inventory_sha256") != sha256(inventory_path)
    ):
        raise SilverError("Silver manifest source, contract or inventory mismatch")
    records = manifest.get("files")
    expected = [_relative_path(item["date"]) for item in inventory["files"]]
    if (
        not isinstance(records, list)
        or len(records) != len(expected)
        or [record.get("path") for record in records] != expected
        or {str(path.relative_to(directory)) for path in directory.rglob("*.parquet")}
        != set(expected)
    ):
        raise SilverError("Missing, untracked or inconsistent Silver partitions")
    paths = []
    for item, record in zip(inventory["files"], records, strict=True):
        if (
            record.get("input_filename") != item["filename"]
            or record.get("input_size_bytes") != item["size_bytes"]
            or record.get("input_git_blob_sha1") != item["git_blob_sha1"]
            or not re.fullmatch(r"[0-9a-f]{64}", str(record.get("input_sha256", "")))
        ):
            raise SilverError(f"Input identity mismatch: {item['filename']}")
        path = directory / record["path"]
        if path.stat().st_size != record.get("size_bytes") or sha256(path) != record.get("sha256"):
            raise SilverError(f"Parquet integrity mismatch: {path.name}")
        parquet = pq.ParquetFile(path)
        if not parquet.schema_arrow.equals(
            ARROW_SCHEMA, check_metadata=False
        ) or parquet.metadata.num_rows != record.get("rows"):
            raise SilverError(f"Parquet schema or row count mismatch: {path}")
        paths.append(path)
    counts = _counts(paths)
    if counts != manifest.get("counts") or counts["rows"] != counts["distinct_transaction_ids"]:
        raise SilverError("Silver count reconciliation failed")
    return {"silver_path": str(directory), "verified_partitions": len(paths), **counts}


def verify_silver(
    output_root: Path = DEFAULT_OUTPUT,
    *,
    inventory_path: Path = DEFAULT_INVENTORY,
    contract_path: Path = DEFAULT_CONTRACT,
) -> dict:
    """Check output integrity, schema, inventory coverage and SQL counts offline."""
    contract = load_silver_contract(contract_path)
    inventory = load_inventory(inventory_path)
    return _verify_directory(
        output_root / inventory["source"]["commit"] / contract["version"],
        inventory,
        inventory_path,
        contract_path,
    )


def connect_silver(
    output_root: Path = DEFAULT_OUTPUT,
    *,
    inventory_path: Path = DEFAULT_INVENTORY,
    contract_path: Path = DEFAULT_CONTRACT,
) -> duckdb.DuckDBPyConnection:
    """Return an in-memory SQL session over verified files; caller closes it."""
    verified = verify_silver(
        output_root, inventory_path=inventory_path, contract_path=contract_path
    )
    inventory = load_inventory(inventory_path)
    directory = Path(verified["silver_path"])
    return _connection([directory / _relative_path(item["date"]) for item in inventory["files"]])


@contextmanager
def _lock(parent: Path, version: str):
    parent.mkdir(parents=True, exist_ok=True)
    path = parent / f".{version}.lock"
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(datetime.now(UTC).isoformat())
    except FileExistsError as exc:
        raise SilverError(f"Silver lock exists: {path}") from exc
    try:
        yield
    finally:
        path.unlink(missing_ok=True)


def build_silver(
    bronze_root: Path = DEFAULT_BRONZE,
    output_root: Path = DEFAULT_OUTPUT,
    *,
    inventory_path: Path = DEFAULT_INVENTORY,
    contract_path: Path = DEFAULT_CONTRACT,
) -> dict:
    """Publish all partitions together; existing outputs are verified and reused."""
    contract = load_silver_contract(contract_path)
    inventory = load_inventory(inventory_path)
    if inventory["files"][0]["date"] != contract["temporal_origin"]:
        raise SilverError("Inventory temporal origin differs from Silver contract")
    parent = output_root / inventory["source"]["commit"]
    destination = parent / contract["version"]
    snapshot = bronze_root / inventory["source"]["commit"]
    run_id = uuid4().hex
    audit_path = parent / "runs" / f"silver_{run_id}.json"
    with _lock(parent, contract["version"]):
        audit = {
            "schema_version": 1,
            "run_id": run_id,
            "status": "running",
            "started_at_utc": datetime.now(UTC).isoformat(),
            "source": inventory["source"],
            "inventory_sha256": sha256(inventory_path),
            "contract_sha256": sha256(contract_path),
            "contract_version": contract["version"],
            "builder_sha256": source_fingerprint(
                PROJECT_ROOT,
                [
                    Path(__file__),
                    PROJECT_ROOT / "fraud_detection_mlops/data/contracts/silver_contract.py",
                    PROJECT_ROOT / "fraud_detection_mlops/data/contracts/transaction_schema.py",
                ],
            ),
            "partitions_written": 0,
            "environment": {
                "python": sys.version.split()[0],
                "pandas": pd.__version__,
                "pyarrow": pa.__version__,
                "duckdb": duckdb.__version__,
            },
        }
        write_json(audit_path, audit)
        try:
            # Recompute acceptance from the complete snapshot, never from a stale report.
            profile = profile_bronze(bronze_root, inventory_path)
            counts = accept_bronze_profile(profile)
            audit["profiler_sha256"] = profile["profiler_sha256"]
            audit["acceptance_summary"] = profile["summary"]
            inputs = {
                record["filename"]: record
                for record in json.loads((snapshot / "manifest.json").read_text())["files"]
            }
            if destination.exists():
                result = _verify_directory(destination, inventory, inventory_path, contract_path)
                manifest = json.loads((destination / "manifest.json").read_text())
                if manifest["counts"] != counts or any(
                    record["input_sha256"] != inputs[record["input_filename"]]["sha256"]
                    for record in manifest["files"]
                ):
                    raise SilverError("Existing Silver does not reconcile with current Bronze")
                audit["status"] = "reused"
            else:
                with TemporaryDirectory(prefix=".silver-staging-", dir=parent) as temporary:
                    staging = Path(temporary) / contract["version"]
                    staging.mkdir()
                    records = []
                    for item in inventory["files"]:
                        payload = (snapshot / item["filename"]).read_bytes()
                        input_sha = hashlib.sha256(payload).hexdigest()
                        if input_sha != inputs[item["filename"]]["sha256"]:
                            raise SilverError(
                                f"Bronze changed after verification: {item['filename']}"
                            )
                        frame = normalize_dataframe(pd.read_pickle(BytesIO(payload)))
                        path = staging / _relative_path(item["date"])
                        path.parent.mkdir(parents=True)
                        frame.to_parquet(
                            path, engine="pyarrow", compression="zstd", index=False, version="2.6"
                        )
                        # ParquetFile avoids injecting the Hive partition column into the frame.
                        pd.testing.assert_frame_equal(
                            frame, pq.ParquetFile(path).read().to_pandas(), check_exact=True
                        )
                        records.append(
                            {
                                "path": _relative_path(item["date"]),
                                "rows": len(frame),
                                "size_bytes": path.stat().st_size,
                                "sha256": sha256(path),
                                "input_filename": item["filename"],
                                "input_size_bytes": item["size_bytes"],
                                "input_git_blob_sha1": item["git_blob_sha1"],
                                "input_sha256": input_sha,
                            }
                        )
                        audit["partitions_written"] += 1
                    write_json(
                        staging / "manifest.json",
                        {
                            "schema_version": 1,
                            "layer": "silver",
                            "source": inventory["source"],
                            "contract_version": contract["version"],
                            "contract_sha256": audit["contract_sha256"],
                            "inventory_sha256": audit["inventory_sha256"],
                            "created_at_utc": datetime.now(UTC).isoformat(),
                            "run_id": run_id,
                            "builder_sha256": audit["builder_sha256"],
                            "profiler_sha256": audit["profiler_sha256"],
                            "counts": counts,
                            "files": records,
                        },
                    )
                    _verify_directory(staging, inventory, inventory_path, contract_path)
                    staging.rename(destination)
                result = {
                    "silver_path": str(destination),
                    "verified_partitions": len(records),
                    **counts,
                }
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


@app.command()
def build(
    bronze_root: Annotated[Path, typer.Option()] = DEFAULT_BRONZE,
    output_root: Annotated[Path, typer.Option()] = DEFAULT_OUTPUT,
    inventory: Annotated[Path, typer.Option()] = DEFAULT_INVENTORY,
    contract: Annotated[Path, typer.Option()] = DEFAULT_CONTRACT,
):
    """Validate complete Bronze, write Parquet and reconcile SQL counts."""
    typer.echo("Verifying and profiling complete Bronze before Silver conversion...")
    try:
        result = build_silver(
            bronze_root, output_root, inventory_path=inventory, contract_path=contract
        )
    except Exception as exc:
        typer.echo(f"Silver build failed: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(json.dumps(result, indent=2))


@app.command()
def verify(
    output_root: Annotated[Path, typer.Option()] = DEFAULT_OUTPUT,
    inventory: Annotated[Path, typer.Option()] = DEFAULT_INVENTORY,
    contract: Annotated[Path, typer.Option()] = DEFAULT_CONTRACT,
):
    """Verify complete Silver integrity and SQL reconciliation offline."""
    try:
        result = verify_silver(output_root, inventory_path=inventory, contract_path=contract)
    except Exception as exc:
        typer.echo(f"Silver verification failed: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(json.dumps(result, indent=2))


if __name__ == "__main__":
    app()
