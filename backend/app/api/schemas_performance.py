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

    since: date
    date: date
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
