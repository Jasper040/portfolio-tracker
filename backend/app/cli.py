"""Command line entry points."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated
from uuid import UUID

import typer
from sqlmodel import Session, select

from app.analytics.rebuild import ChargeMismatch, rebuild
from app.db import create_engine_and_tables
from app.domain.lots import LOT_METHODS
from app.ingest.corporate_actions import CorporateAction
from app.ingest.degiro.account_csv import parse_account_csv
from app.ingest.degiro.portfolio_csv import parse_portfolio_csv
from app.ingest.degiro.transactions_csv import parse_transactions_csv
from app.ingest.importer import (
    QuarantineError,
    ensure_default_account,
    import_degiro_export,
    undo_batch,
)
from app.ingest.prices import UnresolvedSymbols, build_providers, fetch_prices
from app.ingest.reconcile import reconcile
from app.ingest.symbols import MANUAL_ANSWER, ResolutionReport, load_symbol_answers
from app.models.ledger import CorporateActionReview, ImportBatch
from app.models.market import SymbolReview
from app.providers.base import ProviderError
from app.settings import get_settings

app = typer.Typer(help="Portfolio tracker maintenance commands.")


def _report_quarantine(unresolved: tuple[CorporateAction, ...], resolutions_path: Path) -> None:
    """Print the open questions and a paste-ready answer for them.

    The key is what joins this message to the file the operator edits, so it is
    printed verbatim and again as YAML: retyping a key by hand produces an answer
    that parses cleanly and applies to nothing.
    """
    typer.echo(
        f"{len(unresolved)} corporate action(s) must be answered before this export imports."
    )
    typer.echo(f"Nothing was written. Add each key to {resolutions_path} and run this again.\n")
    for candidate in unresolved:
        named = candidate.label or "(Account.csv does not name this one -- check the wording)"
        typer.echo(f"  {candidate.key}")
        typer.echo(f"      kind:   {candidate.kind}")
        typer.echo(f"      amount: {candidate.local_amount} on {candidate.trade_date}")
        typer.echo(f"      label:  {named}")
    typer.echo("\nresolutions:")
    for candidate in unresolved:
        typer.echo(f"  - key: {candidate.key}")
        typer.echo("    treatment: corporate_action")


@app.command("import")
def import_command(
    export_dir: Path,
    resolutions: Annotated[
        Path | None,
        typer.Option(
            "--resolutions",
            help="Corporate-action answers. Defaults to the configured path.",
        ),
    ] = None,
) -> None:
    """Import a DeGiro export directory (Transactions.csv and Account.csv).

    Refuses, writing nothing, while any corporate action is unanswered: design doc
    Sec 6.3. Exits 1 on a refusal and 2 when an export file is missing, so this can
    gate a script.
    """
    settings = get_settings()
    resolutions_path = resolutions or Path(settings.corporate_actions_path)
    engine = create_engine_and_tables(settings.database_url)
    account_id = ensure_default_account(engine)

    try:
        result = import_degiro_export(engine, export_dir, account_id, resolutions_path)
    except FileNotFoundError as missing:
        typer.echo(str(missing), err=True)
        raise typer.Exit(code=2) from missing
    except QuarantineError as quarantined:
        _report_quarantine(quarantined.pending, resolutions_path)
        raise typer.Exit(code=1) from quarantined

    typer.echo(
        f"batch {result.batch_id}: parsed {result.rows_parsed}, "
        f"inserted {result.rows_inserted}, skipped {result.rows_skipped}"
    )


@app.command("review")
def review_command() -> None:
    """Show the corporate-action review queue (design doc Sec 6.3).

    The queue is rebuilt by every import, so this always describes the export as it
    stands rather than a history of what was once asked.
    """
    engine = create_engine_and_tables(get_settings().database_url)
    with Session(engine) as session:
        rows = session.exec(
            select(CorporateActionReview).order_by(
                CorporateActionReview.trade_date  # type: ignore[arg-type]
            )
        ).all()

    if not rows:
        typer.echo("no corporate actions detected")
        return

    for row in rows:
        state = "resolved" if row.resolved else "OPEN    "
        typer.echo(f"{state}  {row.key}  {row.kind}  {row.label}")

    open_count = sum(1 for row in rows if not row.resolved)
    typer.echo(f"\n{len(rows)} detected, {open_count} still open")


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


@app.command("rebuild")
def rebuild_command(
    method: Annotated[
        str, typer.Option("--method", help="FIFO, LIFO or HIFO. Defaults to the configured one.")
    ] = "",
    through: Annotated[
        str,
        typer.Option(
            "--through",
            help="Last day of the daily position and cash series. Defaults to today.",
        ),
    ] = "",
) -> None:
    """Recompute the derived tables from the ledger (design doc Sec 11.2).

    Derived tables only -- the ledger and the price cache are both untouched.
    Exits non-zero if attributed charges do not equal the ledger's, having
    written nothing.

    The daily series runs to `--through`, defaulting to today rather than to the
    last trade: a position held since the last trade is still held, and a chart
    that stopped there would say otherwise.
    """
    settings = get_settings()
    chosen = (method or settings.lot_method).upper()
    if chosen not in LOT_METHODS:
        typer.echo(f"unknown method {chosen!r}; expected one of {list(LOT_METHODS)}", err=True)
        raise typer.Exit(code=2)

    try:
        window_end = date.fromisoformat(through) if through else date.today()
    except ValueError as bad:
        typer.echo(f"--through {through!r} is not YYYY-MM-DD", err=True)
        raise typer.Exit(code=2) from bad

    engine = create_engine_and_tables(settings.database_url)
    try:
        result = rebuild(engine, chosen, through=window_end)
    except ChargeMismatch as mismatch:
        typer.echo(str(mismatch), err=True)
        raise typer.Exit(code=1) from mismatch

    typer.echo(
        f"{result.method}: {result.lots} open lots, {result.closures} closures, "
        f"charges {result.charges_attributed} == ledger {result.charges_in_ledger}"
    )
    typer.echo(
        f"daily series: {result.position_days} position rows, "
        f"{result.cash_days} cash days through {window_end.isoformat()}"
    )


def _report_symbol_quarantine(report: ResolutionReport, answers_path: Path) -> None:
    """Print the open questions and a paste-ready answer for them.

    The same shape as `_report_quarantine`, and for the same reason: an operator
    retyping a key from memory produces an answer that parses cleanly and applies
    to nothing. Here the key is the ISIN, and the evidence is the measured ratio
    of their own executed prices against each candidate -- which is the only thing
    that distinguishes the right ticker from a leveraged product on the same
    underlying.
    """
    typer.echo(
        f"{len(report.pending)} instrument(s) must be answered before prices can be fetched."
    )
    typer.echo(f"No prices were written. Add each ISIN to {answers_path} and run this again.\n")
    for item in report.pending:
        typer.echo(f"  {item.isin}  {item.product_name}  (trades in {item.trade_currency})")
        if not item.verdicts:
            typer.echo("      no candidate ticker was found at all")
        for verdict in item.verdicts:
            ratios = ", ".join(f"{ratio:.2f}" for ratio in verdict.ratios) or "none measured"
            typer.echo(f"      {verdict.symbol}: {verdict.reason}")
            typer.echo(f"          executed price / provider close: {ratios}")
    typer.echo("\nA correct ticker sits near 1.00 on every trade.")
    typer.echo(f"Answer with a ticker, or with {MANUAL_ANSWER!r} to price it from the CSV.\n")
    typer.echo("symbols:")
    for item in report.pending:
        typer.echo(f"  - isin: {item.isin}")
        typer.echo("    symbol: ")


@app.command("fetch-prices")
def fetch_prices_command(
    full: Annotated[
        bool,
        typer.Option(
            "--full", help="Refetch the whole five-year history, not only what is missing."
        ),
    ] = False,
) -> None:
    """Fill the price and FX cache (M2 spec section 4).

    Refuses, writing nothing, while any instrument's symbol is unanswered. Exits
    1 on a refusal and 2 when a provider could not be reached, so this can gate a
    script.
    """
    settings = get_settings()
    answers_path = Path(settings.instrument_symbols_path)
    engine = create_engine_and_tables(settings.database_url)

    try:
        result = fetch_prices(
            engine,
            build_providers(settings),
            answers=load_symbol_answers(answers_path),
            now=datetime.now(tz=UTC),
            full=full,
        )
    except UnresolvedSymbols as refused:
        _report_symbol_quarantine(refused.report, answers_path)
        raise typer.Exit(code=1) from refused
    except ProviderError as unreachable:
        typer.echo(str(unreachable), err=True)
        raise typer.Exit(code=2) from unreachable

    breakdown = ", ".join(f"{name}={count}" for name, count in sorted(result.sources.items()))
    typer.echo(
        f"{result.instruments} instruments: {result.price_rows} price rows "
        f"({breakdown or 'none'}), {result.fx_rows} FX rows"
    )
    if result.earliest is not None:
        typer.echo(f"cache reaches back to {result.earliest.isoformat()}")

    if result.resolution_notes:
        # Visible, not silent: each of these was picked automatically among two
        # or more venues that agreed with each other and with the ledger. The
        # operator can override any of them by adding the ISIN to
        # `instrument_symbols.yaml`.
        typer.echo(
            f"\nauto-resolved among several venues (override in {answers_path} if wrong):"
        )
        for isin, note in sorted(result.resolution_notes.items()):
            typer.echo(f"  {isin}: {note}")


@app.command("symbols")
def symbols_command() -> None:
    """Show the symbol review queue (M2 spec section 6.3).

    Rebuilt by every `fetch-prices` run, so this always describes the export as
    it stands rather than a history of what was once asked.
    """
    engine = create_engine_and_tables(get_settings().database_url)
    with Session(engine) as session:
        rows = session.exec(
            select(SymbolReview).order_by(SymbolReview.isin)
        ).all()

    if not rows:
        typer.echo("no unresolved symbols")
        return

    for row in rows:
        typer.echo(f"OPEN  {row.isin}  {row.product_name}  ({row.trade_currency})")
        for candidate in json.loads(row.candidates):
            ratios = ", ".join(candidate["ratios"]) or "none measured"
            typer.echo(f"        {candidate['symbol']}: {candidate['reason']}  [{ratios}]")

    typer.echo(f"\n{len(rows)} instrument(s) still open")


if __name__ == "__main__":
    app()
