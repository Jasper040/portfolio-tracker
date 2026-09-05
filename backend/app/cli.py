"""Command line entry points."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import typer
from sqlmodel import Session, select

from app.db import create_engine_and_tables
from app.ingest.importer import ensure_default_account, import_transactions_file, undo_batch
from app.models.ledger import ImportBatch
from app.settings import get_settings

app = typer.Typer(help="Portfolio tracker maintenance commands.")


@app.command("import")
def import_command(path: Path) -> None:
    """Import a DeGiro Transactions.csv export."""
    engine = create_engine_and_tables(get_settings().database_url)
    account_id = ensure_default_account(engine)
    result = import_transactions_file(engine, path, account_id)
    typer.echo(
        f"batch {result.batch_id}: parsed {result.rows_parsed}, "
        f"inserted {result.rows_inserted}, skipped {result.rows_skipped}"
    )


@app.command("undo")
def undo_command(batch_id: str) -> None:
    """Undo one import batch."""
    engine = create_engine_and_tables(get_settings().database_url)
    removed = undo_batch(engine, UUID(batch_id))
    typer.echo(f"removed {removed} transactions")


@app.command("batches")
def batches_command() -> None:
    """List import batches, newest first.

    `undo` is the ledger's only reversal mechanism and its batch id is printed
    once, at import time, then lost when the terminal scrolls. This is how to
    find it again.
    """
    engine = create_engine_and_tables(get_settings().database_url)
    with Session(engine) as session:
        batches = session.exec(
            select(ImportBatch).order_by(ImportBatch.imported_at.desc())  # type: ignore[attr-defined]
        ).all()

    if not batches:
        typer.echo("no import batches")
        return

    for batch in batches:
        typer.echo(
            f"{batch.id}  {batch.imported_at.isoformat()}  {batch.filename}  "
            f"rows={batch.row_count} inserted={batch.inserted_count}"
        )


if __name__ == "__main__":
    app()
