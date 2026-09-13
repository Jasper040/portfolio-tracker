# M6a — Portfolio Return — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A daily chain-linked, time-weighted portfolio return — cash included and contributing zero — with a figure for any window that is withheld across a gap, compared against one benchmark, labelled on both sides, on a new live Performance screen.

**Architecture:** Two new pure-ish analytics modules extend M2's valuation lane: `analytics/flows.py` extracts external flows per day from the ledger, and `analytics/portfolio_return.py` differences and chain-links a `ValuationSeries` into contiguous runs. A third, `analytics/portfolio_benchmark.py`, compares those runs against a benchmark's adjusted closes using arithmetic shared with M3's instrument comparison, which moves into `analytics/indexing.py` for the purpose. `api/routes_performance.py` composes them and computes nothing; it reaches the benchmark reader and provably cannot reach `total_return.py`.

**Tech Stack:** Python 3.12+, SQLModel/SQLAlchemy, FastAPI, pytest; React + Vite + TypeScript + ECharts, Vitest.

**Spec:** `docs/superpowers/specs/2026-09-07-m6a-portfolio-return-design.md` (M6a), under `docs/superpowers/specs/2026-09-05-portfolio-tracker-design.md` (the parent). A `§` refers to the parent; "M6a section N" refers to the M6a design. **Read both before Task 1.**

**Jira:** PT-25, under epic PT-7.

## Global Constraints

- **Money is `Decimal`, never `float`.** Everywhere, including tests, returns and index values. Strings over the wire.
- **M6a-2:** portfolio return is computed on the **unadjusted-close-plus-cash** lane, never the adjusted one.
- **M6a-3:** cash is inside the portfolio number and contributes zero to it. Not excluded, not netted out.
- **M6a-4:** sub-period boundaries are **daily**. Not per transaction.
- **M6a-5:** flows are **effective at the close** of the day they are dated. A trade needs no timing convention.
- **M6a-6:** external flows are `DEPOSIT` and `WITHDRAWAL` **only**. Sweep rows are not flows.
- **M6a-7:** a day with no valuation invalidates the **two** periods it bounds, and **no single figure spans a gap**.
- **M6a-10:** the comparison is **labelled on both sides** — the portfolio's dividends sit idle in cash and are net of withholding, the benchmark's are reinvested and gross.
- **§7.4:** no endpoint returns an unlabelled return. TWR is the only figure compared against a benchmark.
- **§8.1:** a value that cannot be computed is `null` with a reason. Never `0`, never an omitted key.
- **Excess is arithmetic** (`portfolio − benchmark`), matching M3's `IntervalExcess.excess`.
- **No module on the performance path may reach `analytics/total_return.py`.** `tests/integration/test_no_double_count.py` walks imports, so a transitive hop counts.
- **Out of scope, and a defect if present:** per-instrument return, contribution to return, MWR/XIRR, industry weight over time, any per-period P&L in euros.
- **NO REAL HOLDINGS IN ANY TRACKED FILE** — no ISIN, instrument name, ticker, benchmark brand word, or amount at five significant digits. That includes this plan and every fixture. `realdata` tests derive expectations from the gitignored export at run time via `backend/tests/integration/realdata_subject.py`.
- **TDD strictly.** Write the test, run it, watch it fail *for the right reason*, then implement.
- **Type hints on every function. Frozen dataclasses. Files 200–400 lines typical, 800 maximum.**
- **No new dependency, no new table, no new column.** M6a reads what M2 and M3 already store.
- **Commits:** `<type>: <description> (PT-25)`, ending with the line `Claude-Session: https://claude.ai/code/session_013U6MLH7npaNQwK1ZS8UFbS`.

## What is already done — do not redo it

M6a section 6's two structural findings shipped ahead of this plan as their own issues:

- **PT-27** moved `benchmark_total_return_series` into `analytics/benchmark_return.py`, with the shared point type in `analytics/adjusted.py`.
- **PT-28** derives the guard's endpoint list from `api/routes_*.py`, so `routes_performance.py` is guarded the moment the file exists.

## Decisions this plan makes that the design leaves open

| | |
|---|---|
| P-1 | **A flow belongs to the link whose close it falls before.** The valuation grid is weekdays and `cash_daily` applies every row dated on or before a day, so a Saturday deposit is inside Monday's `V`. `F(d)` is every flow dated after the previous point and on or before `d`. Looking flows up by exact date would report a weekend deposit as Monday's gain. |
| P-2 | **A link needs a positive denominator.** Leading closes with a known `V ≤ 0` are trimmed (M6a section 3.4). An interior `V(d−1) ≤ 0` gives that link no return, with its own reason, and breaks the run exactly as a gap does. |
| P-3 | **The window figure exists only when every link in the window has a return.** A leading or trailing gap voids it too: reporting the surviving run as the window's return would be a return over a span nobody asked for. |
| P-4 | **The default window starts at the ledger's first day**, not valuation's M2-7 default of the first position, so the first buy day's price-versus-close effect is measured. The route asks `value_series` for `start=date.min` and reports `clamped` only when the caller supplied `from`. |
| P-5 | **M3's conversion, rebasing, span and linking helpers move to `analytics/indexing.py`.** The portfolio comparison needs the same rules, and importing them from `instrument_return.py` would reach `total_return.py`. |
| P-6 | **The comparison lives in `analytics/portfolio_benchmark.py`**, not in the route (M3's convention: routes compute nothing) and not in `portfolio_return.py` (so M6b can consume the return series without acquiring a benchmark reader). It accepts runs structurally, as `instrument_return` accepts intervals. |
| P-7 | **`get_benchmarks` moves to `api/dependencies.py`.** Importing it from `routes_instrument.py` would give the performance route a path to `total_return.py`. |
| P-8 | **Performance response models go in `api/schemas_performance.py`.** `schemas.py` is already past 500 lines. |
| P-9 | **The benchmark selector, span badge and TER label move to `components/ui/BenchmarkControls.tsx`**, shared by Instrument and Performance. |
| P-10 | **The Performance tab sits right after Benchmarks & Industry**, as Instrument sits after Stock Detail: the live counterpart after the modelled screen. Benchmarks stays `MODELLED`; its industry sections are M6b's. |

## The verification gate

All six commands, green, before **every** commit. A red suite is a stop-and-fix, not a note-and-continue.

```bash
cd backend && ./.venv/Scripts/python.exe -m pytest -q \
  && ./.venv/Scripts/python.exe -m pytest -q -m realdata \
  && ./.venv/Scripts/python.exe -m ruff check . \
  && ./.venv/Scripts/python.exe -m mypy app
cd ../frontend && npx tsc --noEmit && npx vitest run
```

Baseline at the start of M6a: **637 backend tests, 59 opt-in `realdata` (none skipped — the local price cache is populated), 165 frontend**, ruff clean, `mypy --strict` clean over 58 source files. Every task adds tests; none may remove or weaken one.

## Environment notes that will bite

- **There is exactly one backend virtualenv and it is `backend/.venv`.** Every command here is `./.venv/Scripts/python.exe -m …` from `backend/`, so activation cannot go wrong. See `docs/RUNBOOK.md` section 1.
- **The guard follows imports, not names.** Importing anything from `routes_instrument.py` or `instrument_return.py` into the performance path fails `test_no_route_module_can_reach_the_total_return_module`. That is the guard working. Fix the import, never the guard.
- **The leak scanner reads this plan and every file you write.** It runs inside `-m realdata`, matching real instrument-name tokens as whole words, case-insensitively. If it flags an ordinary identifier or word, rename or reword it. A word goes into `_NOT_A_HOLDING` only if it identifies no holding.
- **Ruff selects E, F, I, B.** E741 forbids a variable named `l`; B905 forbids `zip()` without `strict=` — use `itertools.pairwise`.
- **Do not create a directory named `data`** anywhere new. `.gitignore`'s bare `data/` rule swallows it at any depth.
- **No migrations.** M6a adds no table and no column; keep it that way.
- Git Bash heredocs mangle large Python and JSX payloads. Use the Write tool for anything longer than a few lines.
- Frontend component tests run in jsdom via a `@vitest-environment jsdom` docblock; the default environment is `node`. The first `vitest run` after a cold checkout can time out a `userEvent` test while Vite transforms — re-run before investigating.

## File structure

| File | Responsibility |
|---|---|
| `backend/app/analytics/indexing.py` | *new* — conversion to base, rebasing, span judgement, linking. Moved out of `instrument_return.py` |
| `backend/app/analytics/instrument_return.py` | *modified* — keeps the order of operations and the instrument comparison |
| `backend/app/analytics/flows.py` | *new* — external flows per calendar day |
| `backend/app/analytics/portfolio_return.py` | *new* — daily links, runs, gaps and the window figure |
| `backend/app/analytics/portfolio_benchmark.py` | *new* — each run against a benchmark, labelled |
| `backend/app/api/dependencies.py` | *new* — `get_benchmarks`, reaching nothing |
| `backend/app/api/routes_instrument.py` | *modified* — imports `get_benchmarks` from there |
| `backend/app/api/schemas_performance.py` | *new* — the performance response models |
| `backend/app/api/routes_performance.py` | *new* — `GET /api/performance` |
| `backend/app/main.py` | *modified* — registers the router |
| `backend/tests/integration/synthetic_ledger.py` | *new* — a hand-written ledger driven through `rebuild()` |
| `frontend/src/api/types.ts`, `api/client.ts` | *modified* — `PerformanceReport`, `fetchPerformance` |
| `frontend/src/lib/performance.ts` | *new* — the pure chart option builder |
| `frontend/src/components/ui/BenchmarkControls.tsx` | *new* — moved out of `screens/Instrument.tsx` |
| `frontend/src/screens/Performance.tsx` | *new* — the live screen |
| `frontend/src/navigation.ts`, `App.tsx` | *modified* — a twelfth tab |
| `docs/RUNBOOK.md` | *modified* — the twelfth screen, the endpoint, the counts |

Task 1 is a pure refactor that changes no number. Tasks 2–4 are analytics. Task 5 makes them readable over HTTP, 6–7 visible, 8 proves them against the real export. Stopping after Task 5 leaves a working, guarded API; stopping after Task 7 leaves a working screen. Both are coherent.

## Before Task 1

- [ ] **Step 1:** On branch `m6a-portfolio-return` (cut from `master`), confirm `git status` is clean.
- [ ] **Step 2:** Move PT-25 to `In Progress` in Jira.
- [ ] **Step 3:** Run the verification gate and confirm the baseline above.

---

### Task 1: Move the index arithmetic into `analytics/indexing.py`

**Files:**
- Create: `backend/app/analytics/indexing.py`
- Modify: `backend/app/analytics/instrument_return.py`
- Test: `backend/tests/unit/test_indexing.py`
- Test: `backend/tests/integration/test_no_double_count.py` (append one test)

**Interfaces:**
- Consumes: `TotalReturnPoint` from `app.analytics.adjusted`; `STALE_DAYS`, `Quote`, `in_base` from `app.analytics.quotes`; `FxDaily`; `SpanCoverage`.
- Produces (all in `app.analytics.indexing`):
  - `SPAN_FULL`, `SPAN_PARTIAL`, `SPAN_MISSING: SpanCoverage`; `ONE`, `HUNDRED: Decimal`
  - `IndexPoint(on: date, index: Decimal)` frozen dataclass
  - `BasePoint(on: date, value: Decimal)` frozen dataclass
  - `in_base_points(points: Sequence[TotalReturnPoint], base: str, rates: Mapping[tuple[str, str], list[FxDaily]]) -> tuple[tuple[BasePoint, ...], int]`
  - `rebase(points: Sequence[BasePoint], start: date, end: date) -> tuple[tuple[IndexPoint, ...], Decimal | None]`
  - `worst_span(values: Sequence[SpanCoverage]) -> SpanCoverage`
  - `span_coverage(points: Sequence[BasePoint], start: date, end: date, measured: Decimal | None) -> SpanCoverage`
  - `link(returns: Sequence[Decimal | None]) -> Decimal | None`
  - `side_reason(what: str, points: Sequence[BasePoint], start: date, end: date, coverage: SpanCoverage) -> str | None`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/unit/test_indexing.py`:

```python
"""The shared index arithmetic, asserted on its own now that two comparisons use it.

Split out of `instrument_return.py` in M6a. Everything here was already exercised
through `comparison()` in `tests/integration/test_instrument_return.py`. What those
tests could not say is that the helpers work WITHOUT an instrument -- which is the
reason they moved, because the portfolio comparison has none.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal as D
from uuid import uuid4

from app.analytics.adjusted import TotalReturnPoint
from app.analytics.indexing import (
    SPAN_FULL,
    SPAN_MISSING,
    SPAN_PARTIAL,
    BasePoint,
    in_base_points,
    link,
    rebase,
    side_reason,
    span_coverage,
    worst_span,
)
from app.models.market import FxDaily

MON = date(2025, 3, 3)
TUE = date(2025, 3, 4)
WED = date(2025, 3, 5)
FRI = date(2025, 3, 7)
NEXT_MON = date(2025, 3, 10)
NEXT_FRI = date(2025, 3, 14)


def test_rebase_indexes_to_100_at_the_start_and_returns_the_ratio() -> None:
    points = (BasePoint(MON, D("50")), BasePoint(TUE, D("55")), BasePoint(WED, D("60")))
    index, measured = rebase(points, MON, WED)
    assert [p.index for p in index] == [D("100"), D("110"), D("120")]
    assert measured == D("0.2")


def test_rebase_ignores_points_outside_the_span() -> None:
    points = (BasePoint(MON, D("40")), BasePoint(TUE, D("50")), BasePoint(WED, D("55")))
    index, measured = rebase(points, TUE, WED)
    assert [p.on for p in index] == [TUE, WED]
    assert measured == D("0.1")


def test_an_empty_or_zero_based_span_measures_nothing() -> None:
    assert rebase((), MON, WED) == ((), None)
    assert rebase((BasePoint(MON, D("0")),), MON, WED) == ((), None)


def test_a_series_starting_well_after_the_span_covers_only_part_of_it() -> None:
    points = (BasePoint(NEXT_MON, D("50")), BasePoint(NEXT_FRI, D("55")))
    _, measured = rebase(points, MON, NEXT_FRI)
    assert span_coverage(points, MON, NEXT_FRI, measured) == SPAN_PARTIAL


def test_a_weekend_at_the_edge_of_the_span_is_not_a_shortfall() -> None:
    points = (BasePoint(TUE, D("50")), BasePoint(FRI, D("55")))
    _, measured = rebase(points, MON, FRI)
    assert span_coverage(points, MON, FRI, measured) == SPAN_FULL


def test_nothing_measured_is_missing() -> None:
    assert span_coverage((), MON, FRI, None) == SPAN_MISSING


def test_worst_span_ranks_missing_above_partial_above_full() -> None:
    assert worst_span([SPAN_FULL, SPAN_PARTIAL]) == SPAN_PARTIAL
    assert worst_span([SPAN_PARTIAL, SPAN_MISSING, SPAN_FULL]) == SPAN_MISSING
    assert worst_span([]) == SPAN_FULL


def test_link_chains_and_refuses_a_missing_link() -> None:
    assert link([D("0.1"), D("0.1")]) == D("0.21")
    assert link([D("0.1"), None]) is None
    assert link([]) == D("0")


def test_conversion_divides_by_the_rate_and_counts_what_it_drops() -> None:
    rates = {
        ("USD", "EUR"): [
            FxDaily(
                id=uuid4(), from_ccy="USD", to_ccy="EUR", rate_date=MON, rate=D("1.25"),
                source="ecb", fetched_at=datetime(2026, 9, 6, 12, 0, 0),
            )
        ]
    }
    points = (
        TotalReturnPoint(on=date(2025, 2, 28), close_adjusted=D("50"), currency="USD"),
        TotalReturnPoint(on=MON, close_adjusted=D("50"), currency="USD"),
    )
    converted, dropped = in_base_points(points, "EUR", rates)
    assert converted == (BasePoint(on=MON, value=D("40")),)
    assert dropped == 1


def test_side_reason_names_the_span_it_fell_short_of() -> None:
    points = (BasePoint(NEXT_MON, D("50")), BasePoint(NEXT_FRI, D("55")))
    assert side_reason("benchmark 'world'", points, MON, NEXT_FRI, SPAN_PARTIAL) == (
        "benchmark 'world' covers 2025-03-10 to 2025-03-14, "
        "not the whole of 2025-03-03 to 2025-03-14"
    )
    assert side_reason("x", (), MON, FRI, SPAN_MISSING) == "no x data from 2025-03-03 to 2025-03-07"
    assert side_reason("x", points, MON, FRI, SPAN_FULL) is None
```

Append to `backend/tests/integration/test_no_double_count.py`:

```python
def test_the_shared_index_arithmetic_reaches_no_reader() -> None:
    """`indexing.py` is imported by the instrument comparison AND the portfolio
    comparison, so a read added to it would reach both at once. It stays
    arithmetic: no price reader, no adjusted-close reader, no valuation.

    The `is_file` assertion is not decoration -- `_reachable_from` skips a module
    that does not exist, so without it this passes vacuously."""
    assert (APP / "analytics" / "indexing.py").is_file()
    reachable = _reachable_from("app.analytics.indexing")
    for reader in (
        "app.analytics.total_return",
        "app.analytics.benchmark_return",
        "app.analytics.prices",
        "app.analytics.valuation",
    ):
        assert reader not in reachable, reader
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && ./.venv/Scripts/python.exe -m pytest tests/unit/test_indexing.py tests/integration/test_no_double_count.py -q
```

Expected: collection error in `test_indexing.py` — `ModuleNotFoundError: No module named 'app.analytics.indexing'`; `test_the_shared_index_arithmetic_reaches_no_reader` fails on the `is_file` assertion.

- [ ] **Step 3: Create `backend/app/analytics/indexing.py`**

The bodies are moved from `instrument_return.py` unchanged except for the renames in the Interfaces block and `side_reason` taking `start`/`end` instead of an interval.

```python
"""Total-return index arithmetic, shared by every comparison against a benchmark.

Split out of `instrument_return.py` in M6a. The portfolio comparison has to do to
a benchmark exactly what the instrument comparison already does -- convert every
close to base, rebase over a span, judge whether the series actually reaches
across that span, link -- and a second copy of any one of those steps would be
free to disagree with the first about what "the benchmark covered it" means.

The move is forced rather than tidy. `instrument_return.py` imports
`total_return.py`, which reads an INSTRUMENT's adjusted closes, and an endpoint
that values the portfolio on the unadjusted close plus cash must never reach that
module (parent doc Sec 7.5; `tests/integration/test_no_double_count.py`).
Importing these helpers from `instrument_return.py` would have handed the
performance endpoint the path.

Nothing here queries. Callers hand in `TotalReturnPoint`s -- the adjusted close is
the only close this module ever touches, and only on a point it was given -- plus
a rate history, and get back base-currency levels, indices, returns and a span
verdict. `quotes.py` supplies FX and names neither close. The ORDER these are
applied in is not this module's to decide: `instrument_return.py`'s docstring
states it, and every caller follows it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.analytics.adjusted import TotalReturnPoint
from app.analytics.quotes import STALE_DAYS, Quote, in_base
from app.models.market import FxDaily
from app.models.types import SpanCoverage

#: The span vocabulary's own constants. `quotes.py` exports `FULL`, `PARTIAL`
#: and `MISSING` typed `Coverage`, so returning one of those from a
#: `SpanCoverage` function is precisely the mix this type split exists to
#: prevent -- and mypy rejects it, which is the split earning its keep on the
#: first edit after it landed.
SPAN_FULL: SpanCoverage = "full"
SPAN_PARTIAL: SpanCoverage = "partial"
SPAN_MISSING: SpanCoverage = "missing"

ONE = Decimal("1")
HUNDRED = Decimal("100")


@dataclass(frozen=True, slots=True)
class IndexPoint:
    on: date
    #: 100 at the start of the span this point belongs to.
    index: Decimal


@dataclass(frozen=True, slots=True)
class BasePoint:
    """One day's total-return level, already in the owner's currency."""

    on: date
    value: Decimal


def in_base_points(
    points: Sequence[TotalReturnPoint],
    base: str,
    rates: Mapping[tuple[str, str], list[FxDaily]],
) -> tuple[tuple[BasePoint, ...], int]:
    """Convert every point to `base`. Always before `rebase`, never after (M3-7).

    Returns the convertible points and a count of the ones dropped for want of
    a rate, because a series quietly shortened is how a return ends up measured
    over a span nobody chose.
    """
    out: list[BasePoint] = []
    dropped = 0
    for point in points:
        quote = Quote(
            close=point.close_adjusted,
            currency=point.currency,
            on=point.on,
            # `Quote.source` exists to feed `classify()`, which this module
            # never calls: coverage here is a question about the span the
            # benchmark covers, not the age of any one quote. Left empty rather
            # than filled with an invented provider name.
            source="",
        )
        # Quantity 1: this is an index, not a valuation.
        converted = in_base(quote, ONE, point.on, base, rates)
        if converted is None:
            dropped += 1
            continue
        out.append(BasePoint(on=point.on, value=converted[0]))
    return tuple(out), dropped


def rebase(
    points: Sequence[BasePoint], start: date, end: date
) -> tuple[tuple[IndexPoint, ...], Decimal | None]:
    """Index to 100 at `start`, and the total return to `end`.

    The span is the caller's interval or run, never a viewport: an index rebased
    at the edge of the requested window changes meaning when the reader drags
    the range control.
    """
    window = [p for p in points if start <= p.on <= end]
    if not window or window[0].value == 0:
        return (), None
    first = window[0].value
    index = tuple(IndexPoint(on=p.on, index=p.value / first * HUNDRED) for p in window)
    return index, window[-1].value / first - ONE


#: Worst news first, over the span vocabulary only. A private twin of
#: `quotes.worst_coverage` rather than a call into it: that function ranks
#: STALENESS and hands back a `Coverage`, so routing spans through it would
#: return a type that can say `manual` and reintroduce, at the boundary, exactly
#: the confusion `SpanCoverage` exists to remove.
_SPAN_SEVERITY: dict[SpanCoverage, int] = {SPAN_FULL: 0, SPAN_PARTIAL: 1, SPAN_MISSING: 2}


def worst_span(values: Sequence[SpanCoverage]) -> SpanCoverage:
    """The most severe span among `values`.

    Empty is `full`, for the reason `quotes.worst_coverage` spells out: an empty
    set of observations carries no bad news. Both callers return early with
    `missing` when there is nothing to compare, which is each of them saying at
    its own call site that ITS kind of empty is the bad kind.
    """
    return max(values, key=lambda value: _SPAN_SEVERITY[value], default=SPAN_FULL)


def span_coverage(
    points: Sequence[BasePoint], start: date, end: date, measured: Decimal | None
) -> SpanCoverage:
    """How much of `start`..`end` the series actually spans.

    `STALE_DAYS` is borrowed from `quotes.py` deliberately: it is already this
    codebase's answer to "how long a gap is a weekend and a holiday, and how
    long is a fault". A benchmark whose first point lands a month into a
    quarter-long span has not covered it, and differencing two returns anyway
    is precisely the plausible wrong number this arithmetic exists to avoid.
    """
    if measured is None:
        return SPAN_MISSING
    window = [p for p in points if start <= p.on <= end]
    if not window:
        return SPAN_MISSING
    # Compound names, so no local variable here can collide with the leak
    # scanner's token list: it matches whole words case-insensitively, and an
    # underscore is a word character.
    opening_gap = (window[0].on - start).days
    closing_gap = (end - window[-1].on).days
    return (
        SPAN_PARTIAL
        if opening_gap > STALE_DAYS or closing_gap > STALE_DAYS
        else SPAN_FULL
    )


def link(returns: Sequence[Decimal | None]) -> Decimal | None:
    """`(1 + a) * (1 + b) - 1`.

    `None` the moment any one link is `None`: a chain missing a link is not a
    shorter chain, and silently dropping the unmeasurable stretch would report
    the return of a period nobody had.
    """
    total = ONE
    for value in returns:
        if value is None:
            return None
        total *= ONE + value
    return total - ONE


def side_reason(
    what: str,
    points: Sequence[BasePoint],
    start: date,
    end: date,
    coverage: SpanCoverage,
) -> str | None:
    """Why one side could not be measured over the whole of `start`..`end`."""
    span = f"{start} to {end}"
    if coverage == SPAN_MISSING:
        return f"no {what} data from {span}"
    if coverage == SPAN_PARTIAL:
        window = [p for p in points if start <= p.on <= end]
        return f"{what} covers {window[0].on} to {window[-1].on}, not the whole of {span}"
    return None
```

- [ ] **Step 4: Point `instrument_return.py` at it**

In `backend/app/analytics/instrument_return.py`:

1. Delete, in this order: the `SPAN_*` constants and their comment block, `ONE`, `HUNDRED`, `IndexPoint`, `_BasePoint`, `_in_base`, `_rebase`, `_SPAN_SEVERITY` with its comment, `_worst_span`, `_span_coverage`, `_link`, `_side_reason`. Keep `BASIS`, `HoldingInterval`, `IntervalExcess`, `Comparison`, `_empty`, `_reason`, `comparison`.
2. Replace the import block with:

```python
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol

from sqlalchemy import Engine
from sqlmodel import Session

from app.analytics.benchmark_return import benchmark_total_return_series
from app.analytics.indexing import (
    SPAN_MISSING,
    SPAN_PARTIAL,
    BasePoint,
    IndexPoint,
    in_base_points,
    link,
    rebase,
    side_reason,
    span_coverage,
    worst_span,
)
from app.analytics.quotes import base_currency, rate_history
from app.analytics.total_return import total_return_series
from app.models.types import SpanCoverage
```

3. Replace `_reason`'s signature types and body:

```python
def _reason(
    isin: str,
    benchmark_key: str,
    interval: HoldingInterval,
    instrument: Sequence[BasePoint],
    instrument_coverage: SpanCoverage,
    benchmark: Sequence[BasePoint],
    benchmark_coverage: SpanCoverage,
) -> str | None:
    """Why the excess is `None`. `None` when it is not.

    Both sides, symmetrically. An earlier version asked the span question of
    the benchmark only, which let a 4-day instrument return be differenced
    against an 86-day benchmark return and published with no reason at all --
    the exact subtraction `span_coverage` exists to prevent, escaping through
    the side nobody checked.
    """
    parts = [
        text
        for text in (
            side_reason(
                isin, instrument, interval.start, interval.end, instrument_coverage
            ),
            side_reason(
                f"benchmark {benchmark_key!r}",
                benchmark,
                interval.start,
                interval.end,
                benchmark_coverage,
            ),
        )
        if text is not None
    ]
    return "; ".join(parts) if parts else None
```

4. Inside `comparison()`, rename the calls: `_in_base` → `in_base_points`, `_rebase` → `rebase`, `_span_coverage` → `span_coverage`, `_link` → `link`, `_worst_span` → `worst_span`. Nothing else in that function changes.
5. Append this paragraph to the module docstring, after the "What this module may not touch" paragraph:

```text
**What moved out.** The arithmetic behind steps 2, 3 and 5, and the span
judgement, live in `indexing.py` since M6a, so the portfolio comparison applies
the same rules without reaching `total_return.py` through this file. The order
above is still this module's to state and to follow.
```

- [ ] **Step 5: Run the tests to verify they pass, and that M3 did not move**

```bash
cd backend && ./.venv/Scripts/python.exe -m pytest tests/unit/test_indexing.py tests/integration/test_no_double_count.py tests/integration/test_instrument_return.py tests/integration/test_instrument_api.py -q
```

Expected: PASS. Every `test_instrument_return.py` and `test_instrument_api.py` figure is unchanged — this task changes no number.

- [ ] **Step 6: Run the verification gate, then commit**

```bash
git add backend/app/analytics/indexing.py backend/app/analytics/instrument_return.py \
  backend/tests/unit/test_indexing.py backend/tests/integration/test_no_double_count.py
git commit -m "$(cat <<'EOF'
refactor(analytics): move the index arithmetic out of instrument_return (PT-25)

Claude-Session: https://claude.ai/code/session_013U6MLH7npaNQwK1ZS8UFbS
EOF
)"
```

---

### Task 2: `analytics/flows.py` — external flows per day

**Files:**
- Create: `backend/app/analytics/flows.py`
- Create: `backend/tests/integration/synthetic_ledger.py` (a helper, not a test module)
- Test: `backend/tests/unit/test_flows.py`
- Test: `backend/tests/integration/test_flows.py`

**Interfaces:**
- Consumes: `Transaction` from `app.models.ledger`; `rebuild` from `app.analytics.rebuild`.
- Produces:
  - `EXTERNAL_FLOW_TYPES: frozenset[str]` — exactly `{"DEPOSIT", "WITHDRAWAL"}`
  - `LedgerFlowRow` Protocol: `txn_type: str`, `trade_date: date`, `net_base: Decimal`
  - `net_flows_by_day(rows: Iterable[LedgerFlowRow]) -> dict[date, Decimal]` — pure, keys in date order
  - `external_flows(engine: Engine) -> dict[date, Decimal]`
  - `tests.integration.synthetic_ledger.Ledger` with chained methods `deposit(on, amount)`, `withdraw(on, amount)`, `row(on, txn_type, amount)`, `buy(on, quantity, price, *, fee="0.00", isin=ISIN)`, `close(on, price, *, isin=ISIN)`, property `engine`, and `rebuilt(through: date) -> Engine`; module constants `ISIN = "XX0000000061"`, `OTHER = "XX0000000062"`

- [ ] **Step 1: Write the failing unit tests**

Create `backend/tests/unit/test_flows.py`:

```python
"""Which ledger rows cross the account boundary, and how they net. No database.

M6a-6: `DEPOSIT` and `WITHDRAWAL` only. Every other type that moves cash -- a
trade, a dividend, its withholding, a fee, interest, a conversion -- is inside
the portfolio, and a time-weighted return exists to measure it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal as D

from app.analytics.flows import EXTERNAL_FLOW_TYPES, net_flows_by_day

MON = date(2025, 3, 3)
TUE = date(2025, 3, 4)
WED = date(2025, 3, 5)


@dataclass
class Row:
    txn_type: str
    trade_date: date
    net_base: D


def test_a_deposit_is_a_positive_flow_and_a_withdrawal_a_negative_one() -> None:
    rows = [Row("DEPOSIT", MON, D("1000.00")), Row("WITHDRAWAL", WED, D("-250.00"))]
    assert net_flows_by_day(rows) == {MON: D("1000.00"), WED: D("-250.00")}


def test_nothing_else_that_moves_cash_is_a_flow() -> None:
    """Every other type the ledger holds. Each changes `V`; none crosses the boundary."""
    rows = [
        Row(txn_type, MON, D("12.34"))
        for txn_type in (
            "BUY", "SELL", "DIVIDEND", "DIVIDEND_TAX", "FEE", "TAX", "INTEREST",
            "SECURITIES_LENDING", "FX_CONVERT", "CORPORATE_ACTION",
        )
    ]
    assert net_flows_by_day(rows) == {}


def test_the_boundary_is_exactly_two_types() -> None:
    assert EXTERNAL_FLOW_TYPES == {"DEPOSIT", "WITHDRAWAL"}


def test_flows_on_one_day_net_and_the_day_stays_present() -> None:
    """Two flows that cancel are still two flows that happened. Neither changes a
    return; dropping the key would make the mapping claim nothing happened."""
    rows = [Row("WITHDRAWAL", TUE, D("-800.00")), Row("DEPOSIT", TUE, D("800.00"))]
    assert net_flows_by_day(rows) == {TUE: D("0.00")}


def test_days_come_out_in_date_order() -> None:
    rows = [Row("DEPOSIT", WED, D("1.00")), Row("DEPOSIT", MON, D("1.00"))]
    assert list(net_flows_by_day(rows)) == [MON, WED]
```

- [ ] **Step 2: Run them to verify they fail**

```bash
cd backend && ./.venv/Scripts/python.exe -m pytest tests/unit/test_flows.py -q
```

Expected: collection error — `ModuleNotFoundError: No module named 'app.analytics.flows'`.

- [ ] **Step 3: Write `backend/app/analytics/flows.py`**

```python
"""External cash flows per day. The one quantity M6a adds to what M2 built.

M6a section 3.2. A time-weighted return removes the money the owner moved across
the account boundary, because a deposit is not a gain. Everything else that
changes the cash balance -- a trade's settlement, a dividend, its withholding, a
fee, interest -- is inside the portfolio and is part of what the return measures.

**Two types, and only two (M6a-6).** `DEPOSIT` and `WITHDRAWAL` are the rows that
cross the account boundary. The Sec 3.3 cash sweeps are absent because they are
not in the ledger at all: `ingest/degiro/account_csv.py` drops them at parse
time, and `tests/integration/test_portfolio_return.py` proves the return series
is identical with and without them. The flatex transfers ARE present -- that
parser types them by sign, because they move money to and from the owner's own
bank, which is outside the pot `cash_daily` sums.

The type names are restated as plain strings rather than imported from the
parser, for the reason `domain/positions.py` gives about `FX_CONVERT`: a ledger
row is judged by the shape it already has, and analytics takes no dependency on
a broker-specific parser module.

`net_base` is the amount, signed as it hit the cash account. It is the column
`cash_daily` is a running sum of, which is what makes subtracting a flow from a
change in value exact rather than approximately right.

This module sums per CALENDAR day. Moving a flow onto the valuation grid -- a
Saturday deposit belongs to Monday's link -- is `portfolio_return.py`'s job,
because only a caller holding the grid knows which close a date falls before.
It names no close column and touches no price table.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from decimal import Decimal
from typing import Protocol

from sqlalchemy import Engine
from sqlmodel import Session, col, select

from app.models.ledger import Transaction

#: The rows that cross the account boundary. See the module docstring.
EXTERNAL_FLOW_TYPES: frozenset[str] = frozenset({"DEPOSIT", "WITHDRAWAL"})

_ZERO = Decimal("0.00")


class LedgerFlowRow(Protocol):
    """The three fields a flow needs off a `Transaction`."""

    txn_type: str
    trade_date: date
    net_base: Decimal


def net_flows_by_day(rows: Iterable[LedgerFlowRow]) -> dict[date, Decimal]:
    """Net external flow per calendar day, in date order. Pure.

    A day whose deposit and withdrawal cancel is present with zero rather than
    absent: the ledger recorded flows that day, and dropping the key would make
    "nothing happened" and "two things cancelled" look the same. Neither changes
    a return.
    """
    totals: dict[date, Decimal] = {}
    for row in rows:
        if row.txn_type not in EXTERNAL_FLOW_TYPES:
            continue
        totals[row.trade_date] = totals.get(row.trade_date, _ZERO) + row.net_base
    return dict(sorted(totals.items()))


def external_flows(engine: Engine) -> dict[date, Decimal]:
    """Every external flow the ledger holds, netted per calendar day."""
    with Session(engine) as session:
        rows = session.exec(
            select(Transaction).where(
                col(Transaction.txn_type).in_(sorted(EXTERNAL_FLOW_TYPES))
            )
        ).all()
    return net_flows_by_day(rows)
```

- [ ] **Step 4: Run the unit tests to verify they pass**

```bash
cd backend && ./.venv/Scripts/python.exe -m pytest tests/unit/test_flows.py -q
```

Expected: 5 passed.

- [ ] **Step 5: Write the synthetic ledger helper**

Create `backend/tests/integration/synthetic_ledger.py`:

```python
"""A hand-written ledger for the M6a tests: deposits, trades and closes.

Not a test module. Built on the real tables and driven through the real
`rebuild()`, because M6a's claims about trades -- that a buy at the close leaves
the day's return untouched -- are claims about how `cash_daily` and
`position_daily` are DERIVED from a fill's `net_base`. Seeding those two tables
by hand, as `test_valuation.py` does, would test the arithmetic against the
author's belief about the derivation rather than against the derivation.

Same shape as the `Seed` builders elsewhere in this directory: each call commits
and returns the builder. Every ISIN and amount is invented.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import Engine
from sqlmodel import Session

from app.analytics.rebuild import rebuild
from app.db import create_engine_and_tables
from app.models.ledger import Account, ImportBatch, Transaction
from app.models.market import PriceDaily

D = Decimal
ZERO = D("0.00")
FETCHED = datetime(2026, 9, 6, 12, 0, 0)

ISIN = "XX0000000061"  # invented; no holding of anyone's
OTHER = "XX0000000062"  # invented; no holding of anyone's


class Ledger:
    def __init__(self) -> None:
        self._engine = create_engine_and_tables("sqlite://")
        self._account_id = uuid4()
        self._batch_id = uuid4()
        with Session(self._engine) as session:
            session.add(
                Account(
                    id=self._account_id, broker="degiro", name="synthetic",
                    base_currency="EUR",
                )
            )
            session.add(
                ImportBatch(
                    id=self._batch_id, source="degiro", filename="synthetic",
                    file_sha256="0" * 64, parser_version="1", imported_at=FETCHED,
                    row_count=0, inserted_count=0,
                )
            )
            session.commit()

    @property
    def engine(self) -> Engine:
        return self._engine

    def _add(self, **fields: object) -> "Ledger":
        with Session(self._engine) as session:
            session.add(
                Transaction(
                    id=uuid4(),
                    account_id=self._account_id,
                    import_batch_id=self._batch_id,
                    source="degiro",
                    source_ref=f"synthetic-{uuid4()}",
                    raw_json="{}",
                    **fields,
                )
            )
            session.commit()
        return self

    def row(self, on: date, txn_type: str, amount: str) -> "Ledger":
        """A cash-only row: a dividend, a fee, a flow. `amount` is signed as it
        hit the cash account."""
        return self._add(
            txn_type=txn_type, trade_date=on, fee_base=ZERO, tax_base=ZERO,
            net_base=D(amount),
        )

    def deposit(self, on: date, amount: str) -> "Ledger":
        return self.row(on, "DEPOSIT", amount)

    def withdraw(self, on: date, amount: str) -> "Ledger":
        """`amount` is written positive and booked negative, as the broker does."""
        return self.row(on, "WITHDRAWAL", str(-D(amount)))

    def buy(
        self, on: date, quantity: str, price: str, *, fee: str = "0.00", isin: str = ISIN
    ) -> "Ledger":
        """One fill. `value_base` is what the shares cost and `fee_base` a debit,
        both negative; `net_base` is their sum -- what left the cash account."""
        cost = -(D(quantity) * D(price))
        charge = -D(fee)
        return self._add(
            txn_type="BUY", trade_date=on, trade_time="12:00", isin=isin,
            quantity=D(quantity), price_local=D(price), currency_local="EUR",
            value_base=cost, fee_base=charge, tax_base=ZERO, autofx_fee_base=ZERO,
            net_base=cost + charge, order_ref=f"order-{uuid4()}",
        )

    def close(self, on: date, price: str, *, isin: str = ISIN) -> "Ledger":
        """One unadjusted close. The adjusted close is deliberately different, so
        a reader taking the wrong column produces a wrong number."""
        with Session(self._engine) as session:
            session.add(
                PriceDaily(
                    id=uuid4(), isin=isin, price_date=on, close_unadjusted=D(price),
                    close_adjusted=D(price) / 2, currency="EUR", source="yahoo",
                    fetched_at=FETCHED,
                )
            )
            session.commit()
        return self

    def rebuilt(self, through: date) -> Engine:
        """Derive `position_daily` and `cash_daily` the way the app does."""
        rebuild(self._engine, "FIFO", through=through)
        return self._engine
```

- [ ] **Step 6: Write the failing integration test**

Create `backend/tests/integration/test_flows.py`:

```python
"""`external_flows` against a real table: which rows it reads, and nothing else."""

from __future__ import annotations

from datetime import date
from decimal import Decimal as D

from app.analytics.flows import external_flows
from app.db import create_engine_and_tables
from tests.integration.synthetic_ledger import Ledger

MON = date(2025, 3, 3)
TUE = date(2025, 3, 4)
WED = date(2025, 3, 5)
FRI = date(2025, 3, 7)


def test_reads_only_the_rows_that_cross_the_account_boundary() -> None:
    engine = (
        Ledger()
        .deposit(MON, "1000.00")
        .buy(TUE, "10", "20.00", fee="2.00")
        .row(WED, "DIVIDEND", "5.00")
        .row(WED, "DIVIDEND_TAX", "-0.75")
        .withdraw(FRI, "100.00")
        .engine
    )
    assert external_flows(engine) == {MON: D("1000.00"), FRI: D("-100.00")}


def test_an_empty_ledger_has_no_flows() -> None:
    assert external_flows(create_engine_and_tables("sqlite://")) == {}
```

- [ ] **Step 7: Run it to verify it passes**

```bash
cd backend && ./.venv/Scripts/python.exe -m pytest tests/integration/test_flows.py tests/unit/test_flows.py -q
```

Expected: 7 passed. (The module already exists from Step 3, so this is a confirmation run; if it fails, the fault is in the helper — fix the helper, not the assertion.)

- [ ] **Step 8: Run the verification gate, then commit**

```bash
git add backend/app/analytics/flows.py backend/tests/unit/test_flows.py \
  backend/tests/integration/test_flows.py backend/tests/integration/synthetic_ledger.py
git commit -m "$(cat <<'EOF'
feat(analytics): external flows per day, deposits and withdrawals only (PT-25)

Claude-Session: https://claude.ai/code/session_013U6MLH7npaNQwK1ZS8UFbS
EOF
)"
```

---

### Task 3: `analytics/portfolio_return.py` — links, runs, gaps, the window figure

**Files:**
- Create: `backend/app/analytics/portfolio_return.py`
- Test: `backend/tests/unit/test_portfolio_return.py`
- Test: `backend/tests/integration/test_portfolio_return.py`
- Test: `backend/tests/integration/test_no_double_count.py` (append one test)

**Interfaces:**
- Consumes: `ValuationPoint` from `app.analytics.valuation`; `HUNDRED`, `ONE`, `IndexPoint` from `app.analytics.indexing`; `MISSING`, `worst_coverage` from `app.analytics.quotes`; Task 2's `external_flows` and `Ledger`.
- Produces:
  - `BASIS = "time_weighted"`, `LANE = "unadjusted_close_plus_cash"`, `DIVIDENDS = "held_as_cash_net_of_withholding"`
  - `ReturnLink(on: date, since: date, flow_base: Decimal, daily_return: Decimal | None, coverage: Coverage, reason: str | None)`
  - `ReturnRun(start: date, end: date, links: tuple[ReturnLink, ...], linked_return: Decimal, coverage: Coverage, index: tuple[IndexPoint, ...])`
  - `PortfolioReturn(basis: str, lane: str, dividends: str, start: date | None, end: date | None, links: tuple[ReturnLink, ...], runs: tuple[ReturnRun, ...], linked_return: Decimal | None, gaps: int, coverage: Coverage, reason: str | None)`
  - `portfolio_returns(points: Sequence[ValuationPoint], flows: Mapping[date, Decimal]) -> PortfolioReturn`

- [ ] **Step 1: Write the load-bearing test first, alone, and watch it fail**

Create `backend/tests/unit/test_portfolio_return.py` with the header, the helper and **only** `TestFlows.test_a_deposit_into_a_flat_market_returns_zero`:

```python
"""The portfolio's time-weighted return, over hand-built valuation days.

M6a section 8. No database: `portfolio_returns` is pure over a sequence of
`ValuationPoint`s and a flow mapping, so every figure below is computed by hand
and asserted exactly. `tests/integration/test_portfolio_return.py` drives the
same function through `rebuild()`, which is where the claims about trades live.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal as D

from app.analytics.portfolio_return import BASIS, DIVIDENDS, LANE, portfolio_returns
from app.analytics.quotes import FULL, MANUAL, MISSING, PARTIAL
from app.analytics.valuation import ValuationPoint
from app.models.types import Coverage

MON = date(2025, 3, 3)
TUE = date(2025, 3, 4)
WED = date(2025, 3, 5)
THU = date(2025, 3, 6)
FRI = date(2025, 3, 7)
SAT = date(2025, 3, 8)
NEXT_MON = date(2025, 3, 10)


def day(on: date, value: str | None, coverage: Coverage = FULL) -> ValuationPoint:
    """One close. Only `value_base` and `coverage` are read; the rest is filled
    so the point is a real `ValuationPoint` rather than a lookalike."""
    if value is None:
        return ValuationPoint(
            on=on, holdings_base=None, cash_base=D("0.00"), value_base=None,
            coverage=MISSING, covered_pct=None,
        )
    return ValuationPoint(
        on=on, holdings_base=D("0.00"), cash_base=D(value), value_base=D(value),
        coverage=coverage, covered_pct=D("1"),
    )


class TestFlows:
    def test_a_deposit_into_a_flat_market_returns_zero(self) -> None:
        """The load-bearing test (M6a section 8), written and failed first.
        Prices flat, a deposit of any size: zero that day and every day after.
        It fails for a sign error, for the flow left in the numerator, for the
        flow added to the denominator, and for a flow dated to the wrong side
        of the close."""
        points = [
            day(MON, "1000.00"), day(TUE, "1000.00"),
            day(WED, "6000.00"), day(THU, "6000.00"),
        ]
        result = portfolio_returns(points, {WED: D("5000.00")})
        assert [step.daily_return for step in result.links] == [D("0"), D("0"), D("0")]
        assert result.linked_return == D("0")
```

```bash
cd backend && ./.venv/Scripts/python.exe -m pytest tests/unit/test_portfolio_return.py -q
```

Expected: collection error — `ModuleNotFoundError: No module named 'app.analytics.portfolio_return'`.

- [ ] **Step 2: Add the rest of the unit tests**

Append to `backend/tests/unit/test_portfolio_return.py` — first the remaining `TestFlows` methods (indented inside that class), then the new classes:

```python
    def test_a_withdrawal_from_a_flat_market_returns_zero(self) -> None:
        points = [day(MON, "1000.00"), day(TUE, "600.00")]
        result = portfolio_returns(points, {TUE: D("-400.00")})
        assert result.links[0].daily_return == D("0")

    def test_a_deposit_earns_nothing_on_the_day_it_arrives(self) -> None:
        """M6a-5: flows are effective at the close. 1000 rises 10% on the day 500
        arrives: the return is 10% of what was there BEFORE, not 100/1500."""
        points = [day(MON, "1000.00"), day(TUE, "1600.00")]
        result = portfolio_returns(points, {TUE: D("500.00")})
        assert result.links[0].daily_return == D("0.1")

    def test_a_weekend_deposit_belongs_to_the_next_weekday(self) -> None:
        """P-1. `cash_daily` applies a Saturday row to Monday's balance, so
        Monday's close already holds the deposit. Looked up by exact date, it
        would be missed and read as Monday's gain."""
        points = [day(FRI, "1000.00"), day(NEXT_MON, "1500.00")]
        result = portfolio_returns(points, {SAT: D("500.00")})
        assert result.links[0].flow_base == D("500.00")
        assert result.links[0].daily_return == D("0")

    def test_a_flow_on_or_before_the_first_close_is_in_no_link(self) -> None:
        """It is inside `V(start)` already; subtracting it again counts it twice."""
        points = [day(MON, "1000.00"), day(TUE, "1000.00")]
        result = portfolio_returns(
            points, {date(2025, 2, 28): D("50.00"), MON: D("1000.00")}
        )
        assert result.links[0].flow_base == D("0.00")
        assert result.links[0].daily_return == D("0")


class TestLinking:
    def test_with_no_flows_the_linked_figure_is_the_plain_ratio(self) -> None:
        """M6a section 8. Chain-linking earns its complexity only where flows
        exist; this pins that it costs nothing where they do not."""
        points = [
            day(MON, "1000.00"), day(TUE, "1250.00"),
            day(WED, "1000.00"), day(THU, "1500.00"),
        ]
        result = portfolio_returns(points, {})
        assert result.linked_return == D("1500.00") / D("1000.00") - 1

    def test_doubling_every_amount_leaves_every_daily_return_unchanged(self) -> None:
        """Scale invariance: what distinguishes TWR from MWR, and why parent doc
        Sec 7.4 compares only TWR against a benchmark."""
        days = (MON, TUE, WED, THU)
        values = ("1000.00", "1100.00", "1600.00", "1540.00")
        flows = {WED: D("500.00")}
        once = portfolio_returns([day(d, v) for d, v in zip(days, values, strict=True)], flows)
        twice = portfolio_returns(
            [day(d, str(D(v) * 2)) for d, v in zip(days, values, strict=True)],
            {on: amount * 2 for on, amount in flows.items()},
        )
        assert [s.daily_return for s in twice.links] == [s.daily_return for s in once.links]
        assert twice.linked_return == once.linked_return

    def test_the_index_starts_at_100_and_follows_the_links(self) -> None:
        points = [day(MON, "1000.00"), day(TUE, "1100.00"), day(WED, "1210.00")]
        (run,) = portfolio_returns(points, {}).runs
        assert [(p.on, p.index) for p in run.index] == [
            (MON, D("100")), (TUE, D("110")), (WED, D("121")),
        ]

    def test_the_result_is_labelled(self) -> None:
        """Parent doc Sec 7.4: no unlabelled return. The labels ride on the object."""
        result = portfolio_returns([day(MON, "1000.00"), day(TUE, "1000.00")], {})
        assert (result.basis, result.lane, result.dividends) == (BASIS, LANE, DIVIDENDS)
        assert BASIS == "time_weighted"


class TestGaps:
    def _gapped(self) -> list[ValuationPoint]:
        return [
            day(MON, "1000.00"), day(TUE, "1100.00"), day(WED, None),
            day(THU, "1200.00"), day(FRI, "1260.00"),
        ]

    def test_one_unpriceable_day_breaks_two_links_and_withholds_the_figure(self) -> None:
        """M6a section 8, asserted as an absence and never as a zero."""
        result = portfolio_returns(self._gapped(), {})
        assert [s.daily_return for s in result.links] == [D("0.1"), None, None, D("0.05")]
        assert len(result.runs) == 2
        assert result.linked_return is None
        assert result.gaps == 1
        assert "no single figure spans a gap" in (result.reason or "")

    def test_each_run_keeps_its_own_figure(self) -> None:
        first, second = portfolio_returns(self._gapped(), {}).runs
        assert (first.start, first.end, first.linked_return) == (MON, TUE, D("0.1"))
        assert (second.start, second.end, second.linked_return) == (THU, FRI, D("0.05"))

    def test_a_broken_link_names_the_day_with_no_valuation(self) -> None:
        result = portfolio_returns(self._gapped(), {})
        assert result.links[1].reason == "no valuation on 2025-03-05"
        assert result.links[2].reason == "no valuation on 2025-03-05"

    def test_an_unpriceable_first_day_withholds_the_window_figure_too(self) -> None:
        """P-3. Reporting the later run as the window's return would be a return
        over a span nobody asked for."""
        points = [day(MON, None), day(TUE, "1000.00"), day(WED, "1100.00")]
        result = portfolio_returns(points, {})
        assert len(result.runs) == 1
        assert result.linked_return is None
        assert result.gaps == 1

    def test_a_gap_is_counted_once_however_long_it_lasts(self) -> None:
        points = [day(MON, "1000.00"), day(TUE, None), day(WED, None), day(THU, "1000.00")]
        result = portfolio_returns(points, {})
        assert result.gaps == 1
        assert result.runs == ()
        assert result.reason == "no link in this window could be measured"


class TestCapital:
    def test_days_before_the_first_capital_are_not_part_of_the_series(self) -> None:
        """M6a section 3.4: before the first deposit there is no return to
        measure, which is a different statement from a return of zero."""
        points = [
            day(MON, "0.00"), day(TUE, "0.00"),
            day(WED, "1000.00"), day(THU, "1100.00"),
        ]
        result = portfolio_returns(points, {WED: D("1000.00")})
        assert result.start == WED
        assert [s.on for s in result.links] == [THU]
        assert result.linked_return == D("0.1")

    def test_an_emptied_account_breaks_the_run_with_its_own_reason(self) -> None:
        """P-2. A figure chained across a stretch with no capital in it would be
        as much an invention as one chained across a stretch with no prices."""
        points = [
            day(MON, "1000.00"), day(TUE, "0.00"),
            day(WED, "500.00"), day(THU, "550.00"),
        ]
        result = portfolio_returns(points, {TUE: D("-1000.00"), WED: D("500.00")})
        assert [s.daily_return for s in result.links] == [D("0"), None, D("0.1")]
        assert result.links[1].reason == "no capital at the close of 2025-03-04"
        assert result.linked_return is None
        assert result.gaps == 1

    def test_one_close_is_nothing_to_measure_and_says_missing(self) -> None:
        """No observations is not a well-covered set of them."""
        result = portfolio_returns([day(MON, "1000.00")], {})
        assert result.links == ()
        assert result.linked_return is None
        assert result.coverage == MISSING
        assert result.reason is not None

    def test_an_empty_window_measures_nothing(self) -> None:
        result = portfolio_returns([], {})
        assert result.start is None
        assert result.coverage == MISSING


class TestCoverage:
    def test_a_link_carries_the_worse_of_its_two_closes(self) -> None:
        points = [day(MON, "1000.00"), day(TUE, "1000.00", PARTIAL), day(WED, "1000.00")]
        assert [s.coverage for s in portfolio_returns(points, {}).links] == [PARTIAL, PARTIAL]

    def test_a_run_reports_the_worst_close_it_was_built_from(self) -> None:
        """A figure built from stale or hand-typed prices says so (M6a section 4)."""
        points = [day(MON, "1000.00", MANUAL), day(TUE, "1000.00"), day(WED, "1000.00")]
        (run,) = portfolio_returns(points, {}).runs
        assert run.coverage == MANUAL

    def test_the_window_reports_its_worst_day(self) -> None:
        points = [day(MON, "1000.00"), day(TUE, None), day(WED, "1000.00")]
        assert portfolio_returns(points, {}).coverage == MISSING
```

- [ ] **Step 3: Write `backend/app/analytics/portfolio_return.py`**

```python
"""The portfolio's time-weighted return: differenced, chain-linked, and honest
about where it cannot be.

M6a sections 3 and 4. For each valuation day `d` with a predecessor:

             V(d) - V(d-1) - F(d)
    r(d)  =  --------------------          R  =  prod(1 + r(d)) - 1
                   V(d-1)

`V` is `ValuationPoint.value_base` -- holdings at the UNADJUSTED close plus cash
(M6a-2, M6a-3). This module names no close column; it inherits the lane from
`valuation.py`, and it must never import `total_return.py`: a cash balance
already holds every dividend, and an adjusted close holds them again, which is
parent doc Sec 7.5's double count.

`F` is the external flow (`flows.py`). Nothing else enters. Dividends and fees
are inside `V` through cash, and a trade moves value between cash and holdings
without changing `V`, so it needs no timing rule (M6a section 3.3). What survives
a trade is its fee and the gap between its price and the close -- exactly what
the owner gained or lost that day.

**Three rules the design states, and two it leaves to this file.**

1. Flows are effective at the close (M6a-5): a deposit on `d` is in `V(d)` and
   subtracted from the numerator, so it earns nothing on the day it lands.
2. `r(d)` is `None` when either close has no valuation. A return is a
   difference, so one unusable day breaks TWO links (M6a section 4).
3. No single figure spans a gap (M6a-7). The series is reported as contiguous
   runs, each with its own linked return; the window figure exists only when
   every link in the window has a return.
4. **A flow belongs to the link whose close it falls before.** The grid is
   weekdays and `cash_daily` applies every row dated on or before a day, so a
   Saturday deposit is in Monday's `V`. `F(d)` is every flow dated after the
   previous close and on or before `d`. By exact date, the deposit would be
   missed and reported as Monday's gain.
5. **A link needs capital.** Before the first close with `V > 0` there is nothing
   to measure (M6a section 3.4), so leading closes at or below zero are trimmed.
   The same fact inside the window -- an account emptied and refilled -- leaves
   `r(d)` no denominator. That link is `None` with its own reason and breaks the
   run exactly as a gap does: a figure chained across a stretch with no capital
   in it would be as much an invention as one chained across missing prices.

M6b consumes both outputs: the flows for MWR/XIRR, and the linked aggregate as
the figure contribution to return must sum to (M6a section 9).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from itertools import pairwise

from app.analytics.indexing import HUNDRED, ONE, IndexPoint
from app.analytics.quotes import MISSING, worst_coverage
from app.analytics.valuation import ValuationPoint
from app.models.types import Coverage

#: Every figure on a `PortfolioReturn` is time-weighted. Stated on the object
#: rather than assumed by the reader -- parent doc Sec 7.4: no endpoint returns
#: an unlabelled return, and TWR is the only figure compared against a benchmark.
BASIS = "time_weighted"
#: Which closes value the holdings, and that cash is in the figure.
LANE = "unadjusted_close_plus_cash"
#: How dividends reach this figure: as cash on the pay date, after withholding,
#: idle until the owner does something with them (M6a section 7).
DIVIDENDS = "held_as_cash_net_of_withholding"

_ZERO = Decimal("0.00")
_NOTHING_TO_MEASURE = (
    "fewer than two valuation days with capital in them; there is no return to measure"
)


@dataclass(frozen=True, slots=True)
class ReturnLink:
    """One day's return, measured from the previous valuation day's close."""

    on: date
    #: The close this link is measured FROM.
    since: date
    #: Net external flow dated in `(since, on]`. See rule 4.
    flow_base: Decimal
    #: `None`, never 0, when either close has no valuation or `since` had no capital.
    daily_return: Decimal | None
    #: The worse of the two closes' coverage.
    coverage: Coverage
    #: Why `daily_return` is `None`. Set exactly when it is.
    reason: str | None


@dataclass(frozen=True, slots=True)
class ReturnRun:
    """A contiguous stretch of measurable links, and the one figure it earns."""

    #: The close the run is measured from -- the day BEFORE its first link.
    start: date
    end: date
    links: tuple[ReturnLink, ...]
    #: `prod(1 + r) - 1` over `links`. Never `None`: a run has no gap by construction.
    linked_return: Decimal
    #: The worst coverage over every close in the run, `start` included. A figure
    #: built from stale or manual prices says so (M6a section 4).
    coverage: Coverage
    #: 100 at `start`, then one point per link.
    index: tuple[IndexPoint, ...]


@dataclass(frozen=True, slots=True)
class PortfolioReturn:
    basis: str
    lane: str
    dividends: str
    #: The first and last close measured, after leading closes with no capital
    #: are trimmed. `None` when there is nothing to measure.
    start: date | None
    end: date | None
    #: Every link, broken ones included.
    links: tuple[ReturnLink, ...]
    runs: tuple[ReturnRun, ...]
    #: `None` unless every link in the window has a return (rule 3).
    linked_return: Decimal | None
    #: How many separate stretches of broken links the window holds.
    gaps: int
    #: The worst close in the window; `missing` when there is nothing to measure.
    coverage: Coverage
    #: Why `linked_return` is `None`. Set exactly when it is.
    reason: str | None


def portfolio_returns(
    points: Sequence[ValuationPoint], flows: Mapping[date, Decimal]
) -> PortfolioReturn:
    """Link every close in `points` and report the runs, the gaps and the window.

    `points` is a valuation window in date order, as `value_series` returns it.
    `flows` is `flows.external_flows`'s per-calendar-day mapping and may hold
    days outside the window; only flows between two closes are used.
    """
    measured = _from_first_capital(points)
    if len(measured) < 2:
        return _nothing_to_measure(measured)

    links = tuple(
        _link(before, after, _flow_between(flows, before.on, after.on))
        for before, after in pairwise(measured)
    )
    runs = _runs(measured, links)
    gaps = _gap_count(links)
    return PortfolioReturn(
        basis=BASIS,
        lane=LANE,
        dividends=DIVIDENDS,
        start=measured[0].on,
        end=measured[-1].on,
        links=links,
        runs=runs,
        # No gap means every link has a return, which means exactly one run.
        linked_return=runs[0].linked_return if gaps == 0 else None,
        gaps=gaps,
        coverage=worst_coverage([point.coverage for point in measured]),
        reason=None if gaps == 0 else _window_reason(gaps, len(runs)),
    )


def _from_first_capital(points: Sequence[ValuationPoint]) -> tuple[ValuationPoint, ...]:
    """Drop leading closes KNOWN to hold no capital (rule 5).

    A leading close with no valuation at all is a coverage gap, not an empty
    account, and trimming it would silently shorten the window the reader asked
    for -- so trimming stops there.
    """
    for position, point in enumerate(points):
        if point.value_base is None or point.value_base > 0:
            return tuple(points[position:])
    return ()


def _flow_between(flows: Mapping[date, Decimal], since: date, through: date) -> Decimal:
    """Every flow dated after one close and on or before the next (rule 4)."""
    return sum((amount for on, amount in flows.items() if since < on <= through), _ZERO)


def _link(before: ValuationPoint, after: ValuationPoint, flow: Decimal) -> ReturnLink:
    """One day's return, or the reason there is none."""
    coverage = worst_coverage([before.coverage, after.coverage])
    value_before, value_after = before.value_base, after.value_base
    if value_before is None or value_after is None:
        unvalued = " or ".join(
            point.on.isoformat() for point in (before, after) if point.value_base is None
        )
        return _broken(before, after, flow, coverage, f"no valuation on {unvalued}")
    if value_before <= 0:
        return _broken(
            before, after, flow, coverage, f"no capital at the close of {before.on}"
        )
    return ReturnLink(
        on=after.on,
        since=before.on,
        flow_base=flow,
        daily_return=(value_after - value_before - flow) / value_before,
        coverage=coverage,
        reason=None,
    )


def _broken(
    before: ValuationPoint,
    after: ValuationPoint,
    flow: Decimal,
    coverage: Coverage,
    reason: str,
) -> ReturnLink:
    return ReturnLink(
        on=after.on, since=before.on, flow_base=flow, daily_return=None,
        coverage=coverage, reason=reason,
    )


def _runs(
    points: Sequence[ValuationPoint], links: Sequence[ReturnLink]
) -> tuple[ReturnRun, ...]:
    """Split `links` into maximal stretches with no broken link in them (rule 3)."""
    coverage_on = {point.on: point.coverage for point in points}
    runs: list[ReturnRun] = []
    stretch: list[tuple[ReturnLink, Decimal]] = []
    for item in links:
        if item.daily_return is not None:
            stretch.append((item, item.daily_return))
            continue
        if stretch:
            runs.append(_run(stretch, coverage_on))
        stretch = []
    if stretch:
        runs.append(_run(stretch, coverage_on))
    return tuple(runs)


def _run(
    stretch: Sequence[tuple[ReturnLink, Decimal]], coverage_on: Mapping[date, Coverage]
) -> ReturnRun:
    """Chain one stretch, indexed at 100 on the close it is measured from.

    The growth factor is carried by hand rather than through `indexing.link`
    because the index needs every intermediate level, not only the last one.
    """
    start = stretch[0][0].since
    growth = ONE
    index = [IndexPoint(on=start, index=HUNDRED)]
    for item, daily in stretch:
        growth *= ONE + daily
        index.append(IndexPoint(on=item.on, index=HUNDRED * growth))
    closes = [start, *(item.on for item, _ in stretch)]
    return ReturnRun(
        start=start,
        end=stretch[-1][0].on,
        links=tuple(item for item, _ in stretch),
        linked_return=growth - ONE,
        coverage=worst_coverage([coverage_on[on] for on in closes]),
        index=tuple(index),
    )


def _gap_count(links: Sequence[ReturnLink]) -> int:
    """Separate stretches of broken links. One unpriceable day breaks two
    adjacent links and is one gap, not two."""
    gaps = 0
    previous_broken = False
    for item in links:
        broken = item.daily_return is None
        if broken and not previous_broken:
            gaps += 1
        previous_broken = broken
    return gaps


def _window_reason(gaps: int, runs: int) -> str:
    if runs == 0:
        return "no link in this window could be measured"
    stretches = "stretch" if gaps == 1 else "stretches"
    run_word = "run" if runs == 1 else "runs"
    return (
        f"{gaps} unmeasurable {stretches} split this window into {runs} {run_word}; "
        "no single figure spans a gap"
    )


def _nothing_to_measure(measured: Sequence[ValuationPoint]) -> PortfolioReturn:
    """Fewer than two closes with capital: no link, so no return. `missing`
    rather than `full` -- no observations is not a well-covered set of them."""
    return PortfolioReturn(
        basis=BASIS,
        lane=LANE,
        dividends=DIVIDENDS,
        start=measured[0].on if measured else None,
        end=measured[-1].on if measured else None,
        links=(),
        runs=(),
        linked_return=None,
        gaps=0,
        coverage=MISSING,
        reason=_NOTHING_TO_MEASURE,
    )
```

- [ ] **Step 4: Run the unit tests to verify they pass**

```bash
cd backend && ./.venv/Scripts/python.exe -m pytest tests/unit/test_portfolio_return.py -q
```

Expected: 21 passed.

- [ ] **Step 5: Write the integration tests — trades and sweeps through the real derivation**

Create `backend/tests/integration/test_portfolio_return.py`:

```python
"""The claims about trades and sweeps, driven through `rebuild()`.

`tests/unit/test_portfolio_return.py` proves the arithmetic over hand-built
closes. It cannot prove that `V` is continuous across a trade: that is a claim
about how `cash_daily` and `position_daily` are derived from a fill's
`net_base`, and the only honest test of it goes through the derivation.
"""

from __future__ import annotations

import csv
import shutil
from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pytest
from sqlalchemy import Engine

from app.analytics.flows import external_flows
from app.analytics.portfolio_return import PortfolioReturn, portfolio_returns
from app.analytics.rebuild import rebuild
from app.analytics.valuation import value_series
from app.db import create_engine_and_tables
from app.ingest.importer import ensure_default_account, import_degiro_export
from tests.integration.synthetic_ledger import Ledger

MON = date(2025, 3, 3)
TUE = date(2025, 3, 4)
WED = date(2025, 3, 5)

GOLDEN = Path(__file__).parents[1] / "golden"
RESOLVE_BOTH = """\
resolutions:
  - key: NL0000000003:2025-01-17:100.00
    treatment: corporate_action
  - key: US0000000002:2025-01-18:100.00
    treatment: corporate_action
"""
#: Account.csv descriptions that move money within the account and never across
#: its boundary (Sec 3.3). The parser drops every one of them.
INTERNAL_TRANSFERS = ("degiro cash sweep transfer", "overboeking", "reservation ideal")


def _returns(engine: Engine) -> PortfolioReturn:
    """What the performance route computes, from the ledger's first day."""
    return portfolio_returns(
        value_series(engine, start=date.min).points, external_flows(engine)
    )


def _wednesday(engine: Engine) -> D | None:
    return next(step.daily_return for step in _returns(engine).links if step.on == WED)


def _held_since_monday() -> Ledger:
    """1000.00 deposited and 10 shares bought at Monday's close of 20.00. Flat
    into Tuesday, up 10% on Wednesday: the holding alone earns 20.00 on the
    1000.00 there at Tuesday's close, which is 0.02."""
    return (
        Ledger()
        .deposit(MON, "1000.00")
        .buy(MON, "10", "20.00")
        .close(MON, "20.00")
        .close(TUE, "20.00")
        .close(WED, "22.00")
    )


class TestADepositThroughTheLedger:
    def test_a_deposit_into_a_flat_market_returns_zero(self) -> None:
        """The load-bearing test again, through `rebuild()`, so the deposit
        reaches `V` the way a real one does -- as a row in `cash_daily`."""
        engine = (
            Ledger()
            .deposit(MON, "1000.00")
            .buy(MON, "10", "20.00")
            .deposit(WED, "5000.00")
            .close(MON, "20.00")
            .close(TUE, "20.00")
            .close(WED, "20.00")
            .rebuilt(through=WED)
        )
        assert [step.daily_return for step in _returns(engine).links] == [D("0"), D("0")]


class TestATradeIsNotAFlow:
    def test_without_a_trade_the_holding_earns_two_percent(self) -> None:
        """The control. Without it the next test passes for an implementation
        that returns 0.02 on every Wednesday."""
        assert _wednesday(_held_since_monday().rebuilt(through=WED)) == D("0.02")

    @pytest.mark.parametrize("quantity", ["50", "500"])
    def test_a_buy_at_the_close_with_no_fee_leaves_the_day_unchanged(
        self, quantity: str
    ) -> None:
        """M6a-5: a buy moves value from cash to holdings, both inside `V`. At
        the close and free of charge nothing survives the netting, at any size
        -- including one that drives cash deeply negative. This is the assertion
        that fails the moment cash is dropped from the denominator."""
        engine = _held_since_monday().buy(WED, quantity, "22.00").rebuilt(through=WED)
        assert _wednesday(engine) == D("0.02")

    def test_a_buy_below_the_close_adds_the_gap_less_the_fee(self) -> None:
        """50 shares at 21.00 against a 22.00 close gain 50.00 on the day, less a
        2.00 fee: 48.00 on the 1000.00 there at Tuesday's close. By that and by
        nothing else. Stated separately because the test above passes for an
        implementation that ignores trades altogether."""
        engine = (
            _held_since_monday().buy(WED, "50", "21.00", fee="2.00").rebuilt(through=WED)
        )
        assert _wednesday(engine) == D("0.02") + D("48.00") / D("1000.00")


def _is_internal_transfer(line: str) -> bool:
    description = next(csv.reader([line]))[5]
    return description.strip().casefold().startswith(INTERNAL_TRANSFERS)


def _golden(tmp_path: Path, *, with_transfers: bool) -> Engine:
    export = tmp_path / ("with" if with_transfers else "without")
    export.mkdir()
    shutil.copy(GOLDEN / "degiro_transactions_golden.csv", export / "Transactions.csv")
    header, *body = (GOLDEN / "degiro_account_golden.csv").read_text(
        encoding="utf-8"
    ).splitlines(keepends=True)
    kept = body if with_transfers else [line for line in body if not _is_internal_transfer(line)]
    (export / "Account.csv").write_text(header + "".join(kept), encoding="utf-8")
    answers = tmp_path / "corporate_actions.yaml"
    answers.write_text(RESOLVE_BOTH, encoding="utf-8")

    engine = create_engine_and_tables("sqlite://")
    import_degiro_export(engine, export, ensure_default_account(engine), answers)
    rebuild(engine, "FIFO")
    return engine


class TestSweepsAreNotFlows:
    def test_the_fixture_really_carries_internal_transfers(self) -> None:
        """The vacuity check: stripping nothing would pass the next test for the
        wrong reason."""
        lines = (GOLDEN / "degiro_account_golden.csv").read_text(encoding="utf-8").splitlines()
        assert sum(1 for line in lines[1:] if _is_internal_transfer(line)) == 4

    def test_the_return_series_is_identical_with_and_without_them(
        self, tmp_path: Path
    ) -> None:
        """Sec 3.3's finding, made checkable (M6a section 8)."""
        with_them = _golden(tmp_path, with_transfers=True)
        without_them = _golden(tmp_path, with_transfers=False)
        assert external_flows(with_them) == external_flows(without_them)
        assert _returns(with_them) == _returns(without_them)

    def test_the_golden_flows_are_the_genuine_ones(self, tmp_path: Path) -> None:
        """One deposit, one withdrawal, and a flatex pair that crosses the
        boundary both ways on one day and nets to zero."""
        assert external_flows(_golden(tmp_path, with_transfers=True)) == {
            date(2025, 1, 8): D("0.00"),
            date(2025, 1, 9): D("-75.00"),
            date(2025, 1, 10): D("1000.00"),
        }
```

Append to `backend/tests/integration/test_no_double_count.py`:

```python
def test_the_portfolio_return_module_cannot_reach_an_adjusted_close_reader() -> None:
    """M6a-2. `portfolio_return.py` values the portfolio on the unadjusted close
    plus cash, and cash already carries every dividend, so reaching an adjusted
    reader would put Sec 7.5's double count one import away. The benchmark reader
    is excluded too: the comparison is composed in the route, so M6b can consume
    this module without acquiring a benchmark."""
    assert (APP / "analytics" / "portfolio_return.py").is_file()
    reachable = _reachable_from("app.analytics.portfolio_return")
    assert "app.analytics.valuation" in reachable
    assert "app.analytics.total_return" not in reachable
    assert "app.analytics.benchmark_return" not in reachable


def test_the_flow_reader_reaches_no_other_analytics_module() -> None:
    """`flows.py` reads the ledger and nothing else: no price, no close, no FX."""
    assert (APP / "analytics" / "flows.py").is_file()
    others = {
        module
        for module in _reachable_from("app.analytics.flows")
        if module.startswith("app.analytics.") and not module.startswith("app.analytics.flows")
    }
    assert not others, others
```

- [ ] **Step 6: Run the integration and guard tests**

```bash
cd backend && ./.venv/Scripts/python.exe -m pytest tests/integration/test_portfolio_return.py tests/integration/test_no_double_count.py -q
```

Expected: PASS. If `test_a_buy_below_the_close_adds_the_gap_less_the_fee` fails, print `value_series(engine, start=date.min).points` and check each close by hand against the fixture docstring before touching `portfolio_return.py` — the derivation is what this test exists to exercise.

- [ ] **Step 7: Run the verification gate, then commit**

```bash
git add backend/app/analytics/portfolio_return.py backend/tests/unit/test_portfolio_return.py \
  backend/tests/integration/test_portfolio_return.py backend/tests/integration/test_no_double_count.py
git commit -m "$(cat <<'EOF'
feat(analytics): chain-linked portfolio return, reported in runs (PT-25)

Claude-Session: https://claude.ai/code/session_013U6MLH7npaNQwK1ZS8UFbS
EOF
)"
```

---

### Task 4: `analytics/portfolio_benchmark.py` — each run against a benchmark

**Files:**
- Create: `backend/app/analytics/portfolio_benchmark.py`
- Modify: `backend/tests/integration/synthetic_ledger.py` (add `benchmark` and `rate`)
- Test: `backend/tests/integration/test_portfolio_benchmark.py`
- Test: `backend/tests/integration/test_no_double_count.py` (append one test)

**Interfaces:**
- Consumes: `benchmark_total_return_series` (`app.analytics.benchmark_return`); Task 1's `SPAN_MISSING`, `SPAN_PARTIAL`, `IndexPoint`, `in_base_points`, `rebase`, `side_reason`, `span_coverage`, `worst_span`; `base_currency`, `rate_history` (`app.analytics.quotes`); Task 3's `portfolio_returns` in tests.
- Produces:
  - `BASIS = "total_return"`, `DIVIDENDS = "reinvested_gross"`
  - `MeasuredRun` Protocol: read-only `start: date`, `end: date`, `linked_return: Decimal`
  - `RunExcess(start: date, end: date, portfolio_return: Decimal, benchmark_return: Decimal | None, excess: Decimal | None, span: SpanCoverage, reason: str | None)`
  - `PortfolioComparison(basis: str, dividends: str, benchmark_key: str, benchmark_index: tuple[IndexPoint, ...], runs: tuple[RunExcess, ...], benchmark_return: Decimal | None, excess: Decimal | None, span: SpanCoverage)`
  - `compare_to_benchmark(engine: Engine, runs: Sequence[MeasuredRun], *, window_return: Decimal | None, benchmark_key: str) -> PortfolioComparison`
  - `Ledger.benchmark(on, close, *, key="world", currency="EUR")`, `Ledger.rate(on, from_ccy, value)`

- [ ] **Step 1: Extend the synthetic ledger**

In `backend/tests/integration/synthetic_ledger.py`, change the market import to `from app.models.market import BenchmarkDaily, FxDaily, PriceDaily` and add these methods after `close`:

```python
    def benchmark(
        self, on: date, close: str, *, key: str = "world", currency: str = "EUR"
    ) -> "Ledger":
        """One benchmark close. The unadjusted close is a flat 100.00 on every
        day, so a reader taking the wrong column reports a benchmark that went
        nowhere rather than the same number."""
        with Session(self._engine) as session:
            session.add(
                BenchmarkDaily(
                    id=uuid4(), key=key, price_date=on, close_unadjusted=D("100.00"),
                    close_adjusted=D(close), currency=currency, source="yahoo",
                    fetched_at=FETCHED,
                )
            )
            session.commit()
        return self

    def rate(self, on: date, from_ccy: str, value: str) -> "Ledger":
        """Units of `from_ccy` per 1 EUR -- divide by it to reach EUR."""
        with Session(self._engine) as session:
            session.add(
                FxDaily(
                    id=uuid4(), from_ccy=from_ccy, to_ccy="EUR", rate_date=on,
                    rate=D(value), source="ecb", fetched_at=FETCHED,
                )
            )
            session.commit()
        return self
```

- [ ] **Step 2: Write the failing tests**

Create `backend/tests/integration/test_portfolio_benchmark.py`:

```python
"""The portfolio against a benchmark: per run, labelled, never across a gap.

The runs are real `portfolio_returns` output over hand-built closes, so the
structural `MeasuredRun` contract is exercised by the type it exists for. The
benchmark is seeded into `benchmark_daily` because the conversion and span rules
read it from there.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal as D

from app.analytics.portfolio_benchmark import compare_to_benchmark
from app.analytics.portfolio_return import PortfolioReturn, portfolio_returns
from app.analytics.valuation import ValuationPoint
from tests.integration.synthetic_ledger import Ledger

MON = date(2025, 3, 3)
TUE = date(2025, 3, 4)
WED = date(2025, 3, 5)
THU = date(2025, 3, 6)
FRI = date(2025, 3, 7)
NEXT_THU = date(2025, 3, 13)
NEXT_FRI = date(2025, 3, 14)


def day(on: date, value: str | None) -> ValuationPoint:
    return ValuationPoint(
        on=on,
        holdings_base=None if value is None else D("0.00"),
        cash_base=D("0.00") if value is None else D(value),
        value_base=None if value is None else D(value),
        coverage="missing" if value is None else "full",
        covered_pct=None if value is None else D("1"),
    )


def _one_run() -> PortfolioReturn:
    """1000.00 to 1100.00, Monday to Tuesday: 0.1."""
    return portfolio_returns([day(MON, "1000.00"), day(TUE, "1100.00")], {})


def _gapped() -> PortfolioReturn:
    """0.1 from Monday to Tuesday, no valuation Wednesday, 0.05 Thursday to Friday."""
    return portfolio_returns(
        [
            day(MON, "1000.00"), day(TUE, "1100.00"), day(WED, None),
            day(THU, "1200.00"), day(FRI, "1260.00"),
        ],
        {},
    )


class TestOneRun:
    def test_the_excess_is_the_portfolio_return_less_the_benchmarks(self) -> None:
        ledger = Ledger().benchmark(MON, "50.00").benchmark(TUE, "52.00")
        measured = _one_run()
        comparison = compare_to_benchmark(
            ledger.engine, measured.runs,
            window_return=measured.linked_return, benchmark_key="world",
        )
        (row,) = comparison.runs
        assert row.portfolio_return == D("0.1")
        assert row.benchmark_return == D("0.04")
        assert row.excess == D("0.06")
        assert (comparison.benchmark_return, comparison.excess) == (D("0.04"), D("0.06"))
        assert comparison.span == "full"

    def test_a_foreign_benchmark_is_converted_before_it_is_rebased(self) -> None:
        """M3-7. Flat at 50.00 in dollars while the rate moves from 1.00 to 1.25:
        in euros it fell from 50.00 to 40.00, which is what the owner would have
        got."""
        ledger = (
            Ledger()
            .benchmark(MON, "50.00", currency="USD")
            .benchmark(TUE, "50.00", currency="USD")
            .rate(MON, "USD", "1.00")
            .rate(TUE, "USD", "1.25")
        )
        measured = _one_run()
        comparison = compare_to_benchmark(
            ledger.engine, measured.runs,
            window_return=measured.linked_return, benchmark_key="world",
        )
        assert comparison.runs[0].benchmark_return == D("-0.2")

    def test_both_sides_are_labelled(self) -> None:
        """M6a-10: the benchmark's figure is a total return with dividends
        reinvested gross; the portfolio's labels ride on `PortfolioReturn`."""
        measured = _one_run()
        comparison = compare_to_benchmark(
            Ledger().engine, measured.runs,
            window_return=measured.linked_return, benchmark_key="world",
        )
        assert (comparison.basis, comparison.dividends) == ("total_return", "reinvested_gross")

    def test_the_benchmark_index_is_100_at_the_run_start(self) -> None:
        ledger = Ledger().benchmark(MON, "50.00").benchmark(TUE, "52.00")
        measured = _one_run()
        comparison = compare_to_benchmark(
            ledger.engine, measured.runs,
            window_return=measured.linked_return, benchmark_key="world",
        )
        assert [(p.on, p.index) for p in comparison.benchmark_index] == [
            (MON, D("100")), (TUE, D("104")),
        ]


class TestSpan:
    def test_no_benchmark_data_is_missing_with_a_reason_and_no_excess(self) -> None:
        measured = _one_run()
        comparison = compare_to_benchmark(
            Ledger().engine, measured.runs,
            window_return=measured.linked_return, benchmark_key="world",
        )
        (row,) = comparison.runs
        assert row.benchmark_return is None
        assert row.excess is None
        assert row.span == "missing"
        assert "world" in (row.reason or "")
        assert comparison.excess is None
        assert comparison.span == "missing"

    def test_a_benchmark_starting_late_yields_a_return_but_no_excess(self) -> None:
        """Differencing two returns measured over different spans is a
        subtraction that compiles and means nothing."""
        closes = [
            day(on, "1000.00")
            for on in (MON, TUE, WED, THU, FRI, date(2025, 3, 10), date(2025, 3, 11),
                       date(2025, 3, 12), NEXT_THU, NEXT_FRI)
        ]
        measured = portfolio_returns(closes, {})
        ledger = Ledger().benchmark(NEXT_THU, "50.00").benchmark(NEXT_FRI, "51.00")
        comparison = compare_to_benchmark(
            ledger.engine, measured.runs,
            window_return=measured.linked_return, benchmark_key="world",
        )
        (row,) = comparison.runs
        assert row.benchmark_return is not None
        assert row.excess is None
        assert row.span == "partial"
        assert "not the whole of" in (row.reason or "")

    def test_a_day_that_cannot_be_converted_marks_the_span_partial(self) -> None:
        """The drawn series is shorter than the one the provider sent."""
        ledger = (
            Ledger()
            .benchmark(MON, "50.00", currency="USD")
            .benchmark(TUE, "50.00", currency="USD")
            .rate(TUE, "USD", "1.25")
        )
        measured = _one_run()
        comparison = compare_to_benchmark(
            ledger.engine, measured.runs,
            window_return=measured.linked_return, benchmark_key="world",
        )
        assert comparison.span == "partial"


class TestGaps:
    def _ledger(self) -> Ledger:
        return (
            Ledger()
            .benchmark(MON, "50.00").benchmark(TUE, "51.00").benchmark(WED, "52.00")
            .benchmark(THU, "50.00").benchmark(FRI, "51.00")
        )

    def test_each_run_is_compared_over_its_own_span(self) -> None:
        measured = _gapped()
        comparison = compare_to_benchmark(
            self._ledger().engine, measured.runs,
            window_return=measured.linked_return, benchmark_key="world",
        )
        assert [(row.start, row.end, row.excess) for row in comparison.runs] == [
            (MON, TUE, D("0.08")),
            (THU, FRI, D("0.03")),
        ]

    def test_no_window_figure_spans_the_gap(self) -> None:
        """M6a-7, with a benchmark subtracted: still forbidden."""
        measured = _gapped()
        comparison = compare_to_benchmark(
            self._ledger().engine, measured.runs,
            window_return=measured.linked_return, benchmark_key="world",
        )
        assert comparison.benchmark_return is None
        assert comparison.excess is None

    def test_the_benchmark_index_restarts_at_100_on_each_run(self) -> None:
        measured = _gapped()
        comparison = compare_to_benchmark(
            self._ledger().engine, measured.runs,
            window_return=measured.linked_return, benchmark_key="world",
        )
        starts = [p.index for p in comparison.benchmark_index if p.on in (MON, THU)]
        assert starts == [D("100"), D("100")]


def test_no_runs_is_nothing_to_compare_and_says_missing() -> None:
    comparison = compare_to_benchmark(
        Ledger().engine, (), window_return=None, benchmark_key="world"
    )
    assert comparison.runs == ()
    assert comparison.span == "missing"
```

Append to `backend/tests/integration/test_no_double_count.py`:

```python
def test_the_portfolio_comparison_reaches_a_benchmark_and_no_holding() -> None:
    """M6a section 6.1, the reason the benchmark reader was split out. This
    module needs a benchmark's adjusted closes and must be provably unable to
    reach any holding's -- or the unadjusted-close readers, which it has no
    use for."""
    assert (APP / "analytics" / "portfolio_benchmark.py").is_file()
    reachable = _reachable_from("app.analytics.portfolio_benchmark")
    assert "app.analytics.benchmark_return" in reachable
    assert "app.analytics.total_return" not in reachable
    assert "app.analytics.prices" not in reachable
    assert "app.analytics.valuation" not in reachable
```

- [ ] **Step 3: Run them to verify they fail**

```bash
cd backend && ./.venv/Scripts/python.exe -m pytest tests/integration/test_portfolio_benchmark.py -q
```

Expected: collection error — `ModuleNotFoundError: No module named 'app.analytics.portfolio_benchmark'`.

- [ ] **Step 4: Write `backend/app/analytics/portfolio_benchmark.py`**

```python
"""The portfolio's time-weighted return against one benchmark, labelled on both sides.

M6a section 7. Measures a benchmark over each of `portfolio_return.py`'s runs,
with the same conversion, rebasing and span rules the instrument comparison uses
-- `indexing.py` holds them once for both.

**Why a benchmark is safe beside a valuation path when an instrument is not.** A
benchmark is never held, pays the owner nothing and appears in no cash balance,
so there is nothing to count twice (M6a section 6.1). This module reaches
`benchmark_return.py` and never `total_return.py`;
`tests/integration/test_no_double_count.py` asserts both.

**Per run, never across a gap.** Each run is compared over exactly its own span.
The window's figures exist only when the portfolio's own window figure does: an
excess spanning a gap would be M6a-7's forbidden figure with a benchmark
subtracted from it.

**The two sides differ, and both labels say how** (M6a-10). The portfolio's
dividends land in cash net of withholding and sit there; the benchmark's adjusted
close reinvests them gross. Both differences favour the benchmark. Neither is
engineered away -- a figure that silently folded withholding tax and
reinvestment convention into "you underperformed" would be the unlabelled return
parent doc Sec 7.4 forbids.

Excess is the ARITHMETIC difference, `portfolio - benchmark`, for the reasons
`instrument_return.IntervalExcess.excess` gives.

Runs arrive structurally through `MeasuredRun`, for the reason
`instrument_return.HoldingInterval` gives: importing `portfolio_return` would
put this module one hop from `valuation.py` and `prices.py`, and it needs
nothing from either.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol

from sqlalchemy import Engine
from sqlmodel import Session

from app.analytics.benchmark_return import benchmark_total_return_series
from app.analytics.indexing import (
    SPAN_MISSING,
    SPAN_PARTIAL,
    IndexPoint,
    in_base_points,
    rebase,
    side_reason,
    span_coverage,
    worst_span,
)
from app.analytics.quotes import base_currency, rate_history
from app.models.types import SpanCoverage

#: The benchmark side's construction: adjusted closes, dividends included.
BASIS = "total_return"
#: How dividends reach the benchmark's figure: reinvested on the ex-date, gross.
DIVIDENDS = "reinvested_gross"


class MeasuredRun(Protocol):
    """What the comparison needs from a run: its span and its one figure."""

    @property
    def start(self) -> date: ...

    @property
    def end(self) -> date: ...

    @property
    def linked_return(self) -> Decimal: ...


@dataclass(frozen=True, slots=True)
class RunExcess:
    start: date
    end: date
    portfolio_return: Decimal
    benchmark_return: Decimal | None
    #: `None`, never 0, when the benchmark does not span the run.
    excess: Decimal | None
    span: SpanCoverage
    #: Why `excess` is `None`. Set exactly when it is.
    reason: str | None


@dataclass(frozen=True, slots=True)
class PortfolioComparison:
    #: The BENCHMARK side's labels. The portfolio's are on `PortfolioReturn`.
    basis: str
    dividends: str
    benchmark_key: str
    #: 100 at each run's start, like `instrument_return.Comparison.benchmark_index`.
    benchmark_index: tuple[IndexPoint, ...]
    runs: tuple[RunExcess, ...]
    #: The window's figures: `None` unless the portfolio's own window figure exists.
    benchmark_return: Decimal | None
    excess: Decimal | None
    #: The benchmark's span over every run -- a span, not the portfolio's
    #: staleness; see `models/types.py` for why the two are different types.
    span: SpanCoverage


def compare_to_benchmark(
    engine: Engine,
    runs: Sequence[MeasuredRun],
    *,
    window_return: Decimal | None,
    benchmark_key: str,
) -> PortfolioComparison:
    """Every run against the benchmark over the same span, and the window when
    the portfolio has a window figure of its own."""
    ordered = sorted(runs, key=lambda run: (run.start, run.end))
    if not ordered:
        return _empty(benchmark_key)

    with Session(engine) as session:
        base = base_currency(session)
        rates = rate_history(session)

    # Convert, then rebase. Never the other way round (M3-7).
    series, dropped = in_base_points(
        benchmark_total_return_series(
            engine,
            benchmark_key,
            start=ordered[0].start,
            end=max(run.end for run in ordered),
        ),
        base,
        rates,
    )

    what = f"benchmark {benchmark_key!r}"
    rows: list[RunExcess] = []
    index: list[IndexPoint] = []
    for run in ordered:
        run_index, measured = rebase(series, run.start, run.end)
        span = span_coverage(series, run.start, run.end, measured)
        reason = side_reason(what, series, run.start, run.end, span)
        index.extend(run_index)
        rows.append(
            RunExcess(
                start=run.start,
                end=run.end,
                portfolio_return=run.linked_return,
                benchmark_return=measured,
                excess=(
                    run.linked_return - measured
                    if measured is not None and reason is None
                    else None
                ),
                span=span,
                reason=reason,
            )
        )

    overall = worst_span([row.span for row in rows])
    if dropped:
        # Days the benchmark could not be expressed in the base currency at all:
        # the drawn series is shorter than the one the provider sent.
        overall = worst_span([overall, SPAN_PARTIAL])

    whole = rows[0] if window_return is not None and len(rows) == 1 else None
    return PortfolioComparison(
        basis=BASIS,
        dividends=DIVIDENDS,
        benchmark_key=benchmark_key,
        benchmark_index=tuple(index),
        runs=tuple(rows),
        benchmark_return=whole.benchmark_return if whole is not None else None,
        excess=whole.excess if whole is not None else None,
        span=overall,
    )


def _empty(benchmark_key: str) -> PortfolioComparison:
    """No run, so nothing to compare. `missing` rather than `full`: an empty set
    of observations is not a well-covered one."""
    return PortfolioComparison(
        basis=BASIS,
        dividends=DIVIDENDS,
        benchmark_key=benchmark_key,
        benchmark_index=(),
        runs=(),
        benchmark_return=None,
        excess=None,
        span=SPAN_MISSING,
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd backend && ./.venv/Scripts/python.exe -m pytest tests/integration/test_portfolio_benchmark.py tests/integration/test_no_double_count.py -q
```

Expected: PASS.

- [ ] **Step 6: Run the verification gate, then commit**

```bash
git add backend/app/analytics/portfolio_benchmark.py backend/tests/integration/synthetic_ledger.py \
  backend/tests/integration/test_portfolio_benchmark.py backend/tests/integration/test_no_double_count.py
git commit -m "$(cat <<'EOF'
feat(analytics): compare portfolio runs against a benchmark (PT-25)

Claude-Session: https://claude.ai/code/session_013U6MLH7npaNQwK1ZS8UFbS
EOF
)"
```

---

### Task 5: `GET /api/performance`, guarded on arrival

**Files:**
- Create: `backend/app/api/dependencies.py`
- Modify: `backend/app/api/routes_instrument.py` (import `get_benchmarks` from there)
- Create: `backend/app/api/schemas_performance.py`
- Create: `backend/app/api/routes_performance.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/integration/test_performance_api.py`
- Test: `backend/tests/integration/test_no_double_count.py` (strengthen two tests, append one)

**Interfaces:**
- Consumes: Task 2's `external_flows`; Task 3's `PortfolioReturn`, `portfolio_returns`; Task 4's `PortfolioComparison`, `compare_to_benchmark`; `value_series`; `IndexPointOut`, `Provenance` from `app.api.schemas`; `get_engine` from `app.api.routes_transactions`.
- Produces:
  - `app.api.dependencies.get_benchmarks(request: Request) -> tuple[Benchmark, ...]`
  - `ReturnLinkOut`, `ReturnRunOut`, `RunExcessOut`, `PortfolioComparisonOut`, `PerformanceOut` in `app.api.schemas_performance`
  - `GET /api/performance?from=&to=&benchmark=` — the JSON shape of `PerformanceOut` below; Task 6 mirrors it field for field.

- [ ] **Step 1: Write the failing API tests**

Create `backend/tests/integration/test_performance_api.py`:

```python
"""What `GET /api/performance` puts on the wire (M6a).

Driven through `rebuild()` on the synthetic ledger, so every figure below is the
same arithmetic the analytics tests pin, reached the way a reader reaches it.

The ledger: 1000.00 deposited the Friday before, 10 shares bought at Monday's
close of 20.00, up 10% on Wednesday and flat after. That is 0.02 over the
window. The benchmark goes from 50.00 to 50.50 over the same days: 0.01.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal as D

from fastapi.testclient import TestClient

from app.ingest.benchmarks import Benchmark
from app.main import create_app
from tests.integration.synthetic_ledger import OTHER, Ledger

PREV_FRI = date(2025, 2, 28)
MON = date(2025, 3, 3)
TUE = date(2025, 3, 4)
WED = date(2025, 3, 5)
THU = date(2025, 3, 6)
FRI = date(2025, 3, 7)

WORLD = Benchmark(
    key="world", symbol="AAA.XX", currency="EUR", name="A world proxy", ter=D("0.20")
)


def _ledger(*, gap: bool = False) -> Ledger:
    """With `gap`, a second instrument bought on Wednesday has no close until
    Thursday, so Wednesday cannot be valued."""
    ledger = Ledger().deposit(PREV_FRI, "1000.00").buy(MON, "10", "20.00")
    for on, close in ((MON, "20.00"), (TUE, "20.00"), (WED, "22.00"), (THU, "22.00"), (FRI, "22.00")):
        ledger.close(on, close)
    for on, close in ((MON, "50.00"), (TUE, "50.00"), (WED, "50.50"), (THU, "50.50"), (FRI, "50.50")):
        ledger.benchmark(on, close)
    if gap:
        ledger.buy(WED, "1", "10.00", isin=OTHER).close(THU, "10.00", isin=OTHER)
    return ledger


def _client(ledger: Ledger) -> TestClient:
    return TestClient(create_app(engine=ledger.rebuilt(through=FRI), benchmarks=(WORLD,)))


class TestEnvelope:
    def test_reports_no_lot_method_because_none_was_applied(self) -> None:
        """TWR is built from share counts, closes and cash, and does not change
        with the lot method. Naming one would claim a computation that never ran."""
        assert _client(_ledger()).get("/api/performance").json()["method"] is None

    def test_labels_the_portfolio_figure(self) -> None:
        body = _client(_ledger()).get("/api/performance").json()
        assert body["basis"] == "time_weighted"
        assert body["lane"] == "unadjusted_close_plus_cash"
        assert body["dividends"] == "held_as_cash_net_of_withholding"

    def test_carries_the_worst_coverage_and_the_base_currency(self) -> None:
        body = _client(_ledger()).get("/api/performance").json()
        assert body["coverage"] == "full"
        assert body["base_currency"] == "EUR"


class TestTheFigure:
    def test_the_window_figure_crosses_the_wire_as_a_string(self) -> None:
        body = _client(_ledger()).get("/api/performance").json()
        assert body["linked_return"] == "0.02"
        assert body["gaps"] == 0
        assert body["reason"] is None

    def test_every_link_carries_its_flow_as_a_string(self) -> None:
        body = _client(_ledger().deposit(WED, "500.00")).get("/api/performance").json()
        wednesday = next(step for step in body["links"] if step["date"] == WED.isoformat())
        assert wednesday["flow_base"] == "500.00"
        assert wednesday["daily_return"] == "0.02"
        assert body["linked_return"] == "0.02"

    def test_the_index_is_on_the_wire_from_the_first_close(self) -> None:
        body = _client(_ledger()).get("/api/performance").json()
        assert body["portfolio_index"][0] == {"date": PREV_FRI.isoformat(), "index": "100"}


class TestTheWindow:
    def test_the_default_window_starts_at_the_ledgers_first_day(self) -> None:
        """P-4. Not the first position: the day money first buys shares earns or
        loses the gap between its price and the close."""
        body = _client(_ledger()).get("/api/performance").json()
        assert body["start"] == PREV_FRI.isoformat()
        assert body["links"][0]["date"] == MON.isoformat()
        assert body["requested_from"] is None
        assert body["clamped"] is False

    def test_an_explicit_start_is_honoured(self) -> None:
        body = _client(_ledger()).get(f"/api/performance?from={TUE}").json()
        assert body["start"] == TUE.isoformat()
        assert [step["date"] for step in body["links"]] == [
            WED.isoformat(), THU.isoformat(), FRI.isoformat(),
        ]
        assert body["linked_return"] == "0.02"
        assert body["clamped"] is False

    def test_a_start_before_the_ledger_is_clamped_and_says_so(self) -> None:
        five_years_back = (PREV_FRI - timedelta(days=365 * 5)).isoformat()
        body = _client(_ledger()).get(f"/api/performance?from={five_years_back}").json()
        assert body["requested_from"] == five_years_back
        assert body["clamped"] is True
        assert body["start"] == PREV_FRI.isoformat()

    def test_refuses_a_window_that_ends_before_it_starts(self) -> None:
        response = _client(_ledger()).get(f"/api/performance?from={TUE}&to={MON}")
        assert response.status_code == 422

    def test_refuses_a_date_it_cannot_read(self) -> None:
        assert _client(_ledger()).get("/api/performance?from=03-03-2025").status_code == 422


class TestGaps:
    def test_a_gap_sends_null_with_a_reason_and_keeps_both_runs(self) -> None:
        body = _client(_ledger(gap=True)).get("/api/performance").json()
        assert body["linked_return"] is None
        assert body["gaps"] == 1
        assert "no single figure spans a gap" in body["reason"]
        wednesday = next(step for step in body["links"] if step["date"] == WED.isoformat())
        assert wednesday["daily_return"] is None
        assert wednesday["reason"] == f"no valuation on {WED.isoformat()}"
        assert len(body["runs"]) == 2
        assert body["coverage"] == "missing"


class TestBenchmark:
    def test_no_benchmark_asked_for_means_no_comparison(self) -> None:
        assert _client(_ledger()).get("/api/performance").json()["comparison"] is None

    def test_an_unknown_benchmark_is_a_422_naming_the_configured_set(self) -> None:
        response = _client(_ledger()).get("/api/performance?benchmark=nope")
        assert response.status_code == 422
        assert "world" in response.json()["detail"]

    def test_the_comparison_is_labelled_on_the_benchmark_side(self) -> None:
        comparison = _client(_ledger()).get("/api/performance?benchmark=world").json()["comparison"]
        assert comparison["basis"] == "total_return"
        assert comparison["dividends"] == "reinvested_gross"

    def test_the_figures_cross_the_wire_as_strings(self) -> None:
        comparison = _client(_ledger()).get("/api/performance?benchmark=world").json()["comparison"]
        assert comparison["benchmark_return"] == "0.01"
        assert comparison["excess"] == "0.01"
        assert comparison["span"] == "full"
        (row,) = comparison["runs"]
        assert (row["portfolio_return"], row["excess"], row["reason"]) == ("0.02", "0.01", None)

    def test_across_a_gap_every_run_is_compared_and_the_window_is_not(self) -> None:
        comparison = _client(_ledger(gap=True)).get("/api/performance?benchmark=world").json()["comparison"]
        assert len(comparison["runs"]) == 2
        assert comparison["excess"] is None
```

In `backend/tests/integration/test_no_double_count.py`:

1. In `test_the_read_side_modules_exist_so_this_is_not_vacuous`, append:

```python
    assert any(path.name == "indexing.py" for path in modules)
    assert any(path.name == "flows.py" for path in modules)
    assert any(path.name == "portfolio_return.py" for path in modules)
    assert any(path.name == "portfolio_benchmark.py" for path in modules)
```

2. In `test_the_route_modules_are_discovered_so_this_is_not_vacuous`, append:

```python
    assert "app.api.routes_performance" in modules
```

3. Append:

```python
def test_the_performance_route_reaches_a_benchmark_but_no_holdings_adjusted_close() -> None:
    """M6a section 6. The first endpoint to need both lanes in one response: it
    reaches valuation and the benchmark reader, cannot reach the total-return
    module, and -- like the instrument route -- names neither column itself.

    It is NOT in `EXEMPT_ROUTES`. The discovered route list covered it the moment
    the file existed, and the general call-path test above already holds it to
    the rule; this test says why it passes."""
    route = "app.api.routes_performance"
    assert route not in EXEMPT_ROUTES
    reachable = _reachable_from(route)
    assert "app.analytics.valuation" in reachable
    assert "app.analytics.benchmark_return" in reachable
    assert "app.analytics.total_return" not in reachable
    names = _identifiers(APP / "api" / "routes_performance.py")
    assert UNADJUSTED not in names
    assert ADJUSTED not in names


def test_the_shared_route_dependencies_reach_no_analytics_module() -> None:
    """`get_benchmarks` moved here so a route can have it without importing
    `routes_instrument.py`, which reaches `total_return.py`. A shared dependency
    module that imported analytics would reopen that path for every route."""
    reachable = _reachable_from("app.api.dependencies")
    assert not {module for module in reachable if module.startswith("app.analytics.")}
```

- [ ] **Step 2: Run them to verify they fail**

```bash
cd backend && ./.venv/Scripts/python.exe -m pytest tests/integration/test_performance_api.py tests/integration/test_no_double_count.py -q
```

Expected: every API test fails with `404` (no route), and the two strengthened guard tests fail on `app.api.routes_performance`.

- [ ] **Step 3: Move `get_benchmarks` to `backend/app/api/dependencies.py`**

```python
"""FastAPI dependencies more than one route needs, in a module that reaches nothing.

`get_benchmarks` lived in `routes_instrument.py` until M6a. The performance route
needs it too, and importing it from there would have been one line that quietly
gave the performance endpoint a call path to `total_return.py`: `routes_instrument`
imports the instrument comparison, which reads an instrument's adjusted closes.
`tests/integration/test_no_double_count.py` walks imports and would have failed.

`get_engine` is not here only because it predates this file and every route
already imports it from `routes_transactions.py`, which reaches nothing dangerous.
"""

from __future__ import annotations

from fastapi import Request

from app.ingest.benchmarks import Benchmark


def get_benchmarks(request: Request) -> tuple[Benchmark, ...]:
    """Wired onto `app.state` at construction time -- the same seam as
    `get_engine`, and for the same reason: production wiring keeps a seam tests
    can use instead of FastAPI's `dependency_overrides`."""
    benchmarks: tuple[Benchmark, ...] = request.app.state.benchmarks
    return benchmarks
```

In `backend/app/api/routes_instrument.py`: delete the `get_benchmarks` function, remove `Request` from the `fastapi` import, and add `from app.api.dependencies import get_benchmarks` beside the `get_engine` import.

- [ ] **Step 4: Write `backend/app/api/schemas_performance.py`**

```python
"""Response models for `GET /api/performance` (M6a).

In their own file rather than appended to `schemas.py`, which is already past 500
lines. The conventions are that file's and are not restated: money and returns
cross the wire as strings, `None` stays `null`, and the envelope inherits
`Provenance`, so it cannot be constructed without `method` and `coverage`.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, field_serializer

from app.api.schemas import IndexPointOut, Provenance
from app.models.types import Coverage, SpanCoverage


class ReturnLinkOut(BaseModel):
    """One day's time-weighted return. Maps `ReturnLink`. `daily_return` is
    `null`, never "0", when either close had no valuation or the day before had
    no capital, and `reason` says which."""

    date: date
    since: date
    flow_base: Decimal
    daily_return: Decimal | None
    coverage: Coverage
    reason: str | None

    @field_serializer("flow_base")
    def _decimal_as_string(self, value: Decimal) -> str:
        return str(value)

    @field_serializer("daily_return")
    def _optional_decimal_as_string(self, value: Decimal | None) -> str | None:
        return None if value is None else str(value)


class ReturnRunOut(BaseModel):
    """A contiguous stretch of measurable days and its one figure. Maps
    `ReturnRun`; its links are on the envelope rather than repeated here."""

    start: date
    end: date
    days: int
    linked_return: Decimal
    coverage: Coverage

    @field_serializer("linked_return")
    def _decimal_as_string(self, value: Decimal) -> str:
        return str(value)


class RunExcessOut(BaseModel):
    """One run against the benchmark. Maps `RunExcess`. `excess` is the
    ARITHMETIC difference, as on `IntervalExcessOut`; `reason` is present
    exactly when `excess` is `null`."""

    start: date
    end: date
    portfolio_return: Decimal
    benchmark_return: Decimal | None
    excess: Decimal | None
    span: SpanCoverage
    reason: str | None

    @field_serializer("portfolio_return")
    def _decimal_as_string(self, value: Decimal) -> str:
        return str(value)

    @field_serializer("benchmark_return", "excess")
    def _optional_decimal_as_string(self, value: Decimal | None) -> str | None:
        return None if value is None else str(value)


class PortfolioComparisonOut(BaseModel):
    """The portfolio against one benchmark. Maps `PortfolioComparison`.

    `basis` and `dividends` describe the BENCHMARK side; the envelope carries the
    portfolio's. Both are sent because the gap between them is part of the answer
    (M6a section 7): a reader told only "the portfolio trailed by X" cannot know
    that part of X is withholding tax and reinvestment."""

    basis: str
    dividends: str
    benchmark_key: str
    benchmark_index: list[IndexPointOut]
    runs: list[RunExcessOut]
    benchmark_return: Decimal | None
    excess: Decimal | None
    span: SpanCoverage

    @field_serializer("benchmark_return", "excess")
    def _optional_decimal_as_string(self, value: Decimal | None) -> str | None:
        return None if value is None else str(value)


class PerformanceOut(Provenance):
    """The portfolio's time-weighted return over a window.

    `method` is `None`: a return built from share counts, closes and cash ran no
    lot matching, and TWR does not change with the lot method. `coverage` is the
    worst close in the window (Sec 8.1).

    `linked_return` is `null` whenever the window holds a gap, and `reason` says
    so; `runs` still carries one figure per contiguous stretch."""

    basis: str
    lane: str
    dividends: str
    base_currency: str
    start: date | None
    end: date | None
    #: What the caller asked for, echoed back. `null` when `from` was omitted.
    requested_from: date | None
    clamped: bool
    links: list[ReturnLinkOut]
    runs: list[ReturnRunOut]
    #: 100 at each run's start.
    portfolio_index: list[IndexPointOut]
    linked_return: Decimal | None
    gaps: int
    reason: str | None
    #: `null` when no `benchmark` was requested.
    comparison: PortfolioComparisonOut | None

    @field_serializer("linked_return")
    def _optional_decimal_as_string(self, value: Decimal | None) -> str | None:
        return None if value is None else str(value)
```

- [ ] **Step 5: Write `backend/app/api/routes_performance.py`**

```python
"""The portfolio's time-weighted return, optionally against one benchmark (M6a).

Composes four analytics calls and computes nothing of its own:
`valuation.value_series` and `flows.external_flows` feed
`portfolio_return.portfolio_returns`, and `portfolio_benchmark.compare_to_benchmark`
measures a benchmark over the runs that produces.

**The first endpoint that legitimately reaches both lanes** (M6a section 5).
Valuation reads the unadjusted close; the comparison reads a benchmark's adjusted
close. That is safe for a benchmark and would not be for a holding -- a benchmark
appears in no cash balance -- so this route reaches `benchmark_return.py` and
cannot reach `total_return.py`. `test_no_double_count.py` guards it because that
file DISCOVERS routes (PT-28); nothing had to remember to add it, and it is not
exempt. `get_benchmarks` comes from `api/dependencies.py` for the same reason:
importing it from `routes_instrument.py` would have opened the forbidden path.

**The default window starts at the ledger's first day**, not valuation's M2-7
default of the first position. A return is measured from the first close with
capital in it (M6a section 3.4), and the day money first buys shares earns or
loses the gap between its price and the close; starting at that day's close
would drop it. `date.min` asks `value_series` for everything the ledger has, and
`clamped` is reported only when the CALLER supplied `from` -- a sentinel this
route chose is not a request the reader made.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Engine

from app.analytics.flows import external_flows
from app.analytics.portfolio_benchmark import PortfolioComparison, compare_to_benchmark
from app.analytics.portfolio_return import PortfolioReturn, portfolio_returns
from app.analytics.valuation import value_series
from app.api.dependencies import get_benchmarks
from app.api.routes_transactions import get_engine
from app.api.schemas import IndexPointOut
from app.api.schemas_performance import (
    PerformanceOut,
    PortfolioComparisonOut,
    ReturnLinkOut,
    ReturnRunOut,
    RunExcessOut,
)
from app.ingest.benchmarks import Benchmark

router = APIRouter(prefix="/api", tags=["performance"])


@router.get("/performance", response_model=PerformanceOut)
def get_performance(
    engine: Engine = Depends(get_engine),
    benchmarks: tuple[Benchmark, ...] = Depends(get_benchmarks),
    # `from` is a Python keyword; aliased rather than renamed, as on /api/valuation.
    from_: date | None = Query(default=None, alias="from"),
    to: date | None = Query(default=None),
    benchmark: str | None = Query(default=None),
) -> PerformanceOut:
    if from_ is not None and to is not None and from_ > to:
        raise HTTPException(status_code=422, detail=f"from {from_} is after to {to}")
    configured = sorted(b.key for b in benchmarks)
    if benchmark is not None and benchmark not in configured:
        raise HTTPException(
            status_code=422,
            detail=f"unknown benchmark {benchmark!r}; configured benchmarks are {configured}",
        )

    series = value_series(engine, start=from_ if from_ is not None else date.min, end=to)
    result = portfolio_returns(series.points, external_flows(engine))
    comparison = (
        compare_to_benchmark(
            engine, result.runs, window_return=result.linked_return, benchmark_key=benchmark
        )
        if benchmark is not None
        else None
    )
    return _performance_out(
        result,
        comparison,
        base_currency=series.base_currency,
        requested_from=from_,
        clamped=from_ is not None and series.clamped,
    )


def _performance_out(
    result: PortfolioReturn,
    comparison: PortfolioComparison | None,
    *,
    base_currency: str,
    requested_from: date | None,
    clamped: bool,
) -> PerformanceOut:
    return PerformanceOut(
        basis=result.basis,
        lane=result.lane,
        dividends=result.dividends,
        base_currency=base_currency,
        start=result.start,
        end=result.end,
        requested_from=requested_from,
        clamped=clamped,
        links=[
            ReturnLinkOut(
                date=step.on, since=step.since, flow_base=step.flow_base,
                daily_return=step.daily_return, coverage=step.coverage, reason=step.reason,
            )
            for step in result.links
        ],
        runs=[
            ReturnRunOut(
                start=run.start, end=run.end, days=len(run.links),
                linked_return=run.linked_return, coverage=run.coverage,
            )
            for run in result.runs
        ],
        portfolio_index=[
            IndexPointOut(date=point.on, index=point.index)
            for run in result.runs
            for point in run.index
        ],
        linked_return=result.linked_return,
        gaps=result.gaps,
        reason=result.reason,
        comparison=_comparison_out(comparison) if comparison is not None else None,
        method=None,
        coverage=result.coverage,
    )


def _comparison_out(comparison: PortfolioComparison) -> PortfolioComparisonOut:
    return PortfolioComparisonOut(
        basis=comparison.basis,
        dividends=comparison.dividends,
        benchmark_key=comparison.benchmark_key,
        benchmark_index=[
            IndexPointOut(date=point.on, index=point.index)
            for point in comparison.benchmark_index
        ],
        runs=[
            RunExcessOut(
                start=row.start, end=row.end, portfolio_return=row.portfolio_return,
                benchmark_return=row.benchmark_return, excess=row.excess,
                span=row.span, reason=row.reason,
            )
            for row in comparison.runs
        ],
        benchmark_return=comparison.benchmark_return,
        excess=comparison.excess,
        span=comparison.span,
    )
```

In `backend/app/main.py`, add `routes_performance` to the `from app.api import (...)` list and `app.include_router(routes_performance.router)` after the instrument router.

- [ ] **Step 6: Run the tests to verify they pass, then the whole suite**

```bash
cd backend && ./.venv/Scripts/python.exe -m pytest tests/integration/test_performance_api.py tests/integration/test_no_double_count.py tests/integration/test_instrument_api.py -q
```

Expected: PASS. `test_instrument_api.py` is the check that moving `get_benchmarks` broke nothing. If `test_the_index_is_on_the_wire_from_the_first_close` fails on `"100"` versus `"100.0"`, the fault is not the route — check `str(HUNDRED)` in `indexing.py` is `"100"`.

- [ ] **Step 7: Run the verification gate, then commit**

```bash
git add backend/app/api/dependencies.py backend/app/api/routes_instrument.py \
  backend/app/api/schemas_performance.py backend/app/api/routes_performance.py backend/app/main.py \
  backend/tests/integration/test_performance_api.py backend/tests/integration/test_no_double_count.py
git commit -m "$(cat <<'EOF'
feat(api): the performance endpoint, guarded on arrival (PT-25)

Claude-Session: https://claude.ai/code/session_013U6MLH7npaNQwK1ZS8UFbS
EOF
)"
```

---

### Task 6: The frontend data layer and the pure chart builder

**Files:**
- Modify: `frontend/src/api/types.ts` (append)
- Modify: `frontend/src/api/client.ts` (append)
- Create: `frontend/src/lib/performance.ts`
- Test: `frontend/src/api/performance.test.ts`
- Test: `frontend/src/lib/performance.test.ts`

**Interfaces:**
- Consumes: Task 5's JSON shape; `IndexPoint`, `Coverage`, `SpanCoverage`, `Provenance` from `api/types.ts`; `c` from `lib/theme`.
- Produces:
  - Types `ReturnLink`, `ReturnRun`, `RunExcess`, `PortfolioComparison`, `PerformanceReport` (named `…Report` so the screen component can be `Performance`)
  - `fetchPerformance(query: PerformanceQuery): Promise<PerformanceReport>` with `PerformanceQuery { from: string | null; benchmark: string | null }`
  - `PORTFOLIO_SERIES`, `INDEX_AXIS_NAME: string`
  - `performanceDates(report: PerformanceReport): string[]`
  - `indexOnDates(dates: readonly string[], index: readonly IndexPoint[]): Array<number | null>`
  - `dividendTreatment(code: string): string`
  - `performanceChartOption(report: PerformanceReport, config: { benchmarkLabel: string | null }): EChartsOption`

- [ ] **Step 1: Append the types to `frontend/src/api/types.ts`**

```ts
/** One day's time-weighted return, measured from the previous valuation day's
 *  close. Mirrors `ReturnLinkOut`. `daily_return` is `null` -- never "0" -- when
 *  either close had no valuation or the day before had no capital, and `reason`
 *  says which. */
export interface ReturnLink {
  date: string;
  since: string;
  flow_base: string;
  daily_return: string | null;
  coverage: Coverage;
  reason: string | null;
}

/** A contiguous stretch of measurable days and its one figure. Mirrors
 *  `ReturnRunOut`. */
export interface ReturnRun {
  start: string;
  end: string;
  days: number;
  linked_return: string;
  coverage: Coverage;
}

/** One run against the benchmark. Mirrors `RunExcessOut`. `excess` is the
 *  ARITHMETIC difference, as on `IntervalExcess`, and `null` with a `reason`
 *  when the benchmark does not span the run. */
export interface RunExcess {
  start: string;
  end: string;
  portfolio_return: string;
  benchmark_return: string | null;
  excess: string | null;
  span: SpanCoverage;
  reason: string | null;
}

/** The portfolio against one benchmark. Mirrors `PortfolioComparisonOut`.
 *  `basis` and `dividends` describe the BENCHMARK side; `PerformanceReport`
 *  carries the portfolio's. */
export interface PortfolioComparison {
  basis: string;
  dividends: string;
  benchmark_key: string;
  benchmark_index: IndexPoint[];
  runs: RunExcess[];
  benchmark_return: string | null;
  excess: string | null;
  span: SpanCoverage;
}

/** The portfolio's time-weighted return over a window. Mirrors `PerformanceOut`.
 *  `linked_return` is `null` whenever the window holds a gap -- no single figure
 *  spans one (M6a-7) -- and `runs` still carries a figure per stretch. */
export interface PerformanceReport extends Provenance {
  basis: string;
  lane: string;
  dividends: string;
  base_currency: string;
  start: string | null;
  end: string | null;
  requested_from: string | null;
  clamped: boolean;
  links: ReturnLink[];
  runs: ReturnRun[];
  portfolio_index: IndexPoint[];
  linked_return: string | null;
  gaps: number;
  reason: string | null;
  comparison: PortfolioComparison | null;
}
```

- [ ] **Step 2: Write the failing client test**

Create `frontend/src/api/performance.test.ts`:

```ts
import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchPerformance } from "./client";

afterEach(() => vi.unstubAllGlobals());

function stub() {
  const json = vi.fn().mockResolvedValue({ links: [], runs: [] });
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, json });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("fetchPerformance", () => {
  it("sends no parameters for the whole ledger with no benchmark", async () => {
    // The server then measures from the ledger's first day, a decision it
    // already knows the answer to.
    const fetchMock = stub();
    await fetchPerformance({ from: null, benchmark: null });
    expect(String(fetchMock.mock.calls[0]?.[0])).toMatch(/\/api\/performance$/);
  });

  it("sends the start and the benchmark when both are given", async () => {
    const fetchMock = stub();
    await fetchPerformance({ from: "2025-09-06", benchmark: "world" });
    const url = String(fetchMock.mock.calls[0]?.[0]);
    expect(url).toContain("from=2025-09-06");
    expect(url).toContain("benchmark=world");
  });

  it("surfaces a failed response rather than returning an empty report", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: false, status: 500, statusText: "Server Error" }),
    );
    await expect(fetchPerformance({ from: null, benchmark: null })).rejects.toThrow(/500/);
  });
});
```

```bash
cd frontend && npx vitest run src/api/performance.test.ts
```

Expected: FAIL — `fetchPerformance` is not exported from `./client`.

- [ ] **Step 3: Append `fetchPerformance` to `frontend/src/api/client.ts`**

Add `PerformanceReport` to the `import type { … } from "./types"` list, then append:

```ts
export interface PerformanceQuery {
  /** ISO date, or `null` to send none -- the server then measures from the
   *  ledger's first day. The same contract as `ValuationQuery.from`. */
  from: string | null;
  /** `null` omits the parameter: no comparison, not a comparison against "". */
  benchmark: string | null;
}

export async function fetchPerformance(query: PerformanceQuery): Promise<PerformanceReport> {
  const params = new URLSearchParams();
  if (query.from) params.set("from", query.from);
  if (query.benchmark) params.set("benchmark", query.benchmark);
  const suffix = params.toString() ? `?${params}` : "";

  const response = await fetch(`${BASE}/api/performance${suffix}`);
  if (!response.ok) {
    throw new Error(`Failed to load performance: ${response.status} ${response.statusText}`);
  }
  return (await response.json()) as PerformanceReport;
}
```

```bash
cd frontend && npx vitest run src/api/performance.test.ts
```

Expected: 3 passed.

- [ ] **Step 4: Write the failing option-builder tests**

Create `frontend/src/lib/performance.test.ts`:

```ts
/** The performance chart's decisions, tested as a pure function: jsdom has no
 *  canvas, but the option object is where every choice worth pinning lives. */

import { describe, expect, it } from "vitest";

import type { PerformanceReport, ReturnLink } from "../api/types";
import {
  INDEX_AXIS_NAME,
  PORTFOLIO_SERIES,
  dividendTreatment,
  indexOnDates,
  performanceChartOption,
  performanceDates,
} from "./performance";

function link(date: string, dailyReturn: string | null = "0.01"): ReturnLink {
  return {
    date,
    since: "",
    flow_base: "0.00",
    daily_return: dailyReturn,
    coverage: dailyReturn === null ? "missing" : "full",
    reason: dailyReturn === null ? "no valuation" : null,
  };
}

/** Two runs around a Wednesday that could not be valued. */
function gapped(overrides: Partial<PerformanceReport> = {}): PerformanceReport {
  return {
    basis: "time_weighted",
    lane: "unadjusted_close_plus_cash",
    dividends: "held_as_cash_net_of_withholding",
    base_currency: "EUR",
    start: "2025-03-03",
    end: "2025-03-07",
    requested_from: null,
    clamped: false,
    links: [
      link("2025-03-04", "0.1"),
      link("2025-03-05", null),
      link("2025-03-06", null),
      link("2025-03-07", "0.05"),
    ],
    runs: [
      { start: "2025-03-03", end: "2025-03-04", days: 1, linked_return: "0.1", coverage: "full" },
      { start: "2025-03-06", end: "2025-03-07", days: 1, linked_return: "0.05", coverage: "full" },
    ],
    portfolio_index: [
      { date: "2025-03-03", index: "100" },
      { date: "2025-03-04", index: "110" },
      { date: "2025-03-06", index: "100" },
      { date: "2025-03-07", index: "105" },
    ],
    linked_return: null,
    gaps: 1,
    reason: "1 unmeasurable stretch split this window into 2 runs; no single figure spans a gap",
    comparison: null,
    method: null,
    coverage: "missing",
    ...overrides,
  };
}

describe("the drawn axis", () => {
  it("is the window's first close followed by every linked day", () => {
    expect(performanceDates(gapped())).toEqual([
      "2025-03-03",
      "2025-03-04",
      "2025-03-05",
      "2025-03-06",
      "2025-03-07",
    ]);
  });

  it("is empty when there is nothing to measure", () => {
    expect(performanceDates(gapped({ start: null, links: [] }))).toEqual([]);
  });
});

describe("placing an index on the axis", () => {
  it("places each point by date, never by position", () => {
    expect(
      indexOnDates(
        ["2025-03-03", "2025-03-04", "2025-03-05"],
        [
          { date: "2025-03-05", index: "110" },
          { date: "2025-03-03", index: "100" },
        ],
      ),
    ).toEqual([100, null, 110]);
  });
});

describe("the chart option", () => {
  it("draws the portfolio index with the gap left open, never bridged or zeroed", () => {
    const option = performanceChartOption(gapped(), { benchmarkLabel: null });
    const series = option.series as Array<Record<string, unknown>>;
    expect(series).toHaveLength(1);
    expect(series[0]!.name).toBe(PORTFOLIO_SERIES);
    expect(series[0]!.connectNulls).toBe(false);
    expect(series[0]!.data).toEqual([100, 110, null, 100, 105]);
  });

  it("adds the benchmark on the same axis, because both lines are the same kind of number", () => {
    const option = performanceChartOption(
      gapped({
        comparison: {
          basis: "total_return",
          dividends: "reinvested_gross",
          benchmark_key: "world",
          benchmark_index: [{ date: "2025-03-03", index: "100" }],
          runs: [],
          benchmark_return: null,
          excess: null,
          span: "full",
        },
      }),
      { benchmarkLabel: "World Equities" },
    );
    const series = option.series as Array<Record<string, unknown>>;
    expect(series.map((s) => s.name)).toEqual([PORTFOLIO_SERIES, "World Equities"]);
    expect(series[1]!.yAxisIndex).toBeUndefined();
    expect((option.yAxis as Record<string, unknown>).name).toBe(INDEX_AXIS_NAME);
  });

  it("names the benchmark by its key when no label is known", () => {
    const option = performanceChartOption(
      gapped({
        comparison: {
          basis: "total_return",
          dividends: "reinvested_gross",
          benchmark_key: "world",
          benchmark_index: [],
          runs: [],
          benchmark_return: null,
          excess: null,
          span: "missing",
        },
      }),
      { benchmarkLabel: null },
    );
    const series = option.series as Array<Record<string, unknown>>;
    expect(series[1]!.name).toBe("world");
  });
});

describe("dividend treatment", () => {
  it("says in words how each side treats dividends", () => {
    expect(dividendTreatment("held_as_cash_net_of_withholding")).toMatch(/after withholding/);
    expect(dividendTreatment("reinvested_gross")).toMatch(/reinvested/);
  });

  it("shows a code it does not recognise exactly as it arrived", () => {
    expect(dividendTreatment("something_new")).toBe("something_new");
  });
});
```

```bash
cd frontend && npx vitest run src/lib/performance.test.ts
```

Expected: FAIL — cannot resolve `./performance`.

- [ ] **Step 5: Write `frontend/src/lib/performance.ts`**

```ts
/** Everything the performance chart decides before a pixel is drawn.
 *
 *  Pure, and separate from the ECharts wrapper, for the reason `lib/valuation.ts`
 *  gives: jsdom has no canvas, but it can assert on the option object, and that
 *  object is where every decision worth testing lives.
 *
 *  Both lines are INDICES at 100 on each run's first close, exactly as the API
 *  sends them. A day between runs has no point, so it is `null` on the axis and
 *  the line breaks there instead of being drawn straight across days nobody could
 *  measure -- the chart's version of M6a-7's "no single figure spans a gap".
 *
 *  `indexOnDates` is this file's one numeric boundary, kept local rather than
 *  imported for the reason `lib/instrument.ts` gives: the place a string becomes
 *  a number should be visible without following an import.
 */

import type { EChartsOption } from "echarts";

import type { IndexPoint, PerformanceReport } from "../api/types";
import { c } from "./theme";

/** The portfolio line's legend name. */
export const PORTFOLIO_SERIES = "Portfolio (time-weighted)";

/** What the single y-axis measures. Both lines are the same kind of number, so
 *  they share it -- unlike the instrument chart, which puts a price on one axis
 *  and an index on another and has to say so. */
export const INDEX_AXIS_NAME = "index, 100 = run start";

/** The drawn axis: the window's first close, then every day with a link. */
export function performanceDates(report: PerformanceReport): string[] {
  if (report.start === null) return [];
  return [report.start, ...report.links.map((link) => link.date)];
}

/** An index onto a fixed axis, by date and never by position. A day with no
 *  point -- a gap, or a benchmark holiday -- stays `null`, so the line breaks
 *  rather than dropping to a zero that would read as a total loss. */
export function indexOnDates(
  dates: readonly string[],
  index: readonly IndexPoint[],
): Array<number | null> {
  const byDate = new Map(index.map((point) => [point.date, Number(point.index)]));
  return dates.map((date) => byDate.get(date) ?? null);
}

const DIVIDEND_TREATMENT: Readonly<Record<string, string>> = {
  held_as_cash_net_of_withholding: "dividends land in cash after withholding and stay there",
  reinvested_gross: "dividends are reinvested, before withholding",
};

/** The API's dividend label, in words (M6a-10). An unrecognised code is shown as
 *  it arrived: a raw string is more debuggable than a confident wrong sentence. */
export function dividendTreatment(code: string): string {
  return DIVIDEND_TREATMENT[code] ?? code;
}

export interface PerformanceChartConfig {
  /** The benchmark's display name, when the list has one for the key. */
  benchmarkLabel: string | null;
}

export function performanceChartOption(
  report: PerformanceReport,
  config: PerformanceChartConfig,
): EChartsOption {
  const dates = performanceDates(report);
  const comparison = report.comparison;
  const benchmarkName =
    comparison === null ? null : (config.benchmarkLabel ?? comparison.benchmark_key);

  const portfolioSeries = {
    name: PORTFOLIO_SERIES,
    type: "line" as const,
    showSymbol: false,
    connectNulls: false,
    data: indexOnDates(dates, report.portfolio_index),
    lineStyle: { color: c.accent, width: 1.6 },
  };

  return {
    animation: false,
    grid: { left: 56, right: 16, top: 34, bottom: 44 },
    legend: {
      data: benchmarkName === null ? [PORTFOLIO_SERIES] : [PORTFOLIO_SERIES, benchmarkName],
      top: 0,
      left: 0,
      itemGap: 14,
      icon: "roundRect",
      itemWidth: 14,
      itemHeight: 2,
      textStyle: { color: c.textFaint, fontSize: 10 },
    },
    xAxis: {
      type: "category",
      data: dates,
      axisLine: { lineStyle: { color: c.borderSoft } },
      axisLabel: { color: c.textFaint, fontSize: 10 },
    },
    yAxis: {
      type: "value",
      scale: true,
      name: INDEX_AXIS_NAME,
      nameLocation: "end" as const,
      nameGap: 12,
      nameTextStyle: { color: c.textFaint, fontSize: 9.5, align: "left" as const },
      axisLabel: { color: c.textFaint, fontSize: 10 },
      splitLine: { lineStyle: { color: c.borderSoft } },
    },
    dataZoom: [{ type: "inside" }, { type: "slider", height: 18, bottom: 8 }],
    tooltip: { trigger: "axis" },
    series: [
      portfolioSeries,
      // Appended only when a comparison exists -- never pushed and hidden,
      // because a hidden series still takes part in axis scaling and tooltips.
      ...(comparison !== null && benchmarkName !== null
        ? [
            {
              name: benchmarkName,
              type: "line" as const,
              showSymbol: false,
              connectNulls: false,
              data: indexOnDates(dates, comparison.benchmark_index),
              lineStyle: { color: c.neutral, width: 1.5, type: "dashed" as const },
            },
          ]
        : []),
    ],
  };
}
```

- [ ] **Step 6: Run the frontend tests and the typecheck**

```bash
cd frontend && npx vitest run src/lib/performance.test.ts src/api/performance.test.ts && npx tsc --noEmit
```

Expected: 11 passed; `tsc` clean.

- [ ] **Step 7: Run the verification gate, then commit**

```bash
git add frontend/src/api/types.ts frontend/src/api/client.ts frontend/src/lib/performance.ts \
  frontend/src/api/performance.test.ts frontend/src/lib/performance.test.ts
git commit -m "$(cat <<'EOF'
feat: performance data layer and chart option builder (PT-25)

Claude-Session: https://claude.ai/code/session_013U6MLH7npaNQwK1ZS8UFbS
EOF
)"
```

---

### Task 7: The Performance screen

**Files:**
- Create: `frontend/src/components/ui/BenchmarkControls.tsx` (moved out of `screens/Instrument.tsx`)
- Modify: `frontend/src/screens/Instrument.tsx`
- Create: `frontend/src/screens/Performance.tsx`
- Test: `frontend/src/screens/Performance.test.tsx`
- Modify: `frontend/src/navigation.ts`, `frontend/src/App.tsx`

**Interfaces:**
- Consumes: Task 6's `fetchPerformance`, `PerformanceReport`, `PortfolioComparison`, `ReturnRun`, `RunExcess`, `performanceChartOption`, `dividendTreatment`; `fetchBenchmarks`; `RANGE_PRESETS`, `rangeStart`, `RangePreset` from `lib/valuation`; `MethodBadge`, `Notice`, `Panel`, `SegmentedControl`, `TileGrid`, `Table` primitives.
- Produces:
  - `BenchmarkSelector({ benchmarks, error, selected, onSelect })`, `BenchmarkSpanBadge({ span })`, `terLabel(ter: string): string` from `components/ui/BenchmarkControls.tsx`
  - `Performance()` screen; `TabId` gains `"perf"`, in `LEDGER_BACKED`

- [ ] **Step 1: Move the benchmark controls, and prove Instrument did not notice**

Create `frontend/src/components/ui/BenchmarkControls.tsx` from `screens/Instrument.tsx`'s `SPAN_TONE`, `BenchmarkSpanBadge`, `terLabel`, `BenchmarkSelectorProps` and `BenchmarkSelector`. The bodies are unchanged except that the three are exported and the badge's `title` no longer says "holding":

```tsx
/** The benchmark controls two screens share: the selector, the span badge and
 *  the TER label.
 *
 *  Moved out of `screens/Instrument.tsx` in M6a, when Performance became the
 *  second screen that picks a benchmark and reports its span. Two copies of the
 *  selector would be free to disagree about the thing that matters most in it:
 *  a failed benchmark fetch and an empty configured set must never render the
 *  same.
 *
 *  `BenchmarkSpanBadge` stays deliberately separate from `MethodBadge`. Its value
 *  is a SPAN judgement -- how much of the compared stretch the benchmark series
 *  reaches across -- while `MethodBadge`'s COVERAGE is staleness everywhere in
 *  this app. Sharing a component would let an edit to one restyle the other into
 *  looking like the same judgement.
 */

import type { Benchmark, SpanCoverage } from "../../api/types";
import { decimal } from "../../lib/format";
import { c, mono } from "../../lib/theme";
import { Pill } from "./Controls";

const SPAN_TONE: Record<SpanCoverage, { color: string; label: string }> = {
  full: { color: c.textMuted, label: "FULL" },
  partial: { color: c.modelled, label: "PARTIAL" },
  missing: { color: c.negative, label: "MISSING" },
};

/** The benchmark's own span coverage. Deliberately not `MethodBadge` -- see
 *  this file's docstring. */
export function BenchmarkSpanBadge({ span }: { span: SpanCoverage }) {
  const tone = SPAN_TONE[span];
  return (
    <span
      title="How much of the compared stretch the benchmark series itself reaches across -- not how stale it is. A different question from the coverage badge."
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        border: `1px solid ${c.borderStrong}`,
        borderRadius: 4,
        padding: "4px 9px",
        fontSize: 11,
        whiteSpace: "nowrap",
      }}
    >
      <span style={{ fontFamily: mono, fontSize: 9.5, color: c.textFaint }}>BENCHMARK SPAN</span>
      <span style={{ fontFamily: mono, color: tone.color }}>{tone.label}</span>
    </span>
  );
}

/** The proxy's total expense ratio, in the unit the API states: a PERCENTAGE per
 *  year, so it is re-punctuated with `decimal` and never passed through
 *  `decimalPercent` (which shifts the point two places because it takes a
 *  ratio). "0.20" reads as 0,20%/yr. */
export function terLabel(ter: string): string {
  return `TER ${decimal(ter, 2, 2)}%/yr`;
}
```

…followed by `BenchmarkSelectorProps` and `BenchmarkSelector`, copied verbatim from `Instrument.tsx` with `export` added to both.

In `frontend/src/screens/Instrument.tsx`: delete those five definitions; remove `SpanCoverage` from the `../api/types` import and `decimal` from the `../lib/format` import; add `import { BenchmarkSelector, BenchmarkSpanBadge, terLabel } from "../components/ui/BenchmarkControls";`; and in the module docstring change "`BenchmarkSpanBadge` below exists so it cannot be mistaken for one" to "`BenchmarkSpanBadge`, in `components/ui/BenchmarkControls.tsx`, exists so it cannot be mistaken for one".

```bash
cd frontend && npx vitest run src/screens/Instrument.test.tsx && npx tsc --noEmit
```

Expected: every Instrument test passes unchanged; `tsc` clean. A pure move — no test may be edited to make this pass.

- [ ] **Step 2: Write the failing screen tests**

Create `frontend/src/screens/Performance.test.tsx`:

```tsx
/**
 * @vitest-environment jsdom
 */

/** What the Performance screen puts on screen (M6a).
 *
 *  The chart wrapper is mocked as on Positions and Instrument; every drawing
 *  decision is a pure function in `lib/performance.test.ts`. What is left is
 *  what only a rendered screen can get wrong: a window figure across a gap
 *  reading as "0,00%", a comparison shown without saying how each side treats
 *  dividends, and a span badge that reads as the coverage badge.
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fetchBenchmarks, fetchPerformance } from "../api/client";
import type { Benchmark, PerformanceReport, PortfolioComparison } from "../api/types";
import { Performance } from "./Performance";

vi.mock("../api/client", () => ({
  fetchBenchmarks: vi.fn(),
  fetchPerformance: vi.fn(),
}));

vi.mock("../components/charts/EChart", () => ({
  EChart: ({ option }: { option: unknown }) => (
    <div data-testid="chart" data-series={JSON.stringify(option)} />
  ),
}));

const mockBenchmarks = vi.mocked(fetchBenchmarks);
const mockPerformance = vi.mocked(fetchPerformance);

function benchmark(overrides: Partial<Benchmark> = {}): Benchmark {
  return { key: "world", name: "World Equities", ter: "0.20", ...overrides };
}

function comparison(overrides: Partial<PortfolioComparison> = {}): PortfolioComparison {
  return {
    basis: "total_return",
    dividends: "reinvested_gross",
    benchmark_key: "world",
    benchmark_index: [],
    runs: [
      {
        start: "2025-03-03",
        end: "2025-03-04",
        portfolio_return: "0.02",
        benchmark_return: "0.01",
        excess: "0.01",
        span: "full",
        reason: null,
      },
    ],
    benchmark_return: "0.01",
    excess: "0.01",
    span: "full",
    ...overrides,
  };
}

function report(overrides: Partial<PerformanceReport> = {}): PerformanceReport {
  return {
    basis: "time_weighted",
    lane: "unadjusted_close_plus_cash",
    dividends: "held_as_cash_net_of_withholding",
    base_currency: "EUR",
    start: "2025-03-03",
    end: "2025-03-04",
    requested_from: null,
    clamped: false,
    links: [
      {
        date: "2025-03-04",
        since: "2025-03-03",
        flow_base: "0.00",
        daily_return: "0.02",
        coverage: "full",
        reason: null,
      },
    ],
    runs: [{ start: "2025-03-03", end: "2025-03-04", days: 1, linked_return: "0.02", coverage: "full" }],
    portfolio_index: [],
    linked_return: "0.02",
    gaps: 0,
    reason: null,
    comparison: null,
    method: null,
    coverage: "full",
    ...overrides,
  };
}

function gapped(): PerformanceReport {
  return report({
    linked_return: null,
    gaps: 1,
    reason: "1 unmeasurable stretch split this window into 2 runs; no single figure spans a gap",
    runs: [
      { start: "2025-03-03", end: "2025-03-04", days: 1, linked_return: "0.1", coverage: "full" },
      { start: "2025-03-06", end: "2025-03-07", days: 1, linked_return: "0.05", coverage: "full" },
    ],
    coverage: "missing",
  });
}

beforeEach(() => {
  mockBenchmarks.mockReset();
  mockPerformance.mockReset();
  mockBenchmarks.mockResolvedValue([benchmark()]);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("the window figure", () => {
  it("shows the time-weighted return with its basis", async () => {
    mockPerformance.mockResolvedValue(report());
    render(<Performance />);
    expect((await screen.findAllByText("2,00%")).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/time_weighted/).length).toBeGreaterThan(0);
  });

  it("reads a dash and the reason across a gap, never 0,00%", async () => {
    mockPerformance.mockResolvedValue(gapped());
    render(<Performance />);
    expect(await screen.findByText(/no single figure spans a gap/)).toBeInTheDocument();
    expect(screen.queryByText("0,00%")).not.toBeInTheDocument();
  });

  it("still gives every run its own figure", async () => {
    mockPerformance.mockResolvedValue(gapped());
    render(<Performance />);
    const table = await screen.findByRole("table");
    expect(within(table).getByText("10,00%")).toBeInTheDocument();
    expect(within(table).getByText("5,00%")).toBeInTheDocument();
  });
});

describe("the controls", () => {
  it("asks for the whole ledger with no benchmark by default", async () => {
    mockPerformance.mockResolvedValue(report());
    render(<Performance />);
    await waitFor(() =>
      expect(mockPerformance).toHaveBeenCalledWith({ from: null, benchmark: null }),
    );
  });

  it("asks for a start date when a range is picked", async () => {
    mockPerformance.mockResolvedValue(report());
    render(<Performance />);
    const { default: userEvent } = await import("@testing-library/user-event");
    await userEvent.click(await screen.findByRole("button", { name: "1Y" }));
    await waitFor(() =>
      expect(mockPerformance).toHaveBeenCalledWith({
        from: expect.stringMatching(/^\d{4}-\d{2}-\d{2}$/),
        benchmark: null,
      }),
    );
  });

  it("asks for the comparison when a benchmark is picked", async () => {
    mockPerformance.mockResolvedValue(report());
    render(<Performance />);
    const { default: userEvent } = await import("@testing-library/user-event");
    await userEvent.click(await screen.findByRole("button", { name: "World Equities" }));
    await waitFor(() =>
      expect(mockPerformance).toHaveBeenCalledWith({ from: null, benchmark: "world" }),
    );
  });
});

describe("the comparison", () => {
  it("says how each side treats dividends", async () => {
    // M6a-10. Both differences favour the benchmark, and a reader shown only
    // the excess cannot tell how much of it is withholding and reinvestment.
    mockPerformance.mockResolvedValue(report({ comparison: comparison() }));
    render(<Performance />);
    expect(await screen.findByText(/after withholding and stay there/)).toBeInTheDocument();
    expect(screen.getByText(/reinvested, before withholding/)).toBeInTheDocument();
  });

  it("shows the benchmark span as its own badge beside coverage", async () => {
    mockPerformance.mockResolvedValue(
      report({ coverage: "full", comparison: comparison({ span: "partial" }) }),
    );
    render(<Performance />);
    await screen.findByText("FULL");
    expect(screen.getByText(/BENCHMARK SPAN/)).toBeInTheDocument();
    expect(screen.getByText("PARTIAL")).toBeInTheDocument();
  });

  it("renders a dash and the reason when a run's excess could not be computed", async () => {
    mockPerformance.mockResolvedValue(
      report({
        comparison: comparison({
          benchmark_return: null,
          excess: null,
          span: "missing",
          runs: [
            {
              start: "2025-03-03",
              end: "2025-03-04",
              portfolio_return: "0.02",
              benchmark_return: null,
              excess: null,
              span: "missing",
              reason: "no benchmark 'world' data from 2025-03-03 to 2025-03-04",
            },
          ],
        }),
      }),
    );
    render(<Performance />);
    const table = await screen.findByRole("table");
    expect(within(table).getByText(/no benchmark 'world' data/)).toBeInTheDocument();
    expect(within(table).queryByText("0,00%")).not.toBeInTheDocument();
  });
});

describe("what the screen says when there is nothing to show", () => {
  it("distinguishes a dead API from an empty ledger", async () => {
    mockPerformance.mockRejectedValue(new Error("500 Server Error"));
    render(<Performance />);
    expect(await screen.findByText(/Could not reach the API/i)).toBeInTheDocument();
  });

  it("tells the reader what to run when there is nothing to measure", async () => {
    mockPerformance.mockResolvedValue(
      report({ links: [], runs: [], linked_return: null, start: null, end: null, coverage: "missing" }),
    );
    render(<Performance />);
    expect(await screen.findByText(/fetch-prices/)).toBeInTheDocument();
  });

  it("says when the requested window was longer than the ledger", async () => {
    mockPerformance.mockResolvedValue(report({ requested_from: "2020-03-03", clamped: true }));
    render(<Performance />);
    expect(await screen.findByText(/Asked for/)).toBeInTheDocument();
  });
});
```

```bash
cd frontend && npx vitest run src/screens/Performance.test.tsx
```

Expected: FAIL — cannot resolve `./Performance`.

- [ ] **Step 3: Write `frontend/src/screens/Performance.tsx`**

```tsx
/** The portfolio's time-weighted return, against one benchmark (M6a). The fifth
 *  screen reading the live ledger.
 *
 *  It fetches one endpoint and draws what came back: every figure is decided on
 *  the backend, and every money and return string stays a string here (see
 *  `api/types.ts`). The chart's one numeric boundary is `indexOnDates` in
 *  `lib/performance.ts`.
 *
 *  Four rules from the M6a design decide what is on screen:
 *
 *  - **No single figure spans a gap** (M6a-7). `linked_return` is `null` across
 *    one, so the headline tile reads "—" with the API's reason, the line breaks,
 *    and the runs table still carries one figure per run.
 *  - **Every return carries its basis** (parent doc Sec 7.4): time-weighted on
 *    the unadjusted close plus cash for the portfolio, total return for the
 *    benchmark.
 *  - **The comparison says how each side treats dividends** (M6a-10). Both
 *    differences favour the benchmark, and a reader shown only the excess
 *    cannot tell how much of it is withholding and reinvestment.
 *  - **`null` is "—", never "0,00%"** (parent doc Sec 8.1).
 */

import { useEffect, useMemo, useState } from "react";

import { fetchBenchmarks, fetchPerformance } from "../api/client";
import type {
  Benchmark,
  PerformanceReport,
  PortfolioComparison,
  ReturnRun,
  RunExcess,
} from "../api/types";
import { EChart } from "../components/charts/EChart";
import {
  BenchmarkSelector,
  BenchmarkSpanBadge,
  terLabel,
} from "../components/ui/BenchmarkControls";
import { SegmentedControl } from "../components/ui/Controls";
import { MethodBadge } from "../components/ui/MethodBadge";
import { Notice } from "../components/ui/Notice";
import { Panel } from "../components/ui/Panel";
import {
  HeadRow,
  Table,
  TableFrame,
  Td,
  rowBackground,
  type ColumnDef,
} from "../components/ui/Table";
import { TileGrid, type Tile } from "../components/ui/Tiles";
import { decimalIsNegative, decimalPercent, shortDate } from "../lib/format";
import { dividendTreatment, performanceChartOption } from "../lib/performance";
import { c, mono } from "../lib/theme";
import { RANGE_PRESETS, rangeStart, type RangePreset } from "../lib/valuation";

const RUN_COLUMNS: readonly ColumnDef[] = [
  { label: "FROM CLOSE" },
  { label: "TO CLOSE" },
  { label: "DAYS", align: "right" },
  { label: "TWR", align: "right" },
  { label: "COVERAGE", align: "right" },
  { label: "BENCH (TR)", align: "right" },
  { label: "EXCESS", align: "right" },
];

function signColour(value: string | null): string {
  if (value == null) return c.textMuted;
  return decimalIsNegative(value) ? c.negative : c.positive;
}

/** A run's benchmark row, matched on its span and never on array position. */
function excessFor(comparison: PortfolioComparison | null, run: ReturnRun): RunExcess | null {
  if (comparison === null) return null;
  return comparison.runs.find((row) => row.start === run.start && row.end === run.end) ?? null;
}

function excessNote(report: PerformanceReport): string | null {
  const comparison = report.comparison;
  if (comparison === null) return "Pick a benchmark to compare";
  if (comparison.excess !== null) return "portfolio − benchmark, arithmetic";
  return report.reason ?? comparison.runs[0]?.reason ?? null;
}

function headlineTiles(report: PerformanceReport, benchmark: Benchmark | null): Tile[] {
  const comparison = report.comparison;
  const window =
    report.start !== null && report.end !== null
      ? `${shortDate(report.start)} – ${shortDate(report.end)}`
      : "";
  return [
    {
      label: "TIME-WEIGHTED RETURN",
      value: decimalPercent(report.linked_return),
      color: signColour(report.linked_return),
      sub: report.linked_return === null ? report.reason : `${window} · ${report.basis}`,
    },
    {
      label: "BENCHMARK (TOTAL RETURN)",
      value: comparison ? decimalPercent(comparison.benchmark_return) : "—",
      sub: benchmark ? `${benchmark.name} · ${terLabel(benchmark.ter)}` : "No benchmark selected",
    },
    {
      label: "EXCESS",
      value: comparison ? decimalPercent(comparison.excess) : "—",
      color: signColour(comparison?.excess ?? null),
      sub: excessNote(report),
    },
    {
      label: "GAPS",
      value: String(report.gaps),
      sub: `${report.runs.length} ${report.runs.length === 1 ? "run" : "runs"} measured`,
    },
  ];
}

/** Both sides' labels, in words. The comparison is not readable without them. */
function LabelsFooter({ report, benchmark }: { report: PerformanceReport; benchmark: Benchmark | null }) {
  const comparison = report.comparison;
  return (
    <>
      Portfolio: {report.basis} on {report.lane} — {dividendTreatment(report.dividends)}.
      {comparison !== null && (
        <>
          {" "}
          Benchmark{benchmark ? ` (${benchmark.name})` : ""}: {comparison.basis} —{" "}
          {dividendTreatment(comparison.dividends)}. Both differences favour the benchmark,
          and neither is adjusted away.
          {benchmark !== null && (
            <>
              {" "}
              {benchmark.name} carries {terLabel(benchmark.ter)}, reported and never subtracted.
            </>
          )}
        </>
      )}
    </>
  );
}

function ExcessCell({ row }: { row: RunExcess | null }) {
  if (row === null) return <span style={{ color: c.textFaint }}>—</span>;
  if (row.excess === null) {
    return (
      <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 2 }}>
        <span style={{ color: c.textFaint }}>—</span>
        <span style={{ fontSize: 9.5, color: c.textFaint, textAlign: "right" }}>{row.reason}</span>
      </div>
    );
  }
  return <span style={{ color: signColour(row.excess) }}>{decimalPercent(row.excess)}</span>;
}

export function Performance() {
  const [range, setRange] = useState<RangePreset>("MAX");
  const [benchmarkKey, setBenchmarkKey] = useState<string | null>(null);
  const [benchmarks, setBenchmarks] = useState<Benchmark[]>([]);
  const [benchmarksError, setBenchmarksError] = useState<string | null>(null);
  const [report, setReport] = useState<PerformanceReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // The benchmark list does not depend on anything the reader picks.
  useEffect(() => {
    let cancelled = false;
    fetchBenchmarks()
      .then((items) => {
        if (cancelled) return;
        setBenchmarks(items);
        setBenchmarksError(null);
      })
      .catch((cause: unknown) => {
        // Non-fatal, never silent: `BenchmarkSelector` renders a failed list
        // differently from an empty configured one.
        if (cancelled) return;
        setBenchmarksError(cause instanceof Error ? cause.message : String(cause));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // The cancellation guard: a slow MAX response landing after a fast 1Y one
  // would otherwise draw the whole ledger under a one-year control.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchPerformance({ from: rangeStart(range, new Date()), benchmark: benchmarkKey })
      .then((data) => {
        if (cancelled) return;
        setReport(data);
        setLoading(false);
      })
      .catch((cause: unknown) => {
        if (cancelled) return;
        setError(cause instanceof Error ? cause.message : String(cause));
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [range, benchmarkKey]);

  const activeBenchmark = benchmarks.find((b) => b.key === benchmarkKey) ?? null;
  const option = useMemo(
    () =>
      report ? performanceChartOption(report, { benchmarkLabel: activeBenchmark?.name ?? null }) : null,
    [report, activeBenchmark],
  );

  if (error) {
    return (
      <Notice tone="danger">
        Could not reach the API. {error}. Start it with{" "}
        <code style={{ fontFamily: mono }}>python -m uvicorn app.main:create_app --factory</code>,
        and check that CORS_ORIGINS in backend/.env lists this port.
      </Notice>
    );
  }

  if (loading || report === null || option === null) {
    return <div style={{ fontSize: 12, color: c.textFaint }}>Loading…</div>;
  }

  if (report.links.length === 0) {
    return (
      <Notice tone="modelled">
        Nothing to measure yet. Import an export, then run{" "}
        <code style={{ fontFamily: mono }}>python -m app.cli fetch-prices</code> and{" "}
        <code style={{ fontFamily: mono }}>python -m app.cli rebuild</code>.
      </Notice>
    );
  }

  const comparison = report.comparison;
  const shownBenchmark = comparison !== null ? activeBenchmark : null;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
        {/* The window's price coverage -- staleness, as on Positions. `method`
            is always null: a time-weighted return ran no lot matching. */}
        <MethodBadge method={report.method} coverage={report.coverage} />
        {comparison && <BenchmarkSpanBadge span={comparison.span} />}
        <div style={{ marginLeft: "auto" }}>
          <SegmentedControl options={RANGE_PRESETS} value={range} onChange={setRange} label="RANGE" size="sm" />
        </div>
      </div>

      <TileGrid tiles={headlineTiles(report, shownBenchmark)} />

      <Panel
        title="Time-weighted return"
        subtitle="Rebased to 100 at the start of each run — a break in the line is a gap no figure spans"
        actions={
          <BenchmarkSelector
            benchmarks={benchmarks}
            error={benchmarksError}
            selected={benchmarkKey}
            onSelect={setBenchmarkKey}
          />
        }
        footer={<LabelsFooter report={report} benchmark={shownBenchmark} />}
      >
        <EChart option={option} height={300} />
        {report.clamped && report.requested_from !== null && report.start !== null && (
          <div style={{ fontSize: 11, color: c.modelled, marginTop: 8 }}>
            Asked for {shortDate(report.requested_from)}; measurement begins{" "}
            {shortDate(report.start)}. Showing everything there is rather than padding the difference.
          </div>
        )}
      </Panel>

      <TableFrame>
        <Table minWidth={760}>
          <HeadRow columns={RUN_COLUMNS} />
          <tbody>
            {report.runs.map((run, index) => {
              const row = excessFor(comparison, run);
              return (
                <tr
                  key={`${run.start}-${run.end}`}
                  style={{ background: rowBackground(index), borderBottom: `1px solid ${c.borderSoft}` }}
                >
                  <Td numeric>{shortDate(run.start)}</Td>
                  <Td numeric>{shortDate(run.end)}</Td>
                  <Td align="right" numeric>{run.days}</Td>
                  <Td align="right" numeric color={signColour(run.linked_return)}>
                    {decimalPercent(run.linked_return)}
                  </Td>
                  <Td align="right" numeric color={run.coverage === "full" ? c.textFaint : c.modelled}>
                    {run.coverage}
                  </Td>
                  <Td align="right" numeric color={signColour(row?.benchmark_return ?? null)}>
                    {row ? decimalPercent(row.benchmark_return) : "—"}
                  </Td>
                  <Td align="right" numeric>
                    <ExcessCell row={row} />
                  </Td>
                </tr>
              );
            })}
          </tbody>
        </Table>
      </TableFrame>
    </div>
  );
}
```

- [ ] **Step 4: Run the screen tests to verify they pass**

```bash
cd frontend && npx vitest run src/screens/Performance.test.tsx
```

Expected: 12 passed.

- [ ] **Step 5: Wire the twelfth tab**

In `frontend/src/navigation.ts`:

1. Add `| "perf"` to `TabId` after `| "bm"`.
2. Insert after the `bm` entry in `TABS`:

```ts
  {
    id: "perf",
    label: "Performance",
    title: "Performance",
    subtitle: "Time-weighted return, against a benchmark",
  },
```

3. Change `LEDGER_BACKED` to `new Set<TabId>(["pos", "tx", "lots", "instr", "perf"])`.
4. In the docstring, change "The eleven screens" to "The twelve screens", and append to its ordering argument: "Performance sits right after Benchmarks & Industry for the same reason: it is the live counterpart of the modelled screen before it (M6a), whose industry sections stay modelled until M6b."

In `frontend/src/App.tsx`: add `import { Performance } from "./screens/Performance";` and `{tab === "perf" && <Performance />}` after the `bm` line.

- [ ] **Step 6: See it in the real app**

```bash
cd backend && ./.venv/Scripts/python.exe -m uvicorn app.main:create_app --factory --port 8000
cd frontend && npm run dev
```

Open <http://localhost:5173>, choose **Performance**. Confirm, against the operator's own data: the tile shows a figure or "—" with a reason; the line breaks at every gap rather than bridging it; picking the configured benchmark adds a dashed line on the same axis and the footer states both sides' dividend treatment; switching to 1Y re-requests. Stop both servers afterwards. Nothing seen here is written into any tracked file.

- [ ] **Step 7: Run the verification gate, then commit**

```bash
git add frontend/src/components/ui/BenchmarkControls.tsx frontend/src/screens/Instrument.tsx \
  frontend/src/screens/Performance.tsx frontend/src/screens/Performance.test.tsx \
  frontend/src/navigation.ts frontend/src/App.tsx
git commit -m "$(cat <<'EOF'
feat: the live performance screen (PT-25)

Claude-Session: https://claude.ai/code/session_013U6MLH7npaNQwK1ZS8UFbS
EOF
)"
```

---

### Task 8: The real-data acceptance, and the runbook

**Files:**
- Modify: `backend/tests/integration/realdata_subject.py` (append one oracle)
- Test: `backend/tests/integration/test_realdata_performance.py`
- Modify: `docs/RUNBOOK.md`

**Interfaces:**
- Consumes: everything above; `import_degiro_export`, `ensure_default_account`; `load_benchmarks`.
- Produces: `realdata_subject.external_flow_total() -> tuple[int, Decimal]`.

- [ ] **Step 1: Add the flow oracle to `realdata_subject.py`**

```python
#: Account.csv descriptions that cross the account boundary: Sec 3.3's genuine
#: deposits and withdrawal, plus the flatex transfers to and from the owner's
#: own bank. Matched here by prefix on the raw CSV, never through
#: `account_csv.classify`, for the reason this module's docstring gives.
_EXTERNAL_FLOW_PREFIXES = (
    "ideal deposit",
    "sepa instant terugstorting",
    "processed flatex withdrawal",
    "flatex terugstorting",
)


def external_flow_total() -> tuple[int, Decimal]:
    """How many EUR rows cross the account boundary, and their signed sum."""
    count = 0
    total = _ZERO
    with (EXPORT / "Account.csv").open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        next(reader)
        for row in reader:
            if (
                len(row) > 8
                and row[5].strip().casefold().startswith(_EXTERNAL_FLOW_PREFIXES)
                and row[7].strip() == "EUR"
            ):
                count += 1
                total += _decimal(row[8]) or _ZERO
    return count, total
```

- [ ] **Step 2: Write the acceptance tests**

Create `backend/tests/integration/test_realdata_performance.py`:

```python
"""M6a's acceptance, against the owner's own export and price cache.

States no figure of its own. The flow oracle reads `Account.csv` directly; every
other assertion is a property the return series must have whatever the figures
are. The flow checks need only the export; the series checks read the
operator's populated database, and skip with the suite's usual reason when the
price cache is empty.

To run it for real:

    cd backend
    python -m app.cli import ../degiro-export
    python -m app.cli fetch-prices
    python -m app.cli rebuild
    python -m pytest -q -m realdata
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import Engine
from sqlmodel import Session, col, select

from app.analytics.flows import EXTERNAL_FLOW_TYPES, external_flows
from app.analytics.portfolio_benchmark import compare_to_benchmark
from app.analytics.portfolio_return import PortfolioReturn, portfolio_returns
from app.analytics.valuation import value_series
from app.db import create_engine_and_tables
from app.ingest.benchmarks import load_benchmarks
from app.ingest.importer import ensure_default_account, import_degiro_export
from app.models.ledger import PositionDaily, Transaction
from app.models.market import BenchmarkDaily, PriceDaily
from tests.integration import realdata_subject as subject

D = Decimal
URL = subject.local_database_url()
BENCHMARKS_CONFIG = subject.REPO / "config" / "benchmarks.yaml"

pytestmark = [
    pytest.mark.realdata,
    pytest.mark.skipif(
        not subject.available(), reason="real DeGiro export or answers file not present"
    ),
]


@pytest.fixture(scope="module")
def imported() -> Engine:
    """The export alone, in memory. No prices are needed to count flows."""
    engine = create_engine_and_tables("sqlite://")
    import_degiro_export(engine, subject.EXPORT, ensure_default_account(engine), subject.ANSWERS)
    return engine


@pytest.fixture(scope="module")
def local() -> Engine:
    if URL is None:
        pytest.skip("no local database in backend/.env")
    engine = create_engine_and_tables(URL)
    with Session(engine) as session:
        if not session.exec(select(PriceDaily)).first():
            pytest.skip("price cache is empty; run `python -m app.cli fetch-prices` first")
        if not session.exec(select(PositionDaily)).first():
            pytest.skip("no daily series; run `python -m app.cli rebuild` first")
    return engine


@pytest.fixture(scope="module")
def measured(local: Engine) -> PortfolioReturn:
    return portfolio_returns(value_series(local, start=date.min).points, external_flows(local))


class TestFlows:
    def test_the_ledger_holds_exactly_the_flows_the_cash_book_states(
        self, imported: Engine
    ) -> None:
        """M6a-6 against the real export: every boundary-crossing row, and not
        one of the internal transfers that look exactly like them."""
        count, total = subject.external_flow_total()
        assert count > 0, "the export states no external flow; the check would be vacuous"
        with Session(imported) as session:
            rows = session.exec(
                select(Transaction).where(
                    col(Transaction.txn_type).in_(sorted(EXTERNAL_FLOW_TYPES))
                )
            ).all()
        assert len(rows) == count
        assert sum(external_flows(imported).values(), D("0")) == total


class TestTheSeries:
    def test_measures_something(self, measured: PortfolioReturn) -> None:
        assert measured.links, "no link to measure; the checks below would be vacuous"

    def test_every_run_is_the_product_of_its_links(self, measured: PortfolioReturn) -> None:
        for run in measured.runs:
            growth = D("1")
            for step in run.links:
                assert step.daily_return is not None
                growth *= D("1") + step.daily_return
            assert growth - 1 == run.linked_return

    def test_a_window_figure_exists_exactly_when_there_is_no_gap(
        self, measured: PortfolioReturn
    ) -> None:
        assert (measured.linked_return is None) == (measured.gaps > 0)

    def test_a_large_flow_does_not_read_as_a_return(
        self, local: Engine, measured: PortfolioReturn
    ) -> None:
        """A flow at least half the portfolio it lands in. Left in the numerator
        it would read as a return of roughly its own relative size; handled
        correctly, the day's return is the market's. The days are picked from
        the data, and the check skips if the ledger has none."""
        closes = {point.on: point.value_base for point in value_series(local, start=date.min).points}
        large: list[tuple[date, Decimal, Decimal]] = []
        for step in measured.links:
            before = closes.get(step.since)
            if step.daily_return is None or before is None or before <= 0 or step.flow_base == 0:
                continue
            relative = abs(step.flow_base) / before
            if relative >= D("0.5"):
                large.append((step.on, step.daily_return, relative))
        if not large:
            pytest.skip("no flow in the ledger is at least half the portfolio it landed in")
        for on, daily, relative in large:
            assert abs(daily) < relative / 2, on


class TestTheBenchmark:
    def test_every_run_is_compared_over_its_own_span(
        self, local: Engine, measured: PortfolioReturn
    ) -> None:
        benchmarks = load_benchmarks(BENCHMARKS_CONFIG)
        if not benchmarks:
            pytest.skip("config/benchmarks.yaml configures no benchmark")
        with Session(local) as session:
            if not session.exec(select(BenchmarkDaily)).first():
                pytest.skip("benchmark cache is empty; run `python -m app.cli fetch-prices` first")
        comparison = compare_to_benchmark(
            local, measured.runs,
            window_return=measured.linked_return, benchmark_key=benchmarks[0].key,
        )
        assert [(row.start, row.end) for row in comparison.runs] == [
            (run.start, run.end) for run in measured.runs
        ]
        for row in comparison.runs:
            assert (row.excess is None) == (row.reason is not None)
```

- [ ] **Step 3: Run the acceptance suite**

```bash
cd backend && ./.venv/Scripts/python.exe -m pytest -q -m realdata tests/integration/test_realdata_performance.py -rs
```

Expected: PASS, with any skip carrying one of the reasons above. A failing `test_the_ledger_holds_exactly_the_flows_the_cash_book_states` is a classification defect — open a Jira `Bug` under PT-7 describing the shape (never the rows) before changing anything. If `-rs` reports `test_a_large_flow_does_not_read_as_a_return` skipped, say so in the task report: the check exists but this ledger did not exercise it.

- [ ] **Step 4: Recount, then update `docs/RUNBOOK.md`**

Run the full gate and note the three totals it prints. Then count the realdata skips an empty cache produces:

```bash
cd backend
./.venv/Scripts/python.exe -c "from app.db import create_engine_and_tables; create_engine_and_tables('sqlite:///./empty-cache.sqlite')"
DATABASE_URL=sqlite:///./empty-cache.sqlite ./.venv/Scripts/python.exe -m pytest -q -m realdata -rs | tail -40
rm empty-cache.sqlite
```

Edit `docs/RUNBOOK.md`:

1. Section 4: replace `635 tests`, `60 tests`, `93 Vitest tests` with the current totals. In the "A green `-m realdata` run is not the same as a complete one" note, replace the skip count, the total, the "`46 passed`" example and the "sixty passed" sentence with the recounted figures, and add that the performance acceptance tests are among the skips.
2. Section 5, the `api/` line: append `/api/benchmarks, /api/performance` to the endpoint list.
3. Section 5: `The eleven screens` → `The twelve screens`.
4. Section 5: `**Transactions**, **Lots**, **Positions** and **Instrument** read the live API.` → `**Transactions**, **Lots**, **Positions**, **Instrument** and **Performance** read the live API.` The count of modelled screens that follows stays seven.
5. Section 5, after the paragraph about **Instrument**, add:

```markdown
**Performance** is M6a's: the portfolio's time-weighted return, cash included, against
one benchmark, with both sides' dividend treatment stated beside the figure. It is a
different screen from **Benchmarks & Industry**, which stays `MODELLED` — contribution
to return and industry weight over time are M6b's, and the two coexist until then.
```

- [ ] **Step 5: Run the verification gate, then commit twice**

```bash
git add backend/tests/integration/realdata_subject.py backend/tests/integration/test_realdata_performance.py
git commit -m "$(cat <<'EOF'
test: prove portfolio return against the real export (PT-25)

Claude-Session: https://claude.ai/code/session_013U6MLH7npaNQwK1ZS8UFbS
EOF
)"
git add docs/RUNBOOK.md
git commit -m "$(cat <<'EOF'
docs: bring the runbook up to M6a (PT-25)

Claude-Session: https://claude.ai/code/session_013U6MLH7npaNQwK1ZS8UFbS
EOF
)"
```

---

## After Task 8

- [ ] Whole-branch review against the M6a design (`git diff master...HEAD`). Fix CRITICAL and HIGH findings on the branch, each with its own commit.
- [ ] Anything consciously left goes to Jira as a `Task` labelled `carried`, parented to PT-7 — described by shape, never by holding. Nothing goes into a follow-ups file.
- [ ] Merge to `master` with a merge commit only when the owner says so; then move PT-25 to `Done`.

## Self-review

**Spec coverage.**

| M6a design | Task |
|---|---|
| 1.1 external flows per day | 2 |
| 1.1 chain-linked daily series; window figure with coverage verdict | 3 |
| 1.1 benchmark comparison on that series | 4, 5 |
| 1.1 a performance screen | 6, 7 |
| 1.1 / 6 the two structural findings | not this plan's work: PT-27, PT-28; Task 5 asserts the derived list contains the new route |
| M6a-2 unadjusted lane | 3 (imports `valuation`; guard forbids `total_return`) |
| M6a-3 cash counts and contributes zero | 3 (trade tests through `rebuild()`) |
| M6a-4 daily boundaries | 3 |
| M6a-5 flows effective at the close | 3 (`test_a_deposit_earns_nothing_on_the_day_it_arrives`) |
| M6a-6 DEPOSIT and WITHDRAWAL only | 2, 3 (golden with and without sweeps), 8 |
| M6a-7 two links per gap; no figure spans one | 3, 4, 5, 7 |
| M6a-10 both sides labelled | 3, 4 (constants), 5 (wire), 7 (footer) |
| 3.4 series starts after the first `V > 0` | 3 (P-2), 5 (P-4) |
| 4 contiguous runs; `worst_coverage` over a run | 3 |
| 8 deposit into a flat market returns zero — first test | 3 Step 1, then again through `rebuild()` |
| 8 no flows collapses to the ratio | 3 |
| 8 scale invariance | 3 |
| 8 trade at the close with no fee | 3 |
| 8 trade away from the close | 3 |
| 8 a gap yields no figure | 3 |
| 8 sweeps are not flows | 3 |
| 8 the guard covers the new endpoint | 5 |
| 9 hands flows and the linked series to M6b | 2, 3 (public functions and types) |
| Out-of-scope table | nothing in any task computes per-instrument return, contribution, MWR/XIRR, industry weight or a euro P&L |

**Placeholder scan.** No "TBD", "similar to", or unshown code. Task 8 Step 4's counts are measured at run time because they cannot be known in advance, and the step gives the commands that measure them.

**Type consistency.** `portfolio_returns` → `PortfolioReturn.runs: tuple[ReturnRun, ...]` satisfies `MeasuredRun` (read-only `start`, `end`, `linked_return`) in `compare_to_benchmark(engine, runs, *, window_return, benchmark_key)`, called identically in Tasks 4, 5 and 8. `ReturnLink.daily_return` / `ReturnLinkOut.daily_return` / `ReturnLink.daily_return` in TypeScript agree; `PerformanceReport` mirrors `PerformanceOut` field for field; `get_benchmarks` is imported from `app.api.dependencies` in both routes.
