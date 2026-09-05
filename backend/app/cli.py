"""Command line entry points."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import typer

from app.db import create_engine_and_tables
from app.ingest.importer import ensure_default_account, import_transactions_file, undo_batch
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


if __name__ == "__main__":
    app()
