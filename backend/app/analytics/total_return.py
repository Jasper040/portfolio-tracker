"""Total return from the dividend-adjusted close. The ONLY reader of that column.

Parent doc Sec 7.5. `close_adjusted` already contains the effect of every
dividend, so adding dividend income to a return computed from it counts each
dividend twice -- and the error flatters the portfolio, which is the worst
direction for a figure nobody will question.

M2 makes that impossible to do by accident rather than merely discouraged. The
two closes are separate columns, valuation reads one and this module reads the
other, and `tests/integration/test_no_double_count.py` asserts on the codebase's
own syntax tree that no read-side module reaches both and that no valuation
endpoint can reach this module at all.

Nothing calls this yet. M3's instrument chart is what will, and it is written now
because the guarantee is a statement about two modules -- a test that valuation
cannot reach a module that does not exist passes for the wrong reason, which is
precisely the failure mode M1 found nine instances of.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import Engine
from sqlmodel import Session, select

from app.models.market import PriceDaily


@dataclass(frozen=True, slots=True)
class TotalReturnPoint:
    on: date
    close_adjusted: Decimal
    #: Rebased to 1 at the first day in the window, so two instruments can be
    #: compared without either's price level dominating the picture.
    index: Decimal

def total_return_series(
    engine: Engine, isin: str, *, start: date, end: date
) -> tuple[TotalReturnPoint, ...]:
    """One instrument's total-return index over an inclusive window."""
    with Session(engine) as session:
        rows = sorted(
            session.exec(
                select(PriceDaily).where(
                    PriceDaily.isin == isin,
                    PriceDaily.price_date >= start,
                    PriceDaily.price_date <= end,
                )
            ).all(),
            key=lambda row: row.price_date,
        )

    if not rows:
        return ()

    first = rows[0].close_adjusted
    if first == 0:
        return ()

    return tuple(
        TotalReturnPoint(
            on=row.price_date,
            close_adjusted=row.close_adjusted,
            index=row.close_adjusted / first,
        )
        for row in rows
    )
