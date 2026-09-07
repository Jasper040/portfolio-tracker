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

M3's `analytics/instrument_return.py` is the caller M2 wrote this for. Two
things changed when it arrived:

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

This module reads. It does not convert, rebase, difference or link.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import TypeVar

from sqlalchemy import Engine
from sqlmodel import Session, select

from app.models.market import BenchmarkDaily, PriceDaily

#: The two tables that carry a dated adjusted close. Constrained rather than
#: bounded, and rather than a union of sequences: mypy widens
#: `Sequence[PriceDaily] | Sequence[BenchmarkDaily]` to their shared `SQLModel`
#: base and then cannot see any of the three fields. Same shape as
#: `ingest/source_ref.py`'s `_Ref`.
_Row = TypeVar("_Row", PriceDaily, BenchmarkDaily)


@dataclass(frozen=True, slots=True)
class TotalReturnPoint:
    on: date
    close_adjusted: Decimal
    #: The currency the series is quoted in, as the provider reported it --
    #: carried rather than assumed, for the reason parent doc Sec 5.3 gives
    #: about rates: a price without a stated currency is a runtime error
    #: waiting to be plausible.
    currency: str


def _points(rows: Sequence[_Row]) -> tuple[TotalReturnPoint, ...]:
    """The same three fields off either table. Chronological, always."""
    return tuple(
        TotalReturnPoint(
            on=row.price_date,
            close_adjusted=row.close_adjusted,
            currency=row.currency,
        )
        for row in sorted(rows, key=lambda row: row.price_date)
    )


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
    return _points(rows)


def benchmark_total_return_series(
    engine: Engine, key: str, *, start: date, end: date
) -> tuple[TotalReturnPoint, ...]:
    """One benchmark proxy's adjusted closes over an inclusive window.

    `key` is a configuration slug -- `world`, not an ISIN -- because
    `config/benchmarks.yaml` is tracked and an ISIN in a tracked file is a
    holding (M3 section 4.1). `benchmark_daily` stores both closes because the
    provider returns both in one response; only this one is ever read.
    """
    with Session(engine) as session:
        rows = session.exec(
            select(BenchmarkDaily).where(
                BenchmarkDaily.key == key,
                BenchmarkDaily.price_date >= start,
                BenchmarkDaily.price_date <= end,
            )
        ).all()
    return _points(rows)
