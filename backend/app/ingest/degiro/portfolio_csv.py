"""Reader for DeGiro's Portfolio.csv.

Never imported. Design doc Sec 6.2 makes this file purely a reconciliation target:
it is the broker's own statement of what the account holds, so anything computed
from the ledger can be checked against it rather than merely against itself.

Two things are read: the combined cash line and the open positions. The positions
are what M1's sharpest test compares against -- Sec 3.6 notes ORN = 32 is only
reachable if the split is applied as a corporate action rather than booked as a
trade, so this single number validates the whole corporate-action path.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from app.ingest.degiro.dialect import (
    CASH_ROW_PREFIX,
    PORTFOLIO_HEADER,
    PortCol,
    assert_header,
    parse_optional_decimal,
)


@dataclass(frozen=True, slots=True)
class PortfolioPosition:
    product: str
    isin: str
    quantity: Decimal
    value_base: Decimal


@dataclass(frozen=True, slots=True)
class PortfolioSnapshot:
    """The broker's own statement of the account at export time."""

    #: The combined DeGiro cash + flatex (FTX) cash balance, in EUR.
    cash_base: Decimal
    positions: tuple[PortfolioPosition, ...]

    def quantity_of(self, isin: str) -> Decimal:
        for position in self.positions:
            if position.isin == isin:
                return position.quantity
        return Decimal(0)


def parse_portfolio_csv(path: Path) -> PortfolioSnapshot:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        assert_header(header, PORTFOLIO_HEADER, path.name)
        rows = [row for row in reader if any(cell.strip() for cell in row)]

    cash = Decimal(0)
    positions: list[PortfolioPosition] = []

    for row in rows:
        product = row[PortCol.PRODUCT].strip()
        if product.startswith(CASH_ROW_PREFIX):
            cash = parse_optional_decimal(row[PortCol.VALUE_EUR]) or Decimal(0)
            continue

        isin = row[PortCol.ISIN].strip()
        quantity = parse_optional_decimal(row[PortCol.AMOUNT])
        # A row without an ISIN or a quantity is not a position. DeGiro has been
        # known to append summary lines; treating one as a holding would put a
        # phantom instrument into the reconciliation.
        if not isin or quantity is None:
            continue

        positions.append(
            PortfolioPosition(
                product=product,
                isin=isin,
                quantity=quantity,
                value_base=parse_optional_decimal(row[PortCol.VALUE_EUR]) or Decimal(0),
            )
        )

    return PortfolioSnapshot(cash_base=cash, positions=tuple(positions))
