"""Produce descriptive training-only tables and figures over verified Silver files."""

from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from typing import Annotated
from uuid import uuid4

import duckdb
import matplotlib
from matplotlib.backends.backend_agg import FigureCanvasAgg
import matplotlib.dates as mdates
from matplotlib.figure import Figure
import pandas as pd
import typer

from fraud_detection_mlops.bronze import DEFAULT_INVENTORY, _write_json, load_inventory
from fraud_detection_mlops.silver import DEFAULT_CONTRACT, DEFAULT_OUTPUT, verify_silver
from fraud_detection_mlops.temporal import DEFAULT_PROTOCOL, load_protocol

app = typer.Typer(no_args_is_help=True)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def summarize_training(connection: duckdb.DuckDBPyConnection) -> tuple[dict, dict]:
    """Query only the caller's eda_transactions view; fractions have range [0, 1]."""
    summary = (
        connection.execute("""
        SELECT count(*) AS rows, count(DISTINCT TRANSACTION_ID) AS distinct_transaction_ids,
               count(DISTINCT CUSTOMER_ID) AS customers,
               count(DISTINCT TERMINAL_ID) AS terminals,
               count(*) FILTER (WHERE TX_FRAUD = 1) AS fraud_count,
               count(*) FILTER (WHERE TX_AMOUNT = 0) AS zero_amounts,
               avg(TX_FRAUD) AS fraud_rate,
               min(TX_DATETIME) AS first_transaction, max(TX_DATETIME) AS last_transaction
        FROM eda_transactions
    """)
        .fetchdf()
        .to_dict(orient="records")[0]
    )
    if not summary["rows"]:
        raise ValueError("Training window has no transactions")
    summary["genuine_count"] = summary["rows"] - summary["fraud_count"]
    tables = {}
    groups = {
        "daily": "CAST(TX_DATETIME AS DATE)",
        "hourly": "CAST(date_part('hour', TX_DATETIME) AS INTEGER)",
        "weekday": "CAST(date_part('isodow', TX_DATETIME) AS INTEGER)",
    }
    for name, expression in groups.items():
        tables[name] = connection.execute(f"""
            SELECT {expression} AS period, count(*) AS transactions,
                   sum(TX_FRAUD)::BIGINT AS frauds, avg(TX_FRAUD) AS fraud_rate,
                   avg(TX_AMOUNT) AS mean_amount,
                   count(DISTINCT CUSTOMER_ID) AS customers,
                   count(DISTINCT TERMINAL_ID) AS terminals
            FROM eda_transactions GROUP BY period ORDER BY period
        """).fetchdf()
    tables["amount_by_label"] = connection.execute("""
        SELECT TX_FRAUD AS label, count(*) AS transactions,
               count(*) FILTER (WHERE TX_AMOUNT = 0) AS zero_amounts,
               min(TX_AMOUNT) AS minimum, avg(TX_AMOUNT) AS mean,
               stddev_pop(TX_AMOUNT) AS std_population,
               quantile_cont(TX_AMOUNT, 0.5) AS median,
               quantile_cont(TX_AMOUNT, 0.9) AS p90,
               quantile_cont(TX_AMOUNT, 0.99) AS p99, max(TX_AMOUNT) AS maximum
        FROM eda_transactions GROUP BY label ORDER BY label
    """).fetchdf()
    tables["amount_bins"] = connection.execute("""
        WITH binned AS (
            SELECT TX_FRAUD AS label,
                   CASE WHEN TX_AMOUNT = 0 THEN 0 WHEN TX_AMOUNT <= 10 THEN 1
                        WHEN TX_AMOUNT <= 25 THEN 2 WHEN TX_AMOUNT <= 50 THEN 3
                        WHEN TX_AMOUNT <= 100 THEN 4 WHEN TX_AMOUNT <= 250 THEN 5
                        WHEN TX_AMOUNT <= 500 THEN 6 WHEN TX_AMOUNT <= 1000 THEN 7
                        ELSE 8 END AS bin
            FROM eda_transactions
        )
        SELECT label, bin, count(*) AS transactions,
               count(*)::DOUBLE / sum(count(*)) OVER (PARTITION BY label) AS label_fraction
        FROM binned GROUP BY label, bin ORDER BY label, bin
    """).fetchdf()
    tables["scenarios_audit_only"] = connection.execute("""
        SELECT TX_FRAUD_SCENARIO AS scenario, TX_FRAUD AS label, count(*) AS transactions
        FROM eda_transactions GROUP BY scenario, label ORDER BY scenario, label
    """).fetchdf()
    activity = []
    for entity in ("CUSTOMER_ID", "TERMINAL_ID"):
        values = connection.execute(f"""
            WITH counts AS (
                SELECT {entity}, count(*) AS n FROM eda_transactions GROUP BY {entity}
            )
            SELECT count(*) AS entities, min(n) AS minimum, avg(n) AS mean,
                   quantile_cont(n, 0.5) AS median, quantile_cont(n, 0.9) AS p90,
                   quantile_cont(n, 0.99) AS p99, max(n) AS maximum FROM counts
        """).fetchdf()
        values.insert(0, "entity", entity)
        activity.append(values)
    tables["entity_activity"] = pd.concat(activity, ignore_index=True)
    return summary, tables


def _plot(tables: dict, path: Path) -> None:
    """Render exact aggregate data offline, without an interactive display backend."""
    figure = Figure(figsize=(12, 10), layout="constrained")
    FigureCanvasAgg(figure)
    axes = figure.subplots(3, 1)
    daily = tables["daily"]
    axes[0].plot(daily["period"], daily["transactions"], color="#245d85")
    axes[0].set(ylabel="Transações", title="Volume diário — treino")
    axes[1].plot(daily["period"], 100 * daily["fraud_rate"], color="#b45131", marker=".")
    axes[1].set(ylabel="Fraudes (%)", title="Prevalência diária — treino")
    labels = [
        "0",
        "(0,10]",
        "(10,25]",
        "(25,50]",
        "(50,100]",
        "(100,250]",
        "(250,500]",
        "(500,1000]",
        ">1000",
    ]
    for axis in axes[:2]:
        locator = mdates.DayLocator(interval=max(1, (len(daily) + 6) // 7))
        axis.xaxis.set_major_locator(locator)
        axis.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m"))
        axis.set_xlim(
            pd.Timestamp(daily["period"].min()) - pd.Timedelta(hours=12),
            pd.Timestamp(daily["period"].max()) + pd.Timedelta(hours=12),
        )
        axis.set_ylim(bottom=0)
    binned = tables["amount_bins"]
    for label, color in ((0, "#245d85"), (1, "#b45131")):
        subset = binned[binned["label"] == label].set_index("bin")
        fractions = subset["label_fraction"].reindex(range(9), fill_value=0)
        axes[2].bar(
            [i + (label - 0.5) * 0.36 for i in range(9)],
            100 * fractions,
            width=0.36,
            label="Genuína" if label == 0 else "Fraude",
            color=color,
        )
    axes[2].set(
        xticks=list(range(9)),
        xticklabels=labels,
        ylabel="Dentro da classe (%)",
        xlabel="Faixa de valor (unidade da fonte)",
        title="Distribuição de valores — treino",
    )
    axes[2].legend()
    for axis in axes:
        axis.grid(axis="y", alpha=0.2)
    figure.savefig(path, dpi=150, metadata={"Software": "fraud_detection_mlops.eda"})


def build_eda(
    silver_root: Path = DEFAULT_OUTPUT,
    *,
    inventory_path: Path = DEFAULT_INVENTORY,
    contract_path: Path = DEFAULT_CONTRACT,
    protocol_path: Path = DEFAULT_PROTOCOL,
) -> dict:
    """Verify Silver, explore only training partitions and publish a new audited run."""
    inventory = load_inventory(inventory_path)
    protocol = load_protocol(protocol_path, inventory)
    verified = verify_silver(
        silver_root, inventory_path=inventory_path, contract_path=contract_path
    )
    directory = Path(verified["silver_path"])
    manifest_path = directory / "manifest.json"
    manifest_hash = _sha256(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    window = protocol["windows"]["train"]
    selected = [
        record
        for record in manifest["files"]
        if window["start"] <= record["input_filename"][:10] < window["end_exclusive"]
    ]
    parent = silver_root / inventory["source"]["commit"] / "eda_v1"
    parent.mkdir(parents=True, exist_ok=True)
    run_id = uuid4().hex
    audit_path = parent / "runs" / f"{run_id}.json"
    destination = parent / run_id
    audit = {
        "schema_version": 1,
        "run_id": run_id,
        "status": "running",
        "started_at_utc": datetime.now(UTC).isoformat(),
        "source": inventory["source"],
        "protocol_sha256": _sha256(protocol_path),
        "silver_manifest_sha256": manifest_hash,
        "eda_sha256": _sha256(Path(__file__)),
        "temporal_sha256": _sha256(Path(__file__).with_name("temporal.py")),
        "inventory_sha256": _sha256(inventory_path),
        "contract_sha256": _sha256(contract_path),
        "environment": {
            "python": sys.version.split()[0],
            "pandas": pd.__version__,
            "duckdb": duckdb.__version__,
            "matplotlib": matplotlib.__version__,
        },
    }
    _write_json(audit_path, audit)
    try:
        # Only selected train files are exposed to exploratory SQL. Full Silver verification
        # above checks integrity/schema/counts, not holdout distributions for feature choices.
        with duckdb.connect() as connection:
            connection.read_parquet(
                [str(directory / item["path"]) for item in selected], hive_partitioning=True
            ).create_view("eda_transactions")
            summary, tables = summarize_training(connection)
        expected_days = pd.date_range(window["start"], window["end_exclusive"], inclusive="left")
        if set(pd.to_datetime(tables["daily"]["period"])) != set(expected_days):
            raise ValueError("Training partitions contain missing or unexpected transaction dates")
        if summary["rows"] != sum(item["rows"] for item in selected):
            raise ValueError("Training counts do not reconcile with Silver manifest")
        with TemporaryDirectory(prefix=".eda-staging-", dir=parent) as temporary:
            staging = Path(temporary) / run_id
            staging.mkdir()
            for name, frame in tables.items():
                frame.to_csv(staging / f"{name}.csv", index=False)
            _plot(tables, staging / "training_overview.png")
            # Pandas serializes dates and numpy scalar values into JSON-compatible values.
            serial_summary = json.loads(
                pd.DataFrame([summary]).to_json(orient="records", date_format="iso")
            )[0]
            _write_json(
                staging / "report.json",
                {
                    "schema_version": 1,
                    "version": "eda_v1",
                    "run_id": run_id,
                    "scope": "training_only_descriptive",
                    "protocol": protocol,
                    "summary": serial_summary,
                    "inputs": selected,
                    "limitations": [
                        "simulated_data",
                        "no_inferential_tests",
                        "no_model_evaluation",
                        "holdout_distributions_not_explored",
                    ],
                },
            )
            if (
                _sha256(manifest_path) != manifest_hash
                or _sha256(protocol_path) != audit["protocol_sha256"]
                or any(_sha256(directory / item["path"]) != item["sha256"] for item in selected)
            ):
                raise ValueError("Inputs changed during EDA")
            outputs = [
                {"path": path.name, "size_bytes": path.stat().st_size, "sha256": _sha256(path)}
                for path in sorted(staging.iterdir())
            ]
            _write_json(
                staging / "manifest.json", {**audit, "status": "success", "files": outputs}
            )
            verify_eda(staging)
            staging.rename(destination)
        audit["status"] = "success"
        audit["result"] = {
            "eda_path": str(destination),
            "training_partitions": len(selected),
            "rows": summary["rows"],
            "fraud_count": summary["fraud_count"],
            "fraud_rate": summary["fraud_rate"],
        }
    except BaseException as exc:
        audit["status"] = "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
        audit["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        audit["finished_at_utc"] = datetime.now(UTC).isoformat()
        _write_json(audit_path, audit)
    return {**audit["result"], "status": audit["status"], "audit_path": str(audit_path)}


OUTPUT_NAMES = {
    "daily.csv",
    "hourly.csv",
    "weekday.csv",
    "amount_by_label.csv",
    "amount_bins.csv",
    "scenarios_audit_only.csv",
    "entity_activity.csv",
    "training_overview.png",
    "report.json",
}


def verify_eda(run_path: Path) -> dict:
    """Verify a report bundle's byte integrity; source verification is a separate step."""
    manifest = json.loads((run_path / "manifest.json").read_text(encoding="utf-8"))
    records = manifest.get("files", [])
    if (
        manifest.get("schema_version") != 1
        or manifest.get("status") != "success"
        or len(records) != len(OUTPUT_NAMES)
        or {record.get("path") for record in records} != OUTPUT_NAMES
        or {path.name for path in run_path.iterdir()} != OUTPUT_NAMES | {"manifest.json"}
    ):
        raise ValueError("Incomplete or unsupported EDA bundle")
    for record in records:
        path = run_path / record["path"]
        if path.stat().st_size != record["size_bytes"] or _sha256(path) != record["sha256"]:
            raise ValueError(f"EDA integrity mismatch: {path.name}")
    return {"eda_path": str(run_path), "verified_outputs": len(records), "status": "success"}


@app.command()
def verify(run_path: Path):
    """Check the checksums and exact file coverage of an existing EDA bundle."""
    try:
        result = verify_eda(run_path)
    except Exception as exc:
        typer.echo(f"EDA verification failed: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(json.dumps(result, indent=2))


@app.command()
def build(
    silver_root: Annotated[Path, typer.Option()] = DEFAULT_OUTPUT,
    inventory: Annotated[Path, typer.Option()] = DEFAULT_INVENTORY,
    contract: Annotated[Path, typer.Option()] = DEFAULT_CONTRACT,
    protocol: Annotated[Path, typer.Option()] = DEFAULT_PROTOCOL,
):
    """Generate training-only descriptive tables and figures offline."""
    try:
        result = build_eda(
            silver_root, inventory_path=inventory, contract_path=contract, protocol_path=protocol
        )
    except Exception as exc:
        typer.echo(f"EDA failed: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(json.dumps(result, indent=2))


if __name__ == "__main__":
    app()
