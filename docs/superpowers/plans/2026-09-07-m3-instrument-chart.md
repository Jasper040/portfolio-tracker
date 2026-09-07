# M3 — Instrument Chart, Benchmark and Holding Intervals — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One instrument on one screen — a real price line carrying buy and sell markers, shaded where a position was held, with a market benchmark rebased at each entry and excess return over the holding period only.

**Architecture:** M3 is the first feature that legitimately needs both price columns, so the work splits across two modules that each reach exactly one: `analytics/instrument_price.py` reads `close_unadjusted` (the drawn line, bands, markers, intervals) and `analytics/instrument_return.py` reads `close_adjusted` through `total_return.py` (both rebased indices and excess return). The route composes them and reads neither. Benchmarks get their own `benchmark_daily` cache keyed by a configuration slug rather than an ISIN, which is what lets `config/benchmarks.yaml` be tracked.

**Tech Stack:** Python 3.12+, SQLModel/SQLAlchemy, FastAPI, Typer, httpx, pytest; React + Vite + TypeScript + ECharts.

**Spec:** `docs/superpowers/specs/2026-09-07-m3-instrument-chart-design.md` (M3, approved), under `docs/superpowers/specs/2026-09-05-portfolio-tracker-design.md` (the parent). A `§` refers to the parent; "M3 section N" refers to the M3 spec. **Read both before Task 1.**

**Epic:** PT-4.

## Global Constraints

- **Money is `Decimal`, never `float`.** Everywhere, including tests, including prices, FX rates and index values. Strings over the wire.
- **`domain/` is pure.** No ORM import, no file I/O, no network, no `datetime.now()`. Every "as of" date is a parameter.
- **`providers/` is the only package that touches the network.**
- **Prices, FX and benchmarks are a cache, not a derived table.** `rebuild()` must never write, delete or read-then-rewrite `price_daily`, `fx_daily` or `benchmark_daily`. Only `fetch-prices` writes them.
- **The drawn line is `close_unadjusted`; the comparison is `close_adjusted`** (M3 section 3.1). No module may name both — `tests/integration/test_no_double_count.py` walks `app/` and will fail you.
- **Benchmark keys are slugs, never ISINs** (M3-2). An ISIN in a tracked file is a leak; `config/benchmarks.yaml` is tracked, so its keys must be structurally incapable of being one.
- **Both sides convert to base currency before rebasing** (M3-7).
- **Rebase at the start of each in-market interval, never at the window edge** (M3-6). A figure that changes when the reader changes the range control is not a figure.
- **A value that cannot be computed is `null` with a reason.** Never `0`, never an omitted key (§8.1).
- **Staleness threshold is 4 calendar days**; the symbol-validation band is ±30%; the cache backfill is a fixed five years. All unchanged from M2.
- **NO REAL HOLDINGS IN ANY TRACKED FILE** — no ISIN, instrument name, benchmark ticker, or amount at five significant digits. That includes this plan and every fixture. The `realdata` suite states no figures of its own; it derives them at run time from the gitignored export via `backend/tests/integration/realdata_subject.py`. `test_no_real_data_committed.py` scans every tracked file.
- **TDD strictly.** Write the test, run it, watch it fail *for the right reason*, then implement.
- **Type hints on every function.** Files 200–400 lines typical, 800 maximum.
- **Do not implement:** sector/industry classification and the industry-proxy overlay (M6), the counterfactual column (M4), dividend analytics (M5), TWR/MWR/attribution (M6), news (M8).

## The verification gate

All six commands, green, before **every** commit. A red suite is a stop-and-fix, not a note-and-continue.

```bash
cd backend && python -m pytest -q && python -m pytest -q -m realdata \
  && python -m ruff check . && python -m mypy app
cd ../frontend && npx tsc --noEmit && npx vitest run
```

Baseline at the start of M3: **554 backend tests, 53 opt-in `realdata` (9 of which skip until the cache is filled), 124 frontend**, ruff and `mypy --strict` clean. Every task adds tests; none may remove or weaken one.

## Environment notes that will bite

- **There is exactly one backend virtualenv and it is `backend/.venv`.** A `.venv` at the repo root is not used by anything; a shell that activates it has no `fastapi`, no `uvicorn` and no `app` package, and every command below fails with `No module named …`, which reads as a broken backend rather than the wrong environment. `python -c "import sys; print(sys.prefix)"` must end in `\backend\.venv`. See `docs/RUNBOOK.md` section 1.
- Dependencies are managed with **uv** against a committed `backend/uv.lock`: `uv sync --extra dev` from `backend/`. Do not add a dependency by hand-editing the venv — add it to `pyproject.toml` and re-lock, as its own commit. M3 adds **no new dependency**, backend or frontend; ECharts is already installed.
- Every command in this plan is written as `./.venv/Scripts/python.exe -m …` so it works whether or not a venv is activated. `uv run` is equivalent.
- Do **not** create a directory named `data` anywhere new. `.gitignore` carries a bare `data/` rule that swallows any directory of that name at any depth. (Its second failure mode, breaking setuptools' flat-layout discovery during an editable install, is now fixed by the explicit `[tool.setuptools.packages.find]` table in `pyproject.toml` — do not remove it.)
- There are no migrations — `SQLModel.metadata.create_all` is the whole schema story. Adding a **table** is safe and picked up automatically. Adding a **column to an existing table** strands every local sqlite file, which must then be deleted and re-imported. Task 1 adds only a table; keep it that way.
- `sqlite:///` needs a Windows path (`C:/...`), not an MSYS `/c/...` path.
- Git Bash heredocs mangle large Python and JSX payloads. Use the Write tool for anything longer than a few lines.
- Frontend component tests run in jsdom via a `@vitest-environment jsdom` docblock; the default environment is `node`.
- The first `vitest run` after a cold checkout can time out a `userEvent` test at the 5s default while Vite transforms. Re-run before investigating.

## File structure

| File | Responsibility |
|---|---|
| `backend/app/models/market.py` | *modified* — gains `BenchmarkDaily` |
| `backend/app/ingest/benchmarks.py` | *new* — loads and validates `config/benchmarks.yaml` |
| `backend/app/ingest/prices.py` | *modified* — gains the benchmark fetch phase |
| `backend/app/analytics/quotes.py` | *new* — FX, coverage and staleness. Names **neither** close column |
| `backend/app/analytics/prices.py` | *new* — the unadjusted price readers. Names `close_unadjusted` only |
| `backend/app/analytics/valuation.py` | *modified* — keeps `value_series` only |
| `backend/app/analytics/positions_snapshot.py` | *new* — `current_positions`, moved |
| `backend/app/analytics/total_return.py` | *modified* — carries currency, adds the benchmark reader |
| `backend/app/analytics/instrument_price.py` | *new* — line, bands, markers, intervals |
| `backend/app/analytics/instrument_return.py` | *new* — rebasing and excess return |
| `backend/app/api/routes_instrument.py` | *new* — composes the two, reads neither column |
| `frontend/src/lib/instrument.ts` | *new* — the pure ECharts option builder |
| `frontend/src/screens/Instrument.tsx` | *new* — the live screen |

Tasks 1–3 fill the cache and need no analytics. Task 2 is a pure refactor that changes no number. Tasks 4–6 are pure-ish analytics over a populated cache. Task 7 makes it readable, 8–9 visible, 10 proves it against the real export. Stopping after Task 3 leaves the repo green with a benchmark cache and no UI change; stopping after Task 7 leaves a working API. Both are coherent.

---

### Task 1: `benchmark_daily`, and a key that cannot be an ISIN

**Files:**
- Modify: `backend/app/models/market.py` (append after `FxDaily`)
- Create: `backend/app/ingest/benchmarks.py`
- Create: `config/benchmarks.yaml`
- Modify: `backend/app/settings.py:39` (add `benchmarks_path` beside `instrument_symbols_path`)
- Test: `backend/tests/unit/test_benchmarks_config.py`
- Test: `backend/tests/unit/test_market_models.py` (append)

**Interfaces:**
- Consumes: `DecimalString` from `app.models.types`; `Settings` from `app.settings`.
- Produces:
  - `BenchmarkDaily` (SQLModel table `benchmark_daily`), columns `id, key, price_date, close_unadjusted, close_adjusted, currency, source, fetched_at`, unique on `(key, price_date)`.
  - `Benchmark` frozen dataclass: `key: str`, `symbol: str`, `currency: str`, `name: str`, `ter: Decimal`.
  - `load_benchmarks(path: Path) -> tuple[Benchmark, ...]`
  - `BenchmarkConfigError(ValueError)`
  - `Settings.benchmarks_path: str`

- [ ] **Step 1: Write the failing config tests**

Create `backend/tests/unit/test_benchmarks_config.py`:

```python
"""The benchmark set is an answer, not a lookup (M3 section 4.3).

The ISIN-shaped-key test is the one that matters. `config/benchmarks.yaml` is
TRACKED, which is only safe because a key cannot identify a holding. That is a
structural claim, so it gets a structural check rather than a comment asking
people to be careful.
"""

from __future__ import annotations

from decimal import Decimal as D
from pathlib import Path

import pytest

from app.ingest.benchmarks import Benchmark, BenchmarkConfigError, load_benchmarks


def write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "benchmarks.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_loads_a_well_formed_entry(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """
        world:
          symbol: AAA.XX
          currency: EUR
          name: A broad world proxy
          ter: "0.20"
        """,
    )
    assert load_benchmarks(path) == (
        Benchmark(key="world", symbol="AAA.XX", currency="EUR",
                  name="A broad world proxy", ter=D("0.20")),
    )


def test_a_missing_file_is_an_empty_set_not_an_error(tmp_path: Path) -> None:
    """No benchmarks configured is a legitimate state -- the overlay is absent
    and the chart still draws. An exception here would make `fetch-prices`
    unusable for anyone who has not written the file yet."""
    assert load_benchmarks(tmp_path / "absent.yaml") == ()


def test_an_isin_shaped_key_is_refused(tmp_path: Path) -> None:
    """The whole reason this file is tracked. See the module docstring."""
    path = write(
        tmp_path,
        """
        NL0000000001:
          symbol: AAA.XX
          currency: EUR
          name: Nope
          ter: "0.20"
        """,
    )
    with pytest.raises(BenchmarkConfigError, match="looks like an ISIN"):
        load_benchmarks(path)


def test_a_key_outside_the_slug_alphabet_is_refused(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """
        Not A Slug:
          symbol: AAA.XX
          currency: EUR
          name: Nope
          ter: "0.20"
        """,
    )
    with pytest.raises(BenchmarkConfigError, match="slug"):
        load_benchmarks(path)


def test_a_missing_field_names_the_field_and_the_key(tmp_path: Path) -> None:
    path = write(tmp_path, "world:\n  symbol: AAA.XX\n")
    with pytest.raises(BenchmarkConfigError, match="world.*currency"):
        load_benchmarks(path)


def test_the_ter_is_a_decimal_never_a_float(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """
        world:
          symbol: AAA.XX
          currency: EUR
          name: A broad world proxy
          ter: 0.07
        """,
    )
    (bench,) = load_benchmarks(path)
    assert bench.ter == D("0.07")
    assert isinstance(bench.ter, D)
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && ./.venv/Scripts/python.exe -m pytest tests/unit/test_benchmarks_config.py -q
```

Expected: collection error — `ModuleNotFoundError: No module named 'app.ingest.benchmarks'`.

- [ ] **Step 3: Write `backend/app/ingest/benchmarks.py`**

```python
"""The benchmark set: which proxies to fetch, and what they cost to hold.

M3 section 4.3. This file is the human's answer, on the same terms as
`instrument_symbols.yaml` -- the symbol discriminator does not run here and
cannot, because a benchmark has no executed prices in the ledger to be checked
against (M3 section 4.1). Nothing re-derives what is written here.

The difference, and the reason this one is TRACKED while every other config
file in `config/` is gitignored, is that a key here names no holding. That is
enforced rather than asked for: `_check_key` refuses an ISIN-shaped key
outright. Committing a benchmark set is only safe while that holds, so it is a
test rather than a comment.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

import yaml

#: Lowercase slug. Deliberately narrow: it has to be incapable of expressing an
#: ISIN, and "as narrow as the job allows" is the cheapest way to be sure.
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*$")

#: Two letters, nine alphanumerics, one check digit.
_ISIN = re.compile(r"^[A-Za-z]{2}[A-Za-z0-9]{9}[0-9]$")

_REQUIRED = ("symbol", "currency", "name", "ter")


class BenchmarkConfigError(ValueError):
    """The benchmark file says something that cannot be acted on."""


@dataclass(frozen=True, slots=True)
class Benchmark:
    key: str
    symbol: str
    currency: str
    name: str
    #: Total expense ratio, as a percentage per year. Documented rather than
    #: applied: parent doc Sec 7.6 requires proxy drag be visible, and adjusting
    #: for it would invent a series nobody published.
    ter: Decimal


def _check_key(key: str) -> None:
    if _ISIN.match(key):
        raise BenchmarkConfigError(
            f"benchmark key {key!r} looks like an ISIN. Keys are slugs precisely so this "
            "file can be committed; an ISIN in a tracked file is a holding, which "
            "test_no_real_data_committed treats as a leak."
        )
    if not _SLUG.match(key):
        raise BenchmarkConfigError(
            f"benchmark key {key!r} is not a slug: lowercase letters, digits and hyphens."
        )


def load_benchmarks(path: Path) -> tuple[Benchmark, ...]:
    """Read the configured benchmark set. A missing file is an empty set."""
    if not path.exists():
        return ()

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise BenchmarkConfigError(f"{path} must be a mapping of key to benchmark.")

    out: list[Benchmark] = []
    for key, body in raw.items():
        key = str(key)
        _check_key(key)
        if not isinstance(body, dict):
            raise BenchmarkConfigError(f"benchmark {key!r} must be a mapping.")
        for field in _REQUIRED:
            if body.get(field) in (None, ""):
                raise BenchmarkConfigError(f"benchmark {key!r} is missing {field!r}.")
        try:
            # str() first: PyYAML parses an unquoted 0.07 as a float, and a float
            # is exactly what this codebase never lets near a number it reports.
            ter = Decimal(str(body["ter"]))
        except InvalidOperation as exc:
            raise BenchmarkConfigError(
                f"benchmark {key!r} has a ter that is not a number: {body['ter']!r}"
            ) from exc
        out.append(
            Benchmark(
                key=key,
                symbol=str(body["symbol"]),
                currency=str(body["currency"]).upper(),
                name=str(body["name"]),
                ter=ter,
            )
        )
    return tuple(out)
```

- [ ] **Step 4: Run the config tests to verify they pass**

```bash
cd backend && ./.venv/Scripts/python.exe -m pytest tests/unit/test_benchmarks_config.py -q
```

Expected: 6 passed.

- [ ] **Step 5: Write the failing model test**

Append to `backend/tests/unit/test_market_models.py`:

```python
def test_benchmark_daily_is_unique_on_key_and_date() -> None:
    """One row per benchmark per day. A second row for the same day would make
    the series depend on insertion order, which is the bug the constraint on
    `price_daily` already prevents for instruments."""
    from app.models.market import BenchmarkDaily

    constraint = next(
        arg for arg in BenchmarkDaily.__table_args__ if hasattr(arg, "columns")
    )
    assert {column.name for column in constraint.columns} == {"key", "price_date"}


def test_benchmark_daily_has_no_isin_column() -> None:
    """M3 section 4.1: the key is a slug so the config file can be committed.
    An `isin` column here would invite exactly the row this design excludes."""
    from app.models.market import BenchmarkDaily

    assert "isin" not in BenchmarkDaily.model_fields
```

- [ ] **Step 6: Run it to verify it fails**

```bash
cd backend && ./.venv/Scripts/python.exe -m pytest tests/unit/test_market_models.py -q
```

Expected: FAIL with `ImportError: cannot import name 'BenchmarkDaily'`.

- [ ] **Step 7: Add the model**

Append to `backend/app/models/market.py`, after `FxDaily`:

```python
class BenchmarkDaily(SQLModel, table=True):
    """One benchmark proxy's close on one day.

    Deliberately the same shape as `PriceDaily` minus the ISIN, and on the same
    (fetched) side of the determinism line: `rebuild()` must never write here.

    The key is a configuration slug -- `world`, not an ISIN -- for the three
    reasons M3 section 4.1 gives, of which the operative one is that
    `config/benchmarks.yaml` is tracked and an ISIN in a tracked file is a
    holding. `ingest/benchmarks.py` refuses an ISIN-shaped key so that stays
    true.

    Both closes are stored though M3 reads only the adjusted one: the provider
    returns both in one response, and fetching half of it now to re-fetch the
    other half later is a request no free provider has reason to keep serving.
    """

    __tablename__ = "benchmark_daily"
    __table_args__ = (
        UniqueConstraint("key", "price_date", name="uq_benchmark_daily_key_date"),
    )

    id: UUID = Field(primary_key=True)
    key: str = Field(index=True)
    price_date: date = Field(index=True)
    close_unadjusted: Decimal = Field(sa_column=Column(DecimalString(), nullable=False))
    close_adjusted: Decimal = Field(sa_column=Column(DecimalString(), nullable=False))
    currency: str
    source: str
    fetched_at: datetime
```

- [ ] **Step 8: Add the settings field**

In `backend/app/settings.py`, after `manual_prices_path`:

```python
    #: The configured benchmark set (M3 section 4.3). Unlike its neighbours this
    #: file is TRACKED: its keys are slugs, so it names no holding. Absolute for
    #: the same reason the others are -- the CLI runs from wherever the operator
    #: happens to be.
    benchmarks_path: str = str(_REPO_ROOT / "config" / "benchmarks.yaml")
```

- [ ] **Step 9: Write `config/benchmarks.yaml`**

Tracked. Ship it with the file's own documentation and **no entries** — the operator chooses proxies, and inventing a default would put a real ticker in a tracked file for no reason.

```yaml
# The benchmark set (M3 spec section 4.3). TRACKED, unlike every other config
# file here, because a key below is a slug and names no holding of yours.
#
# This file is an ANSWER, not a lookup. The symbol discriminator that guards
# `instrument_symbols.yaml` cannot run here: it validates a candidate series
# against the ledger's own executed prices, and a benchmark was never traded,
# so there is nothing to check it against. Whatever symbol you write is used.
#
# Keys must be lowercase slugs. An ISIN-shaped key is refused at load: an ISIN
# in a tracked file is a holding, and this file is tracked.
#
# `ter` is the proxy's total expense ratio in percent per year. It is reported
# beside the comparison, never subtracted from it -- adjusting would invent a
# series nobody published. A holding that beats its proxy by less than the TER
# has not necessarily beaten the index the proxy tracks.
#
# world:
#   symbol: <the symbol your price provider knows>
#   currency: EUR
#   name: <a short label you will recognise on the chart legend>
#   ter: "0.20"
```

- [ ] **Step 10: Run the full gate**

```bash
cd backend && ./.venv/Scripts/python.exe -m pytest -q \
  && ./.venv/Scripts/python.exe -m ruff check . \
  && ./.venv/Scripts/python.exe -m mypy app
```

Expected: 562 passed (554 + 8), ruff clean, mypy clean.

- [ ] **Step 11: Commit**

```bash
git add backend/app/models/market.py backend/app/ingest/benchmarks.py \
        backend/app/settings.py config/benchmarks.yaml \
        backend/tests/unit/test_benchmarks_config.py backend/tests/unit/test_market_models.py
git commit -m "feat(benchmarks): a slug-keyed cache and a config file that can be committed

An ISIN-shaped key is refused at load rather than discouraged in a
comment. That refusal is the whole reason config/benchmarks.yaml is
tracked while every other file in config/ is ignored.

Claude-Session: https://claude.ai/code/session_01DbQAgwhLhhRC8VsEkVnz3f"
```

---

### Task 2: Split `analytics/valuation.py`, and re-point the guard

A pure refactor. **No number changes**, and the existing suite is the evidence — if a valuation test fails, the move is wrong, not the test.

**Files:**
- Create: `backend/app/analytics/quotes.py`
- Create: `backend/app/analytics/prices.py`
- Create: `backend/app/analytics/positions_snapshot.py`
- Modify: `backend/app/analytics/valuation.py`
- Modify: `backend/app/api/routes_positions.py` (import moves)
- Modify: `backend/tests/integration/test_no_double_count.py:220`
- Test: `backend/tests/integration/test_valuation.py` (unchanged — it is the proof)

**Interfaces:**
- Produces, in `app.analytics.quotes` (names **neither** close column):
  - `Quote` frozen dataclass: `close: Decimal`, `currency: str`, `on: date`, `source: str`
  - `base_currency(session: Session) -> str`
  - `rate_history(session: Session) -> dict[tuple[str, str], list[FxDaily]]`
  - `latest_rate_on_or_before(rows: Sequence[FxDaily], on: date) -> FxDaily | None`
  - `in_base(quote: Quote, quantity: Decimal, on: date, base: str, rates: Mapping[tuple[str, str], list[FxDaily]]) -> tuple[Decimal, int] | None`
  - `classify(source: str, age: int) -> Coverage`
  - `worst_coverage(values: Sequence[Coverage]) -> Coverage`
  - the `FULL`/`PARTIAL`/`MANUAL`/`MISSING`/`STALE_DAYS`/`MANUAL_SOURCE` constants
- Produces, in `app.analytics.prices` (names `close_unadjusted` **only**):
  - `price_history(session: Session) -> dict[str, list[PriceDaily]]`
  - `latest_on_or_before(rows: Sequence[PriceDaily], on: date) -> PriceDaily | None`
  - `quote_for(isin: str, on: date, history: Mapping[str, list[PriceDaily]]) -> Quote | None`
- Produces, in `app.analytics.positions_snapshot`: `current_positions(engine: Engine, method: LotMethod) -> PositionsSnapshot`, plus the `PositionValue` and `PositionsSnapshot` dataclasses.
- `app.analytics.valuation` keeps `value_series`, `_value_day`, `ValuationPoint`, `ValuationSeries`.

- [ ] **Step 1: Move the FX, coverage and staleness helpers into `quotes.py`**

Cut from `valuation.py` and paste into a new `backend/app/analytics/quotes.py`, dropping the leading underscore on each: `_Quote` → `Quote`, `_base_currency` → `base_currency`, `_rate_history` → `rate_history`, `_latest_rate_on_or_before` → `latest_rate_on_or_before`, `_in_base` → `in_base`, `_classify` → `classify`. Move `worst_coverage` and the coverage constants too. Head the file:

```python
"""Currency, staleness and coverage -- the machinery that is the same whichever
close you are reading.

This file names NEITHER close column, and that is the point rather than an
accident. `analytics/instrument_return.py` needs the FX conversion in here to
satisfy M3-7, and if the unadjusted-close readers lived here too it would
acquire a call path to the unadjusted close by importing FX -- quietly
defeating the guard in M3 section 3.2 through the back door. The readers live
next door in `prices.py` for exactly that reason. Do not merge these two files.

`Quote.close` is deliberately not named `close_unadjusted`: it holds whichever
close its caller read, which is what lets `in_base` serve both sides.
"""
```

Change `classify`'s return type from `str` to `Coverage` (imported from `app.api.schemas`, as `worst_coverage` already implies), and the same for `worst_coverage` — the carried M2 finding, fixed here.

- [ ] **Step 2: Move the unadjusted readers into `prices.py`**

New `backend/app/analytics/prices.py` holding `price_history`, `latest_on_or_before` and `quote_for`, importing `Quote` from `quotes.py`. Head the file:

```python
"""Reading the unadjusted close out of the price cache.

The ONLY analytics module that names `close_unadjusted`, which is half of what
parent doc Sec 7.5 requires; `total_return.py` is the other half. Split out of
`valuation.py` in M3 so the instrument chart's price side and its return side
could each reach exactly one column -- see `quotes.py` for why the FX helpers
are not in here.
"""
```

- [ ] **Step 3: Move `current_positions` into `positions_snapshot.py`**

Take `current_positions`, `PositionValue` and `PositionsSnapshot` with it. Change the `coverage` field on both dataclasses from `str` to `Coverage` — the second half of the carried M2 finding.

- [ ] **Step 4: Update `valuation.py` and the importers**

`valuation.py` keeps `value_series`, `_value_day`, `ValuationPoint` and `ValuationSeries`, importing what it needs from `quotes` and `prices`. Change `ValuationPoint.coverage` and `ValuationSeries.coverage` from `str` to `Coverage`. Repoint `backend/app/api/routes_positions.py` at `app.analytics.positions_snapshot`.

- [ ] **Step 5: Run the existing suite — it is the proof the refactor changed nothing**

```bash
cd backend && ./.venv/Scripts/python.exe -m pytest -q
```

Expected: 562 passed. `test_no_double_count.py::test_valuation_reads_only_the_unadjusted_close` **fails** — that is correct and Step 6 fixes it. Every other test must pass untouched; if a valuation number moved, revert and redo the move.

- [ ] **Step 6: Re-point the guard's assertion**

In `backend/tests/integration/test_no_double_count.py`, replace `test_valuation_reads_only_the_unadjusted_close`:

```python
def test_the_price_reader_reads_only_the_unadjusted_close() -> None:
    """Was `test_valuation_reads_only_the_unadjusted_close` until M3 split
    `valuation.py`. Re-pointed rather than deleted, and deliberately so: after
    the split `valuation.py` names neither column, so the old assertion would
    still have passed -- vacuously, having quietly lost the half that checks the
    column is present SOMEWHERE. An assertion that passes for a new reason is
    not the same assertion."""
    names = _identifiers(APP / "analytics" / "prices.py")
    assert UNADJUSTED in names
    assert ADJUSTED not in names


def test_valuation_no_longer_names_a_close_column_directly() -> None:
    """It reads through `prices.py` now. Asserted so a future edit that inlines
    a column read back into `valuation.py` has to argue with a test."""
    names = _identifiers(APP / "analytics" / "valuation.py")
    assert UNADJUSTED not in names
    assert ADJUSTED not in names
```

Update `test_the_read_side_modules_exist_so_this_is_not_vacuous` to require `prices.py` alongside `valuation.py` and `total_return.py`.

- [ ] **Step 7: Run the full gate**

```bash
cd backend && ./.venv/Scripts/python.exe -m pytest -q \
  && ./.venv/Scripts/python.exe -m pytest -q -m realdata \
  && ./.venv/Scripts/python.exe -m ruff check . \
  && ./.venv/Scripts/python.exe -m mypy app
```

Expected: 563 passed; 44 passed / 9 skipped; ruff clean; mypy clean. **`mypy --strict` is the real check on Steps 1–4** — the `str` → `Coverage` change is only worth making if it now enforces the four-value invariant, and mypy is what says so.

- [ ] **Step 8: Commit**

```bash
git add backend/app/analytics backend/app/api/routes_positions.py \
        backend/tests/integration/test_no_double_count.py
git commit -m "refactor(analytics): split valuation.py four ways, coverage becomes a Literal

Both findings M2 carried, fixed in the milestone that had to open the
file. The cut between quotes.py and prices.py is load-bearing rather
than cosmetic: it is what stops the instrument chart's return side
acquiring a call path to the unadjusted close by importing FX.

No number changes; the untouched valuation suite is the evidence.

Claude-Session: https://claude.ai/code/session_01DbQAgwhLhhRC8VsEkVnz3f"
```

---

### Task 3: The benchmark fetch phase

**Files:**
- Modify: `backend/app/ingest/prices.py` (append a phase; do not restructure the existing ones)
- Modify: `backend/app/cli.py:319` (`fetch_prices_command`)
- Test: `backend/tests/integration/test_fetch_benchmarks.py`

**Interfaces:**
- Consumes: `Benchmark`, `load_benchmarks` (Task 1); `BenchmarkDaily` (Task 1); `PriceProvider`, `ProviderError` from `app.providers.base`.
- Produces:
  - `BenchmarkFetchReport` frozen dataclass: `fetched: tuple[str, ...]`, `failed: tuple[tuple[str, str], ...]` (key, reason), `rows_written: int`
  - `fetch_benchmarks(engine: Engine, provider: PriceProvider, benchmarks: Sequence[Benchmark], *, full: bool = False, today: date) -> BenchmarkFetchReport`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/integration/test_fetch_benchmarks.py`:

```python
"""The benchmark phase (M3 section 4.4).

Two behaviours differ from the instrument phase and both are here: a symbol
that returns nothing is a hard error naming the slug rather than a quarantine
entry, and a benchmark failure does not block the instrument phase.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal as D

from sqlmodel import Session, select

from app.ingest.benchmarks import Benchmark
from app.ingest.prices import fetch_benchmarks
from app.models.market import BenchmarkDaily
from app.providers.base import PricePoint, PriceSeries

TODAY = date(2026, 9, 7)

WORLD = Benchmark(key="world", symbol="AAA.XX", currency="EUR",
                  name="A proxy", ter=D("0.20"))


class FakeProvider:
    """Returns a fixed two-day series for one symbol and nothing for anything
    else. A `None` return is how the real provider reports "no such symbol"."""

    def __init__(self, known: dict[str, PriceSeries] | None = None) -> None:
        self.known = known or {}
        self.calls: list[tuple[str, date | None]] = []

    def full_series(self, symbol: str) -> PriceSeries | None:
        self.calls.append((symbol, None))
        return self.known.get(symbol)

    def series_since(self, symbol: str, since: date) -> PriceSeries | None:
        self.calls.append((symbol, since))
        return self.known.get(symbol)


def series(*days: tuple[date, str, str]) -> PriceSeries:
    return PriceSeries(
        symbol="AAA.XX",
        currency="EUR",
        points=tuple(
            PricePoint(on=on, close_unadjusted=D(u), close_adjusted=D(a))
            for on, u, a in days
        ),
    )


TWO_DAYS = (
    (date(2026, 9, 3), "100.00", "98.00"),
    (date(2026, 9, 4), "101.00", "99.00"),
)


def test_writes_a_row_per_day(engine) -> None:
    provider = FakeProvider({"AAA.XX": series(*TWO_DAYS)})
    report = fetch_benchmarks(engine, provider, [WORLD], today=TODAY)

    assert report.fetched == ("world",)
    assert report.failed == ()
    with Session(engine) as session:
        rows = sorted(session.exec(select(BenchmarkDaily)).all(),
                      key=lambda r: r.price_date)
    assert [r.price_date for r in rows] == [date(2026, 9, 3), date(2026, 9, 4)]
    assert [r.key for r in rows] == ["world", "world"]
    assert rows[0].close_adjusted == D("98.00")


def test_an_unknown_symbol_is_a_named_failure_not_an_exception(engine) -> None:
    """M3 section 4.4: the config is the answer, so the only useful report is
    that the answer is wrong. Naming the slug is the whole value."""
    report = fetch_benchmarks(engine, FakeProvider(), [WORLD], today=TODAY)

    assert report.fetched == ()
    assert len(report.failed) == 1
    key, reason = report.failed[0]
    assert key == "world"
    assert "AAA.XX" in reason


def test_a_second_run_writes_nothing_new(engine) -> None:
    """Idempotent, like the instrument phase. The unique constraint would raise
    on a re-insert, so this failing looks like a crash rather than a duplicate."""
    provider = FakeProvider({"AAA.XX": series(*TWO_DAYS)})
    fetch_benchmarks(engine, provider, [WORLD], today=TODAY)
    second = fetch_benchmarks(engine, provider, [WORLD], today=TODAY)

    assert second.rows_written == 0
    with Session(engine) as session:
        assert len(session.exec(select(BenchmarkDaily)).all()) == 2


def test_the_second_run_is_incremental(engine) -> None:
    provider = FakeProvider({"AAA.XX": series(*TWO_DAYS)})
    fetch_benchmarks(engine, provider, [WORLD], today=TODAY)
    provider.calls.clear()
    fetch_benchmarks(engine, provider, [WORLD], today=TODAY)

    assert provider.calls == [("AAA.XX", date(2026, 9, 4))]


def test_full_refetches_the_whole_window(engine) -> None:
    provider = FakeProvider({"AAA.XX": series(*TWO_DAYS)})
    fetch_benchmarks(engine, provider, [WORLD], today=TODAY)
    provider.calls.clear()
    fetch_benchmarks(engine, provider, [WORLD], full=True, today=TODAY)

    assert provider.calls == [("AAA.XX", None)]


def test_one_failing_benchmark_does_not_stop_the_next(engine) -> None:
    other = Benchmark(key="europe", symbol="BBB.XX", currency="EUR",
                      name="Another", ter=D("0.12"))
    provider = FakeProvider({"BBB.XX": series(*TWO_DAYS)})
    report = fetch_benchmarks(engine, provider, [WORLD, other], today=TODAY)

    assert report.fetched == ("europe",)
    assert [key for key, _ in report.failed] == ["world"]


def test_no_benchmarks_configured_is_not_an_error(engine) -> None:
    report = fetch_benchmarks(engine, FakeProvider(), [], today=TODAY)
    assert report == BenchmarkFetchReport(fetched=(), failed=(), rows_written=0)
```

Add the import of `BenchmarkFetchReport` at the top alongside `fetch_benchmarks`. The `engine` fixture already exists in `backend/tests/integration/` — reuse it rather than building one.

- [ ] **Step 2: Run to verify it fails**

```bash
cd backend && ./.venv/Scripts/python.exe -m pytest tests/integration/test_fetch_benchmarks.py -q
```

Expected: `ImportError: cannot import name 'fetch_benchmarks'`.

- [ ] **Step 3: Implement `fetch_benchmarks` in `backend/app/ingest/prices.py`**

Append; leave the existing phases alone. Reuse the module's existing five-year constant and its newest-cached-date helper rather than recomputing either.

```python
@dataclass(frozen=True, slots=True)
class BenchmarkFetchReport:
    fetched: tuple[str, ...]
    #: (key, reason). A configured symbol that returns nothing is the operator's
    #: answer being wrong, so the reason names the symbol they wrote.
    failed: tuple[tuple[str, str], ...]
    rows_written: int


def fetch_benchmarks(
    engine: Engine,
    provider: PriceProvider,
    benchmarks: Sequence[Benchmark],
    *,
    full: bool = False,
    today: date,
) -> BenchmarkFetchReport:
    """Fill `benchmark_daily`. M3 section 4.4.

    Never raises for a benchmark the provider does not know: unlike an
    unresolved instrument symbol, there is no question to put to a human that
    `config/benchmarks.yaml` has not already asked. The caller decides what a
    failure is worth.
    """
    fetched: list[str] = []
    failed: list[tuple[str, str]] = []
    written = 0

    for bench in benchmarks:
        with Session(engine) as session:
            newest = session.exec(
                select(BenchmarkDaily.price_date)
                .where(BenchmarkDaily.key == bench.key)
                .order_by(BenchmarkDaily.price_date.desc())  # type: ignore[attr-defined]
            ).first()

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

        with Session(engine) as session:
            have = {
                row_date
                for row_date in session.exec(
                    select(BenchmarkDaily.price_date).where(
                        BenchmarkDaily.key == bench.key
                    )
                ).all()
            }
            for point in series.points:
                if point.on in have:
                    continue
                session.add(
                    BenchmarkDaily(
                        id=uuid4(),
                        key=bench.key,
                        price_date=point.on,
                        close_unadjusted=point.close_unadjusted,
                        close_adjusted=point.close_adjusted,
                        currency=series.currency.upper(),
                        source=provider.__class__.__name__,
                        fetched_at=datetime.now(tz=UTC),
                    )
                )
                written += 1
            session.commit()
        fetched.append(bench.key)

    return BenchmarkFetchReport(
        fetched=tuple(fetched), failed=tuple(failed), rows_written=written
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd backend && ./.venv/Scripts/python.exe -m pytest tests/integration/test_fetch_benchmarks.py -q
```

Expected: 7 passed.

- [ ] **Step 5: Wire it into the CLI**

In `backend/app/cli.py`, at the end of `fetch_prices_command` — **after** the price and FX phases, so a benchmark failure cannot cost the instrument phase its work:

```python
    benchmarks = load_benchmarks(Path(settings.benchmarks_path))
    bench_report = fetch_benchmarks(engine, price_provider, benchmarks, full=full, today=date.today())
    if benchmarks:
        typer.echo(
            f"Benchmarks: {len(bench_report.fetched)} fetched, "
            f"{bench_report.rows_written} rows written."
        )
    for key, reason in bench_report.failed:
        # Not a quarantine entry: the file already asked the question. See
        # M3 section 4.4.
        typer.echo(f"  benchmark {key!r} failed -- {reason}", err=True)
    if bench_report.failed:
        raise typer.Exit(code=1)
```

- [ ] **Step 6: Run the full gate and commit**

```bash
cd backend && ./.venv/Scripts/python.exe -m pytest -q \
  && ./.venv/Scripts/python.exe -m ruff check . && ./.venv/Scripts/python.exe -m mypy app
```

Expected: 570 passed.

```bash
git add backend/app/ingest/prices.py backend/app/cli.py backend/tests/integration/test_fetch_benchmarks.py
git commit -m "feat(benchmarks): fetch-prices gains a benchmark phase

Runs last, so a wrong symbol in benchmarks.yaml cannot cost the
instrument phase its five-year backfill. A benchmark the provider does
not know is a named failure rather than a quarantine entry: the config
file already asked the question a quarantine exists to ask.

Claude-Session: https://claude.ai/code/session_01DbQAgwhLhhRC8VsEkVnz3f"
```

---

### Task 4: `analytics/instrument_price.py` — line, bands, markers, intervals

**Files:**
- Create: `backend/app/analytics/instrument_price.py`
- Test: `backend/tests/integration/test_instrument_price.py`

**Interfaces:**
- Consumes: `price_history`, `quote_for` from `app.analytics.prices`; `base_currency`, `rate_history`, `in_base`, `classify`, `worst_coverage`, `Quote` from `app.analytics.quotes`; `PositionDaily`, `Transaction` from `app.models.ledger`; `weekdays` from `app.domain.positions`.
- Produces:
  - `InstrumentPricePoint`: `on: date`, `close_base: Decimal | None`, `coverage: Coverage`, `held: bool`
  - `Interval`: `start: date`, `end: date`, `in_market: bool`, `price_return: Decimal | None`
  - `Marker`: `on: date`, `side: str`, `quantity: Decimal`, `price: Decimal`, `fees: Decimal`, `position_after: Decimal`
  - `InstrumentPriceView`: `isin`, `points`, `intervals`, `markers`, `coverage`
  - `instrument_price_view(engine: Engine, isin: str, *, start: date, end: date) -> InstrumentPriceView`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/integration/test_instrument_price.py`. The three-interval case is the one that matters — the real export contains eight instruments with more than one in-market interval, so it is a normal case, not an edge.

```python
"""The price side of the instrument chart (M3 section 5.2).

Reads the unadjusted close only. If this module ever names `close_adjusted`,
`test_no_double_count.py` fails and it is right to.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal as D

from app.analytics.instrument_price import instrument_price_view

ISIN = "XX0000000001"  # invented; no holding of anyone's


def test_a_flat_gap_produces_three_intervals(seeded_engine) -> None:
    """Bought, sold, re-bought: in, out, in. Parent doc Sec 7.7, and the shape
    the real export has eight instances of."""
    view = instrument_price_view(
        seeded_engine, ISIN, start=date(2026, 1, 1), end=date(2026, 3, 31)
    )
    assert [i.in_market for i in view.intervals] == [True, False, True]


def test_an_interval_return_is_measured_end_to_end_within_it(seeded_engine) -> None:
    view = instrument_price_view(
        seeded_engine, ISIN, start=date(2026, 1, 1), end=date(2026, 3, 31)
    )
    first = view.intervals[0]
    assert first.price_return == D("0.10")  # 100.00 -> 110.00


def test_held_days_are_marked_and_flat_days_are_not(seeded_engine) -> None:
    """The line is drawn in two registers off this flag. A day the position was
    zero is still priced -- it is the stretch the reader stops watching, and the
    chart refuses to let it disappear."""
    view = instrument_price_view(
        seeded_engine, ISIN, start=date(2026, 1, 1), end=date(2026, 3, 31)
    )
    assert any(p.held for p in view.points)
    assert any(not p.held for p in view.points)
    assert all(p.close_base is not None for p in view.points)


def test_a_marker_per_ledger_transaction_carrying_the_position_after(seeded_engine) -> None:
    view = instrument_price_view(
        seeded_engine, ISIN, start=date(2026, 1, 1), end=date(2026, 3, 31)
    )
    assert [m.side for m in view.markers] == ["BUY", "SELL", "BUY"]
    assert [m.position_after for m in view.markers] == [D("10"), D("0"), D("5")]


def test_a_foreign_quote_is_converted_to_base(seeded_engine_usd) -> None:
    """The price line is in base currency, like everything else on the screen."""
    view = instrument_price_view(
        seeded_engine_usd, ISIN, start=date(2026, 1, 1), end=date(2026, 1, 5)
    )
    # 110.00 USD at 1.10 USD per EUR. `rate` is units of quote per 1 base, so
    # divide -- the same direction as domain.money.FxRate.
    assert view.points[-1].close_base == D("100.00")


def test_an_unpriceable_day_is_null_and_not_zero(seeded_engine_gap) -> None:
    """Parent doc Sec 8.1. A zero is a claim; this is the absence of one."""
    view = instrument_price_view(
        seeded_engine_gap, ISIN, start=date(2026, 1, 1), end=date(2026, 1, 5)
    )
    missing = [p for p in view.points if p.close_base is None]
    assert missing
    assert all(p.coverage == "missing" for p in missing)


def test_the_view_coverage_is_the_worst_day_in_the_window(seeded_engine_gap) -> None:
    view = instrument_price_view(
        seeded_engine_gap, ISIN, start=date(2026, 1, 1), end=date(2026, 1, 5)
    )
    assert view.coverage == "missing"
```

Build `seeded_engine`, `seeded_engine_usd` and `seeded_engine_gap` as fixtures in this file: an in-memory engine, one `Instrument`, `Transaction` rows for the buy/sell/buy, `PositionDaily` rows for the quantity series, `PriceDaily` rows across the window, and (for the USD case) `FxDaily` rows. Follow the fixture style in `backend/tests/integration/test_valuation.py`, which already builds this shape.

- [ ] **Step 2: Run to verify it fails**

```bash
cd backend && ./.venv/Scripts/python.exe -m pytest tests/integration/test_instrument_price.py -q
```

Expected: `ModuleNotFoundError: No module named 'app.analytics.instrument_price'`.

- [ ] **Step 3: Implement the module**

```python
"""One instrument's price line, the days it was held, and what was traded.

M3 section 5.2. Reads `close_unadjusted` through `analytics/prices.py` and
NEVER the adjusted close: the markers on this line are executed prices, and a
dividend-adjusted series restates every historical close downward, so a marker
would float above the line by a margin that grows the further back you look --
largest exactly where the reader is least able to check it.

The comparison against a benchmark is the opposite case and lives in
`instrument_return.py`. The two modules do not import each other and
`test_no_double_count.py` asserts it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import Engine
from sqlmodel import Session, select

from app.analytics.prices import price_history, quote_for
from app.analytics.quotes import (
    MISSING,
    base_currency,
    classify,
    in_base,
    rate_history,
    worst_coverage,
)
from app.api.schemas import Coverage
from app.domain.positions import weekdays
from app.models.ledger import PositionDaily, Transaction


@dataclass(frozen=True, slots=True)
class InstrumentPricePoint:
    on: date
    #: `None` when the day cannot be priced. Never 0 -- Sec 8.1.
    close_base: Decimal | None
    coverage: Coverage
    #: Whether a position was open. The line is drawn in two registers off this.
    held: bool


@dataclass(frozen=True, slots=True)
class Interval:
    start: date
    end: date
    in_market: bool
    #: `None` when either end of the interval could not be priced.
    price_return: Decimal | None


@dataclass(frozen=True, slots=True)
class Marker:
    on: date
    side: str
    quantity: Decimal
    price: Decimal
    fees: Decimal
    position_after: Decimal


@dataclass(frozen=True, slots=True)
class InstrumentPriceView:
    isin: str
    points: tuple[InstrumentPricePoint, ...]
    intervals: tuple[Interval, ...]
    markers: tuple[Marker, ...]
    coverage: Coverage


def _intervals(points: tuple[InstrumentPricePoint, ...]) -> tuple[Interval, ...]:
    """Alternating held and flat runs, each with its own end-to-end return."""
    if not points:
        return ()

    out: list[Interval] = []
    run_start = 0
    for i in range(1, len(points) + 1):
        ended = i == len(points) or points[i].held != points[run_start].held
        if not ended:
            continue
        a, z = points[run_start], points[i - 1]
        ret: Decimal | None = None
        if a.close_base is not None and z.close_base is not None and a.close_base != 0:
            ret = z.close_base / a.close_base - 1
        out.append(
            Interval(start=a.on, end=z.on, in_market=a.held, price_return=ret)
        )
        run_start = i
    return tuple(out)


def _markers(session: Session, isin: str, start: date, end: date) -> tuple[Marker, ...]:
    """Every real trade, in base currency, with the position it left behind.

    `is_economic` is the load-bearing filter. M0 records a split as a pair of
    legs flagged `is_economic=False` -- `domain/splits.py` reads exactly those
    to derive the ratio -- so without this clause a 10-for-1 split draws as a
    phantom sell of the old shares and a phantom buy of the new ones, on the
    one day the reader is most likely to be checking why the line moved.
    """
    rows = sorted(
        session.exec(
            select(Transaction).where(
                Transaction.isin == isin,
                Transaction.trade_date >= start,
                Transaction.trade_date <= end,
                Transaction.is_economic == True,  # noqa: E712 -- SQL, not Python
            )
        ).all(),
        key=lambda row: (row.trade_date, row.id),
    )
    out: list[Marker] = []
    running = Decimal("0")
    for row in rows:
        if row.txn_type not in ("BUY", "SELL"):
            continue
        if row.quantity is None or row.price_local is None:
            continue
        # The executed price in BASE currency, because the line it sits on is in
        # base. `fx_rate` is the broker's own rate for this trade -- units of the
        # local currency per 1 base, the same direction as domain.money.FxRate --
        # so divide. Using it rather than fx_daily is deliberate: this marker is
        # what the owner actually paid, not what the day's reference rate says
        # they would have.
        price_base = row.price_local
        if row.fx_rate is not None and row.fx_rate != 0:
            price_base = row.price_local / row.fx_rate
        running += row.quantity
        out.append(
            Marker(
                on=row.trade_date,
                side="BUY" if row.quantity > 0 else "SELL",
                quantity=abs(row.quantity),
                price=price_base,
                fees=row.fee_base,
                position_after=running,
            )
        )
    return tuple(out)


def instrument_price_view(
    engine: Engine, isin: str, *, start: date, end: date
) -> InstrumentPriceView:
    """One instrument's priced line over an inclusive window."""
    with Session(engine) as session:
        base = base_currency(session)
        prices = price_history(session)
        rates = rate_history(session)
        held_on = {
            row.on: row.quantity
            for row in session.exec(
                select(PositionDaily).where(PositionDaily.isin == isin)
            ).all()
        }
        markers = _markers(session, isin, start, end)

    points: list[InstrumentPricePoint] = []
    for day in weekdays(start, end):
        quote = quote_for(isin, day, prices)
        held = held_on.get(day, Decimal("0")) > 0
        if quote is None:
            points.append(
                InstrumentPricePoint(on=day, close_base=None, coverage=MISSING, held=held)
            )
            continue
        # Quantity 1: this is a price line, not a valuation. `in_base` returns
        # the converted amount and the age of the older of its two inputs.
        converted = in_base(quote, Decimal("1"), day, base, rates)
        if converted is None:
            points.append(
                InstrumentPricePoint(on=day, close_base=None, coverage=MISSING, held=held)
            )
            continue
        value, age = converted
        points.append(
            InstrumentPricePoint(
                on=day, close_base=value, coverage=classify(quote.source, age), held=held
            )
        )

    frozen = tuple(points)
    return InstrumentPriceView(
        isin=isin,
        points=frozen,
        intervals=_intervals(frozen),
        markers=markers,
        coverage=worst_coverage([p.coverage for p in frozen]) if frozen else MISSING,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd backend && ./.venv/Scripts/python.exe -m pytest tests/integration/test_instrument_price.py -q
```

Expected: 7 passed.

- [ ] **Step 5: Confirm the guard still holds, then commit**

```bash
cd backend && ./.venv/Scripts/python.exe -m pytest -q \
  && ./.venv/Scripts/python.exe -m ruff check . && ./.venv/Scripts/python.exe -m mypy app
```

Expected: 577 passed. `test_no_read_side_module_names_both_closes` covers the new module automatically — the test walks `app/` rather than a whitelist.

```bash
git add backend/app/analytics/instrument_price.py backend/tests/integration/test_instrument_price.py
git commit -m "feat(analytics): the instrument price line, its bands, markers and intervals

Unadjusted close only, so executed-price markers land on the line they
are drawn against. Three intervals from a buy-sell-buy is the shape the
real export has eight instances of, so it is the primary test rather
than an edge case.

Claude-Session: https://claude.ai/code/session_01DbQAgwhLhhRC8VsEkVnz3f"
```

---

### Task 5: `analytics/instrument_return.py` — currency, then rebasing, then excess

**Files:**
- Modify: `backend/app/analytics/total_return.py`
- Modify: `backend/tests/unit/test_total_return.py:59`
- Create: `backend/app/analytics/instrument_return.py`
- Test: `backend/tests/integration/test_instrument_return.py`

**Interfaces:**
- `total_return.py` changes: `TotalReturnPoint` becomes `on: date`, `close_adjusted: Decimal`, `currency: str` — the `index` field is **removed**, because M3-6 moves rebasing to the caller and an index rebased at the window edge is the figure that changes when the reader zooms. Adds `benchmark_total_return_series(engine: Engine, key: str, *, start: date, end: date) -> tuple[TotalReturnPoint, ...]` reading `BenchmarkDaily`.
- Produces, in `app.analytics.instrument_return`:
  - `IndexPoint`: `on: date`, `index: Decimal`
  - `IntervalExcess`: `start`, `end`, `instrument_return: Decimal | None`, `benchmark_return: Decimal | None`, `excess: Decimal | None`, `reason: str | None`
  - `Comparison`: `basis: str` (always `"total_return"`), `benchmark_key: str`, `instrument_index`, `benchmark_index`, `intervals`, `linked_instrument_return`, `linked_benchmark_return`, `linked_excess`, `coverage: Coverage`
  - `comparison(engine: Engine, isin: str, *, benchmark_key: str, intervals: Sequence[Interval]) -> Comparison`

- [ ] **Step 1: Update `total_return.py` and its test**

Replace `index` with `currency` on `TotalReturnPoint` and add the benchmark reader. Then in `backend/tests/unit/test_total_return.py`, replace `test_rebases_to_one_at_the_start_of_the_window`:

```python
def test_carries_the_quoted_currency_so_the_caller_can_convert(engine) -> None:
    """Replaces `test_rebases_to_one_at_the_start_of_the_window`. The requirement
    changed rather than the test being wrong: M3-6 rebases at each in-market
    interval's start, not at the window edge, so rebasing here would produce a
    figure that moves when the reader changes the range control. M3-7 needs the
    currency instead, because an index built before converting is the return a
    local investor got, not the one the owner got."""
    points = total_return_series(engine, ISIN, start=START, end=END)
    assert {p.currency for p in points} == {"EUR"}
```

- [ ] **Step 2: Write the failing comparison tests**

Create `backend/tests/integration/test_instrument_return.py`:

```python
"""The comparison side (M3 section 5.3). Adjusted close only.

The rebasing test is the load-bearing one: a figure that changes when the
reader changes the range control is not a figure.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal as D

from app.analytics.instrument_price import Interval
from app.analytics.instrument_return import comparison

ISIN = "XX0000000001"
KEY = "world"

HELD = Interval(start=date(2026, 1, 5), end=date(2026, 1, 30),
                in_market=True, price_return=D("0.10"))
FLAT = Interval(start=date(2026, 2, 2), end=date(2026, 2, 27),
                in_market=False, price_return=D("0.05"))
HELD_AGAIN = Interval(start=date(2026, 3, 2), end=date(2026, 3, 31),
                      in_market=True, price_return=D("0.04"))


def test_each_index_starts_at_one_hundred_at_its_interval_start(seeded) -> None:
    result = comparison(seeded, ISIN, benchmark_key=KEY, intervals=[HELD, FLAT, HELD_AGAIN])
    starts = [p.index for p in result.instrument_index if p.on in (HELD.start, HELD_AGAIN.start)]
    assert starts == [D("100"), D("100")]


def test_widening_the_window_does_not_move_any_excess_figure(seeded) -> None:
    """M3-6, stated as the property that makes it worth having."""
    narrow = comparison(seeded, ISIN, benchmark_key=KEY, intervals=[HELD])
    wide = comparison(seeded, ISIN, benchmark_key=KEY, intervals=[HELD, FLAT, HELD_AGAIN])
    assert narrow.intervals[0].excess == wide.intervals[0].excess


def test_out_of_market_intervals_get_no_excess_figure(seeded) -> None:
    """Parent doc Sec 7.6: over the actual holding period ONLY. Crediting the
    instrument with drift over a stretch the owner did not own it is the error
    this rules out."""
    result = comparison(seeded, ISIN, benchmark_key=KEY, intervals=[HELD, FLAT, HELD_AGAIN])
    flat = [i for i in result.intervals if i.start == FLAT.start]
    assert flat == []


def test_the_linked_return_chains_the_in_market_intervals_and_skips_the_gap(seeded) -> None:
    result = comparison(seeded, ISIN, benchmark_key=KEY, intervals=[HELD, FLAT, HELD_AGAIN])
    a, b = (i.instrument_return for i in result.intervals)
    assert a is not None and b is not None
    assert result.linked_instrument_return == (1 + a) * (1 + b) - 1


def test_a_foreign_benchmark_is_converted_before_it_is_rebased(seeded_usd_benchmark) -> None:
    """M3-7. The case that still produces a plausible number when you get it
    wrong: a dollar index up 8% while the dollar fell 8% did approximately
    nothing for a euro holder."""
    result = comparison(seeded_usd_benchmark, ISIN, benchmark_key=KEY, intervals=[HELD])
    assert result.intervals[0].benchmark_return == D("0")


def test_a_benchmark_with_no_data_yields_null_excess_and_a_reason(seeded_no_benchmark) -> None:
    result = comparison(seeded_no_benchmark, ISIN, benchmark_key=KEY, intervals=[HELD])
    assert result.intervals[0].excess is None
    assert result.intervals[0].reason
    assert result.linked_excess is None
    assert result.coverage == "missing"


def test_the_basis_is_always_stated(seeded) -> None:
    """Parent doc Sec 7.4: no endpoint returns an unlabelled return."""
    result = comparison(seeded, ISIN, benchmark_key=KEY, intervals=[HELD])
    assert result.basis == "total_return"
```

- [ ] **Step 3: Run to verify it fails**

```bash
cd backend && ./.venv/Scripts/python.exe -m pytest tests/integration/test_instrument_return.py -q
```

Expected: `ModuleNotFoundError: No module named 'app.analytics.instrument_return'`.

- [ ] **Step 4: Implement `instrument_return.py`**

Reads `close_adjusted` only, through `total_return.py`, and imports FX from `quotes.py` — which names neither column, which is why that import is safe. **Do not import `analytics/prices.py` here**; Task 6 asserts you did not.

The order is fixed and is the whole of M3-7: read adjusted closes with their currency → convert each to base through `in_base` (build a `Quote` with the adjusted close in it) → rebase to 100 at each in-market interval's start → difference the two returns → chain-link across in-market intervals only.

```python
def _rebase(points, start, end) -> tuple[tuple[IndexPoint, ...], Decimal | None]:
    """Index to 100 at `start`, and the total return to `end`."""
    window = [p for p in points if start <= p.on <= end]
    if not window or window[0].value == 0:
        return (), None
    first = window[0].value
    index = tuple(IndexPoint(on=p.on, index=p.value / first * Decimal("100")) for p in window)
    return index, window[-1].value / first - 1
```

Link with `(1 + a) * (1 + b) - 1` over the in-market intervals only, returning `None` the moment any one of them is `None` — a chain missing a link is not a shorter chain.

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd backend && ./.venv/Scripts/python.exe -m pytest tests/integration/test_instrument_return.py tests/unit/test_total_return.py -q
```

Expected: 7 passed + the existing total_return tests.

- [ ] **Step 6: Run the full gate and commit**

Expected: 584 passed.

```bash
git add backend/app/analytics/instrument_return.py backend/app/analytics/total_return.py \
        backend/tests/integration/test_instrument_return.py backend/tests/unit/test_total_return.py
git commit -m "feat(analytics): rebased comparison and excess return over the holding period

total_return_series finally has the caller M2 wrote it for. Its index
field is removed rather than reused: rebasing at the window edge makes a
figure that moves when the reader zooms, and M3-6 rebases at each
in-market interval's start instead.

Currency conversion happens before rebasing, not after. A dollar index
up 8% while the dollar fell 8% did approximately nothing for a euro
holder, and getting that backwards still produces a plausible number.

Claude-Session: https://claude.ai/code/session_01DbQAgwhLhhRC8VsEkVnz3f"
```

---

### Task 6: Strengthen the double-count guard

The walk already covers the two new modules. What it does not yet have is the call-path half, which is specific by nature.

**Files:**
- Modify: `backend/tests/integration/test_no_double_count.py`

- [ ] **Step 1: Add the call-path assertions**

```python
def test_the_instrument_price_module_cannot_reach_the_adjusted_close() -> None:
    """The call-path half for M3. Two modules that each name one column would
    still double-count if one could call the other."""
    reachable = _reachable_from("app.analytics.instrument_price")
    assert "app.analytics.total_return" not in reachable


def test_the_instrument_return_module_cannot_reach_the_unadjusted_close() -> None:
    """The direction that is easy to breach by accident: `instrument_return`
    legitimately imports `quotes` for FX, and if the unadjusted readers had
    stayed in that file this assertion would fail. That is why M3 split
    `quotes.py` from `prices.py` -- see M3 section 5.1."""
    reachable = _reachable_from("app.analytics.instrument_return")
    assert "app.analytics.prices" not in reachable
    assert "app.analytics.valuation" not in reachable


def test_the_instrument_route_reaches_both_modules_but_names_neither_column() -> None:
    """The composition point. It is allowed to reach both -- that is its job --
    precisely because it computes nothing itself."""
    reachable = _reachable_from("app.api.routes_instrument")
    assert "app.analytics.instrument_price" in reachable
    assert "app.analytics.instrument_return" in reachable
    names = _identifiers(APP / "api" / "routes_instrument.py")
    assert UNADJUSTED not in names
    assert ADJUSTED not in names
```

Extend `test_the_read_side_modules_exist_so_this_is_not_vacuous` to require `instrument_price.py` and `instrument_return.py`.

- [ ] **Step 2: Watch the guard fail before trusting it**

M1 found nine tests that could not fail. A guard that has just been rewritten is the worst possible place for a tenth, so prove each new assertion can go red:

```bash
cd backend
# Temporarily add `from app.analytics import total_return` to instrument_price.py
./.venv/Scripts/python.exe -m pytest tests/integration/test_no_double_count.py -q
# Expected: test_the_instrument_price_module_cannot_reach_the_adjusted_close FAILS
# Then revert the import and repeat for `from app.analytics import prices` in
# instrument_return.py, which must fail the second assertion.
```

Revert both edits. Do not commit until `git diff` shows only the test file changed.

- [ ] **Step 3: Run the full gate and commit**

Task 6's assertions reference `app.api.routes_instrument`, which Task 7 creates — so run this task's third assertion only after Task 7, or write Task 7 first and commit them together. Committing them together is simpler and is the recommended order.

---

### Task 7: The endpoints

**Files:**
- Create: `backend/app/api/routes_instrument.py`
- Modify: `backend/app/api/schemas.py` (append the output models)
- Modify: `backend/app/main.py` (register the router)
- Test: `backend/tests/integration/test_instrument_api.py`

**Interfaces:**
- `GET /api/instruments/{isin}/chart?range=1Y|3Y|5Y|max&benchmark=<slug>` → `InstrumentChartOut(Provenance)` with `isin`, `points[]`, `intervals[]`, `markers[]`, `comparison` (nullable), `coverage`.
- `GET /api/benchmarks` → `BenchmarkListOut` with `key`, `name`, `ter` per entry. **Never the symbol** — it is provider trivia the screen has no use for, and not sending it is one less thing on the wire.
- Every `Decimal` serialises as a string, via the existing `DecimalStr` pattern in `schemas.py`.
- `method` is `null` on the chart envelope. Copy the reasoning comment from `ValuationSeriesOut`: share counts are method-independent, so the chart is too, and filling it with the configured default would claim a dependency that does not exist.

- [ ] **Step 1: Write the failing API tests**

Cover: the envelope carries `method: null` and a `coverage`; an unknown ISIN is 404 not an empty chart; an unknown benchmark slug is 422 naming the configured set; omitting `benchmark` returns `comparison: null` and still draws; `range=max` reaches further back than `range=1Y`; every money field arrives as a string.

- [ ] **Step 2: Run to verify it fails, then implement, then verify it passes**

The route composes `instrument_price_view(...)` and, when `benchmark` is given, `comparison(...)` over the price view's in-market intervals. It performs no arithmetic of its own — Task 6's third assertion checks that by name.

- [ ] **Step 3: Run the full gate and commit Tasks 6 and 7 together**

```bash
git add backend/app/api backend/app/main.py backend/tests/integration/test_instrument_api.py \
        backend/tests/integration/test_no_double_count.py
git commit -m "feat(api): the instrument chart endpoint, and the guard that keeps it honest

The route composes the price side and the return side and computes
nothing itself, which is what lets it reach both modules without
reaching either column. Asserted by name.

Each new call-path assertion was watched failing against a deliberately
merged import before being trusted. M1 found nine tests that could not
fail; a just-rewritten guard is the worst place for a tenth.

Claude-Session: https://claude.ai/code/session_01DbQAgwhLhhRC8VsEkVnz3f"
```

---

### Task 8: `lib/instrument.ts` — the pure option builder

**Files:**
- Create: `frontend/src/lib/instrument.ts`
- Create: `frontend/src/lib/instrument.test.ts`
- Modify: `frontend/src/api/types.ts`, `frontend/src/api/client.ts`

Every decision about *what* to draw is a pure function here, tested directly; `EChart.tsx` only mounts, resizes and disposes. That division already exists for the valuation chart and is why component tests can mock the wrapper away.

**Interfaces:**
- `fetchInstrumentChart(isin: string, opts: { range: Range; benchmark: string | null }): Promise<InstrumentChart>`
- `fetchBenchmarks(): Promise<BenchmarkOut[]>`
- `instrumentChartOption(chart: InstrumentChart, opts: { showBenchmark: boolean }): EChartsOption`

- [ ] **Step 1: Write the failing tests**

Assert on the option object, not on pixels: one `markArea` entry per in-market interval at the right dates; one `markPoint` per marker; `markPoint` `symbolSize` scaling with the **square root** of quantity, because area is what the eye reads and a 4× quantity should be a 2× radius; the benchmark series absent entirely when `showBenchmark` is false rather than present-and-hidden; a `null` price rendering as a gap rather than a zero.

- [ ] **Step 2: Implement, verify, commit**

```bash
cd frontend && npx vitest run src/lib/instrument.test.ts && npx tsc --noEmit
```

---

### Task 9: The screen

**Files:**
- Create: `frontend/src/screens/Instrument.tsx`
- Create: `frontend/src/screens/Instrument.test.tsx`
- Modify: `frontend/src/navigation.ts:67` (add the tab id to `LEDGER_BACKED`)
- Modify: `frontend/src/App.tsx`

**The `LEDGER_BACKED` edit and the endpoint it points at belong in the same commit.** Adding the tab there without a live endpoint removes the `MODELLED` badge from a screen that is still modelled, which is the one way to make the UI lie about its own provenance.

- [ ] **Step 1: Write the failing component tests**

`@vitest-environment jsdom` docblock at the top; mock `../components/charts/EChart` exactly as `Positions.test.tsx` does. Cover: the instrument picker fetches on change; the range control re-requests with the new range; the benchmark selector adds and removes the overlay; a `null` excess renders "—" and its reason, never "0.00%"; the coverage strip shows the benchmark's own coverage separately from the instrument's; the basis label is on screen next to the excess figure.

- [ ] **Step 2: Implement, then leave `StockDetail.tsx` alone**

The modelled screen stays exactly as it is under its badge until M4 and M5 fill its remaining sections (M3-4). Do not delete it, do not half-convert it.

- [ ] **Step 3: Run the frontend gate and commit**

```bash
cd frontend && npx tsc --noEmit && npx vitest run
```

---

### Task 10: The real-data acceptance

**Files:**
- Modify: `backend/tests/integration/realdata_subject.py`
- Create: `backend/tests/integration/test_realdata_instrument.py`

**States no figure of its own.** Every expectation is derived at run time from the gitignored export. Add derivations to `realdata_subject.py`; never a literal.

- [ ] **Step 1: Add the derivation**

```python
def instrument_with_most_in_market_intervals(engine: Engine) -> tuple[str, int]:
    """§11.3 maps the parent spec's Sec 9.2 scenario to whichever instrument has
    the most in-market intervals. WHICH instrument that is, is a fact about the
    owner's export, so it is derived here and never written down."""
```

- [ ] **Step 2: Write the acceptance tests**

- The derived instrument has at least three in-market intervals, alternating strictly with the flat ones.
- Every day a position was held has a price, or the test skips with the same "cache is empty" reason `test_realdata_prices.py` uses — the outstanding operator step **PT-10** gates this suite exactly as it gates M2's nine.
- No interval's rebased index starts anywhere but 100.
- Where a benchmark is configured, its coverage over each in-market interval is reported and every excess figure is either a number or `null` with a reason.

- [ ] **Step 3: Run and commit**

```bash
cd backend && ./.venv/Scripts/python.exe -m pytest -q -m realdata -rs
```

A green run **with skips** is the expected outcome until the symbol question is answered. Read the skip reasons; do not read the green.

- [ ] **Step 4: File anything carried, then merge**

Record anything triaged as safe to carry as a Jira issue under **PT-4**, labelled `carried`. **Do not write a `docs/M3-FOLLOW-UPS.md`** — carried work lives on the board now, and a markdown list of it is the second source of truth `docs/TRACKING.md` exists to prevent.

The rule that no instrument is named applies to a Jira issue exactly as it applied to that file, and more strictly: `test_no_real_data_committed` cannot see Jira. Describe the shape, give the command that names the specifics.

Then merge to `master` with a merge commit, as M2 did.

---

## Self-review

**Spec coverage:** section 4.1 → Tasks 1 and 6; 4.2 → Task 1; 4.3 → Task 1; 4.4 → Task 3; 5.1 → Task 2; 5.2 → Task 4; 5.3 → Task 5; 5.4 → Tasks 4, 5, 9; 3.2 → Tasks 2 and 6; section 6 → Tasks 7, 8, 9; section 7 → every task, plus Task 10. M3-1 through M3-7 all land. No spec section is unimplemented.

**Known gap, deliberate:** the spec's open item 1 (the linked holding-period return as a time-weighted figure) is implemented as specified. If the owner takes the fallback, Task 5 drops `linked_*` and Task 9 drops the aggregate figure; nothing else changes.

**Type consistency:** `Coverage` is the Literal from `app.api.schemas` everywhere after Task 2 — `classify`, `worst_coverage`, `InstrumentPricePoint.coverage`, `InstrumentPriceView.coverage`, `Comparison.coverage`, `PositionValue.coverage`, `ValuationPoint.coverage`. `Interval` is defined once in `instrument_price.py` and consumed by `instrument_return.comparison`. `TotalReturnPoint` carries `currency` and no `index` from Task 5 onward, and Task 5 Step 1 updates its only existing test.
