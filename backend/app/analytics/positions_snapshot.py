"""The current, open-holdings snapshot -- cost basis, market value, unrealised P&L.

Split out of `valuation.py` in M3 alongside `quotes.py` and `prices.py`. This
module answers "what do I hold right now", while `valuation.py` answers "what
was the portfolio worth on each day" -- two different questions over the same
join, kept in separate files so each importer reaches only the one it needs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import Engine
from sqlmodel import Session, select

from app.analytics.prices import price_history, priced
from app.analytics.quotes import (
    FULL,
    MISSING,
    base_currency,
    rate_history,
    worst_coverage,
)
from app.domain.lots import LotMethod
from app.models.ledger import CashDaily, Lot, PositionDaily, Transaction
from app.models.types import Coverage

_ZERO = Decimal("0.00")


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
    coverage: Coverage


@dataclass(frozen=True, slots=True)
class PositionsSnapshot:
    as_of: date | None
    items: tuple[PositionValue, ...]
    total_cost_basis: Decimal
    #: `None` when ANY position is unpriceable. A total that silently omitted
    #: one reads identically to one that included it.
    total_market_value_base: Decimal | None
    total_unrealised_base: Decimal | None
    coverage: Coverage
    base_currency: str


def current_positions(engine: Engine, method: LotMethod) -> PositionsSnapshot:
    """Open holdings with cost basis, market value and unrealised P&L.

    `method` is required because the cost basis comes from `lot`, and FIFO, LIFO
    and HIFO disagree about it. The share COUNT does not depend on it -- that is
    why `position_daily` has no method column -- so the two halves of a row come
    from two tables with two different provenances, and the response envelope
    says which method it used.
    """
    with Session(engine) as session:
        base = base_currency(session)
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

        prices = price_history(session)
        rates = rate_history(session)

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
        result = priced(
            isin, as_of, holding.quantity, base=base, history=prices, rates=rates
        )
        quote = result.quote

        # Both conditions, though `Priced` sets the two together: it lets mypy
        # narrow `quote` for the branch below without a production assert, which
        # -O would strip out from under it.
        if result.value is None or quote is None:
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

        value = result.value
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
                coverage=result.coverage,
            )
        )

    coverage = worst_coverage([item.coverage for item in items])
    valued = [item.market_value_base for item in items if item.market_value_base is not None]
    complete = len(valued) == len(items)

    # Ruling F6: every `sum(...)` over money passes `_ZERO` as its start value.
    # A bare `sum(generator)` over an empty input returns the int `0`, which
    # would violate Decimal-everywhere at the type level (and can read as
    # `Decimal | int` under `mypy --strict`), even though its runtime value
    # happens to be numerically correct here.
    total_cost_basis = sum((basis.get(item.isin, _ZERO) for item in items), _ZERO)
    total_market_value_base = sum(valued, _ZERO) if complete else None
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
