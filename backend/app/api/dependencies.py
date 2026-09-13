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
