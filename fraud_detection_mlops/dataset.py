from pathlib import Path
from typing import Annotated

import typer

from fraud_detection_mlops.bronze import (
    DEFAULT_INVENTORY,
    BronzeError,
    extract_bronze,
    verify_bronze,
)

DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "data" / "raw" / "handbook"
app = typer.Typer(no_args_is_help=True, help="Acquire and verify Handbook Bronze snapshots.")


@app.command()
def extract(
    output_root: Annotated[
        Path, typer.Option(help="Parent of commit-specific snapshots.")
    ] = DEFAULT_OUTPUT,
    start_date: Annotated[
        str | None, typer.Option(help="First date, inclusive: YYYY-MM-DD.")
    ] = None,
    end_date: Annotated[str | None, typer.Option(help="Last date, inclusive: YYYY-MM-DD.")] = None,
    inventory: Annotated[
        Path, typer.Option(help="Version-controlled source inventory.")
    ] = DEFAULT_INVENTORY,
    timeout: Annotated[
        float, typer.Option(min=1, max=60, help="Network timeout in seconds.")
    ] = 30,
    attempts: Annotated[int, typer.Option(min=1, max=5, help="Maximum attempts per file.")] = 3,
):
    """Download original bytes; resume safely and record an extraction audit."""
    try:
        result = extract_bronze(
            output_root,
            start_date=start_date,
            end_date=end_date,
            inventory_path=inventory,
            timeout=timeout,
            attempts=attempts,
            progress=lambda filename, status: typer.echo(f"{status}: {filename}"),
        )
    except (BronzeError, OSError) as exc:
        typer.echo(f"Extraction failed: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Downloaded: {result['downloaded_count']}; skipped: {result['skipped_count']}")
    typer.echo(f"Snapshot: {result['snapshot_path']}")
    typer.echo(f"Audit: {result['audit_path']}")


@app.command()
def verify(
    output_root: Annotated[
        Path, typer.Option(help="Parent of commit-specific snapshots.")
    ] = DEFAULT_OUTPUT,
    inventory: Annotated[
        Path, typer.Option(help="Version-controlled source inventory.")
    ] = DEFAULT_INVENTORY,
    require_complete: Annotated[bool, typer.Option(help="Require every source date.")] = False,
):
    """Verify provenance, coverage and checksums offline."""
    try:
        result = verify_bronze(
            output_root, inventory_path=inventory, require_complete=require_complete
        )
    except (BronzeError, OSError) as exc:
        typer.echo(f"Verification failed: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(
        f"Verified: {result['verified_file_count']}/{result['expected_file_count']}; "
        f"complete: {result['complete']}"
    )


if __name__ == "__main__":
    app()
