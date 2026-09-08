"""One benchmark proxy's adjusted closes. Split from `total_return.py` in M6a.

Both readers of `close_adjusted` lived in one module until M6a, and next to a
valuation path they are not equally dangerous.

Reaching an **instrument's** adjusted closes from a path that also values that
instrument at the unadjusted close plus cash is parent doc Sec 7.5's double count:
the adjusted series already contains every dividend, and a cash balance is a
running sum that contains them again.

Reaching a **benchmark's** is not. A benchmark is never held, pays the owner
nothing, and appears in no cash balance. There is nothing to count twice.

While the two shared a module, no endpoint could have the second without the
first -- and M6a's performance endpoint needs exactly that: a benchmark to compare
against, on a portfolio value built from the unadjusted close plus cash. It would
have had to reach an instrument's adjusted closes to get one, and the guard in
`tests/integration/test_no_double_count.py` would have been right to stop it.

Nothing in here converts, rebases, differences or links. The shared point type and
its mapper are in `adjusted.py`, which holds no query and is safe to reach from
anywhere; see that module for why they are not in either reader.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import Engine
from sqlmodel import Session, select

from app.analytics.adjusted import TotalReturnPoint, points
from app.models.market import BenchmarkDaily


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
    return points(rows)
