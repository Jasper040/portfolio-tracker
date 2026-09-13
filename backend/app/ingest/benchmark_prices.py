"""Filling `benchmark_daily`. The only writer of that table. M3 section 4.4.

Split out of `ingest/prices.py` in M6a's carried-cleanup pass (PT-33). The
benchmark phase was always self-contained -- its own report type, its own
newest-row query, its own upsert -- and sharing a module with the instrument
phase bought nothing while costing the reader 437 lines to hold at once.

**This module is on the fetched side of the determinism line**, exactly like its
former host: nothing in `rebuild/` or `analytics/` may write a row here, and
this module never writes a derived one.

**It writes both close columns, and that is legitimate.** `benchmark_daily`
stores the pair because the provider returns both in one response, and fetching
half of it now to re-fetch the other half later is a request no free provider has
reason to keep serving. Only the adjusted one is ever read, by
`analytics/benchmark_return.py`. Parent doc Sec 7.5's rule is about a READER
reaching both; a cache boundary that stores what it was given is the thing the
rule exists to make possible, which is why this path is named in
`EXEMPT_FILES` in `tests/integration/test_no_double_count.py` with its own
comment.

**It never raises for a benchmark the provider does not know.** Unlike an
unresolved instrument symbol, there is no question to put to a human that
`config/benchmarks.yaml` has not already asked -- the file is the answer, and the
symbol discriminator cannot run here because a benchmark has no executed prices
in the ledger to check a candidate against (M3 section 4.1). The caller decides
what a failure is worth.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import uuid4

from sqlalchemy import Engine
from sqlmodel import Session, select

from app.ingest.benchmarks import Benchmark
from app.models.market import BenchmarkDaily
from app.providers.base import PriceProvider, ProviderError


def _newest_benchmark(session: Session, key: str) -> date | None:
    rows = session.exec(select(BenchmarkDaily).where(BenchmarkDaily.key == key)).all()
    return max((row.price_date for row in rows), default=None)


@dataclass(frozen=True, slots=True)
class BenchmarkFetchReport:
    fetched: tuple[str, ...]
    #: (key, reason). A configured symbol that returns nothing is the operator's
    #: answer being wrong, so the reason names the symbol they wrote.
    failed: tuple[tuple[str, str], ...]
    #: Rows inserted PLUS rows updated, which is what `FetchResult.price_rows`
    #: has always meant. It counted inserts only until PT-33, so the two numbers
    #: read identically in CLI output while measuring different things: a second
    #: run over an unchanged series reported zero here and the full point count
    #: there, and neither line said which it was.
    rows_written: int


def fetch_benchmarks(
    engine: Engine,
    provider: PriceProvider,
    benchmarks: Sequence[Benchmark],
    *,
    full: bool = False,
) -> BenchmarkFetchReport:
    """Fill `benchmark_daily` from the configured proxies."""
    fetched: list[str] = []
    failed: list[tuple[str, str]] = []
    written = 0

    for bench in benchmarks:
        with Session(engine) as session:
            newest = _newest_benchmark(session, bench.key)

        try:
            # `series_since` subtracts its own revision overlap before asking the
            # provider -- pass the raw newest date, exactly as the instrument
            # phase does, or the overlap is silently doubled.
            series = (
                provider.full_series(bench.symbol)
                if full or newest is None
                else provider.series_since(bench.symbol, newest)
            )
        except ProviderError as exc:
            failed.append((bench.key, f"{bench.symbol}: {exc}"))
            continue

        if series is None or not series.points:
            failed.append(
                (
                    bench.key,
                    f"{bench.symbol}: the price provider returned no series. "
                    "config/benchmarks.yaml is taken as authoritative, so this is "
                    "the configured symbol being wrong rather than a question to "
                    "put to anyone.",
                )
            )
            continue

        quoted = series.currency.upper()
        if quoted != bench.currency.upper():
            # The configured currency was required from the operator and then
            # never read, which made it a field that could only ever be wrong in
            # silence. Checking it is the point of asking: a proxy quoted in a
            # currency the operator did not expect still converts, still rebases,
            # and still draws -- as a plausible line that answers a different
            # question from the one on the axis.
            failed.append(
                (
                    bench.key,
                    f"{bench.symbol}: configured currency {bench.currency.upper()} "
                    f"but the provider quotes it in {quoted}. Nothing was stored "
                    "for this proxy: a benchmark converted from the wrong currency "
                    "is a plausible wrong number, not a visible failure. Correct "
                    "config/benchmarks.yaml, or point the key at a listing that is "
                    "actually quoted the way it says.",
                )
            )
            continue

        with Session(engine) as session:
            # Upsert, exactly like `_store_prices`: a revised bar within the
            # overlap window must correct the stored row rather than being
            # silently discarded, or the overlap the provider pays for buys
            # nothing.
            existing = {
                row.price_date: row
                for row in session.exec(
                    select(BenchmarkDaily).where(BenchmarkDaily.key == bench.key)
                ).all()
            }
            fetched_at = datetime.now(tz=UTC)
            for point in series.points:
                row = existing.get(point.on)
                if row is None:
                    session.add(
                        BenchmarkDaily(
                            id=uuid4(),
                            key=bench.key,
                            price_date=point.on,
                            close_unadjusted=point.close_unadjusted,
                            close_adjusted=point.close_adjusted,
                            currency=quoted,
                            source=series.source,
                            fetched_at=fetched_at,
                        )
                    )
                else:
                    row.close_unadjusted = point.close_unadjusted
                    row.close_adjusted = point.close_adjusted
                    row.currency = quoted
                    row.source = series.source
                    row.fetched_at = fetched_at
                    session.add(row)
                # Inserted or updated, both count. See `rows_written`.
                written += 1
            session.commit()
        fetched.append(bench.key)

    return BenchmarkFetchReport(
        fetched=tuple(fetched), failed=tuple(failed), rows_written=written
    )
