"""Command line entry points."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import typer
from sqlmodel import Session, select

from app.db import create_engine_and_tables
from app.ingest.degiro.account_csv import parse_account_csv
from app.ingest.degiro.portfolio_csv import parse_portfolio_csv
from app.ingest.degiro.transactions_csv import parse_transactions_csv
from app.ingest.importer import ensure_default_account, import_transactions_file, undo_batch
from app.ingest.reconcile import reconcile
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

@app.command("reconcile")
def reconcile_command(export_dir: Path) -> None:
    """Check the cross-file invariants between the DeGiro exports.

    M0's stated outcome is a green report here (design doc Sec 3.6). Exits non-zero
    when any invariant fails, so it can gate an import in a script.

    `Portfolio.csv` is optional: without it the three file-to-file invariants still
    run, and only the cash check is skipped.
    """
    transactions_path = export_dir / "Transactions.csv"
    account_path = export_dir / "Account.csv"
    portfolio_path = export_dir / "Portfolio.csv"

    for required in (transactions_path, account_path):
        if not required.exists():
            typer.echo(f"missing {required}", err=True)
            raise typer.Exit(code=2)

    report = reconcile(
        parse_transactions_csv(transactions_path),
        parse_account_csv(account_path),
        parse_portfolio_csv(portfolio_path) if portfolio_path.exists() else None,
    )

    for invariant in report.invariants:
        mark = "ok  " if invariant.ok else "FAIL"
        typer.echo(f"{mark} {invariant.name}")
        if not invariant.ok:
            typer.echo(f"       expected: {invariant.expected}")
            typer.echo(f"       actual:   {invariant.actual}")
            if invariant.detail:
                typer.echo(f"       detail:   {invariant.detail}")

    if not portfolio_path.exists():
        typer.echo("note: Portfolio.csv absent, cash invariant skipped")

    if not report.ok:
        typer.echo(f"\n{len(report.failures)} invariant(s) failed", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"\nall {len(report.invariants)} invariants green")


if __name__ == "__main__":
    app()
