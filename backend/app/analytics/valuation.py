"""Value the portfolio by joining the ledger's half to the network's. No table.

M2 spec section 4. `position_daily` and `cash_daily` are derived and
deterministic; `price_daily` and `fx_daily` are fetched and dated. Valuation is
where the two meet, and it meets them at READ time.

There is deliberately no `valuation_daily`. A table would have two independent
triggers -- a ledger change and a price refresh -- either of which would silently
invalidate it, and there is no way to look at a stored valuation and tell whether
it is still true. The cost is a join per read rather than a lookup, which at this
portfolio's size is tens of thousands of rows in SQLite. M2 has no scale problem
to solve and should not pre-solve one.

Four rules decide what a day is worth, and each is a claim rather than a
convenience:

**Carry-forward is not separate machinery.** "The latest price dated on or before
this day" already carries a Friday close into Monday. The gap between that
price's date and the day being valued IS the staleness M2-3 requires recorded --
so nothing infers a price, it only says how old the one it used is. A price dated
AFTER the day is never used: that is not carry-forward, it is hindsight, and it
would make yesterday's chart move every time prices were fetched.

**Coverage is weighted by value, not by instrument count.** One large holding
going dark matters more than three small ones, and a count would say the
opposite.

**A manual component's staleness is not measured.** `config/manual_prices.csv` is
sparse by design -- an operator types a price when they have one -- so measuring
its age would pin every manual day at `partial` and `manual` would never be
reached at all, which parent doc Sec 8.1 forbids by requiring all four values to
mean something distinct. `partial` stays reachable because it is a statement
about PROVIDER staleness, and a stale provider price is a different problem from
a hand-maintained one.

**A day with an unpriceable holding has no value.** Not EUR 0, not the sum of
what happens to be priced. A total that quietly drops a position looks exactly
like a total that includes it, and `covered_pct` is `null` there too: there is no
denominator to take a fraction of.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import Engine
from sqlmodel import Session, select

from app.domain.lots import LotMethod
from app.models.ledger import Account, CashDaily, Lot, PositionDaily, Transaction
from app.models.market import FxDaily, PriceDaily
from app.providers.base import MANUAL as MANUAL_SOURCE

#: How old a provider price may be before the day is `partial`. Four calendar
#: days absorbs a weekend plus one holiday (M2 spec section 7.2). A venue closing
#: for longer would show as `partial`, which is arguably correct and has not been
#: observed in the current data.
STALE_DAYS = 4

FULL = "full"
PARTIAL = "partial"
MANUAL = "manual"
MISSING = "missing"

#: Worst news first. `missing` outranks everything because it is the only value
#: that withholds a number. `partial` outranks `manual` because a stale provider
#: price is a fault; a hand-typed one is a choice.
_SEVERITY = {FULL: 0, MANUAL: 1, PARTIAL: 2, MISSING: 3}

_ZERO = Decimal("0.00")
_ONE = Decimal("1")


def worst_coverage(values: Sequence[str]) -> str:
    """The most severe coverage among `values`. Empty means nothing to cover."""
    return max(values, key=lambda value: _SEVERITY[value], default=FULL)


@dataclass(frozen=True, slots=True)
class ValuationPoint:
    on: date
    #: `None` when any held instrument could not be priced. See the docstring.
    holdings_base: Decimal | None
    cash_base: Decimal
    value_base: Decimal | None
    coverage: str
    #: The share of the day's HOLDINGS value that is fresh from a provider or
    #: hand-supplied. `None` exactly when `coverage` is `missing`.
    covered_pct: Decimal | None


@dataclass(frozen=True, slots=True)
class ValuationSeries:
    points: tuple[ValuationPoint, ...]
    start: date | None
    end: date | None
    #: What the caller asked for, kept so the UI can say the window was clamped.
    requested_from: date | None
    clamped: bool
    #: The worst coverage in the series. An envelope claiming `full` over a
    #: series with a missing day would be Sec 8.1's omission one level up.
    coverage: str
    base_currency: str


@dataclass(frozen=True, slots=True)
class PositionValue:
    isin: str
    product_name: str
    currency: str
    quantity: Decimal
    cost_basis: Decimal
    charges_base: Decimal
    price: Decimal | None
    price_date: date | None
    source: str | None
    market_value_base: Decimal | None
    #: What the stock did. Sec 6.4 keeps these three separate all the way to the
    #: screen: gross, charges, net.
    gross_unrealised_base: Decimal | None
    unrealised_base: Decimal | None
    unrealised_pct: Decimal | None
    coverage: str


@dataclass(frozen=True, slots=True)
class PositionsSnapshot:
    as_of: date | None
    items: tuple[PositionValue, ...]
    total_cost_basis: Decimal
    #: `None` when ANY position is unpriceable. A total that silently omitted
    #: one reads identically to one that included it.
    total_market_value_base: Decimal | None
    total_unrealised_base: Decimal | None
    coverage: str
    base_currency: str


# ---------------------------------------------------------------------------
# Reading the four tables
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class _Quote:
    close: Decimal
    currency: str
    on: date
    source: str


def _base_currency(session: Session) -> str:
    account = session.exec(select(Account)).first()
    return account.base_currency if account is not None else "EUR"


def _price_history(session: Session) -> dict[str, list[PriceDaily]]:
    history: dict[str, list[PriceDaily]] = {}
    for row in session.exec(select(PriceDaily)).all():
        history.setdefault(row.isin, []).append(row)
    for rows in history.values():
        rows.sort(key=lambda row: row.price_date)
    return history


def _rate_history(session: Session) -> dict[tuple[str, str], list[FxDaily]]:
    history: dict[tuple[str, str], list[FxDaily]] = {}
    for row in session.exec(select(FxDaily)).all():
        history.setdefault((row.from_ccy, row.to_ccy), []).append(row)
    for rows in history.values():
        rows.sort(key=lambda row: row.rate_date)
    return history


def _latest_on_or_before(rows: Sequence[PriceDaily], on: date) -> PriceDaily | None:
    found: PriceDaily | None = None
    for row in rows:
        if row.price_date > on:
            break  # sorted, so nothing later can qualify
        found = row
    return found


def _latest_rate_on_or_before(rows: Sequence[FxDaily], on: date) -> FxDaily | None:
    found: FxDaily | None = None
    for row in rows:
        if row.rate_date > on:
            break
        found = row
    return found


def _quote_for(
    isin: str, on: date, history: Mapping[str, list[PriceDaily]]
) -> _Quote | None:
    row = _latest_on_or_before(history.get(isin, ()), on)
    if row is None:
        return None
    return _Quote(
        close=row.close_unadjusted,
        currency=row.currency,
        on=row.price_date,
        source=row.source,
    )


def _in_base(
    quote: _Quote,
    quantity: Decimal,
    on: date,
    base: str,
    rates: Mapping[tuple[str, str], list[FxDaily]],
) -> tuple[Decimal, int] | None:
    """Value one holding in the base currency, plus the age of the older input.

    `None` when the currency cannot be converted: a foreign holding needs two
    facts, and having one of them is no answer at all.
    """
    gross = quantity * quote.close
    age = (on - quote.on).days
    if quote.currency == base:
        return gross, age

    rate_row = _latest_rate_on_or_before(rates.get((quote.currency, base), ()), on)
    if rate_row is None or rate_row.rate == 0:
        return None
    # `rate` is units of the quote currency per 1 unit of base -- the same
    # direction as `domain.money.FxRate`. Divide, never multiply.
    return gross / rate_row.rate, max(age, (on - rate_row.rate_date).days)


def _classify(source: str, age: int) -> str:
    if source == MANUAL_SOURCE:
        # Deliberately not aged. See the module docstring.
        return MANUAL
    return PARTIAL if age > STALE_DAYS else FULL


# ---------------------------------------------------------------------------
# The series
# ---------------------------------------------------------------------------
def value_series(
    engine: Engine, *, start: date | None = None, end: date | None = None
) -> ValuationSeries:
    """The daily net portfolio value: holdings at market plus cash.

    Without `start`, the series begins at the first day a position existed
    (M2-7). With one, it begins there -- clamped to the ledger's own first day,
    never padded before it, because a zero portfolio value on a day the account
    did not exist is a false claim rather than a missing one. That clamp is what
    lets a reader ask for five years and get an honest answer whatever the
    ledger's depth.
    """
    with Session(engine) as session:
        base = _base_currency(session)
        cash_rows = sorted(session.exec(select(CashDaily)).all(), key=lambda r: r.cash_date)
        position_rows = session.exec(select(PositionDaily)).all()
        prices = _price_history(session)
        rates = _rate_history(session)

    if not cash_rows:
        return ValuationSeries(
            points=(), start=None, end=None, requested_from=start,
            clamped=False, coverage=FULL, base_currency=base,
        )

    held: dict[date, list[PositionDaily]] = {}
    for row in position_rows:
        held.setdefault(row.position_date, []).append(row)

    floor = cash_rows[0].cash_date
    default_start = min(held) if held else floor
    window_start = max(start, floor) if start is not None else default_start
    window_end = end or cash_rows[-1].cash_date

    points: list[ValuationPoint] = []
    for cash_row in cash_rows:
        day = cash_row.cash_date
        if day < window_start or day > window_end:
            continue
        points.append(
            _value_day(day, held.get(day, []), cash_row.balance_base, base, prices, rates)
        )

    return ValuationSeries(
        points=tuple(points),
        start=points[0].on if points else None,
        end=points[-1].on if points else None,
        requested_from=start,
        clamped=start is not None and start < floor,
        coverage=worst_coverage([point.coverage for point in points]),
        base_currency=base,
    )


def _value_day(
    day: date,
    holdings: Sequence[PositionDaily],
    cash: Decimal,
    base: str,
    prices: Mapping[str, list[PriceDaily]],
    rates: Mapping[tuple[str, str], list[FxDaily]],
) -> ValuationPoint:
    total = _ZERO
    covered = _ZERO
    verdicts: list[str] = []
    missing = False

    for holding in sorted(holdings, key=lambda row: row.isin):
        quote = _quote_for(holding.isin, day, prices)
        converted = (
            None
            if quote is None
            else _in_base(quote, holding.quantity, day, base, rates)
        )
        if quote is None or converted is None:
            missing = True
            verdicts.append(MISSING)
            continue

        value, age = converted
        verdict = _classify(quote.source, age)
        verdicts.append(verdict)
        total += value
        if verdict != PARTIAL:
            covered += value

    coverage = worst_coverage(verdicts)
    if missing:
        # No total, so no fraction of one either.
        return ValuationPoint(
            on=day, holdings_base=None, cash_base=cash, value_base=None,
            coverage=MISSING, covered_pct=None,
        )

    return ValuationPoint(
        on=day,
        holdings_base=total,
        cash_base=cash,
        value_base=total + cash,
        coverage=coverage,
        covered_pct=_ONE if total == 0 else covered / total,
    )


# ---------------------------------------------------------------------------
# The current positions
# ---------------------------------------------------------------------------
def current_positions(engine: Engine, method: LotMethod) -> PositionsSnapshot:
    """Open holdings with cost basis, market value and unrealised P&L.

    `method` is required because the cost basis comes from `lot`, and FIFO, LIFO
    and HIFO disagree about it. The share COUNT does not depend on it -- that is
    why `position_daily` has no method column -- so the two halves of a row come
    from two tables with two different provenances, and the response envelope
    says which method it used.
    """
    with Session(engine) as session:
        base = _base_currency(session)
        position_rows = session.exec(select(PositionDaily)).all()
        cash_rows = session.exec(select(CashDaily)).all()
        if not position_rows and not cash_rows:
            return PositionsSnapshot(
                as_of=None, items=(), total_cost_basis=_ZERO,
                total_market_value_base=None, total_unrealised_base=None,
                coverage=FULL, base_currency=base,
            )
        # `cash_daily` is a complete calendar series (M2-3): its latest row is
        # the most recent day the ledger's derived tables know about, even a day
        # nothing is held. `position_daily` alone would understate "today"
        # whenever the last thing that happened was selling out of a position --
        # that day gets no position row at all, so its max date silently regresses
        # to the day before the sale and re-shows a position that is closed.
        as_of = (
            max(row.cash_date for row in cash_rows)
            if cash_rows
            else max(row.position_date for row in position_rows)
        )
        held = [row for row in position_rows if row.position_date == as_of]

        lots = session.exec(select(Lot).where(Lot.method == method)).all()
        names: dict[str, str] = {}
        currencies: dict[str, str] = {}
        for row in session.exec(select(Transaction)).all():
            if row.isin:
                if row.product_name:
                    names.setdefault(row.isin, row.product_name)
                if row.currency_local:
                    currencies.setdefault(row.isin, row.currency_local)

        prices = _price_history(session)
        rates = _rate_history(session)

    basis: dict[str, Decimal] = {}
    charges: dict[str, Decimal] = {}
    for lot in lots:
        basis[lot.isin] = basis.get(lot.isin, _ZERO) + lot.cost_basis
        charges[lot.isin] = (
            charges.get(lot.isin, _ZERO) + lot.commission + lot.autofx + lot.tax
        )

    items: list[PositionValue] = []
    for holding in sorted(held, key=lambda row: row.isin):
        isin = holding.isin
        cost = basis.get(isin, _ZERO)
        charged = charges.get(isin, _ZERO)
        quote = _quote_for(isin, as_of, prices)
        converted = (
            None
            if quote is None
            else _in_base(quote, holding.quantity, as_of, base, rates)
        )

        if quote is None or converted is None:
            items.append(
                PositionValue(
                    isin=isin,
                    product_name=names.get(isin, ""),
                    currency=currencies.get(isin, base),
                    quantity=holding.quantity,
                    cost_basis=cost,
                    charges_base=charged,
                    price=None if quote is None else quote.close,
                    price_date=None if quote is None else quote.on,
                    source=None if quote is None else quote.source,
                    market_value_base=None,
                    gross_unrealised_base=None,
                    unrealised_base=None,
                    unrealised_pct=None,
                    coverage=MISSING,
                )
            )
            continue

        value, age = converted
        gross = value - cost
        net = gross - charged
        items.append(
            PositionValue(
                isin=isin,
                product_name=names.get(isin, ""),
                currency=quote.currency,
                quantity=holding.quantity,
                cost_basis=cost,
                charges_base=charged,
                price=quote.close,
                price_date=quote.on,
                source=quote.source,
                market_value_base=value,
                gross_unrealised_base=gross,
                unrealised_base=net,
                # `None` rather than zero on a zero basis: a return on nothing is
                # undefined, and 0% would read as "broke even".
                unrealised_pct=None if cost == 0 else net / cost,
                coverage=_classify(quote.source, age),
            )
        )

    coverage = worst_coverage([item.coverage for item in items])
    priced = [item.market_value_base for item in items if item.market_value_base is not None]
    complete = len(priced) == len(items)

    # Ruling F6: every `sum(...)` over money passes `_ZERO` as its start value.
    # A bare `sum(generator)` over an empty input returns the int `0`, which
    # would violate Decimal-everywhere at the type level (and can read as
    # `Decimal | int` under `mypy --strict`), even though its runtime value
    # happens to be numerically correct here.
    total_cost_basis = sum((basis.get(item.isin, _ZERO) for item in items), _ZERO)
    total_market_value_base = sum(priced, _ZERO) if complete else None
    total_unrealised_base = (
        sum((item.unrealised_base or _ZERO for item in items), _ZERO)
        if complete
        else None
    )

    return PositionsSnapshot(
        as_of=as_of,
        items=tuple(items),
        total_cost_basis=total_cost_basis,
        total_market_value_base=total_market_value_base,
        total_unrealised_base=total_unrealised_base,
        coverage=coverage,
        base_currency=base,
    )
