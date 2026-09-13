"""One instrument's adjusted closes. The dangerous half of the adjusted lane.

Parent doc Sec 7.5. `close_adjusted` already contains the effect of every
dividend, so adding dividend income to a return computed from it counts each
dividend twice -- and the error flatters the portfolio, which is the worst
direction for a figure nobody will question.

M2 makes that impossible to do by accident rather than merely discouraged. The
two closes are separate columns, valuation reads one and this lane reads the
other, and `tests/integration/test_no_double_count.py` asserts on the codebase's
own syntax tree that no read-side module reaches both and that no endpoint can
reach this module at all, exemptions named.

M3's `analytics/instrument_return.py` is the caller M2 wrote this for. Two things
changed when it arrived:

* A point carries its quoted **currency**, not an index. Converting to base
  before rebasing is the whole of M3-7 -- an index built from a foreign series
  before conversion is the return a local investor got, not the one the owner
  got -- and the caller cannot convert a number whose currency it was never
  told.
* Rebasing left this module entirely. It used to happen at the first day of the
  requested window, which makes a figure that moves when the reader drags the
  range control; M3-6 rebases at each in-market interval's start instead, where
  the answer is a property of the holding rather than of the viewport. The
  zero-first-close guard went with it: a single unusable close at the window
  edge is no reason to withhold the intervals that start later, and the caller
  already declines to rebase on a zero.

M6a split this module a second time, and the cut is about danger rather than
tidiness. `benchmark_total_return_series` moved to `benchmark_return.py` because a
benchmark is never held and appears in no cash balance, so reaching it beside a
valuation path counts nothing twice -- whereas reaching what is left here does.
Keeping both in one module meant an endpoint wanting the harmless reader had to
take the hazardous one with it. The shared point type went to `adjusted.py`, which
holds no query; see that module for why it is not in either reader.

This module reads. It does not convert, rebase, difference or link.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import Engine
from sqlmodel import Session, select

from app.analytics.adjusted import TotalReturnPoint, points
from app.models.market import PriceDaily


def total_return_series(
    engine: Engine, isin: str, *, start: date, end: date
) -> tuple[TotalReturnPoint, ...]:
    """One instrument's adjusted closes over an inclusive window."""
    with Session(engine) as session:
        rows = session.exec(
            select(PriceDaily).where(
                PriceDaily.isin == isin,
                PriceDaily.price_date >= start,
                PriceDaily.price_date <= end,
            )
        ).all()
    return points(rows)
