"""Filling the price and FX cache. The only writer of `price_daily` and `fx_daily`.

M2 spec section 4 puts this on the fetched side of the determinism line, and the
line is not a metaphor: nothing in `rebuild/` or `analytics/` may write a row
here, and this module never writes a derived one. That separation is what lets
`rebuild()` still claim to be a function of the ledger while the chart it feeds
depends on a network call.

Three behaviours are load-bearing.

**It refuses while a symbol is unresolved** (M2 spec section 6.3), having written
nothing. A partial cache is worse than an empty one: the chart draws, some
instruments are silently absent, and `coverage` reports `partial` as though a
provider were at fault rather than a question being unanswered.

**It backfills a fixed five years** (M2-7). One call per instrument, no date
arithmetic per instrument, and the same depth for FX -- a price series five years
deep with FX two years deep would value the older half of every foreign position
as `missing`, which reads as an outage. The cache depth is deliberately deeper
than the chart's default start, so a reader who asks to look back five years is
answered from the cache rather than told the data does not exist.

**It is incremental afterwards** (M2-9). A five-year refetch on every run is a
request no free provider has a reason to keep serving. `--full` exists for the
day a provider revises its history.

The overlap that protects against a revised recent value is owned by whichever
side actually revises, and the two caches do not agree on who that is.

* **Prices:** `YahooPrices.series_since` already subtracts its own `_OVERLAP`
  before asking Yahoo for anything (Yahoo revises its most recent bars, and the
  provider is the layer that knows how far back). This module passes the raw
  newest-cached date straight through as `since` -- subtracting an overlap
  again here would double it silently.
* **FX:** `EcbRates.series` fetches exactly the `[start, end]` window it is
  given and applies no overlap of its own -- Frankfurter/ECB serves precisely
  what is asked for. An ECB reference rate is rarely but not never corrected,
  and an incremental window that started exactly at the newest cached date
  could never see such a correction. `_FX_OVERLAP` below is this module's own
  guard, applied only on the FX side.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
from sqlalchemy import Engine
from sqlmodel import Session, select

from app.ingest.symbols import (
    ResolutionReport,
    SymbolAnswer,
    resolve_symbols,
    write_symbol_review,
)
from app.models.ledger import Transaction
from app.models.market import FxDaily, PriceDaily
from app.providers.base import (
    BACKFILL_YEARS,
    FxPoint,
    FxProvider,
    PriceProvider,
    PriceSeries,
    SymbolResolver,
)
from app.providers.chain import PriceChain
from app.providers.ecb import EcbRates
from app.providers.manual import ManualPrices
from app.providers.openfigi import OpenFigiResolver
from app.providers.yahoo import YahooPrices
from app.settings import Settings

#: How far before the newest cached rate an incremental FX fetch reaches back.
#: `EcbRates.series` (Frankfurter) has no overlap of its own -- it serves
#: exactly the `[start, end]` window it is asked for -- so this module has to
#: supply the guard against a corrected reference rate itself. This is
#: deliberately NOT mirrored on the price side: `YahooPrices.series_since`
#: already subtracts its own overlap, and doing it again there would double it.
_FX_OVERLAP = timedelta(days=5)


class UnresolvedSymbols(RuntimeError):
    """A symbol is still waiting on a human. Nothing was written."""

    def __init__(self, report: ResolutionReport) -> None:
        super().__init__(f"{len(report.pending)} instrument(s) must be answered")
        self.report = report


@dataclass(frozen=True, slots=True)
class FetchResult:
    instruments: int
    price_rows: int
    fx_rows: int
    #: Provider name -> rows written. This is how the operator sees that an
    #: instrument fell through to `manual`.
    sources: dict[str, int]
    #: The oldest day now in the price cache. The five-year backfill, measured.
    earliest: date | None


@dataclass(frozen=True, slots=True)
class Providers:
    """Everything `fetch_prices` needs from the outside world, in one bag.

    Injected rather than constructed inside, so the tests can run the whole flow
    without a network and the CLI can build the real thing in one place.
    """

    resolver: SymbolResolver
    #: Used to probe candidates during resolution, where the manual fallback
    #: must NOT apply: a hand-typed price would "confirm" any ticker offered.
    prices: PriceProvider
    #: Used to fetch the accepted symbol, where the manual fallback does apply.
    chain: PriceChain
    fx: FxProvider


def build_providers(settings: Settings) -> Providers:
    """The real stack: OpenFIGI, Yahoo, ECB, and the hand-maintained file."""
    client = httpx.Client(follow_redirects=True)
    yahoo = YahooPrices(client)
    manual = ManualPrices.load(Path(settings.manual_prices_path))
    return Providers(
        resolver=OpenFigiResolver(client),
        prices=yahoo,
        chain=PriceChain([yahoo], manual),
        fx=EcbRates(client),
    )


def _ledger_rows(engine: Engine) -> list[Transaction]:
    with Session(engine) as session:
        return list(session.exec(select(Transaction)).all())


def _newest_price(session: Session, isin: str) -> date | None:
    rows = session.exec(select(PriceDaily).where(PriceDaily.isin == isin)).all()
    return max((row.price_date for row in rows), default=None)


def _newest_rate(session: Session, from_ccy: str, to_ccy: str) -> date | None:
    rows = session.exec(
        select(FxDaily).where(FxDaily.from_ccy == from_ccy, FxDaily.to_ccy == to_ccy)
    ).all()
    return max((row.rate_date for row in rows), default=None)


def _store_prices(
    session: Session, isin: str, series: PriceSeries, *, fetched_at: datetime
) -> int:
    """Upsert one instrument's points. Update in place, never a second row.

    A duplicate would make "the price that day" a question with two answers and
    the join would pick one arbitrarily; letting the unique constraint abort the
    run instead would mean a provider revising a single bar broke every fetch.
    """
    existing = {
        row.price_date: row
        for row in session.exec(select(PriceDaily).where(PriceDaily.isin == isin)).all()
    }
    for point in series.points:
        row = existing.get(point.on)
        if row is None:
            session.add(
                PriceDaily(
                    id=uuid4(),
                    isin=isin,
                    price_date=point.on,
                    close_unadjusted=point.close_unadjusted,
                    close_adjusted=point.close_adjusted,
                    currency=series.currency,
                    source=series.source,
                    fetched_at=fetched_at,
                )
            )
        else:
            row.close_unadjusted = point.close_unadjusted
            row.close_adjusted = point.close_adjusted
            row.currency = series.currency
            row.source = series.source
            row.fetched_at = fetched_at
            session.add(row)
    return len(series.points)


def _store_rates(
    session: Session,
    from_ccy: str,
    to_ccy: str,
    points: Iterable[FxPoint],
    *,
    source: str,
    fetched_at: datetime,
) -> int:
    existing = {
        row.rate_date: row
        for row in session.exec(
            select(FxDaily).where(FxDaily.from_ccy == from_ccy, FxDaily.to_ccy == to_ccy)
        ).all()
    }
    written = 0
    for point in points:
        written += 1
        row = existing.get(point.on)
        if row is None:
            session.add(
                FxDaily(
                    id=uuid4(),
                    from_ccy=from_ccy,
                    to_ccy=to_ccy,
                    rate_date=point.on,
                    rate=point.rate,
                    source=source,
                    fetched_at=fetched_at,
                )
            )
        else:
            row.rate = point.rate
            row.source = source
            row.fetched_at = fetched_at
            session.add(row)
    return written


def fetch_prices(
    engine: Engine,
    providers: Providers,
    *,
    answers: Mapping[str, SymbolAnswer],
    now: datetime,
    full: bool,
) -> FetchResult:
    """Resolve symbols, then fill the cache. Refuses while anything is unanswered."""
    rows: Sequence[Transaction] = _ledger_rows(engine)

    report = resolve_symbols(
        rows, answers=answers, resolver=providers.resolver, prices=providers.prices
    )
    write_symbol_review(engine, report, detected_at=now.replace(tzinfo=None))
    if report.pending:
        # Deliberately before any price is written. See the module docstring.
        raise UnresolvedSymbols(report)

    today = now.date()
    # A fixed depth, computed once rather than per instrument (M2-7).
    backfill_start = today - timedelta(days=365 * BACKFILL_YEARS)

    sources: Counter[str] = Counter()
    price_rows = 0
    with Session(engine) as session:
        for isin, symbol in sorted(report.resolved.items()):
            since = None if full else _newest_price(session, isin)
            # The raw newest-cached date, unmodified: `YahooPrices.series_since`
            # already subtracts its own `_OVERLAP` before asking Yahoo for
            # anything. Subtracting one again here would double it, silently.
            series = providers.chain.series(isin, symbol, since=since)
            if series is None or not series.points:
                # Answered, and still unpriceable. That is `coverage: "missing"`,
                # a result the API reports rather than an error that stops here.
                continue
            written = _store_prices(session, isin, series, fetched_at=now.replace(tzinfo=None))
            price_rows += written
            sources[series.source] += written
        session.commit()

    fx_rows = 0
    with Session(engine) as session:
        currencies = {
            row.currency
            for row in session.exec(select(PriceDaily)).all()
            if row.currency
        }
        base = _base_currency(session)
        for currency in sorted(currencies - {base}):
            newest = None if full else _newest_rate(session, currency, base)
            # Unlike the price side, ECB's own series() carries no overlap:
            # it answers exactly the window it is asked for. `_FX_OVERLAP` is
            # this module's own guard against a corrected reference rate.
            start = backfill_start if newest is None else newest - _FX_OVERLAP
            fx_series = providers.fx.series(currency, base, start=start, end=today)
            if fx_series is None:
                continue
            fx_rows += _store_rates(
                session,
                currency,
                base,
                fx_series.points,
                source=fx_series.source,
                fetched_at=now.replace(tzinfo=None),
            )
        session.commit()

    with Session(engine) as session:
        earliest = min(
            (row.price_date for row in session.exec(select(PriceDaily)).all()), default=None
        )

    return FetchResult(
        instruments=len(report.resolved),
        price_rows=price_rows,
        fx_rows=fx_rows,
        sources=dict(sources),
        earliest=earliest,
    )


def _base_currency(session: Session) -> str:
    """The account's base currency, read from the ledger rather than settings.

    The rows being valued belong to an account, and that account states its own
    base. Reading an environment variable here would let a changed `.env` silently
    reinterpret a cache that was fetched against a different one.
    """
    from app.models.ledger import Account

    account = session.exec(select(Account)).first()
    return account.base_currency if account is not None else "EUR"
