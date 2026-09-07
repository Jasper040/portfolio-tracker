# M2 — Prices, FX, Valuation and Coverage — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the M1 lot ledger into a net portfolio value over time — holdings at market plus cash — with a positions table carrying unrealised P&L, and a coverage result that says so when a day cannot be fully priced rather than quietly summing what it has.

**Architecture:** M2 splits the codebase on the determinism line. `providers/` talks to OpenFIGI, Yahoo and the ECB behind Protocols and writes a dated cache (`price_daily`, `fx_daily`) that is never rebuilt. `domain/positions.py` turns ledger rows into daily share counts and a daily cash balance, and `rebuild()` writes those to `position_daily` and `cash_daily` — still a pure function of the ledger. `analytics/valuation.py` joins the two halves at read time; there is deliberately no `valuation_daily` table, because a table would go stale against either half. Symbol resolution is not a lookup: `domain/symbols.py` checks a candidate price series against the ledger's own executed prices and quarantines anything it cannot prove.

**Tech Stack:** Python 3.12+, SQLModel/SQLAlchemy, FastAPI, Typer, httpx, pytest; React + Vite + TypeScript + ECharts.

**Spec:** `docs/superpowers/specs/2026-09-06-m2-prices-fx-valuation-design.md` (M2, approved), which sits under `docs/superpowers/specs/2026-09-05-portfolio-tracker-design.md` (the parent). A `§` refers to the parent; "M2 section N" refers to the M2 spec. **Read both before Task 1.**

## Global Constraints

- **Money is `Decimal`, never `float`.** Everywhere, including tests, including prices and FX rates. Strings over the wire.
- **`domain/` is pure.** No ORM import, no file I/O, no network, no `datetime.now()`. Every "as of" date is a parameter. This is what makes `rebuild()` provably deterministic.
- **`providers/` is the only package that touches the network.** It imports no ORM. Nothing else in the app makes an HTTP call.
- **The ledger (`transaction`) is append-only.** Derived tables are dropped and rewritten. `net_base` is broker truth and is never recomputed (§5.4).
- **Prices and FX are a cache, not a derived table.** `rebuild()` must never write, delete or read-then-rewrite `price_daily` or `fx_daily`. Only `fetch-prices` writes them (M2-5).
- **There is no `valuation_daily` table.** Valuation is a read-time join (M2-5, M2 section 4). This deviates from parent §4.1 on purpose — see "Deliberate deviations" below.
- **`position_daily` has no `method` column.** M1 proved share counts are method-independent and asserts it in `test_every_method_holds_the_same_shares` (M2 spec section 5).
- **Symbol resolution is never auto-accepted on "a series came back"** (M2-2). A candidate is accepted only if it clears all three conditions of M2 section 6.2. Everything else is quarantined.
- **The symbol-validation band is ±30%** (M2-10): a ratio must lie in `[0.70, 1.30]`.
- **Staleness threshold is 4 calendar days** (M2 section 7.2), absorbing a weekend plus one holiday.
- **A day with an unpriceable held instrument has `value = null`.** Never €0, never a silent omission (§8.1, M2 section 7.2).
- **The five-year backfill is fixed at five years** (M2-7): one call per instrument, no date arithmetic. The chart's *default* start is the first day a position existed, but the API and UI must let the reader ask for the full five years back — see Task 9 and Task 12.
- **`close_unadjusted` and `close_adjusted` are two columns behind two accessors.** Valuation reaches only the first; `total_return_series()` only the second. An architectural test asserts no call path reaches both (§7.5, M2 section 7.3).
- **NO REAL HOLDINGS IN ANY TRACKED FILE** — no ISIN, instrument name, amount at five significant digits, or corporate-action date. That includes this plan, every fixture, and every provider response fixture. The `realdata` suite states no figures of its own: it derives them at run time from the gitignored export via `backend/tests/integration/realdata_subject.py`. Add a derivation there; never a literal. `test_no_real_data_committed.py` scans every tracked file and will fail you.
- **TDD strictly.** Write the test, run it, watch it fail *for the right reason*, then implement. Nine tests that could not fail were found and fixed during M1.
- **Type hints on every function.** Files 200–400 lines typical, 800 maximum.
- **Do not implement:** TWR/MWR (M6), benchmarks (M3), counterfactuals (M4), dividend analytics (M5), news (M8), the instrument chart with markers and holding bands (M3), sector/industry classification (M3). Unrealised P&L *is* in scope.

## The verification gate

All six commands, green, before **every** commit. A red suite is a stop-and-fix, not a note-and-continue.

```bash
cd backend && python -m pytest -q && python -m pytest -q -m realdata \
  && python -m ruff check . && python -m mypy app
cd ../frontend && npx tsc --noEmit && npx vitest run
```

Baseline at the start of M2: **304 backend tests, 35 opt-in `realdata`, 93 frontend**, ruff and `mypy --strict` clean. Every task adds tests; none may remove or weaken one.

## Environment notes that will bite

- Run the API with `python -m uvicorn app.main:create_app --factory` from `backend/`.
- Vite drifts off port 5173. `CORS_ORIGINS` in `backend/.env` must list the port it actually chose, or every request fails as a bare "Failed to fetch".
- `sqlite:///` needs a Windows path (`C:/...`), not an MSYS `/c/...` path.
- Git Bash heredocs mangle large Python and JSX payloads. Use the Write tool for anything longer than a few lines.
- Frontend component tests run in jsdom via a `@vitest-environment jsdom` docblock; the default environment is `node`. See `docs/RUNBOOK.md` section 4.
- `.gitignore` contains a bare `data/` rule, which swallows any directory named `data` at any depth. Do not create one; test fixtures go in `backend/tests/fixtures/`.

## Deliberate deviations from the parent spec

Three, each already argued in the M2 spec and repeated here so an executor meets them before the code does.

- **No `valuation_daily` table.** §4.1 lists it beside `lot` and `position_daily` as a table `rebuild()` rewrites, and the same section calls `rebuild()` provably deterministic. Once valuation depends on a fetched price those two cannot both hold — the same ledger rebuilt on two days would produce different rows from identical facts, and M1's determinism tests would be asserting something false. M2 section 4 resolves it by making valuation a read-time join. Nothing writes a valuation anywhere.
- **`httpx` moves from a dev dependency to a runtime dependency.** It is already installed (FastAPI's test client uses it); `providers/` needs a real HTTP client, and adding a second one would mean two timeout and TLS configurations.
- **`echarts` is added to the frontend.** §9.1 chose it and named the four features it was chosen for. The existing hand-rolled SVG charts stay where they are; M2 adds one `<EChart option={...} />` wrapper and nothing else imports the library.

One clarification that is *not* a deviation but reads like one. The M2 schema names its two price columns `close_unadjusted` and `close_adjusted`, and Yahoo's response calls the same two series `close` and `adjclose`. They line up like this:

| Column | Yahoo field | Adjusted for | Read by |
|---|---|---|---|
| `close_unadjusted` | `indicators.quote[0].close` | splits only | valuation, and the symbol discriminator |
| `close_adjusted` | `indicators.adjclose[0].adjclose` | splits **and** dividends | `total_return_series()` only |

"Unadjusted" means *dividend*-unadjusted. Both series are split-adjusted, which is why the discriminator has to divide an executed price by M1's derived split factor before comparing.

## Natural stopping points

Tasks 1–3 are pure and need no network: they produce the daily series and the discriminator, both fully tested against invented data. Tasks 4–8 build the cache and fill it. Task 9–11 make it readable. Tasks 12–13 make it visible and prove it against the real export. Stopping after Task 8 leaves the repo green with a populated cache and no UI change; stopping after Task 11 leaves a working API. Both are coherent.

---

### Task 1: The five new tables

Four tables from M2 section 5, plus the symbol quarantine from section 6.3. Which side of the determinism line each sits on is the whole point, so they go in two files rather than one.

**Files:**
- Create: `backend/app/models/market.py`
- Modify: `backend/app/models/ledger.py` (append `PositionDaily` and `CashDaily`)
- Modify: `backend/app/db.py:11` (register the new module on `SQLModel.metadata`)
- Test: `backend/tests/unit/test_market_models.py`

**Interfaces:**
- Consumes: `DecimalString` from `app.models.types`.
- Produces:
  - `PriceDaily(id, isin, price_date, close_unadjusted, close_adjusted, currency, source, fetched_at)`
  - `FxDaily(id, from_ccy, to_ccy, rate_date, rate, source, fetched_at)`
  - `SymbolReview(id, isin, product_name, trade_currency, candidates, detected_at, resolved)`
  - `PositionDaily(id, position_date, isin, quantity)`
  - `CashDaily(id, cash_date, balance_base)`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_market_models.py`:

```python
"""The cache tables, and the two properties that make them a cache.

`price_daily` and `fx_daily` hold what the network said and when it said it. Two
things have to be true of them and are asserted here rather than assumed:

* money round-trips as an exact Decimal, so a close of 12.30 comes back as
  "12.30" and not as 12.299999999999999;
* the same (instrument, day) or (currency pair, day) cannot be stored twice,
  because a second row would make "the price on that day" a question with two
  answers and the join would pick one arbitrarily.

The FX direction is asserted too. `rate` is units of `from_ccy` per 1 unit of
`to_ccy` -- the same direction as `domain.money.FxRate` and as DeGiro's own
`Exchange rate` column, which means you DIVIDE by it to reach the base currency.
Storing the other direction would produce numbers that are wrong and entirely
plausible, which is the failure `FxRate` exists to make impossible.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.db import create_engine_and_tables
from app.models.ledger import CashDaily, PositionDaily
from app.models.market import FxDaily, PriceDaily, SymbolReview

D = Decimal
FETCHED = datetime(2026, 9, 6, 12, 0, 0)

@pytest.fixture(name="engine")
def _engine():
    return create_engine_and_tables("sqlite://")

def _price(**overrides) -> PriceDaily:
    fields = dict(
        id=uuid4(),
        isin="NL0000000001",
        price_date=date(2025, 3, 3),
        close_unadjusted=D("12.30"),
        close_adjusted=D("11.80"),
        currency="EUR",
        source="yahoo",
        fetched_at=FETCHED,
    )
    fields.update(overrides)
    return PriceDaily(**fields)

class TestPriceDaily:
    def test_stores_both_closes_as_exact_decimals(self, engine) -> None:
        with Session(engine) as session:
            session.add(_price())
            session.commit()
        with Session(engine) as session:
            row = session.exec(select(PriceDaily)).one()
        assert row.close_unadjusted == D("12.30")
        assert str(row.close_unadjusted) == "12.30"
        assert row.close_adjusted == D("11.80")

    def test_refuses_a_second_price_for_the_same_instrument_and_day(self, engine) -> None:
        with Session(engine) as session:
            session.add(_price())
            session.commit()
        with Session(engine) as session, pytest.raises(IntegrityError):
            session.add(_price(close_unadjusted=D("99.00")))
            session.commit()

    def test_records_who_supplied_it_and_when(self, engine) -> None:
        """Provenance is not decoration: `coverage: "manual"` is only a fact
        because the row says which provider answered."""
        with Session(engine) as session:
            session.add(_price(source="manual"))
            session.commit()
        with Session(engine) as session:
            row = session.exec(select(PriceDaily)).one()
        assert row.source == "manual"
        assert row.fetched_at == FETCHED

class TestFxDaily:
    def test_stores_the_currency_pair_explicitly(self, engine) -> None:
        """A rate without a stated direction is a runtime error waiting to be
        plausible (parent doc Sec 5.3)."""
        with Session(engine) as session:
            session.add(
                FxDaily(
                    id=uuid4(),
                    from_ccy="USD",
                    to_ccy="EUR",
                    rate_date=date(2025, 3, 3),
                    rate=D("1.0854"),
                    source="ecb",
                    fetched_at=FETCHED,
                )
            )
            session.commit()
        with Session(engine) as session:
            row = session.exec(select(FxDaily)).one()
        assert (row.from_ccy, row.to_ccy) == ("USD", "EUR")
        assert row.rate == D("1.0854")

    def test_the_stored_rate_is_the_one_FxRate_divides_by(self, engine) -> None:
        """The direction contract, asserted rather than commented.

        1.0854 USD per 1 EUR means 108.54 USD is 100.00 EUR. If the reciprocal
        were stored, this would come out as 117.81 EUR -- wrong by 18% and
        entirely believable.
        """
        from app.domain.money import FxRate, Money

        rate = FxRate(
            from_currency="USD", to_currency="EUR", rate=D("1.0854"), as_of=date(2025, 3, 3)
        )
        converted = rate.convert(Money(D("108.54"), "USD"))
        assert converted.currency == "EUR"
        assert converted.amount == D("100")

    def test_refuses_a_second_rate_for_the_same_pair_and_day(self, engine) -> None:
        def row(rate: str) -> FxDaily:
            return FxDaily(
                id=uuid4(),
                from_ccy="USD",
                to_ccy="EUR",
                rate_date=date(2025, 3, 3),
                rate=D(rate),
                source="ecb",
                fetched_at=FETCHED,
            )

        with Session(engine) as session:
            session.add(row("1.0854"))
            session.commit()
        with Session(engine) as session, pytest.raises(IntegrityError):
            session.add(row("1.2000"))
            session.commit()

class TestDerivedDailyTables:
    def test_position_daily_has_no_method_column(self, engine) -> None:
        """M1 proved share counts are method-independent and asserts it in
        `test_every_method_holds_the_same_shares`. A `method` column here would
        invite three copies of one answer, and the day they disagreed there
        would be no way to say which was right."""
        assert "method" not in PositionDaily.model_fields

    def test_position_daily_refuses_two_quantities_for_one_instrument_day(self, engine) -> None:
        def row(quantity: str) -> PositionDaily:
            return PositionDaily(
                id=uuid4(),
                position_date=date(2025, 3, 3),
                isin="NL0000000001",
                quantity=D(quantity),
            )

        with Session(engine) as session:
            session.add(row("10"))
            session.commit()
        with Session(engine) as session, pytest.raises(IntegrityError):
            session.add(row("20"))
            session.commit()

    def test_cash_daily_holds_one_balance_per_day(self, engine) -> None:
        def row(balance: str) -> CashDaily:
            return CashDaily(id=uuid4(), cash_date=date(2025, 3, 3), balance_base=D(balance))

        with Session(engine) as session:
            session.add(row("-100.00"))
            session.commit()
        with Session(engine) as session:
            stored = session.exec(select(CashDaily)).one()
        assert stored.balance_base == D("-100.00")
        with Session(engine) as session, pytest.raises(IntegrityError):
            session.add(row("50.00"))
            session.commit()

    def test_cash_balance_may_be_negative(self, engine) -> None:
        """The account runs a debit balance and pays interest on it (parent doc
        Sec 3.5). A schema that could not hold that would make the net portfolio
        value wrong by the size of the overdraft."""
        with Session(engine) as session:
            session.add(
                CashDaily(id=uuid4(), cash_date=date(2025, 4, 4), balance_base=D("-2000.00"))
            )
            session.commit()
        with Session(engine) as session:
            assert session.exec(select(CashDaily)).one().balance_base < 0

class TestSymbolReview:
    def test_holds_one_open_question_per_instrument(self, engine) -> None:
        """A projection, like `corporate_action_review`: one row per instrument
        still unanswered, rebuilt each run rather than accumulated."""
        def row(candidates: str) -> SymbolReview:
            return SymbolReview(
                id=uuid4(),
                isin="NL0000000001",
                product_name="Example Holdings",
                trade_currency="EUR",
                candidates=candidates,
                detected_at=FETCHED,
            )

        with Session(engine) as session:
            session.add(row('[{"symbol": "EXA.AS"}]'))
            session.commit()
        with Session(engine) as session, pytest.raises(IntegrityError):
            session.add(row('[{"symbol": "EXB.DE"}]'))
            session.commit()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && python -m pytest tests/unit/test_market_models.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.models.market'`

- [ ] **Step 3: Write the market models**

Create `backend/app/models/market.py`:

```python
"""The fetched side of the determinism line (M2 spec section 4).

Everything in this file holds what the network said, dated by when it said it.
None of it is rebuilt: `rebuild()` must never write, delete or rewrite a row
here. `fetch-prices` is the only writer.

That separation is the whole architecture of M2. What the ledger knows is derived
and deterministic -- the same rows rebuilt on two days give the same answer. What
the network knows is fetched and dated, and the same request on two days may
legitimately give two answers, because a provider revises its history. Mixing the
two into one table would mean `rebuild()` was no longer a function of the ledger,
and M1's determinism tests would be asserting something false.

Prices carry `source` and `fetched_at` for the same reason. `coverage: "manual"`
is a claim about where a number came from; without a column recording who
answered, it would be an assumption.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Column, UniqueConstraint
from sqlmodel import Field, SQLModel

from app.models.types import DecimalString

class PriceDaily(SQLModel, table=True):
    """One instrument's close on one day, as one provider reported it.

    Two closes, in two columns, because parent doc Sec 7.5 forbids any call path
    from reaching both: total return computed from dividend-adjusted prices PLUS
    dividend income counts dividends twice. Separate columns behind separate
    accessors make that a checkable property of the call graph rather than a
    naming convention that decays.

    Both series are split-adjusted. "Unadjusted" means dividend-unadjusted:

    * `close_unadjusted` is the provider's plain close (Yahoo's `close`), and is
      what valuation and the symbol discriminator read;
    * `close_adjusted` is the total-return series (Yahoo's `adjclose`), read
      only by `analytics/total_return.py`.
    """

    __tablename__ = "price_daily"
    __table_args__ = (
        UniqueConstraint("isin", "price_date", name="uq_price_daily_isin_date"),
    )

    id: UUID = Field(primary_key=True)
    isin: str = Field(index=True)
    price_date: date = Field(index=True)
    close_unadjusted: Decimal = Field(sa_column=Column(DecimalString(), nullable=False))
    close_adjusted: Decimal = Field(sa_column=Column(DecimalString(), nullable=False))
    #: The currency the series is quoted in, as the provider reported it. Not
    #: assumed from the ledger: a match between the two is one of the three
    #: conditions the symbol discriminator checks (M2 spec section 6.2).
    currency: str
    #: Which provider answered. "manual" is what makes `coverage: "manual"` a fact.
    source: str
    fetched_at: datetime

class FxDaily(SQLModel, table=True):
    """One exchange rate on one day.

    `rate` is **units of `from_ccy` per 1 unit of `to_ccy`** -- so a USD->EUR row
    holding 1.0854 means 1.0854 USD buys 1 EUR, and you DIVIDE a USD amount by it
    to reach EUR. That is deliberately the same direction as
    `domain.money.FxRate` and as DeGiro's own `Exchange rate` column (parent doc
    Sec 3.5), so a row can be handed straight to `FxRate` without a reciprocal.

    A reciprocal would cost exactness -- one division on every stored rate -- and,
    worse, would put two directions in one codebase. The provider is queried in
    whichever direction returns this number verbatim (see `providers/ecb.py`).

    An explicit currency triple rather than an implied base, for the reason parent
    doc Sec 5.3 gives: a rate without a stated direction is a runtime error
    waiting to be plausible.
    """

    __tablename__ = "fx_daily"
    __table_args__ = (
        UniqueConstraint("from_ccy", "to_ccy", "rate_date", name="uq_fx_daily_pair_date"),
    )

    id: UUID = Field(primary_key=True)
    from_ccy: str = Field(index=True)
    to_ccy: str = Field(index=True)
    rate_date: date = Field(index=True)
    rate: Decimal = Field(sa_column=Column(DecimalString(), nullable=False))
    source: str
    fetched_at: datetime

class SymbolReview(SQLModel, table=True):
    """The symbol quarantine (M2 spec section 6.3). Deliberately NOT the ledger.

    The same shape as `CorporateActionReview`, and for the same reasons: a
    projection of `(the ledger, config/instrument_symbols.yaml)` rebuilt on every
    `fetch-prices` run rather than a queue that accumulates, with the answers
    living in a hand-edited file beside the ledger.

    One row per unresolved instrument. `candidates` is a JSON list of what the
    resolver found and what the discriminator measured against each -- the
    ratios, not just a verdict, because the operator answering this needs to see
    why a candidate was rejected. The real failure this guards against is a
    leveraged or inverse ETF on the same underlying, which passes every naive
    check: the ISIN resolves, the ticker exists, years of daily bars come back,
    and the currency matches (M2 spec section 3.2).
    """

    __tablename__ = "symbol_review"
    __table_args__ = (UniqueConstraint("isin", name="uq_symbol_review_isin"),)

    id: UUID = Field(primary_key=True)
    isin: str = Field(index=True)
    product_name: str
    #: The currency the ledger's own trades in this instrument were priced in.
    trade_currency: str
    #: JSON: [{"symbol", "currency", "ratios", "worst_ratio", "accepted", "reason"}]
    candidates: str
    detected_at: datetime
    resolved: bool = False
```

- [ ] **Step 4: Add the two derived tables**

Append to `backend/app/models/ledger.py`, after `LotClosure`:

```python
class PositionDaily(SQLModel, table=True):
    """Shares held in one instrument at the end of one day. Derived, not ledger.

    **No `method` column, on purpose.** FIFO, LIFO and HIFO disagree about which
    lot a sale consumed and therefore about realised P&L, but they cannot
    disagree about how many shares are left -- M1 asserts exactly that in
    `test_every_method_holds_the_same_shares`. A method column here would store
    three copies of one answer, and the day two of them differed there would be
    no way to say which was right.

    Rows exist only for days the position was non-zero. An absent row means "not
    held", which is a different statement from "held zero" and reads correctly in
    the valuation join without a special case.

    Quantities are post-split, because the fills they come from have already been
    through `apply_splits`.
    """

    __tablename__ = "position_daily"
    __table_args__ = (
        UniqueConstraint("position_date", "isin", name="uq_position_daily_date_isin"),
    )

    id: UUID = Field(primary_key=True)
    position_date: date = Field(index=True)
    isin: str = Field(index=True)
    quantity: Decimal = Field(sa_column=Column(DecimalString(), nullable=False))

class CashDaily(SQLModel, table=True):
    """The cash balance at the end of one day. Derived, not ledger.

    A running sum of `net_base`, which is broker truth (Sec 5.4) -- so this is
    pure ledger arithmetic and belongs on the derived side of the line, even
    though it ends up in the same chart as a fetched price.

    It exists because M2-4 makes portfolio value **net**: holdings at market plus
    cash, so the account's debit balance reduces the total rather than being
    quietly left out. Cash is reported as its own component so a reader can see
    which half moved.
    """

    __tablename__ = "cash_daily"
    __table_args__ = (UniqueConstraint("cash_date", name="uq_cash_daily_date"),)

    id: UUID = Field(primary_key=True)
    cash_date: date = Field(index=True)
    balance_base: Decimal = Field(sa_column=Column(DecimalString(), nullable=False))
```

- [ ] **Step 5: Register the new module so its tables are created**

Modify `backend/app/db.py`, replacing the single import line at line 11:

```python
import app.models.ledger  # noqa: F401  - registers tables on SQLModel.metadata
import app.models.market  # noqa: F401  - the fetched-side cache tables
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd backend && python -m pytest tests/unit/test_market_models.py -q`
Expected: PASS, 11 tests.

- [ ] **Step 7: Run the whole gate**

```bash
cd backend && python -m pytest -q && python -m pytest -q -m realdata \
  && python -m ruff check . && python -m mypy app
cd ../frontend && npx tsc --noEmit && npx vitest run
```
Expected: 315 backend passed, 35 realdata passed, ruff and mypy clean, 93 frontend passed.

- [ ] **Step 8: Commit**

```bash
git add backend/app/models/market.py backend/app/models/ledger.py backend/app/db.py \
        backend/tests/unit/test_market_models.py
git commit -m "feat(models): the price and FX cache, and the daily derived tables

Claude-Session: https://claude.ai/code/session_01UzfynZY2Rqs9oMAtCivdSF"
```

---

### Task 2: `domain/positions.py` — the daily share and cash series

The pure half of the chart. Ledger rows in, one quantity per instrument per weekday and one cash balance per weekday out. No ORM, no network, no `date.today()` — the end of the window is a parameter, which is what keeps `rebuild()` deterministic once Task 8 wires this in.

**Files:**
- Create: `backend/app/domain/positions.py`
- Test: `backend/tests/unit/test_positions.py`

**Interfaces:**
- Consumes:
  - `LedgerRow` (Protocol), `to_lot_transactions`, from `app.domain.orders`
  - `Split`, `derive_splits`, `apply_splits`, from `app.domain.splits`
  - `LotTransaction` from `app.domain.lots`
- Produces:
  - `PositionPoint(on: date, isin: str, quantity: Decimal)` frozen dataclass
  - `CashPoint(on: date, balance_base: Decimal)` frozen dataclass
  - `DailySeries(positions: tuple[PositionPoint, ...], cash: tuple[CashPoint, ...])` frozen dataclass, with `.first_position_day: date | None`
  - `LedgerCashRow(LedgerRow, Protocol)` adding `net_base: Decimal`
  - `weekdays(start: date, end: date) -> Iterator[date]`
  - `daily_series(rows: Sequence[LedgerCashRow], *, through: date) -> DailySeries`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_positions.py`:

```python
"""The daily share count and cash balance, from the ledger alone.

Pure arithmetic over invented rows. Everything the chart draws that does not need
a price is decided here, which is why this suite is the one that gets to be
exhaustive: the join in `analytics/valuation.py` can only be as right as this is.

Four things are easy to get wrong and each has a test that fails when they are:

* a weekend transaction has to land somewhere, and the rule is "the balance on
  day D is every row dated on or before D" -- so a Saturday trade shows up on
  Monday rather than vanishing;
* a pre-split buy has to be counted in post-split shares, or the position ends
  at the wrong number on every day after the split;
* a position that goes to zero has to STOP producing rows, because an absent row
  and a zero row mean different things to the valuation join;
* cash counts every row's `net_base`, including the suppressed corporate-action
  legs, because `net_base` is what actually hit the account.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.domain.positions import CashPoint, PositionPoint, daily_series, weekdays

D = Decimal
ZERO = D("0.00")

@dataclass(frozen=True, slots=True)
class Row:
    """A ledger row, structurally. Matches `LedgerCashRow`."""

    source_ref: str
    trade_date: date
    net_base: Decimal
    isin: str | None = None
    trade_time: str | None = "10:00"
    txn_type: str = "BUY"
    quantity: Decimal | None = None
    price_local: Decimal | None = None
    value_base: Decimal | None = None
    fee_base: Decimal = ZERO
    autofx_fee_base: Decimal | None = ZERO
    tax_base: Decimal = ZERO
    order_ref: str | None = "ord-1"
    is_economic: bool = True

def trade(ref: str, on: date, isin: str, qty: str, value: str, *, order: str = "") -> Row:
    """A share movement. `value_base` is the euro amount the broker recorded."""
    return Row(
        source_ref=ref,
        trade_date=on,
        isin=isin,
        quantity=D(qty),
        value_base=D(value),
        net_base=D(value),
        order_ref=order or ref,
    )

def cash_row(ref: str, on: date, amount: str) -> Row:
    """A deposit, dividend or fee: cash moved, no shares."""
    return Row(source_ref=ref, trade_date=on, net_base=D(amount), quantity=None, order_ref=None)

class TestWeekdays:
    def test_skips_saturday_and_sunday(self) -> None:
        # 2025-01-03 is a Friday; 2025-01-06 the following Monday.
        assert list(weekdays(date(2025, 1, 3), date(2025, 1, 6))) == [
            date(2025, 1, 3),
            date(2025, 1, 6),
        ]

    def test_is_inclusive_at_both_ends(self) -> None:
        assert list(weekdays(date(2025, 1, 6), date(2025, 1, 6))) == [date(2025, 1, 6)]

    def test_is_empty_when_the_end_precedes_the_start(self) -> None:
        assert list(weekdays(date(2025, 1, 6), date(2025, 1, 3))) == []

class TestPositions:
    def test_a_buy_holds_from_its_trade_date_onward(self) -> None:
        series = daily_series(
            [trade("a", date(2025, 1, 6), "NL0000000001", "10", "-1000.00")],
            through=date(2025, 1, 8),
        )
        assert series.positions == (
            PositionPoint(date(2025, 1, 6), "NL0000000001", D("10")),
            PositionPoint(date(2025, 1, 7), "NL0000000001", D("10")),
            PositionPoint(date(2025, 1, 8), "NL0000000001", D("10")),
        )

    def test_stops_producing_rows_once_the_position_closes(self) -> None:
        """An absent row means "not held". A zero row would mean "held zero",
        which the valuation join would then have to price."""
        series = daily_series(
            [
                trade("a", date(2025, 1, 6), "NL0000000001", "10", "-1000.00"),
                trade("b", date(2025, 1, 7), "NL0000000001", "-10", "1100.00"),
            ],
            through=date(2025, 1, 9),
        )
        assert [p.on for p in series.positions] == [date(2025, 1, 6)]

    def test_reopens_after_a_flat_interval(self) -> None:
        """Eight instruments in the real export have multiple in-market
        intervals (parent doc Sec 7.7). A series that could not go back up would
        under-report every one of them."""
        series = daily_series(
            [
                trade("a", date(2025, 1, 6), "NL0000000001", "10", "-1000.00"),
                trade("b", date(2025, 1, 7), "NL0000000001", "-10", "1100.00"),
                trade("c", date(2025, 1, 9), "NL0000000001", "4", "-500.00"),
            ],
            through=date(2025, 1, 10),
        )
        held = {p.on: p.quantity for p in series.positions}
        assert held == {
            date(2025, 1, 6): D("10"),
            date(2025, 1, 9): D("4"),
            date(2025, 1, 10): D("4"),
        }

    def test_counts_a_pre_split_buy_in_post_split_shares(self) -> None:
        """The M1 property, carried into the daily series. A 1-share buy before a
        10-for-1 split is 10 shares after it -- and the day AFTER the split is the
        first day that is true, because DeGiro books the adjustment on the split
        date itself."""
        rows = [
            trade("a", date(2025, 1, 6), "NL0000000001", "1", "-1000.00"),
            # The suppressed corporate-action legs: 1 share out, 10 in.
            Row(
                source_ref="split-out",
                trade_date=date(2025, 1, 8),
                isin="NL0000000001",
                quantity=D("-1"),
                value_base=D("1000.00"),
                net_base=ZERO,
                order_ref=None,
                is_economic=False,
            ),
            Row(
                source_ref="split-in",
                trade_date=date(2025, 1, 8),
                isin="NL0000000001",
                quantity=D("10"),
                value_base=D("-1000.00"),
                net_base=ZERO,
                order_ref=None,
                is_economic=False,
            ),
        ]
        series = daily_series(rows, through=date(2025, 1, 9))
        held = {p.on: p.quantity for p in series.positions}
        assert held[date(2025, 1, 9)] == D("10")
        # Every day carries the post-split count: `apply_splits` restates the
        # fill, it does not insert an event.
        assert held[date(2025, 1, 6)] == D("10")

    def test_ignores_rows_that_moved_no_shares(self) -> None:
        series = daily_series(
            [
                trade("a", date(2025, 1, 6), "NL0000000001", "10", "-1000.00"),
                cash_row("div", date(2025, 1, 7), "25.00"),
            ],
            through=date(2025, 1, 7),
        )
        assert {p.isin for p in series.positions} == {"NL0000000001"}

    def test_a_weekend_trade_lands_on_the_following_weekday(self) -> None:
        """2025-01-04 is a Saturday. The rule is "on or before", so the position
        appears on Monday rather than never."""
        series = daily_series(
            [trade("a", date(2025, 1, 4), "NL0000000001", "10", "-1000.00")],
            through=date(2025, 1, 6),
        )
        assert [p.on for p in series.positions] == [date(2025, 1, 6)]

    def test_orders_points_by_day_then_instrument(self) -> None:
        series = daily_series(
            [
                trade("a", date(2025, 1, 6), "NL0000000002", "1", "-100.00"),
                trade("b", date(2025, 1, 6), "NL0000000001", "2", "-200.00"),
            ],
            through=date(2025, 1, 6),
        )
        assert [p.isin for p in series.positions] == ["NL0000000001", "NL0000000002"]

class TestCash:
    def test_is_a_running_sum_of_net_base(self) -> None:
        series = daily_series(
            [
                cash_row("dep", date(2025, 1, 6), "1000.00"),
                trade("buy", date(2025, 1, 7), "NL0000000001", "10", "-400.00"),
            ],
            through=date(2025, 1, 8),
        )
        assert series.cash == (
            CashPoint(date(2025, 1, 6), D("1000.00")),
            CashPoint(date(2025, 1, 7), D("600.00")),
            CashPoint(date(2025, 1, 8), D("600.00")),
        )

    def test_goes_negative_and_stays_there(self) -> None:
        """The account runs a debit balance (parent doc Sec 3.5), and M2-4 makes
        portfolio value net of it. Clamping at zero would overstate the total by
        the size of the overdraft."""
        series = daily_series(
            [
                cash_row("dep", date(2025, 1, 6), "100.00"),
                trade("buy", date(2025, 1, 7), "NL0000000001", "10", "-500.00"),
            ],
            through=date(2025, 1, 7),
        )
        assert series.cash[-1].balance_base == D("-400.00")

    def test_counts_suppressed_rows_too(self) -> None:
        """`net_base` is what hit the account. A corporate action's two legs
        offset, so including them changes nothing -- but the rule is "every
        row", not "every economic row", and stating it that way is what makes
        the balance reconcile to the broker's own cash line."""
        rows = [
            cash_row("dep", date(2025, 1, 6), "1000.00"),
            Row(
                source_ref="ca-out",
                trade_date=date(2025, 1, 7),
                isin="NL0000000001",
                quantity=D("-1"),
                net_base=D("-50.00"),
                is_economic=False,
                order_ref=None,
            ),
            Row(
                source_ref="ca-in",
                trade_date=date(2025, 1, 7),
                isin="NL0000000001",
                quantity=D("10"),
                net_base=D("50.00"),
                is_economic=False,
                order_ref=None,
            ),
        ]
        assert daily_series(rows, through=date(2025, 1, 7)).cash[-1].balance_base == D("1000.00")

    def test_starts_on_the_first_ledger_day_not_on_the_first_position(self) -> None:
        """A deposit that sits in cash for a week is real money in the account.
        Starting the cash series at the first BUY would hide it."""
        series = daily_series(
            [
                cash_row("dep", date(2025, 1, 6), "1000.00"),
                trade("buy", date(2025, 1, 9), "NL0000000001", "10", "-400.00"),
            ],
            through=date(2025, 1, 9),
        )
        assert series.cash[0].on == date(2025, 1, 6)
        assert series.first_position_day == date(2025, 1, 9)

class TestPurity:
    def test_the_window_end_is_a_parameter(self) -> None:
        """`domain/` calls no `date.today()`. Two calls with the same arguments
        must give the same answer on any day, which is what lets `rebuild()`
        claim determinism."""
        rows = [trade("a", date(2025, 1, 6), "NL0000000001", "10", "-1000.00")]
        first = daily_series(rows, through=date(2025, 1, 10))
        second = daily_series(rows, through=date(2025, 1, 10))
        assert first == second

    def test_an_empty_ledger_produces_an_empty_series(self) -> None:
        series = daily_series([], through=date(2025, 1, 10))
        assert series.positions == ()
        assert series.cash == ()
        assert series.first_position_day is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && python -m pytest tests/unit/test_positions.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.domain.positions'`

- [ ] **Step 3: Write the implementation**

Create `backend/app/domain/positions.py`:

```python
"""The daily share count and the daily cash balance. Pure.

This is the half of the portfolio-value chart that the ledger can prove. A price
is a fact about the world; how many shares were held on a Tuesday is a fact about
the account, and this module derives it from nothing else.

Two series come out of one pass because they come from one pass of the same rows,
and separating them would mean reading the ledger twice to answer one question.

The rule for both is **"on or before"**: the state on day D reflects every row
dated D or earlier. That is what makes a Saturday transaction land on Monday
rather than disappear, and it is the same rule the valuation join uses for prices
-- so carry-forward is not separate machinery anywhere in M2, it is what "latest
on or before" means.

Share counts are post-split. `apply_splits` restates the fills that predate a
split rather than inserting an event on the split date, so the count is in
today's shares on every day of the series -- which is what makes the chart
continuous across a split instead of stepping by a factor of ten.

Purity is not decoration here. `through` is a parameter, never `date.today()`,
which is what lets `rebuild()` write `position_daily` and still be a function of
its inputs alone.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Protocol

from app.domain.lots import LotTransaction
from app.domain.orders import LedgerRow, to_lot_transactions
from app.domain.splits import apply_splits, derive_splits

_ZERO = Decimal("0.00")
_SATURDAY = 5

class LedgerCashRow(LedgerRow, Protocol):
    """`LedgerRow` plus the one field the cash series needs.

    Extending the Protocol rather than restating it: the share side of this
    module hands its rows straight to `to_lot_transactions`, so the two views of
    a row must not be allowed to drift apart.
    """

    net_base: Decimal

@dataclass(frozen=True, slots=True)
class PositionPoint:
    on: date
    isin: str
    quantity: Decimal

@dataclass(frozen=True, slots=True)
class CashPoint:
    on: date
    balance_base: Decimal

@dataclass(frozen=True, slots=True)
class DailySeries:
    """Everything the ledger knows about a day, before any price is applied."""

    positions: tuple[PositionPoint, ...]
    cash: tuple[CashPoint, ...]

    @property
    def first_position_day(self) -> date | None:
        """Where the value chart starts (M2-7).

        Not where the cache starts and not where the cash series starts. Valuing
        days on which nothing was held would draw a flat line at the cash balance
        for however long the account sat empty, and invite the reader to wonder
        what broke.
        """
        return self.positions[0].on if self.positions else None

def weekdays(start: date, end: date) -> Iterator[date]:
    """Every Monday-to-Friday from `start` to `end`, both inclusive.

    M2-3: every weekday is valued, with no holes. Weekends are not holes -- no
    venue was open and no reader expects a point -- but a closed venue on a
    Tuesday is, which is why the staleness of a carried-forward price is recorded
    rather than smoothed away.
    """
    day = start
    while day <= end:
        if day.weekday() < _SATURDAY:
            yield day
        day += timedelta(days=1)

def _fills_by_isin(rows: Sequence[LedgerCashRow]) -> dict[str, list[LotTransaction]]:
    """Split-adjusted fills per instrument, chronological.

    Borrowed wholesale from M1 rather than re-derived: `to_lot_transactions`
    already decides which rows are share movements and `apply_splits` already
    restates the pre-split ones. A second implementation here would be free to
    disagree with the one that produced `lot`, and the two would then report
    different share counts for the same day with nothing to say which was right.
    """
    splits = derive_splits(rows)
    return {
        isin: apply_splits(fills, [s for s in splits if s.isin == isin])
        for isin, fills in to_lot_transactions(rows).items()
    }

def _position_points(
    fills_by_isin: dict[str, list[LotTransaction]], *, start: date, through: date
) -> tuple[PositionPoint, ...]:
    points: list[PositionPoint] = []
    for day in weekdays(start, through):
        for isin in sorted(fills_by_isin):
            quantity = _ZERO
            for fill in fills_by_isin[isin]:
                if fill.trade_date > day:
                    break  # chronological, so nothing later can apply
                quantity += fill.quantity if fill.side == "BUY" else -fill.quantity
            if quantity != 0:
                points.append(PositionPoint(on=day, isin=isin, quantity=quantity))
    return tuple(points)

def _cash_points(
    rows: Sequence[LedgerCashRow], *, start: date, through: date
) -> tuple[CashPoint, ...]:
    """A running sum of `net_base` over EVERY row, economic or not.

    `net_base` is DeGiro's own `Total EUR` -- what actually hit the cash account
    (Sec 5.4), never recomputed. A corporate action's two suppressed legs offset
    to zero, so including them changes nothing; the point is that the rule is
    "every row", which is what makes this balance reconcile against the broker's
    own cash line rather than approximately agree with it.
    """
    ordered = sorted(rows, key=lambda row: (row.trade_date, row.source_ref))
    points: list[CashPoint] = []
    balance = _ZERO
    index = 0
    for day in weekdays(start, through):
        while index < len(ordered) and ordered[index].trade_date <= day:
            balance += ordered[index].net_base
            index += 1
        points.append(CashPoint(on=day, balance_base=balance))
    return tuple(points)

def daily_series(rows: Sequence[LedgerCashRow], *, through: date) -> DailySeries:
    """Daily share counts and cash balances, from the first ledger day to `through`.

    `through` is a parameter and not today's date, so this function has one
    answer for one input on any day it is called.
    """
    if not rows:
        return DailySeries(positions=(), cash=())

    start = min(row.trade_date for row in rows)
    if through < start:
        return DailySeries(positions=(), cash=())

    return DailySeries(
        positions=_position_points(_fills_by_isin(rows), start=start, through=through),
        cash=_cash_points(rows, start=start, through=through),
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && python -m pytest tests/unit/test_positions.py -q`
Expected: PASS, 17 tests.

- [ ] **Step 5: Run the whole gate**

```bash
cd backend && python -m pytest -q && python -m pytest -q -m realdata \
  && python -m ruff check . && python -m mypy app
cd ../frontend && npx tsc --noEmit && npx vitest run
```
Expected: 332 backend passed, 35 realdata passed, everything else clean.

- [ ] **Step 6: Commit**

```bash
git add backend/app/domain/positions.py backend/tests/unit/test_positions.py
git commit -m "feat(domain): daily share counts and cash balance, split-adjusted and pure

Claude-Session: https://claude.ai/code/session_01UzfynZY2Rqs9oMAtCivdSF"
```

---
### Task 3: `domain/symbols.py` — the discriminator, and the impostor it exists to catch

The most important task in M2. A real ISIN lookup returning a real multi-year series in the right currency resolved roughly one instrument in nine to a **leveraged or inverse ETF on the same underlying** (M2 spec section 3.2). Every naive check passed. This module is the check that does not.

The ledger is the oracle: an executed price, split-adjusted using M1's `derive_splits`, must agree with the provider's close within ±30% on every trade, and the agreement must be stable. Pure — candidate series and ledger trades in, a verdict out — so the whole decision is unit-testable against invented series.

**Files:**
- Create: `backend/app/domain/symbols.py`
- Test: `backend/tests/unit/test_symbols.py`

**Interfaces:**
- Consumes: `Split` from `app.domain.splits`.
- Produces:
  - `BAND_LOW = Decimal("0.70")`, `BAND_HIGH = Decimal("1.30")`, `SPREAD_MAX = Decimal("1.30")`, `NEAR_DAYS = 4`
  - `TradeObservation(trade_date: date, price_local: Decimal, currency: str)` frozen dataclass
  - `CandidateSeries(symbol: str, currency: str, closes: Mapping[date, Decimal])` frozen dataclass
  - Reason constants: `AGREES`, `NO_TRADES`, `CURRENCY_MISMATCH`, `NO_CLOSE_NEAR_TRADE`, `OUT_OF_BAND`, `UNSTABLE`
  - `Verdict(symbol: str, accepted: bool, reason: str, ratios: tuple[Decimal, ...])` frozen dataclass, with `.worst_ratio: Decimal | None`
  - `split_factor(splits: Sequence[Split], on: date) -> Decimal`
  - `assess(candidate: CandidateSeries, trades: Sequence[TradeObservation], splits: Sequence[Split]) -> Verdict`
  - `judge(candidates: Sequence[CandidateSeries], trades, splits) -> tuple[Verdict, ...]`
  - `accepted_symbol(verdicts: Sequence[Verdict]) -> str | None`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_symbols.py`:

```python
"""Whether a price series belongs to the instrument the ledger traded.

The failure this exists to catch is not a near miss. In the provider spike, one
large holding resolved -- via a genuine ISIN lookup, to a real ticker, returning
years of real daily bars in the right currency -- to a 2x-SHORT product on the
same underlying, trading at a fraction of the price and moving the opposite way.
Valued that way the position is wrong by a factor of tens, and the portfolio
chart slopes the wrong way on exactly the days the reader most wants to trust it.

So "a series came back" is not evidence. The account is its own oracle: it
records what was actually paid, per instrument, per date. `TestTheImpostor`
below is that case as a golden fixture, with invented numbers in the shape the
spike measured -- a discriminator that cannot be shown to reject it is
decoration.

One wrinkle the tests pin down. A provider's series is split-adjusted and an
executed price is not, so a pre-split trade compares at the split ratio rather
than at parity. M1 already derives that ratio from the corporate-action legs M0
suppressed, so `TestSplitAdjustment` shows the same trade failing at 10.0 without
the correction and passing at 1.0 with it -- the check independently
rediscovering a split M1 derived by a completely different route.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.domain.splits import Split
from app.domain.symbols import (
    AGREES,
    CURRENCY_MISMATCH,
    NO_CLOSE_NEAR_TRADE,
    NO_TRADES,
    OUT_OF_BAND,
    UNSTABLE,
    CandidateSeries,
    TradeObservation,
    accepted_symbol,
    assess,
    judge,
    split_factor,
)

D = Decimal

def buy(on: date, price: str, currency: str = "EUR") -> TradeObservation:
    return TradeObservation(trade_date=on, price_local=D(price), currency=currency)

def series(symbol: str, closes: dict[date, str], currency: str = "EUR") -> CandidateSeries:
    return CandidateSeries(
        symbol=symbol,
        currency=currency,
        closes={on: D(value) for on, value in closes.items()},
    )

class TestSplitFactor:
    def test_is_one_when_no_split_follows_the_trade(self) -> None:
        splits = [Split("NL0000000001", date(2025, 1, 8), D("10"))]
        assert split_factor(splits, date(2025, 2, 1)) == D("1")

    def test_is_the_ratio_when_a_split_follows(self) -> None:
        splits = [Split("NL0000000001", date(2025, 1, 8), D("10"))]
        assert split_factor(splits, date(2025, 1, 6)) == D("10")

    def test_a_split_on_the_trade_date_itself_does_not_count(self) -> None:
        """`apply_splits` uses the same strict inequality: DeGiro books the
        adjustment on the split date, so a trade that day is already in new
        shares and correcting it would count the split twice."""
        splits = [Split("NL0000000001", date(2025, 1, 8), D("10"))]
        assert split_factor(splits, date(2025, 1, 8)) == D("1")

    def test_two_splits_compound(self) -> None:
        splits = [
            Split("NL0000000001", date(2025, 1, 8), D("10")),
            Split("NL0000000001", date(2025, 6, 2), D("2")),
        ]
        assert split_factor(splits, date(2025, 1, 6)) == D("20")

class TestAgreement:
    def test_accepts_a_series_that_matches_every_trade(self) -> None:
        verdict = assess(
            series("EXA.AS", {date(2025, 1, 6): "20.00", date(2025, 2, 3): "24.00"}),
            [buy(date(2025, 1, 6), "20.00"), buy(date(2025, 2, 3), "24.00")],
            [],
        )
        assert verdict.accepted
        assert verdict.reason == AGREES

    def test_tolerates_the_gap_between_an_intraday_fill_and_a_close(self) -> None:
        """An executed price is an intraday fill and a close is end of day, so a
        few percent of honest disagreement is expected on a volatile day. The
        band is +/-30% because the closest wrong answer observed was off by a
        factor of nearly three -- around five times the margin needed, and the
        asymmetry justifies erring wide."""
        verdict = assess(
            series("EXA.AS", {date(2025, 1, 6): "20.00"}),
            [buy(date(2025, 1, 6), "21.20")],  # ratio 1.06
            [],
        )
        assert verdict.accepted

    def test_uses_the_latest_close_on_or_before_the_trade(self) -> None:
        """A venue holiday must not fail an otherwise correct symbol. Carrying
        the last close forward is the same rule the valuation join uses."""
        verdict = assess(
            series("EXA.AS", {date(2025, 1, 3): "20.00"}),
            [buy(date(2025, 1, 6), "20.20")],
            [],
        )
        assert verdict.accepted

    def test_rejects_when_no_close_sits_near_a_trade(self) -> None:
        """A series that starts after the trade, or has a week-long hole around
        it, has not been checked -- and unchecked is not the same as agreed."""
        verdict = assess(
            series("EXA.AS", {date(2024, 11, 1): "20.00"}),
            [buy(date(2025, 1, 6), "20.00")],
            [],
        )
        assert not verdict.accepted
        assert verdict.reason == NO_CLOSE_NEAR_TRADE

class TestTheImpostor:
    """The golden fixture: a leveraged or inverse product on the same underlying.

    Shape taken from the spike, numbers invented. The ISIN resolved, the ticker
    existed, the series returned years of daily bars, and the currency matched
    the ledger. The only thing that disagreed was the account.
    """

    def test_rejects_a_leveraged_product_trading_at_a_fraction_of_the_price(self) -> None:
        verdict = assess(
            series(
                "EXA2S.DE",
                {date(2025, 1, 6): "6.00", date(2025, 2, 3): "5.00", date(2025, 3, 3): "0.60"},
            ),
            [
                buy(date(2025, 1, 6), "20.00"),   # ratio 3.33
                buy(date(2025, 2, 3), "24.00"),   # ratio 4.80
                buy(date(2025, 3, 3), "26.00"),   # ratio 43.33
            ],
            [],
        )
        assert not verdict.accepted
        assert verdict.reason == OUT_OF_BAND
        # The ratios are kept on the verdict, not just a yes/no: the operator
        # answering the quarantine needs to see WHY it was rejected, and "3.33,
        # 4.80, 43.33 with no stable value" is the sentence that explains it.
        assert verdict.worst_ratio is not None
        assert verdict.worst_ratio > D("40")

    def test_rejects_an_inverse_product_that_moves_the_other_way(self) -> None:
        """The underlying rose 30% over these three trades; the inverse product
        fell. Each individual ratio drifts, and no band that admits an honest
        intraday gap can also admit this."""
        verdict = assess(
            series(
                "EXA1S.DE",
                {date(2025, 1, 6): "20.00", date(2025, 2, 3): "16.00", date(2025, 3, 3): "13.00"},
            ),
            [
                buy(date(2025, 1, 6), "20.00"),   # ratio 1.00
                buy(date(2025, 2, 3), "24.00"),   # ratio 1.50
                buy(date(2025, 3, 3), "26.00"),   # ratio 2.00
            ],
            [],
        )
        assert not verdict.accepted
        assert verdict.reason == OUT_OF_BAND

    def test_rejects_a_series_that_agrees_once_and_then_drifts(self) -> None:
        """Every ratio inside the band, and still wrong: 0.75 then 1.02 is a 36%
        spread across two trades of one instrument. A single lucky ratio proves
        nothing, which is why stability is a condition of its own."""
        verdict = assess(
            series("EXA.L", {date(2025, 1, 6): "20.00", date(2025, 2, 3): "24.00"}),
            [buy(date(2025, 1, 6), "15.00"), buy(date(2025, 2, 3), "24.48")],
            [],
        )
        assert not verdict.accepted
        assert verdict.reason == UNSTABLE

class TestCurrency:
    def test_rejects_a_series_quoted_in_another_currency(self) -> None:
        verdict = assess(
            series("EXA", {date(2025, 1, 6): "20.00"}, currency="USD"),
            [buy(date(2025, 1, 6), "20.00", currency="EUR")],
            [],
        )
        assert not verdict.accepted
        assert verdict.reason == CURRENCY_MISMATCH

    def test_currency_agreement_alone_does_not_accept(self) -> None:
        """Necessary, and not sufficient: every impostor in the spike was
        denominated in the same currency as its target."""
        verdict = assess(
            series("EXA2S.DE", {date(2025, 1, 6): "6.00"}, currency="EUR"),
            [buy(date(2025, 1, 6), "20.00", currency="EUR")],
            [],
        )
        assert not verdict.accepted

    def test_rejects_when_the_ledger_itself_traded_two_currencies(self) -> None:
        """Not a provider problem: an instrument the ledger priced in two
        currencies has no single trade currency to match against, so no series
        can be proved right and a human has to look."""
        verdict = assess(
            series("EXA", {date(2025, 1, 6): "20.00", date(2025, 2, 3): "24.00"}),
            [buy(date(2025, 1, 6), "20.00", "EUR"), buy(date(2025, 2, 3), "24.00", "USD")],
            [],
        )
        assert not verdict.accepted
        assert verdict.reason == CURRENCY_MISMATCH

class TestTheSeriesCameBackTrap:
    def test_refuses_to_accept_a_series_with_nothing_to_check_it_against(self) -> None:
        """M2-2: never auto-accept on "a series came back". With no comparable
        trade, "every trade agrees" is vacuously true -- which is exactly the
        reasoning that put a 2x-short product on the chart."""
        verdict = assess(series("EXA.AS", {date(2025, 1, 6): "20.00"}), [], [])
        assert not verdict.accepted
        assert verdict.reason == NO_TRADES

    def test_refuses_a_series_with_no_closes_at_all(self) -> None:
        verdict = assess(series("EXA.AS", {}), [buy(date(2025, 1, 6), "20.00")], [])
        assert not verdict.accepted

class TestSplitAdjustment:
    """The check rediscovers, by a completely different route, a split M1 derived
    from the corporate-action legs M0 suppressed."""

    SPLITS = [Split("NL0000000001", date(2025, 1, 8), D("10"))]
    # Bought 1 share at 200.00 before a 10-for-1; the provider's split-adjusted
    # close for that day is 20.00.
    TRADES = [buy(date(2025, 1, 6), "200.00"), buy(date(2025, 2, 3), "24.00")]
    SERIES = series("EXA.AS", {date(2025, 1, 6): "20.00", date(2025, 2, 3): "24.00"})

    def test_the_correct_symbol_agrees_across_the_split(self) -> None:
        verdict = assess(self.SERIES, self.TRADES, self.SPLITS)
        assert verdict.accepted
        assert verdict.ratios == (D("1"), D("1"))

    def test_and_would_be_rejected_at_ten_to_one_without_the_correction(self) -> None:
        """The control. Without M1's ratio the pre-split trade reads as a
        tenfold disagreement, and the right symbol gets quarantined."""
        verdict = assess(self.SERIES, self.TRADES, [])
        assert not verdict.accepted
        assert verdict.ratios[0] == D("10")

class TestChoosingBetweenCandidates:
    TRADES = [buy(date(2025, 1, 6), "20.00"), buy(date(2025, 2, 3), "24.00")]
    RIGHT = series("EXA.AS", {date(2025, 1, 6): "20.00", date(2025, 2, 3): "24.00"})
    WRONG = series("EXA2S.DE", {date(2025, 1, 6): "6.00", date(2025, 2, 3): "5.00"})

    def test_picks_the_one_candidate_that_agrees(self) -> None:
        verdicts = judge([self.WRONG, self.RIGHT], self.TRADES, [])
        assert accepted_symbol(verdicts) == "EXA.AS"

    def test_reports_a_verdict_for_every_candidate_in_the_order_given(self) -> None:
        """The quarantine shows the operator what was tried and what each
        measured. A rejected candidate that vanished silently would leave them
        answering a question with no evidence attached."""
        verdicts = judge([self.WRONG, self.RIGHT], self.TRADES, [])
        assert [v.symbol for v in verdicts] == ["EXA2S.DE", "EXA.AS"]

    def test_refuses_to_choose_when_two_candidates_agree(self) -> None:
        """Two listings of the same instrument on two venues both pass, and
        picking one arbitrarily would silently prefer a venue with worse
        liquidity or a different close time. A human decides."""
        twin = series("EXA.PA", {date(2025, 1, 6): "20.00", date(2025, 2, 3): "24.00"})
        assert accepted_symbol(judge([self.RIGHT, twin], self.TRADES, [])) is None

    def test_refuses_when_none_agree(self) -> None:
        assert accepted_symbol(judge([self.WRONG], self.TRADES, [])) is None

    def test_refuses_when_there_are_no_candidates(self) -> None:
        assert accepted_symbol(judge([], self.TRADES, [])) is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && python -m pytest tests/unit/test_symbols.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.domain.symbols'`

- [ ] **Step 3: Write the implementation**

Create `backend/app/domain/symbols.py`:

```python
"""Does this price series belong to the instrument the ledger traded?

M2 spec section 6. Symbol resolution is NOT a lookup. Run against the real
export, a genuine ISIN lookup returning a genuine multi-year series in the right
currency resolved roughly one instrument in nine to a LEVERAGED OR INVERSE ETF on
the same underlying -- once, to a 2x-short product trading at a fraction of the
underlying's price and moving in the opposite direction. Valued that way the
position is wrong by a factor of tens and the portfolio chart slopes the wrong
way on exactly the days the reader most wants to trust it.

Every one of those wrong matches passed the checks an implementation would
naturally apply: the ISIN resolved, the ticker existed, the series returned years
of daily bars, and the currency matched the ledger. **"A series came back" is not
evidence that it is the right series.**

The account is the oracle. It records what was actually paid, per instrument, per
date, and a price series for the same instrument must agree with that. Three
conditions, none sufficient alone:

1. the series currency matches the ledger's trade currency;
2. every executed trade agrees with the series, after split adjustment, within
   +/-30% (M2-10);
3. the agreement is stable across trades -- a single lucky ratio proves nothing.

The band is deliberately generous. An executed price is an intraday fill and a
close is end of day, so a few percent of honest disagreement is expected on a
volatile day, while the closest wrong answer observed was off by a factor of
nearly three. There is around five times the margin needed, and the asymmetry
justifies erring wide: a false quarantine costs one question, a false accept puts
a badly wrong number on the chart and offers no clue that it is wrong.

Pure: candidate series and ledger trades in, verdicts out. That is what makes the
leveraged-ETF case a unit-testable golden fixture rather than something only
reproducible against the network.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.domain.splits import Split

#: A ratio must lie inside this band (M2-10). Executed price over provider close.
BAND_LOW = Decimal("0.70")
BAND_HIGH = Decimal("1.30")

#: max(ratios) / min(ratios) across an instrument's trades. Condition 3: every
#: ratio can sit inside the band and still drift, which is what a wrong series
#: tracking a correlated underlying looks like.
SPREAD_MAX = Decimal("1.30")

#: How far back a close may be carried to meet a trade. The same four days the
#: valuation join calls fresh: a weekend plus one holiday.
NEAR_DAYS = 4

AGREES = "agrees"
NO_TRADES = "no comparable trade in the ledger"
CURRENCY_MISMATCH = "currency does not match the ledger's trade currency"
NO_CLOSE_NEAR_TRADE = "no close within four days of a trade"
OUT_OF_BAND = "an executed price disagrees with the series by more than 30%"
UNSTABLE = "the agreement is not stable across trades"

_ONE = Decimal("1")

@dataclass(frozen=True, slots=True)
class TradeObservation:
    """One executed fill, as the broker recorded it.

    `price_local` is the TRADE currency price, not the base-currency one M1's lot
    matcher uses. A provider quotes a series in its own currency, so the
    comparison has to happen there or the FX rate becomes a third unknown in a
    check that already has two.

    Not split-adjusted: `assess` applies M1's derived ratio.
    """

    trade_date: date
    price_local: Decimal
    currency: str

@dataclass(frozen=True, slots=True)
class CandidateSeries:
    """One provider's answer, reduced to what the check needs.

    `closes` are split-adjusted and dividend-UNadjusted -- the `close_unadjusted`
    column. Comparing against a total-return series would introduce a growing
    divergence that looks exactly like a slowly wrong symbol.
    """

    symbol: str
    currency: str
    closes: Mapping[date, Decimal]

@dataclass(frozen=True, slots=True)
class Verdict:
    symbol: str
    accepted: bool
    reason: str
    #: Every measured ratio, kept even on a rejection: the operator answering the
    #: quarantine needs to see why, and "3.33, 4.80, 43.33" is the sentence that
    #: explains it.
    ratios: tuple[Decimal, ...]

    @property
    def worst_ratio(self) -> Decimal | None:
        """The ratio furthest from parity, in either direction."""
        if not self.ratios:
            return None
        return max(self.ratios, key=lambda r: max(r, _ONE / r) if r else Decimal("Infinity"))

def split_factor(splits: Sequence[Split], on: date) -> Decimal:
    """New shares per old share for a trade executed on `on`.

    The product of every split effective strictly AFTER the trade date -- the
    same strict inequality `apply_splits` uses, because DeGiro books the
    adjustment on the split date itself and a trade that day is already
    denominated in new shares.

    `splits` must already be filtered to one instrument.
    """
    factor = _ONE
    for split in splits:
        if split.effective_on > on:
            factor *= split.ratio
    return factor

def _close_on_or_before(closes: Mapping[date, Decimal], on: date) -> Decimal | None:
    """The last close at most `NEAR_DAYS` before `on`, or None.

    A hole wider than that has not been checked, and unchecked is not agreed.
    """
    for back in range(NEAR_DAYS + 1):
        found = closes.get(on - timedelta(days=back))
        if found is not None and found != 0:
            return found
    return None

def assess(
    candidate: CandidateSeries,
    trades: Sequence[TradeObservation],
    splits: Sequence[Split],
) -> Verdict:
    """Measure one candidate against the ledger. All three conditions, in order."""
    if not trades:
        return Verdict(candidate.symbol, False, NO_TRADES, ())

    currencies = {trade.currency for trade in trades}
    if len(currencies) != 1 or candidate.currency not in currencies:
        return Verdict(candidate.symbol, False, CURRENCY_MISMATCH, ())

    ratios: list[Decimal] = []
    for trade in trades:
        close = _close_on_or_before(candidate.closes, trade.trade_date)
        if close is None:
            return Verdict(candidate.symbol, False, NO_CLOSE_NEAR_TRADE, tuple(ratios))
        adjusted = trade.price_local / split_factor(splits, trade.trade_date)
        ratios.append(adjusted / close)

    measured = tuple(ratios)
    if any(ratio < BAND_LOW or ratio > BAND_HIGH for ratio in measured):
        return Verdict(candidate.symbol, False, OUT_OF_BAND, measured)

    if len(measured) > 1 and max(measured) / min(measured) > SPREAD_MAX:
        return Verdict(candidate.symbol, False, UNSTABLE, measured)

    return Verdict(candidate.symbol, True, AGREES, measured)

def judge(
    candidates: Sequence[CandidateSeries],
    trades: Sequence[TradeObservation],
    splits: Sequence[Split],
) -> tuple[Verdict, ...]:
    """A verdict for every candidate, in the order they were offered.

    All of them, not just the winner: a candidate that vanished silently would
    leave the operator answering a quarantine with no evidence attached.
    """
    return tuple(assess(candidate, trades, splits) for candidate in candidates)

def accepted_symbol(verdicts: Sequence[Verdict]) -> str | None:
    """The one symbol that passed, or None.

    None when nothing passed AND when more than one did. Two listings of the same
    instrument on two venues both agree with the ledger, and picking one
    arbitrarily would silently choose a venue with different liquidity and a
    different close time. Ambiguity is a question, not a tie-break.
    """
    accepted = [verdict.symbol for verdict in verdicts if verdict.accepted]
    return accepted[0] if len(accepted) == 1 else None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && python -m pytest tests/unit/test_symbols.py -q`
Expected: PASS, 22 tests.

- [ ] **Step 5: Run the whole gate**

Expected: 354 backend passed, 35 realdata, everything clean.

- [ ] **Step 6: Commit**

```bash
git add backend/app/domain/symbols.py backend/tests/unit/test_symbols.py
git commit -m "feat(domain): prove a price series belongs to the instrument the ledger traded

The ledger is the oracle: an executed price, split-adjusted with M1's derived
ratio, must agree with the provider's close within +/-30% on every trade and stay
stable across them. The leveraged-ETF case from the provider spike is a golden
fixture -- it passes every naive check and this rejects it.

Claude-Session: https://claude.ai/code/session_01UzfynZY2Rqs9oMAtCivdSF"
```

---

### Task 4: Provider Protocols, the manual fallback, and the chain that records who answered

The interfaces the network lives behind, plus the two pieces that need no network at all. `chain.py` recording which provider answered is what lets `coverage: "manual"` be a fact rather than an assumption.

**Files:**
- Create: `backend/app/providers/__init__.py`
- Create: `backend/app/providers/base.py`
- Create: `backend/app/providers/manual.py`
- Create: `backend/app/providers/chain.py`
- Create: `config/manual_prices.example.csv`
- Modify: `.gitignore` (add `config/manual_prices.csv` and `config/instrument_symbols.yaml`)
- Modify: `backend/app/settings.py` (add `manual_prices_path`, `instrument_symbols_path`)
- Modify: `.env.example`
- Modify: `backend/pyproject.toml` (move `httpx` into runtime dependencies)
- Test: `backend/tests/unit/test_providers_manual.py`
- Test: `backend/tests/unit/test_providers_chain.py`
- Test: `backend/tests/unit/test_settings.py` (existing — extend)

**Interfaces:**
- Produces, in `app.providers.base`:
  - `PricePoint(on: date, close_unadjusted: Decimal, close_adjusted: Decimal)`
  - `PriceSeries(symbol: str, currency: str, source: str, points: tuple[PricePoint, ...])`
  - `FxPoint(on: date, rate: Decimal)`
  - `FxSeries(from_ccy: str, to_ccy: str, source: str, points: tuple[FxPoint, ...])`
  - `SymbolCandidate(symbol: str, name: str, exchange_code: str, source: str)`
  - `ProviderError(RuntimeError)`
  - `SymbolResolver`, `PriceProvider`, `FxProvider` Protocols
  - `MANUAL = "manual"`
- Produces, in `app.providers.manual`:
  - `MalformedManualPrices(ValueError)`
  - `ManualPrices.load(path: Path) -> ManualPrices`, `.series(isin: str) -> PriceSeries | None`, `.isins: frozenset[str]`
- Produces, in `app.providers.chain`:
  - `PriceChain(providers: Sequence[PriceProvider], manual: ManualPrices)`, `.series(isin: str, symbol: str | None, *, since: date | None) -> PriceSeries | None`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/unit/test_providers_manual.py`:

```python
"""`config/manual_prices.csv` -- the fallback for instruments with no public series.

Some holdings have no series on any free tier: a private company, an exchange
microcap, an instrument quoted on a venue nobody indexes. The parent doc
predicted which ones; the spike found a different set, so the list is a fact
about the data rather than a constant, and it belongs in the quarantine's output.

The loader is strict for the same reason `load_resolutions` is: a hand-edited
file whose typos are interpreted rather than rejected produces an import that
succeeds and is wrong. A price is the one number in this app with no cross-check
at all, so a misplaced decimal point here reaches the chart unchallenged.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.providers.manual import MalformedManualPrices, ManualPrices

D = Decimal

GOOD = """isin,date,close,currency
NL0000000001,2025-01-06,12.50,EUR
NL0000000001,2025-01-07,12.75,EUR
US0000000404,2025-01-06,3.20,USD
"""

def write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "manual_prices.csv"
    path.write_text(body, encoding="utf-8")
    return path

class TestLoading:
    def test_a_missing_file_holds_nothing(self, tmp_path: Path) -> None:
        """The normal state of a fresh checkout. Not an error -- the file is
        written by answering the first quarantine."""
        prices = ManualPrices.load(tmp_path / "absent.csv")
        assert prices.isins == frozenset()
        assert prices.series("NL0000000001") is None

    def test_reads_prices_as_exact_decimals(self, tmp_path: Path) -> None:
        prices = ManualPrices.load(write(tmp_path, GOOD))
        found = prices.series("NL0000000001")
        assert found is not None
        assert [p.close_unadjusted for p in found.points] == [D("12.50"), D("12.75")]

    def test_a_hand_typed_price_is_its_own_total_return(self, tmp_path: Path) -> None:
        """There is no dividend adjustment to apply to a number somebody typed,
        so both closes hold the same value. Leaving `close_adjusted` empty would
        make `total_return_series()` silently skip the instrument."""
        found = ManualPrices.load(write(tmp_path, GOOD)).series("US0000000404")
        assert found is not None
        assert found.points[0].close_adjusted == found.points[0].close_unadjusted

    def test_records_manual_as_the_source(self, tmp_path: Path) -> None:
        """This is the whole mechanism behind `coverage: "manual"`."""
        found = ManualPrices.load(write(tmp_path, GOOD)).series("NL0000000001")
        assert found is not None
        assert found.source == "manual"

    def test_carries_the_currency_the_operator_stated(self, tmp_path: Path) -> None:
        found = ManualPrices.load(write(tmp_path, GOOD)).series("US0000000404")
        assert found is not None
        assert found.currency == "USD"

    def test_orders_points_by_date_whatever_order_the_file_is_in(self, tmp_path: Path) -> None:
        prices = ManualPrices.load(
            write(
                tmp_path,
                "isin,date,close,currency\n"
                "NL0000000001,2025-01-07,12.75,EUR\n"
                "NL0000000001,2025-01-06,12.50,EUR\n",
            )
        )
        found = prices.series("NL0000000001")
        assert found is not None
        assert [p.on for p in found.points] == [date(2025, 1, 6), date(2025, 1, 7)]

    def test_lists_which_instruments_it_answers_for(self, tmp_path: Path) -> None:
        prices = ManualPrices.load(write(tmp_path, GOOD))
        assert prices.isins == frozenset({"NL0000000001", "US0000000404"})

class TestRejections:
    @pytest.mark.parametrize(
        ("body", "fragment"),
        [
            ("isin,day,close,currency\nNL0000000001,2025-01-06,12.50,EUR\n", "header"),
            ("isin,date,close,currency\nNL0000000001,06-01-2025,12.50,EUR\n", "date"),
            ("isin,date,close,currency\nNL0000000001,2025-01-06,twelve,EUR\n", "close"),
            ("isin,date,close,currency\nNL0000000001,2025-01-06,12.50,\n", "currency"),
            ("isin,date,close,currency\n,2025-01-06,12.50,EUR\n", "isin"),
        ],
    )
    def test_names_the_file_the_line_and_the_field(
        self, tmp_path: Path, body: str, fragment: str
    ) -> None:
        path = write(tmp_path, body)
        with pytest.raises(MalformedManualPrices) as raised:
            ManualPrices.load(path)
        message = str(raised.value)
        assert str(path) in message
        assert fragment in message

    def test_refuses_two_prices_for_one_instrument_and_day(self, tmp_path: Path) -> None:
        """Two answers to "what was it worth that day" is not a tie-break."""
        body = (
            "isin,date,close,currency\n"
            "NL0000000001,2025-01-06,12.50,EUR\n"
            "NL0000000001,2025-01-06,13.50,EUR\n"
        )
        with pytest.raises(MalformedManualPrices, match="twice"):
            ManualPrices.load(write(tmp_path, body))

    def test_refuses_two_currencies_for_one_instrument(self, tmp_path: Path) -> None:
        body = (
            "isin,date,close,currency\n"
            "NL0000000001,2025-01-06,12.50,EUR\n"
            "NL0000000001,2025-01-07,13.50,USD\n"
        )
        with pytest.raises(MalformedManualPrices, match="currency"):
            ManualPrices.load(write(tmp_path, body))

    def test_refuses_a_negative_price(self, tmp_path: Path) -> None:
        body = "isin,date,close,currency\nNL0000000001,2025-01-06,-12.50,EUR\n"
        with pytest.raises(MalformedManualPrices, match="close"):
            ManualPrices.load(write(tmp_path, body))
```

Create `backend/tests/unit/test_providers_chain.py`:

```python
"""Trying providers in order, and recording which one answered.

The recording is the point. `coverage: "manual"` is a claim about where a number
came from, and without the chain naming the provider on every series it would be
an assumption -- the same class of mistake as a metric that reports a method it
did not use.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from app.providers.base import PricePoint, PriceSeries
from app.providers.chain import PriceChain
from app.providers.manual import ManualPrices

D = Decimal

class Stub:
    """A `PriceProvider` that answers for the symbols it was told about."""

    def __init__(self, name: str, answers: dict[str, PriceSeries]) -> None:
        self.name = name
        self._answers = answers
        self.full_calls: list[str] = []
        self.since_calls: list[tuple[str, date]] = []

    def full_series(self, symbol: str) -> PriceSeries | None:
        self.full_calls.append(symbol)
        return self._answers.get(symbol)

    def series_since(self, symbol: str, since: date) -> PriceSeries | None:
        self.since_calls.append((symbol, since))
        return self._answers.get(symbol)

def priced(symbol: str, source: str) -> PriceSeries:
    return PriceSeries(
        symbol=symbol,
        currency="EUR",
        source=source,
        points=(PricePoint(date(2025, 1, 6), D("12.50"), D("12.50")),),
    )

def manual_file(tmp_path: Path) -> ManualPrices:
    path = tmp_path / "manual_prices.csv"
    path.write_text(
        "isin,date,close,currency\nUS0000000404,2025-01-06,3.20,USD\n", encoding="utf-8"
    )
    return ManualPrices.load(path)

class TestOrder:
    def test_takes_the_first_provider_that_answers(self, tmp_path: Path) -> None:
        first = Stub("first", {"EXA.AS": priced("EXA.AS", "first")})
        second = Stub("second", {"EXA.AS": priced("EXA.AS", "second")})
        chain = PriceChain([first, second], manual_file(tmp_path))

        found = chain.series("NL0000000001", "EXA.AS", since=None)

        assert found is not None
        assert found.source == "first"
        assert second.full_calls == []

    def test_falls_through_a_provider_that_has_nothing(self, tmp_path: Path) -> None:
        empty = Stub("empty", {})
        second = Stub("second", {"EXA.AS": priced("EXA.AS", "second")})
        chain = PriceChain([empty, second], manual_file(tmp_path))

        found = chain.series("NL0000000001", "EXA.AS", since=None)

        assert found is not None and found.source == "second"

    def test_falls_through_a_provider_that_answers_with_no_points(self, tmp_path: Path) -> None:
        """An empty series is not an answer. Accepting it would leave an
        instrument silently unpriced with no fallback attempted."""
        hollow = Stub(
            "hollow",
            {"EXA.AS": PriceSeries("EXA.AS", "EUR", "hollow", ())},
        )
        second = Stub("second", {"EXA.AS": priced("EXA.AS", "second")})
        chain = PriceChain([hollow, second], manual_file(tmp_path))

        found = chain.series("NL0000000001", "EXA.AS", since=None)

        assert found is not None and found.source == "second"

class TestManual:
    def test_goes_straight_to_manual_when_the_answer_file_said_so(self, tmp_path: Path) -> None:
        """`symbol: manual` in `instrument_symbols.yaml` routes here. No provider
        is called, because the operator has already said none of them can help."""
        provider = Stub("provider", {"EXA.AS": priced("EXA.AS", "provider")})
        chain = PriceChain([provider], manual_file(tmp_path))

        found = chain.series("US0000000404", None, since=None)

        assert found is not None
        assert found.source == "manual"
        assert provider.full_calls == []

    def test_manual_is_the_last_resort_when_no_provider_answers(self, tmp_path: Path) -> None:
        chain = PriceChain([Stub("empty", {})], manual_file(tmp_path))
        found = chain.series("US0000000404", "EXA.AS", since=None)
        assert found is not None and found.source == "manual"

    def test_returns_nothing_when_nobody_can_answer(self, tmp_path: Path) -> None:
        """Not an exception and not an empty series: None is what the caller
        turns into `coverage: "missing"`, and a day it touches values `null`."""
        chain = PriceChain([Stub("empty", {})], manual_file(tmp_path))
        assert chain.series("NL0000000009", "EXA.AS", since=None) is None

class TestIncremental:
    def test_asks_for_the_full_history_when_since_is_none(self, tmp_path: Path) -> None:
        provider = Stub("provider", {"EXA.AS": priced("EXA.AS", "provider")})
        PriceChain([provider], manual_file(tmp_path)).series("NL0000000001", "EXA.AS", since=None)
        assert provider.full_calls == ["EXA.AS"]
        assert provider.since_calls == []

    def test_asks_only_for_what_the_cache_lacks_when_given_a_date(self, tmp_path: Path) -> None:
        """M2-9: `fetch-prices` is incremental. A five-year refetch on every run
        is a request the provider has no reason to keep serving."""
        provider = Stub("provider", {"EXA.AS": priced("EXA.AS", "provider")})
        PriceChain([provider], manual_file(tmp_path)).series(
            "NL0000000001", "EXA.AS", since=date(2025, 6, 1)
        )
        assert provider.since_calls == [("EXA.AS", date(2025, 6, 1))]
        assert provider.full_calls == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && python -m pytest tests/unit/test_providers_manual.py tests/unit/test_providers_chain.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.providers'`

- [ ] **Step 3: Write the Protocols**

Create `backend/app/providers/__init__.py` (empty file) and `backend/app/providers/base.py`:

```python
"""What a price, FX or symbol provider looks like from the inside of the app.

Protocols rather than base classes, and the network confined to this package,
for the same reason `domain/` imports no ORM: the arithmetic has to be testable
without either. Nothing outside `app/providers/` makes an HTTP call.

The value objects here are deliberately thin. A provider's job is to answer one
question and say who answered it; deciding whether the answer is believable is
`domain/symbols.py`, and deciding what a missing answer means is
`analytics/valuation.py`. A provider that made either decision would be an
untestable place for the most important judgement in M2 to live.

`source` travels on every series because M2's coverage values are claims about
provenance. `coverage: "manual"` says a component came from a file somebody
typed, and the only reason that can be a fact rather than an assumption is that
the row records who supplied it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol

#: The source string a hand-maintained price carries. Reaching `coverage:
#: "manual"` is a string comparison against this, so it lives in one place.
MANUAL = "manual"

#: The cache depth (M2-7): a fixed five years, one call per instrument, no date
#: arithmetic. The valuation series starts later -- at the first day a position
#: existed -- because cache depth and chart start are separate concerns. The
#: depth is what lets a reader ask to look back five years and be answered.
BACKFILL_YEARS = 5

class ProviderError(RuntimeError):
    """A provider could not be reached or answered with something unusable.

    Distinct from "answered with nothing", which is `None` and is a normal
    result: an instrument may genuinely have no series. An exception here means
    the network or the response shape failed, and `fetch-prices` reports it
    rather than recording an absence that would read as `coverage: "missing"`.
    """

@dataclass(frozen=True, slots=True)
class PricePoint:
    """One day's closes.

    Both are split-adjusted. "Unadjusted" means dividend-unadjusted:
    `close_unadjusted` is the plain close that valuation and the symbol
    discriminator read, `close_adjusted` the total-return series that only
    `analytics/total_return.py` may read (parent doc Sec 7.5).
    """

    on: date
    close_unadjusted: Decimal
    close_adjusted: Decimal

@dataclass(frozen=True, slots=True)
class PriceSeries:
    symbol: str
    #: As the PROVIDER reported it, never assumed from the ledger. A match
    #: between the two is one of the three conditions the discriminator checks.
    currency: str
    source: str
    points: tuple[PricePoint, ...]

@dataclass(frozen=True, slots=True)
class FxPoint:
    on: date
    #: Units of `from_ccy` per 1 unit of `to_ccy`. Divide, never multiply.
    rate: Decimal

@dataclass(frozen=True, slots=True)
class FxSeries:
    from_ccy: str
    to_ccy: str
    source: str
    points: tuple[FxPoint, ...]

@dataclass(frozen=True, slots=True)
class SymbolCandidate:
    """One possible ticker for an ISIN. A candidate, never an answer.

    The provider spike's central finding is that a resolver returning this is
    saying "here is something with that ISIN attached", not "here is the
    instrument you traded". `domain/symbols.py` decides.
    """

    symbol: str
    name: str
    exchange_code: str
    source: str

class SymbolResolver(Protocol):
    name: str

    def candidates(self, isin: str) -> tuple[SymbolCandidate, ...]:
        """Every ticker this resolver associates with `isin`, best guess first."""
        ...

class PriceProvider(Protocol):
    name: str

    def full_series(self, symbol: str) -> PriceSeries | None:
        """The fixed five-year history (M2-7). One call, no date arithmetic."""
        ...

    def series_since(self, symbol: str, since: date) -> PriceSeries | None:
        """Only what the cache lacks (M2-9), from `since` to today inclusive."""
        ...

class FxProvider(Protocol):
    name: str

    def series(self, from_ccy: str, to_ccy: str, *, start: date, end: date) -> FxSeries | None:
        """Daily rates for one pair. `rate` follows `FxDaily`'s direction."""
        ...

def latest_point(points: Sequence[PricePoint]) -> PricePoint | None:
    """The most recent point, or None. Used to decide whether to refetch."""
    return max(points, key=lambda point: point.on) if points else None
```

- [ ] **Step 4: Write the manual provider**

Create `backend/app/providers/manual.py`:

```python
"""Hand-maintained prices, for instruments no free provider covers.

Parent doc Sec 8.2 puts a hand-editable CSV at the end of the provider chain, and
the spike confirmed it is needed: some holdings have no public series at any
price. The set is a fact about the data rather than a constant, so it is
discovered by the quarantine rather than hardcoded here.

The loader is strict in the same way `ingest/corporate_actions.load_resolutions`
is, and for a sharper reason. Every other number in this app is cross-checked
against something: a trade against the broker's own total, a lot against
`Portfolio.csv`, a symbol against the executed prices. A manual price is checked
against nothing at all, so a misplaced decimal point reaches the chart
unchallenged. Guessing at a malformed row would be guessing at the one number
with no second opinion.
"""

from __future__ import annotations

import csv
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from app.providers.base import MANUAL, PricePoint, PriceSeries

_COLUMNS = ("isin", "date", "close", "currency")

class MalformedManualPrices(ValueError):
    """The file exists but cannot be trusted. Names the file, line and field."""

@dataclass(frozen=True, slots=True)
class ManualPrices:
    """Every hand-typed price, indexed by instrument."""

    _by_isin: Mapping[str, PriceSeries]

    @property
    def isins(self) -> frozenset[str]:
        return frozenset(self._by_isin)

    def series(self, isin: str) -> PriceSeries | None:
        return self._by_isin.get(isin)

    @classmethod
    def load(cls, path: Path) -> "ManualPrices":
        """Read the file. A missing one answers nothing, which is not an error."""
        if not path.exists():
            return cls(_by_isin={})

        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.reader(handle)
            header = next(reader, None)
            if header is None or tuple(cell.strip() for cell in header) != _COLUMNS:
                raise MalformedManualPrices(
                    f"{path}: header must be exactly {','.join(_COLUMNS)}"
                )
            rows = [row for row in reader if any(cell.strip() for cell in row)]

        points: dict[str, dict[date, PricePoint]] = {}
        currencies: dict[str, str] = {}
        for line_no, row in enumerate(rows, start=2):
            where = f"{path}: line {line_no}"
            if len(row) != len(_COLUMNS):
                raise MalformedManualPrices(f"{where} has {len(row)} fields, expected 4")

            isin, on_text, close_text, currency = (cell.strip() for cell in row)
            if not isin:
                raise MalformedManualPrices(f"{where}: isin is empty")
            if not currency:
                raise MalformedManualPrices(f"{where}: currency is empty")

            try:
                on = date.fromisoformat(on_text)
            except ValueError as bad:
                raise MalformedManualPrices(
                    f"{where}: date {on_text!r} is not YYYY-MM-DD"
                ) from bad

            try:
                close = Decimal(close_text)
            except InvalidOperation as bad:
                raise MalformedManualPrices(
                    f"{where}: close {close_text!r} is not a number"
                ) from bad
            if close <= 0:
                raise MalformedManualPrices(f"{where}: close {close} is not positive")

            held = currencies.setdefault(isin, currency)
            if held != currency:
                raise MalformedManualPrices(
                    f"{where}: {isin} is priced in {held} elsewhere in this file, "
                    f"not {currency}"
                )

            days = points.setdefault(isin, {})
            if on in days:
                raise MalformedManualPrices(f"{where}: {isin} is priced twice on {on}")
            # A number somebody typed has no dividend adjustment to apply, so it
            # is its own total return. Leaving the adjusted close empty would
            # make `total_return_series()` skip the instrument in silence.
            days[on] = PricePoint(on=on, close_unadjusted=close, close_adjusted=close)

        return cls(
            _by_isin={
                isin: PriceSeries(
                    symbol=isin,
                    currency=currencies[isin],
                    source=MANUAL,
                    points=tuple(days[on] for on in sorted(days)),
                )
                for isin, days in points.items()
            }
        )
```

- [ ] **Step 5: Write the chain**

Create `backend/app/providers/chain.py`:

```python
"""Try providers in order, and record which one answered.

Parent doc Sec 8.2's chained `PriceProvider`s with a hand-editable fallback. The
recording half is what M2 depends on: `coverage: "manual"` is a claim about where
a number came from, and the only thing that makes it a fact is that the series
carries the name of whoever supplied it all the way into `price_daily.source`.

The chain is keyed by ISIN and symbol together, because the two ends of it are
keyed differently. A network provider knows tickers; the manual file knows
instruments, since an instrument routed to it usually has no ticker to know.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from app.providers.base import PriceProvider, PriceSeries
from app.providers.manual import ManualPrices

class PriceChain:
    """The provider order, with the manual file last."""

    def __init__(self, providers: Sequence[PriceProvider], manual: ManualPrices) -> None:
        self._providers = tuple(providers)
        self._manual = manual

    def series(
        self, isin: str, symbol: str | None, *, since: date | None
    ) -> PriceSeries | None:
        """Prices for one instrument, from the first source that has any.

        `symbol is None` means the operator answered `manual` in
        `instrument_symbols.yaml`: go straight to the file without asking a
        provider, because they have already said none can help.

        `since is None` asks for the fixed five-year history (M2-7); a date asks
        only for what the cache lacks (M2-9).

        Returns `None` when nothing can answer. Not an exception and not an empty
        series: `None` is what the caller turns into `coverage: "missing"`, and a
        day it touches is valued `null` rather than short.
        """
        if symbol is not None:
            for provider in self._providers:
                found = (
                    provider.full_series(symbol)
                    if since is None
                    else provider.series_since(symbol, since)
                )
                # An empty series is not an answer. Accepting it would leave the
                # instrument unpriced with no fallback attempted.
                if found is not None and found.points:
                    return found
        return self._manual.series(isin)
```

- [ ] **Step 6: Add the settings, config example and gitignore entries**

Modify `backend/app/settings.py`, adding after `corporate_actions_path`:

```python
    #: The operator's symbol answers (M2 spec section 6.3). Ledger-adjacent and
    #: gitignored for the same reason `corporate_actions.yaml` is: a key here is
    #: an ISIN, which is a holding.
    instrument_symbols_path: str = str(_REPO_ROOT / "config" / "instrument_symbols.yaml")
    #: Hand-maintained prices for instruments no provider covers (parent doc Sec 8.2).
    manual_prices_path: str = str(_REPO_ROOT / "config" / "manual_prices.csv")
```

Create `config/manual_prices.example.csv`:

```csv
isin,date,close,currency
# Prices for instruments no provider covers. Copy to manual_prices.csv and edit.
# One row per instrument per day. The close is in the instrument's own currency;
# FX to the base currency is applied from fx_daily, exactly as for a fetched price.
# A price here carries coverage "manual" wherever it is used, so a reader can see
# that the number was typed rather than measured.
NL0000000001,2025-01-06,12.50,EUR
NL0000000001,2025-01-07,12.75,EUR
```

Modify `.gitignore`, extending the corporate-actions block:

```
# Corporate-action answers (design doc Sec 6.3). A key is ISIN:date:amount --
# a holding and its size. The committed .example.yaml documents the format.
config/corporate_actions.yaml
# Symbol answers and hand-maintained prices (M2 spec section 6.3). Both are keyed
# by ISIN, which is a holding. The .example files document the formats.
config/instrument_symbols.yaml
config/manual_prices.csv
```

Modify `.env.example`, appending:

```
# Symbol answers and hand-maintained prices (M2). Both default to config/ at the
# repo root, resolved absolutely so they do not depend on where you run from.
# INSTRUMENT_SYMBOLS_PATH=
# MANUAL_PRICES_PATH=
```

Modify `backend/pyproject.toml`, moving `httpx` into the runtime dependency list:

```toml
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.32",
    "sqlmodel>=0.0.22",
    "pydantic-settings>=2.6",
    "typer>=0.15",
    "pandas>=2.2",
    "pyyaml>=6.0",
    # Runtime from M2: `providers/` needs a real HTTP client. It was already
    # installed as a dev dependency for FastAPI's test client; two HTTP clients
    # would mean two timeout and TLS configurations.
    "httpx>=0.28",
]

[project.optional-dependencies]
dev = ["pytest>=8.3", "ruff>=0.8", "mypy>=1.13", "types-PyYAML>=6.0"]
```

- [ ] **Step 7: Extend the settings test**

Append to `backend/tests/unit/test_settings.py`:

```python
def test_the_symbol_and_price_answer_files_default_beside_the_ledger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Absolute, anchored on the repo, for the reason `corporate_actions_path`
    is: the API starts from `backend/` and the CLI runs from wherever the
    operator happens to be. A relative default breaks in the worst way -- an
    unreadable answers file answers nothing, so `fetch-prices` refuses and
    reports every instrument as unresolved while the file sits there, answered,
    one directory up."""
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    settings = Settings()
    assert Path(settings.instrument_symbols_path).is_absolute()
    assert Path(settings.instrument_symbols_path).name == "instrument_symbols.yaml"
    assert Path(settings.manual_prices_path).is_absolute()
    assert Path(settings.manual_prices_path).name == "manual_prices.csv"
```

Add `from pathlib import Path` to that file's imports if it is not already there.

- [ ] **Step 8: Run the tests to verify they pass**

Run: `cd backend && python -m pytest tests/unit/test_providers_manual.py tests/unit/test_providers_chain.py tests/unit/test_settings.py -q`
Expected: PASS, 26 new tests plus the existing settings tests.

- [ ] **Step 9: Run the whole gate**

Expected: 380 backend passed, 35 realdata, ruff and mypy clean, 93 frontend.

- [ ] **Step 10: Commit**

```bash
git add backend/app/providers/ backend/tests/unit/test_providers_manual.py \
        backend/tests/unit/test_providers_chain.py backend/tests/unit/test_settings.py \
        backend/app/settings.py backend/pyproject.toml \
        config/manual_prices.example.csv .gitignore .env.example
git commit -m "feat(providers): the provider Protocols, the manual fallback and the recording chain

Claude-Session: https://claude.ai/code/session_01UzfynZY2Rqs9oMAtCivdSF"
```

---
### Task 5: OpenFIGI, Yahoo and the ECB, against recorded fixtures

The three adopted providers (M2 spec section 3.1), all keyless. CI never touches the network: every test runs through `httpx.MockTransport` against a fixture. A test that needs the internet is a test that fails for reasons unrelated to the code.

**The fixtures are recorded in shape and invented in content.** They reproduce the exact JSON structure each endpoint returns — including the null gaps Yahoo leaves on holidays and the `gmtoffset` that decides what day a bar belongs to — with instruments that do not exist. Recording the owner's own ISINs into a tracked file would breach the standing rule, and the parser is tested against the shape, not the content.

**Files:**
- Create: `backend/app/providers/openfigi.py`
- Create: `backend/app/providers/yahoo.py`
- Create: `backend/app/providers/ecb.py`
- Create: `backend/tests/fixtures/providers/openfigi_mapping.json`
- Create: `backend/tests/fixtures/providers/openfigi_not_found.json`
- Create: `backend/tests/fixtures/providers/yahoo_chart_eur.json`
- Create: `backend/tests/fixtures/providers/yahoo_chart_asx.json`
- Create: `backend/tests/fixtures/providers/yahoo_not_found.json`
- Create: `backend/tests/fixtures/providers/frankfurter_series.json`
- Test: `backend/tests/unit/test_providers_openfigi.py`
- Test: `backend/tests/unit/test_providers_yahoo.py`
- Test: `backend/tests/unit/test_providers_ecb.py`

**Interfaces:**
- Consumes: `PriceProvider`, `SymbolResolver`, `FxProvider`, `PricePoint`, `PriceSeries`, `FxPoint`, `FxSeries`, `SymbolCandidate`, `ProviderError`, `BACKFILL_YEARS` from `app.providers.base`.
- Produces:
  - `OpenFigiResolver(client: httpx.Client)` with `.name = "openfigi"`, `.candidates(isin) -> tuple[SymbolCandidate, ...]`, and module constant `EXCHANGE_SUFFIX: Mapping[str, str]`
  - `YahooPrices(client: httpx.Client)` with `.name = "yahoo"`, `.full_series(symbol)`, `.series_since(symbol, since)`
  - `EcbRates(client: httpx.Client)` with `.name = "ecb"`, `.series(from_ccy, to_ccy, *, start, end)`

- [ ] **Step 1: Write the fixtures**

Create `backend/tests/fixtures/providers/openfigi_mapping.json`:

```json
[
  {
    "data": [
      {
        "figi": "BBG000000001",
        "name": "EXAMPLE HOLDINGS NV",
        "ticker": "EXA",
        "exchCode": "NA",
        "compositeFIGI": "BBG000000001",
        "securityType": "Common Stock",
        "marketSector": "Equity",
        "securityType2": "Common Stock",
        "securityDescription": "EXA"
      },
      {
        "figi": "BBG000000002",
        "name": "EXAMPLE HOLDINGS NV",
        "ticker": "EXA",
        "exchCode": "GY",
        "compositeFIGI": "BBG000000002",
        "securityType": "Common Stock",
        "marketSector": "Equity",
        "securityType2": "Common Stock",
        "securityDescription": "EXA"
      }
    ]
  }
]
```

Create `backend/tests/fixtures/providers/openfigi_not_found.json`:

```json
[{ "warning": "No identifier found." }]
```

Create `backend/tests/fixtures/providers/yahoo_chart_eur.json`. Timestamps are 2025-01-06, 2025-01-07 and 2025-01-08 at 08:00 UTC with a `gmtoffset` of 3600, and the middle day is a null gap:

```json
{
  "chart": {
    "result": [
      {
        "meta": {
          "currency": "EUR",
          "symbol": "EXA.AS",
          "exchangeName": "AMS",
          "exchangeTimezoneName": "Europe/Amsterdam",
          "gmtoffset": 3600,
          "regularMarketPrice": 12.75
        },
        "timestamp": [1736150400, 1736236800, 1736323200],
        "indicators": {
          "quote": [{ "close": [12.5, null, 12.75] }],
          "adjclose": [{ "adjclose": [12.2, null, 12.45] }]
        }
      }
    ],
    "error": null
  }
}
```

Create `backend/tests/fixtures/providers/yahoo_chart_asx.json`. The one bar is timestamped 2025-01-05 23:00 UTC with `gmtoffset` 39600 — which is 2025-01-06 in Sydney, and the day the bar actually belongs to:

```json
{
  "chart": {
    "result": [
      {
        "meta": {
          "currency": "AUD",
          "symbol": "EXB.AX",
          "exchangeName": "ASX",
          "exchangeTimezoneName": "Australia/Sydney",
          "gmtoffset": 39600,
          "regularMarketPrice": 0.4
        },
        "timestamp": [1736118000],
        "indicators": {
          "quote": [{ "close": [0.4] }],
          "adjclose": [{ "adjclose": [0.4] }]
        }
      }
    ],
    "error": null
  }
}
```

Create `backend/tests/fixtures/providers/yahoo_not_found.json`:

```json
{
  "chart": {
    "result": null,
    "error": { "code": "Not Found", "description": "No data found, symbol may be delisted" }
  }
}
```

Create `backend/tests/fixtures/providers/frankfurter_series.json`:

```json
{
  "amount": 1.0,
  "base": "EUR",
  "start_date": "2025-01-06",
  "end_date": "2025-01-08",
  "rates": {
    "2025-01-06": { "USD": 1.04 },
    "2025-01-07": { "USD": 1.05 },
    "2025-01-08": { "USD": 1.06 }
  }
}
```

- [ ] **Step 2: Write the failing tests**

Create `backend/tests/unit/test_providers_openfigi.py`:

```python
"""ISIN -> ticker, via OpenFIGI. Keyless below 25 requests a minute.

It resolved every ISIN in the export, which is why it was adopted -- and it
resolved roughly one in nine to the wrong instrument, which is why what it
returns is called a CANDIDATE everywhere in this codebase. This module's job is
to turn an ISIN into a list of things worth checking; `domain/symbols.py` decides
which of them the ledger agrees with.

The exchange-code mapping is where the candidates come from. OpenFIGI answers in
Bloomberg exchange codes and Yahoo wants its own suffixes, so one ISIN produces
one candidate per listing plus a bare-ticker fallback for anything unmapped --
better one extra candidate the discriminator rejects in a millisecond than a
silent miss on a venue nobody thought of.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.providers.base import ProviderError
from app.providers.openfigi import EXCHANGE_SUFFIX, OpenFigiResolver

FIXTURES = Path(__file__).parents[1] / "fixtures" / "providers"

def client_returning(payload: object, status: int = 200) -> httpx.Client:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=payload)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    client.seen = seen  # type: ignore[attr-defined]
    return client

def fixture(name: str) -> object:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))

class TestCandidates:
    def test_maps_each_listing_to_a_yahoo_symbol(self) -> None:
        resolver = OpenFigiResolver(client_returning(fixture("openfigi_mapping.json")))
        symbols = [c.symbol for c in resolver.candidates("NL0000000001")]
        assert "EXA.AS" in symbols   # Bloomberg NA -> Euronext Amsterdam
        assert "EXA.DE" in symbols   # Bloomberg GY -> Xetra

    def test_always_offers_the_bare_ticker_as_well(self) -> None:
        """US listings carry no suffix, and an unmapped exchange code would
        otherwise produce no candidate at all. One extra candidate costs a
        rejection; a missing one costs a quarantine."""
        resolver = OpenFigiResolver(client_returning(fixture("openfigi_mapping.json")))
        assert "EXA" in [c.symbol for c in resolver.candidates("NL0000000001")]

    def test_offers_each_symbol_once(self) -> None:
        resolver = OpenFigiResolver(client_returning(fixture("openfigi_mapping.json")))
        symbols = [c.symbol for c in resolver.candidates("NL0000000001")]
        assert len(symbols) == len(set(symbols))

    def test_carries_the_name_and_exchange_for_the_quarantine(self) -> None:
        """An operator answering a quarantine is choosing between tickers. A
        list of bare symbols with no names is a list they cannot answer."""
        resolver = OpenFigiResolver(client_returning(fixture("openfigi_mapping.json")))
        first = resolver.candidates("NL0000000001")[0]
        assert first.name == "EXAMPLE HOLDINGS NV"
        assert first.exchange_code in EXCHANGE_SUFFIX
        assert first.source == "openfigi"

    def test_an_unknown_isin_yields_nothing(self) -> None:
        """A warning, not an error: an instrument with no public identifier is a
        fact about the data, and it belongs in the quarantine rather than
        stopping the run."""
        resolver = OpenFigiResolver(client_returning(fixture("openfigi_not_found.json")))
        assert resolver.candidates("NL0000000009") == ()

    def test_asks_for_the_isin_by_the_identifier_type_openfigi_expects(self) -> None:
        client = client_returning(fixture("openfigi_mapping.json"))
        OpenFigiResolver(client).candidates("NL0000000001")
        body = json.loads(client.seen[0].content)  # type: ignore[attr-defined]
        assert body == [{"idType": "ID_ISIN", "idValue": "NL0000000001"}]

class TestFailures:
    def test_raises_rather_than_reporting_no_candidates_on_a_server_error(self) -> None:
        """"Answered with nothing" and "could not be reached" are different
        facts. Collapsing them would quarantine an instrument for a network
        blip and tell the operator to go and find a ticker that already works."""
        resolver = OpenFigiResolver(client_returning({}, status=500))
        with pytest.raises(ProviderError):
            resolver.candidates("NL0000000001")

    def test_raises_on_a_rate_limit(self) -> None:
        resolver = OpenFigiResolver(client_returning({}, status=429))
        with pytest.raises(ProviderError, match="429"):
            resolver.candidates("NL0000000001")

    def test_raises_when_the_response_is_not_the_shape_it_documents(self) -> None:
        resolver = OpenFigiResolver(client_returning({"unexpected": True}))
        with pytest.raises(ProviderError):
            resolver.candidates("NL0000000001")
```

Create `backend/tests/unit/test_providers_yahoo.py`:

```python
"""Daily bars from Yahoo's chart endpoint. Keyless, roughly five years per call.

Adopted for prices because it covers every venue the portfolio touches and
reports the series currency, which is one of the three things the symbol
discriminator checks. Undocumented, and acceptable on M2-6's terms: personal use
only. The chained-provider design keeps the cost of replacing it low.

Three parsing details decide whether the numbers are right:

* `close` is split-adjusted and dividend-UNadjusted; `adjclose` is both. They go
  into two columns because parent doc Sec 7.5 forbids any call path from reaching
  both.
* Yahoo leaves `null` in the arrays on days a venue was shut. A null is a
  missing day, not a zero -- and a zero close would sail through the symbol
  discriminator's division as an infinity or a crash.
* A timestamp is UTC, but the day a bar belongs to is the day at the EXCHANGE.
  An ASX bar stamped 23:00 UTC belongs to the following day in Sydney, and
  reading it as a UTC date would shift the whole series back by one -- enough to
  put a price on a public holiday and misalign every trade the discriminator
  checks.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from app.providers.base import ProviderError
from app.providers.yahoo import YahooPrices

D = Decimal
FIXTURES = Path(__file__).parents[1] / "fixtures" / "providers"

def client_returning(payload: object, status: int = 200) -> httpx.Client:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=payload)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    client.seen = seen  # type: ignore[attr-defined]
    return client

def fixture(name: str) -> object:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))

class TestParsing:
    def test_reports_the_currency_the_series_is_quoted_in(self) -> None:
        series = YahooPrices(client_returning(fixture("yahoo_chart_eur.json"))).full_series("EXA.AS")
        assert series is not None
        assert series.currency == "EUR"

    def test_reads_both_closes_into_separate_fields(self) -> None:
        series = YahooPrices(client_returning(fixture("yahoo_chart_eur.json"))).full_series("EXA.AS")
        assert series is not None
        first = series.points[0]
        assert first.close_unadjusted == D("12.5")
        assert first.close_adjusted == D("12.2")

    def test_prices_are_decimals_not_floats(self) -> None:
        """A JSON number is an IEEE double. Every price in this app goes through
        `str()` before `Decimal()` so the value stored is the one Yahoo printed."""
        series = YahooPrices(client_returning(fixture("yahoo_chart_eur.json"))).full_series("EXA.AS")
        assert series is not None
        assert all(isinstance(p.close_unadjusted, Decimal) for p in series.points)

    def test_drops_the_days_yahoo_left_null(self) -> None:
        series = YahooPrices(client_returning(fixture("yahoo_chart_eur.json"))).full_series("EXA.AS")
        assert series is not None
        assert [p.on for p in series.points] == [date(2025, 1, 6), date(2025, 1, 8)]

    def test_dates_a_bar_by_the_exchange_day_not_the_utc_day(self) -> None:
        """The ASX fixture is stamped 2025-01-05 23:00 UTC with a +11 offset. In
        Sydney that is 2025-01-06, and Sydney is where the market was open."""
        series = YahooPrices(client_returning(fixture("yahoo_chart_asx.json"))).full_series("EXB.AX")
        assert series is not None
        assert [p.on for p in series.points] == [date(2025, 1, 6)]

    def test_records_itself_as_the_source(self) -> None:
        series = YahooPrices(client_returning(fixture("yahoo_chart_eur.json"))).full_series("EXA.AS")
        assert series is not None
        assert series.source == "yahoo"

    def test_returns_points_in_date_order(self) -> None:
        series = YahooPrices(client_returning(fixture("yahoo_chart_eur.json"))).full_series("EXA.AS")
        assert series is not None
        assert list(series.points) == sorted(series.points, key=lambda p: p.on)

class TestRequests:
    def test_the_full_history_asks_for_the_fixed_five_years(self) -> None:
        """M2-7: one call per instrument, no date arithmetic."""
        client = client_returning(fixture("yahoo_chart_eur.json"))
        YahooPrices(client).full_series("EXA.AS")
        request = client.seen[0]  # type: ignore[attr-defined]
        assert request.url.params["range"] == "5y"
        assert request.url.params["interval"] == "1d"
        assert "EXA.AS" in str(request.url)

    def test_an_incremental_call_asks_only_for_what_the_cache_lacks(self) -> None:
        """M2-9. `period1` is a few days before the requested date so a provider
        that revises its most recent bars corrects them rather than leaving a
        stale close permanently cached."""
        client = client_returning(fixture("yahoo_chart_eur.json"))
        YahooPrices(client).series_since("EXA.AS", date(2025, 6, 1))
        request = client.seen[0]  # type: ignore[attr-defined]
        assert "period1" in request.url.params
        assert "period2" in request.url.params
        assert "range" not in request.url.params

    def test_sends_a_user_agent(self) -> None:
        """Without one the endpoint answers 429 to everything, which would read
        as a rate limit nobody hit."""
        client = client_returning(fixture("yahoo_chart_eur.json"))
        YahooPrices(client).full_series("EXA.AS")
        assert client.seen[0].headers.get("user-agent")  # type: ignore[attr-defined]

class TestNoSeries:
    def test_a_delisted_or_unknown_symbol_answers_nothing(self) -> None:
        """`None`, not an exception: an instrument with no series is a normal
        outcome that ends as `coverage: "missing"` and a `null` on the chart."""
        assert (
            YahooPrices(client_returning(fixture("yahoo_not_found.json"), status=404)).full_series(
                "NOPE"
            )
            is None
        )

    def test_a_server_error_raises_instead(self) -> None:
        with pytest.raises(ProviderError):
            YahooPrices(client_returning({}, status=500)).full_series("EXA.AS")

    def test_a_response_missing_the_arrays_raises(self) -> None:
        payload = {"chart": {"result": [{"meta": {"currency": "EUR", "gmtoffset": 0}}]}}
        with pytest.raises(ProviderError):
            YahooPrices(client_returning(payload)).full_series("EXA.AS")
```

Create `backend/tests/unit/test_providers_ecb.py`:

```python
"""ECB reference rates via Frankfurter. Keyless, full history in one call.

The direction is the whole test suite. `FxDaily.rate` is units of `from_ccy` per
1 unit of `to_ccy` -- the same direction as `domain.money.FxRate` and as DeGiro's
own `Exchange rate` column -- so you DIVIDE a foreign amount by it to reach EUR.

Frankfurter is therefore queried with `base=EUR&symbols=USD`, which returns "1
EUR = 1.04 USD". That number IS the stored rate, verbatim, with no reciprocal:
taking one would cost exactness on every row and put two directions in one
codebase, which is precisely the failure `FxRate` exists to make impossible.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from app.domain.money import FxRate, Money
from app.providers.base import ProviderError
from app.providers.ecb import EcbRates

D = Decimal
FIXTURES = Path(__file__).parents[1] / "fixtures" / "providers"

def client_returning(payload: object, status: int = 200) -> httpx.Client:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=payload)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    client.seen = seen  # type: ignore[attr-defined]
    return client

def fixture() -> object:
    return json.loads((FIXTURES / "frankfurter_series.json").read_text(encoding="utf-8"))

class TestDirection:
    def test_queries_with_eur_as_the_base(self) -> None:
        client = client_returning(fixture())
        EcbRates(client).series("USD", "EUR", start=date(2025, 1, 6), end=date(2025, 1, 8))
        params = client.seen[0].url.params  # type: ignore[attr-defined]
        assert params["base"] == "EUR"
        assert params["symbols"] == "USD"

    def test_stores_frankfurters_number_verbatim(self) -> None:
        series = EcbRates(client_returning(fixture())).series(
            "USD", "EUR", start=date(2025, 1, 6), end=date(2025, 1, 8)
        )
        assert series is not None
        assert series.points[0].rate == D("1.04")

    def test_the_stored_rate_converts_the_way_FxRate_does(self) -> None:
        """104 USD at 1.04 USD per EUR is 100 EUR. The reciprocal would say
        108.16 -- wrong by 8% and entirely believable."""
        series = EcbRates(client_returning(fixture())).series(
            "USD", "EUR", start=date(2025, 1, 6), end=date(2025, 1, 8)
        )
        assert series is not None
        rate = FxRate("USD", "EUR", series.points[0].rate, date(2025, 1, 6))
        assert rate.convert(Money(D("104.00"), "USD")).amount == D("100")

class TestParsing:
    def test_returns_one_point_per_published_day_in_order(self) -> None:
        series = EcbRates(client_returning(fixture())).series(
            "USD", "EUR", start=date(2025, 1, 6), end=date(2025, 1, 8)
        )
        assert series is not None
        assert [p.on for p in series.points] == [
            date(2025, 1, 6),
            date(2025, 1, 7),
            date(2025, 1, 8),
        ]

    def test_rates_are_decimals_not_floats(self) -> None:
        series = EcbRates(client_returning(fixture())).series(
            "USD", "EUR", start=date(2025, 1, 6), end=date(2025, 1, 8)
        )
        assert series is not None
        assert all(isinstance(p.rate, Decimal) for p in series.points)

    def test_records_itself_as_the_source(self) -> None:
        series = EcbRates(client_returning(fixture())).series(
            "USD", "EUR", start=date(2025, 1, 6), end=date(2025, 1, 8)
        )
        assert series is not None
        assert series.source == "ecb"

    def test_the_identity_pair_needs_no_request(self) -> None:
        """EUR to EUR is 1 by definition. Fetching it would record a network
        fact where an arithmetic one belongs, and Frankfurter does not serve it."""
        client = client_returning(fixture())
        series = EcbRates(client).series("EUR", "EUR", start=date(2025, 1, 6), end=date(2025, 1, 8))
        assert series is None
        assert client.seen == []  # type: ignore[attr-defined]

    def test_an_unsupported_currency_answers_nothing(self) -> None:
        assert (
            EcbRates(client_returning({"rates": {}})).series(
                "XYZ", "EUR", start=date(2025, 1, 6), end=date(2025, 1, 8)
            )
            is None
        )

class TestFailures:
    def test_a_server_error_raises(self) -> None:
        with pytest.raises(ProviderError):
            EcbRates(client_returning({}, status=500)).series(
                "USD", "EUR", start=date(2025, 1, 6), end=date(2025, 1, 8)
            )

    def test_refuses_a_pair_that_does_not_reach_the_base_currency(self) -> None:
        """Frankfurter is queried with EUR as the base, so it can only answer
        pairs ending in EUR. Asking for USD->AUD would silently return
        EUR-denominated numbers under the wrong label."""
        with pytest.raises(ValueError, match="EUR"):
            EcbRates(client_returning(fixture())).series(
                "USD", "AUD", start=date(2025, 1, 6), end=date(2025, 1, 8)
            )
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd backend && python -m pytest tests/unit/test_providers_openfigi.py tests/unit/test_providers_yahoo.py tests/unit/test_providers_ecb.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.providers.openfigi'`

- [ ] **Step 4: Write OpenFIGI**

Create `backend/app/providers/openfigi.py`:

```python
"""ISIN -> ticker candidates, via OpenFIGI. Free, no key below 25 req/min.

Adopted because it resolved every ISIN in the export where the alternatives could
not (M2 spec section 3.1). It is a CANDIDATE generator and nothing more: roughly
one ISIN in nine resolved -- correctly, by OpenFIGI's own lights -- to a
leveraged or inverse product on the same underlying. OpenFIGI is not wrong about
that; a 2x-short ETF genuinely carries its own identifiers. The question "which of
these did the account actually trade" is one only the ledger can answer, and
`domain/symbols.py` answers it.

The exchange mapping is where candidates come from. OpenFIGI speaks Bloomberg
exchange codes and Yahoo wants its own suffixes, so each listing becomes one
candidate. Anything unmapped falls back to the bare ticker, which is also always
offered: US listings carry no suffix at all, and an extra candidate the
discriminator rejects in a millisecond is cheaper than a venue nobody mapped.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx

from app.providers.base import ProviderError, SymbolCandidate

_ENDPOINT = "https://api.openfigi.com/v3/mapping"
_SOURCE = "openfigi"

#: Bloomberg exchange code -> Yahoo suffix. Covers the venues the portfolio
#: touches (Euronext, Xetra, ASX, LSE, US) plus the neighbours it is one trade
#: away from. Unmapped codes are not an error: the bare ticker is always offered
#: as well, so a missing entry costs a candidate, not a resolution.
EXCHANGE_SUFFIX: Mapping[str, str] = {
    "NA": ".AS",   # Euronext Amsterdam
    "FP": ".PA",   # Euronext Paris
    "BB": ".BR",   # Euronext Brussels
    "GY": ".DE",   # Xetra
    "GR": ".DE",   # Germany, composite
    "GF": ".F",    # Frankfurt floor
    "TQ": ".DE",   # Tradegate
    "LN": ".L",    # London
    "SM": ".MC",   # Madrid
    "IM": ".MI",   # Milan
    "SW": ".SW",   # SIX Swiss
    "AT": ".AX",   # ASX
    "HK": ".HK",   # Hong Kong
    "US": "",      # US composite
    "UN": "",      # NYSE
    "UQ": "",      # Nasdaq
    "UW": "",      # Nasdaq
    "UA": "",      # NYSE American
    "UR": "",      # US OTC
}

class OpenFigiResolver:
    """`SymbolResolver` over OpenFIGI's mapping endpoint."""

    name = _SOURCE

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def candidates(self, isin: str) -> tuple[SymbolCandidate, ...]:
        try:
            response = self._client.post(
                _ENDPOINT,
                json=[{"idType": "ID_ISIN", "idValue": isin}],
                headers={"Content-Type": "application/json"},
                timeout=20.0,
            )
        except httpx.HTTPError as unreachable:
            raise ProviderError(f"openfigi: {isin}: {unreachable}") from unreachable

        if response.status_code != 200:
            # Reached, not answered. Collapsing this into "no candidates" would
            # quarantine an instrument for a network blip and send the operator
            # looking for a ticker that already works.
            raise ProviderError(f"openfigi: {isin}: HTTP {response.status_code}")

        payload = response.json()
        if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
            raise ProviderError(f"openfigi: {isin}: unexpected response shape")

        first = payload[0]
        if "warning" in first:
            # A genuine "no such identifier". A fact about the data, and it
            # belongs in the quarantine rather than stopping the run.
            return ()

        entries = first.get("data")
        if not isinstance(entries, list):
            raise ProviderError(f"openfigi: {isin}: response carries no data list")

        return tuple(_candidates_from(entries))

def _candidates_from(entries: list[Any]) -> list[SymbolCandidate]:
    found: list[SymbolCandidate] = []
    seen: set[str] = set()

    def offer(symbol: str, name: str, exchange: str) -> None:
        if symbol and symbol not in seen:
            seen.add(symbol)
            found.append(
                SymbolCandidate(
                    symbol=symbol, name=name, exchange_code=exchange, source=_SOURCE
                )
            )

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        ticker = str(entry.get("ticker") or "").strip()
        if not ticker:
            continue
        name = str(entry.get("name") or "").strip()
        exchange = str(entry.get("exchCode") or "").strip()
        suffix = EXCHANGE_SUFFIX.get(exchange)
        if suffix is not None:
            offer(f"{ticker}{suffix}", name, exchange)

    # The bare ticker, always, and last: it is the fallback for an unmapped
    # exchange and the only right answer for a US listing.
    for entry in entries:
        if isinstance(entry, dict):
            ticker = str(entry.get("ticker") or "").strip()
            if ticker:
                offer(
                    ticker,
                    str(entry.get("name") or "").strip(),
                    str(entry.get("exchCode") or "").strip(),
                )

    return found
```

- [ ] **Step 5: Write the Yahoo provider**

Create `backend/app/providers/yahoo.py`:

```python
"""Daily bars from Yahoo's chart endpoint. Free, no key, ~5 years per call.

Adopted because it covers every venue the portfolio touches and reports the
series currency, which is one of the three conditions the symbol discriminator
checks (M2 spec section 3.1). Undocumented, and acceptable on M2-6's terms:
personal use only. It carries no service guarantee and its terms are grey beyond
that, which reopens the moment this project is published -- the chained-provider
design keeps the cost of replacing it to writing one new `PriceProvider`.

Three parsing details decide whether the numbers are right, and each has a test:

* `quote[0].close` is split-adjusted and dividend-UNadjusted; `adjclose[0]` is
  both. They land in two columns because parent doc Sec 7.5 forbids any call path
  from reaching both -- total return from dividend-adjusted prices PLUS dividend
  income counts dividends twice.
* `null` appears in both arrays on days a venue was shut. A null is a missing
  day, not a zero. A zero close would divide by zero in the discriminator and
  value a position at nothing on the chart.
* A timestamp is UTC seconds, but a bar belongs to the day at the EXCHANGE. An
  ASX bar stamped 23:00 UTC is the next day in Sydney; read as a UTC date the
  whole series shifts back by one, putting prices on public holidays and
  misaligning every trade the discriminator checks. `meta.gmtoffset` is the fix.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from app.providers.base import PricePoint, PriceSeries, ProviderError

_ENDPOINT = "https://query1.finance.yahoo.com/v8/finance/chart/"
_SOURCE = "yahoo"

#: Without one the endpoint answers 429 to everything, which reads as a rate
#: limit nobody hit.
_HEADERS = {"User-Agent": "portfolio-tracker/0.1 (personal use)"}

#: How far before the requested date an incremental call reaches back. A
#: provider revises its most recent bars; without the overlap a stale close
#: would stay cached for ever because the incremental window never covers it
#: again.
_OVERLAP = timedelta(days=5)

class YahooPrices:
    """`PriceProvider` over the chart endpoint."""

    name = _SOURCE

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def full_series(self, symbol: str) -> PriceSeries | None:
        """The fixed five years (M2-7). `range=5y`: one call, no date arithmetic."""
        return self._fetch(symbol, {"range": "5y", "interval": "1d"})

    def series_since(self, symbol: str, since: date) -> PriceSeries | None:
        """Only what the cache lacks (M2-9), with a few days of overlap."""
        start = datetime.combine(since - _OVERLAP, datetime.min.time(), tzinfo=UTC)
        end = datetime.now(tz=UTC) + timedelta(days=1)
        return self._fetch(
            symbol,
            {
                "period1": str(int(start.timestamp())),
                "period2": str(int(end.timestamp())),
                "interval": "1d",
            },
        )

    def _fetch(self, symbol: str, params: dict[str, str]) -> PriceSeries | None:
        try:
            response = self._client.get(
                f"{_ENDPOINT}{symbol}", params=params, headers=_HEADERS, timeout=30.0
            )
        except httpx.HTTPError as unreachable:
            raise ProviderError(f"yahoo: {symbol}: {unreachable}") from unreachable

        if response.status_code in (404, 422):
            # A delisted or unknown symbol. A normal outcome that ends as
            # `coverage: "missing"` and a `null` on the chart.
            return None
        if response.status_code != 200:
            raise ProviderError(f"yahoo: {symbol}: HTTP {response.status_code}")

        return _parse(symbol, response.json())

def _parse(symbol: str, payload: Any) -> PriceSeries | None:
    chart = payload.get("chart") if isinstance(payload, dict) else None
    if not isinstance(chart, dict):
        raise ProviderError(f"yahoo: {symbol}: response carries no chart")

    results = chart.get("result")
    if not results:
        return None

    result = results[0]
    meta = result.get("meta") or {}
    currency = str(meta.get("currency") or "").upper()
    if not currency:
        raise ProviderError(f"yahoo: {symbol}: response states no currency")

    # Seconds to add to a UTC timestamp to reach the exchange's local clock.
    offset = int(meta.get("gmtoffset") or 0)

    stamps = result.get("timestamp")
    indicators = result.get("indicators") or {}
    quotes = (indicators.get("quote") or [{}])[0]
    adjusted = (indicators.get("adjclose") or [{}])[0]
    closes = quotes.get("close")
    adj_closes = adjusted.get("adjclose")

    if not isinstance(stamps, list) or not isinstance(closes, list):
        raise ProviderError(f"yahoo: {symbol}: response carries no close series")
    if not isinstance(adj_closes, list) or len(adj_closes) != len(closes):
        # Falling back to the plain close here would put a dividend-unadjusted
        # number in the total-return column, and Sec 7.5's whole point is that
        # the two must never be confused.
        raise ProviderError(f"yahoo: {symbol}: adjusted and plain closes disagree in length")

    points: list[PricePoint] = []
    for stamp, close, adj_close in zip(stamps, closes, adj_closes, strict=True):
        if close is None or adj_close is None:
            continue  # a day the venue was shut; a null is missing, not zero
        try:
            unadjusted_value = Decimal(str(close))
            adjusted_value = Decimal(str(adj_close))
        except InvalidOperation as bad:
            raise ProviderError(f"yahoo: {symbol}: unreadable close {close!r}") from bad
        if unadjusted_value <= 0 or adjusted_value <= 0:
            continue
        points.append(
            PricePoint(
                on=datetime.fromtimestamp(int(stamp) + offset, tz=UTC).date(),
                close_unadjusted=unadjusted_value,
                close_adjusted=adjusted_value,
            )
        )

    return PriceSeries(
        symbol=symbol,
        currency=currency,
        source=_SOURCE,
        points=tuple(sorted(points, key=lambda point: point.on)),
    )
```

- [ ] **Step 6: Write the ECB provider**

Create `backend/app/providers/ecb.py`:

```python
"""ECB reference rates, via Frankfurter. Free, no key, full history in one call.

Parent doc Sec 8.2 anticipated ECB rates and the spike confirmed them: one
request covers five years of a currency pair, which is the whole FX backfill.

**The direction is the entire design of this module.** `FxDaily.rate` holds units
of `from_ccy` per 1 unit of `to_ccy`, matching `domain.money.FxRate` and DeGiro's
own `Exchange rate` column -- so a foreign amount is DIVIDED by it to reach EUR.

Frankfurter is therefore queried with the BASE currency as its base:
`base=EUR&symbols=USD` returns "1 EUR = 1.04 USD", and 1.04 is the stored rate
verbatim. Querying the other way and taking a reciprocal would cost exactness on
every row and put two FX directions in one codebase, which is exactly the
plausible-wrong-number failure `FxRate` exists to prevent.

A consequence worth stating: this provider can only answer pairs whose `to_ccy`
is the base currency. Asking for USD->AUD raises rather than returning
EUR-denominated numbers under the wrong label.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from app.providers.base import FxPoint, FxSeries, ProviderError

_ENDPOINT = "https://api.frankfurter.app/"
_SOURCE = "ecb"
_BASE = "EUR"

class EcbRates:
    """`FxProvider` over Frankfurter's ECB series."""

    name = _SOURCE

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def series(
        self, from_ccy: str, to_ccy: str, *, start: date, end: date
    ) -> FxSeries | None:
        if to_ccy != _BASE:
            raise ValueError(
                f"frankfurter is queried with {_BASE} as its base, so it cannot answer "
                f"{from_ccy}->{to_ccy}; a rate to anything but {_BASE} would be a "
                "different number under this label"
            )
        if from_ccy == to_ccy:
            # 1 by definition. Fetching it would record a network fact where an
            # arithmetic one belongs, and Frankfurter does not serve it.
            return None

        url = f"{_ENDPOINT}{start.isoformat()}..{end.isoformat()}"
        try:
            response = self._client.get(
                url, params={"base": _BASE, "symbols": from_ccy}, timeout=30.0
            )
        except httpx.HTTPError as unreachable:
            raise ProviderError(f"ecb: {from_ccy}->{to_ccy}: {unreachable}") from unreachable

        if response.status_code != 200:
            raise ProviderError(f"ecb: {from_ccy}->{to_ccy}: HTTP {response.status_code}")

        points = _parse(from_ccy, response.json())
        if not points:
            return None
        return FxSeries(from_ccy=from_ccy, to_ccy=to_ccy, source=_SOURCE, points=points)

def _parse(from_ccy: str, payload: Any) -> tuple[FxPoint, ...]:
    rates = payload.get("rates") if isinstance(payload, dict) else None
    if not isinstance(rates, dict):
        raise ProviderError(f"ecb: {from_ccy}: response carries no rates")

    points: list[FxPoint] = []
    for day, quoted in rates.items():
        if not isinstance(quoted, dict) or from_ccy not in quoted:
            continue
        try:
            # `str()` first: a JSON number is an IEEE double, and every rate in
            # this app is the exact figure the ECB published.
            rate = Decimal(str(quoted[from_ccy]))
            on = datetime.strptime(day, "%Y-%m-%d").date()
        except (InvalidOperation, ValueError) as bad:
            raise ProviderError(f"ecb: {from_ccy}: unreadable rate on {day!r}") from bad
        if rate > 0:
            points.append(FxPoint(on=on, rate=rate))

    return tuple(sorted(points, key=lambda point: point.on))
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `cd backend && python -m pytest tests/unit/test_providers_openfigi.py tests/unit/test_providers_yahoo.py tests/unit/test_providers_ecb.py -q`
Expected: PASS, 28 tests.

- [ ] **Step 8: Run the whole gate**

Expected: 408 backend passed, 35 realdata, ruff and mypy clean, 93 frontend.

- [ ] **Step 9: Commit**

```bash
git add backend/app/providers/openfigi.py backend/app/providers/yahoo.py \
        backend/app/providers/ecb.py backend/tests/fixtures/providers/ \
        backend/tests/unit/test_providers_openfigi.py \
        backend/tests/unit/test_providers_yahoo.py backend/tests/unit/test_providers_ecb.py
git commit -m "feat(providers): OpenFIGI, Yahoo and ECB, tested against recorded shapes

Fixtures reproduce each endpoint's exact JSON -- Yahoo's null holiday gaps and
its exchange-local gmtoffset included -- with instruments that do not exist. CI
never touches the network.

Claude-Session: https://claude.ai/code/session_01UzfynZY2Rqs9oMAtCivdSF"
```

---

### Task 6: Symbol resolution, the answers file, and the quarantine

The flow M2-2 describes: auto-accept what validates against the ledger, quarantine the rest, and answer it in `config/instrument_symbols.yaml`. Deliberately the same shape as M0's `corporate_action_review` and `corporate_actions.yaml` — one pattern for "the machine is unsure, a human decides", not two.

**Files:**
- Create: `backend/app/ingest/symbols.py`
- Create: `config/instrument_symbols.example.yaml`
- Test: `backend/tests/unit/test_ingest_symbols.py`
- Test: `backend/tests/integration/test_symbol_review.py`

**Interfaces:**
- Consumes: `judge`, `accepted_symbol`, `CandidateSeries`, `TradeObservation`, `Verdict` from `app.domain.symbols`; `derive_splits`, `Split` from `app.domain.splits`; `SymbolResolver`, `PriceProvider` from `app.providers.base`; `SymbolReview` from `app.models.market`.
- Produces:
  - `MANUAL_ANSWER = "manual"`
  - `MalformedSymbolAnswers(ValueError)`
  - `SymbolAnswer(isin: str, symbol: str | None, note: str)` — `symbol is None` means routed to `manual_prices.csv`
  - `load_symbol_answers(path: Path) -> dict[str, SymbolAnswer]`
  - `PricedRow(LedgerRow, Protocol)` adding `currency_local: str | None` and `product_name: str | None`
  - `observations(rows: Sequence[PricedRow]) -> dict[str, list[TradeObservation]]`
  - `instrument_names(rows: Sequence[PricedRow]) -> dict[str, str]`
  - `PendingSymbol(isin, product_name, trade_currency, verdicts: tuple[Verdict, ...])`
  - `ResolutionReport(resolved: dict[str, str | None], pending: tuple[PendingSymbol, ...])`
  - `resolve_symbols(rows, *, answers, resolver, prices) -> ResolutionReport`
  - `write_symbol_review(engine, report, *, detected_at: datetime) -> int`

- [ ] **Step 1: Write the failing unit test**

Create `backend/tests/unit/test_ingest_symbols.py`:

```python
"""Turning a ledger into symbols, and everything that refuses to be turned.

The auto-accept path is narrow on purpose (M2-2). An answer file entry wins
outright, because a human said so. Everything else has to clear
`domain/symbols.py` -- and the instruments that do not become questions rather
than guesses, because a false quarantine costs one question and a false accept
puts a badly wrong number on the chart with no clue that it is wrong.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.ingest.symbols import (
    MANUAL_ANSWER,
    MalformedSymbolAnswers,
    load_symbol_answers,
    instrument_names,
    observations,
    resolve_symbols,
)
from app.providers.base import PricePoint, PriceSeries, SymbolCandidate

D = Decimal
ZERO = D("0.00")

@dataclass(frozen=True, slots=True)
class Row:
    source_ref: str
    trade_date: date
    isin: str | None = "NL0000000001"
    product_name: str | None = "Example Holdings"
    trade_time: str | None = "10:00"
    txn_type: str = "BUY"
    quantity: Decimal | None = D("10")
    price_local: Decimal | None = D("20.00")
    currency_local: str | None = "EUR"
    value_base: Decimal | None = D("-200.00")
    net_base: Decimal = D("-200.00")
    fee_base: Decimal = ZERO
    autofx_fee_base: Decimal | None = ZERO
    tax_base: Decimal = ZERO
    order_ref: str | None = "ord-1"
    is_economic: bool = True

class StubResolver:
    name = "stub"

    def __init__(self, by_isin: dict[str, tuple[str, ...]]) -> None:
        self._by_isin = by_isin

    def candidates(self, isin: str) -> tuple[SymbolCandidate, ...]:
        return tuple(
            SymbolCandidate(symbol=s, name="Example", exchange_code="NA", source="stub")
            for s in self._by_isin.get(isin, ())
        )

class StubPrices:
    name = "stub"

    def __init__(self, by_symbol: dict[str, PriceSeries]) -> None:
        self._by_symbol = by_symbol
        self.asked: list[str] = []

    def full_series(self, symbol: str) -> PriceSeries | None:
        self.asked.append(symbol)
        return self._by_symbol.get(symbol)

    def series_since(self, symbol: str, since: date) -> PriceSeries | None:
        return self.full_series(symbol)

def series(symbol: str, closes: dict[date, str], currency: str = "EUR") -> PriceSeries:
    return PriceSeries(
        symbol=symbol,
        currency=currency,
        source="stub",
        points=tuple(
            PricePoint(on=on, close_unadjusted=D(v), close_adjusted=D(v))
            for on, v in sorted(closes.items())
        ),
    )

class TestAnswersFile:
    def test_a_missing_file_answers_nothing(self, tmp_path: Path) -> None:
        assert load_symbol_answers(tmp_path / "absent.yaml") == {}

    def test_reads_a_symbol_answer(self, tmp_path: Path) -> None:
        path = tmp_path / "instrument_symbols.yaml"
        path.write_text(
            "symbols:\n  - isin: NL0000000001\n    symbol: EXA.AS\n    note: checked\n",
            encoding="utf-8",
        )
        answers = load_symbol_answers(path)
        assert answers["NL0000000001"].symbol == "EXA.AS"
        assert answers["NL0000000001"].note == "checked"

    def test_manual_routes_to_the_price_file_rather_than_a_provider(self, tmp_path: Path) -> None:
        """M2 spec section 6.3: an instrument may be answered with a symbol or
        with `manual`. `None` is what the chain reads as "go straight to the
        file"."""
        path = tmp_path / "instrument_symbols.yaml"
        path.write_text(
            f"symbols:\n  - isin: US0000000404\n    symbol: {MANUAL_ANSWER}\n", encoding="utf-8"
        )
        assert load_symbol_answers(path)["US0000000404"].symbol is None

    @pytest.mark.parametrize(
        ("body", "fragment"),
        [
            ("symbols:\n  - symbol: EXA.AS\n", "isin"),
            ("symbols:\n  - isin: NL0000000001\n", "symbol"),
            ("symbols:\n  - isin: NL0000000001\n    ticker: EXA.AS\n", "unknown"),
            ("symbols:\n  - isin: NL0000000001\n    symbol: ''\n", "symbol"),
            ("not-a-mapping\n", "mapping"),
        ],
    )
    def test_refuses_a_shape_it_would_have_to_guess_at(
        self, tmp_path: Path, body: str, fragment: str
    ) -> None:
        """Same strictness as `load_resolutions`, for a sharper reason: a
        mistyped field here silently prices an instrument from the wrong series,
        and nothing downstream can tell."""
        path = tmp_path / "instrument_symbols.yaml"
        path.write_text(body, encoding="utf-8")
        with pytest.raises(MalformedSymbolAnswers, match=fragment):
            load_symbol_answers(path)

    def test_refuses_the_same_instrument_answered_twice(self, tmp_path: Path) -> None:
        path = tmp_path / "instrument_symbols.yaml"
        path.write_text(
            "symbols:\n"
            "  - isin: NL0000000001\n    symbol: EXA.AS\n"
            "  - isin: NL0000000001\n    symbol: EXA.DE\n",
            encoding="utf-8",
        )
        with pytest.raises(MalformedSymbolAnswers, match="twice"):
            load_symbol_answers(path)

class TestObservations:
    def test_takes_the_trade_currency_price_not_the_base_currency_one(self) -> None:
        """A provider quotes in its own currency. Comparing against a EUR price
        would make the FX rate a third unknown in a check that has two."""
        found = observations([Row("a", date(2025, 1, 6))])
        assert found["NL0000000001"][0].price_local == D("20.00")
        assert found["NL0000000001"][0].currency == "EUR"

    def test_skips_rows_that_state_no_local_price(self) -> None:
        found = observations([Row("a", date(2025, 1, 6), price_local=None)])
        assert found == {}

    def test_skips_suppressed_corporate_action_legs(self) -> None:
        """A split leg is not an executed price. Checking a series against one
        would compare a synthetic number to a real close."""
        found = observations([Row("a", date(2025, 1, 6), is_economic=False)])
        assert found == {}

    def test_includes_sells_as_well_as_buys(self) -> None:
        """A sale is an executed price too, and an instrument bought before the
        export window has nothing else to check against."""
        found = observations(
            [Row("a", date(2025, 1, 6), quantity=D("-10"), price_local=D("24.00"))]
        )
        assert found["NL0000000001"][0].price_local == D("24.00")

    def test_orders_observations_oldest_first(self) -> None:
        found = observations(
            [Row("b", date(2025, 2, 3)), Row("a", date(2025, 1, 6))]
        )
        assert [o.trade_date for o in found["NL0000000001"]] == [
            date(2025, 1, 6),
            date(2025, 2, 3),
        ]

    def test_names_each_instrument_from_the_ledger(self) -> None:
        """`models.ledger.Instrument` is declared but nothing populates it, so
        the product name comes from the transaction that carried it."""
        assert instrument_names([Row("a", date(2025, 1, 6))]) == {
            "NL0000000001": "Example Holdings"
        }

class TestResolution:
    TRADES = [Row("a", date(2025, 1, 6)), Row("b", date(2025, 2, 3), price_local=D("24.00"))]
    RIGHT = series("EXA.AS", {date(2025, 1, 6): "20.00", date(2025, 2, 3): "24.00"})
    WRONG = series("EXA2S.DE", {date(2025, 1, 6): "6.00", date(2025, 2, 3): "5.00"})

    def test_accepts_the_candidate_the_ledger_agrees_with(self) -> None:
        report = resolve_symbols(
            self.TRADES,
            answers={},
            resolver=StubResolver({"NL0000000001": ("EXA2S.DE", "EXA.AS")}),
            prices=StubPrices({"EXA.AS": self.RIGHT, "EXA2S.DE": self.WRONG}),
        )
        assert report.resolved == {"NL0000000001": "EXA.AS"}
        assert report.pending == ()

    def test_quarantines_when_only_an_impostor_came_back(self) -> None:
        report = resolve_symbols(
            self.TRADES,
            answers={},
            resolver=StubResolver({"NL0000000001": ("EXA2S.DE",)}),
            prices=StubPrices({"EXA2S.DE": self.WRONG}),
        )
        assert report.resolved == {}
        assert [p.isin for p in report.pending] == ["NL0000000001"]

    def test_the_quarantine_carries_the_measured_ratios(self) -> None:
        """The operator is being asked to choose. A row saying only "could not
        resolve" gives them nothing to choose with."""
        report = resolve_symbols(
            self.TRADES,
            answers={},
            resolver=StubResolver({"NL0000000001": ("EXA2S.DE",)}),
            prices=StubPrices({"EXA2S.DE": self.WRONG}),
        )
        verdict = report.pending[0].verdicts[0]
        assert verdict.symbol == "EXA2S.DE"
        assert verdict.ratios != ()
        assert not verdict.accepted

    def test_quarantines_an_instrument_with_no_candidates_at_all(self) -> None:
        report = resolve_symbols(
            self.TRADES,
            answers={},
            resolver=StubResolver({}),
            prices=StubPrices({}),
        )
        assert [p.isin for p in report.pending] == ["NL0000000001"]

    def test_an_answered_instrument_is_never_probed(self) -> None:
        """A human already decided. Asking a provider anyway would spend a call
        to second-guess them, and would quarantine their answer if the network
        happened to disagree."""
        prices = StubPrices({})
        report = resolve_symbols(
            self.TRADES,
            answers={"NL0000000001": _answer("NL0000000001", "EXA.AS")},
            resolver=StubResolver({"NL0000000001": ("EXA.AS",)}),
            prices=prices,
        )
        assert report.resolved == {"NL0000000001": "EXA.AS"}
        assert prices.asked == []

    def test_an_instrument_answered_manual_resolves_to_none(self) -> None:
        report = resolve_symbols(
            self.TRADES,
            answers={"NL0000000001": _answer("NL0000000001", None)},
            resolver=StubResolver({}),
            prices=StubPrices({}),
        )
        assert report.resolved == {"NL0000000001": None}
        assert report.pending == ()

    def test_carries_the_split_ratio_into_the_check(self) -> None:
        """The pre-split trade only agrees once M1's derived ratio is applied.
        Without it the right symbol would be quarantined on every instrument
        that ever split."""
        rows = [
            Row("a", date(2025, 1, 6), price_local=D("200.00")),
            Row("b", date(2025, 2, 3), price_local=D("24.00")),
            Row(
                "split-out",
                date(2025, 1, 8),
                quantity=D("-1"),
                price_local=D("200.00"),
                order_ref=None,
                is_economic=False,
            ),
            Row(
                "split-in",
                date(2025, 1, 8),
                quantity=D("10"),
                price_local=D("20.00"),
                order_ref=None,
                is_economic=False,
            ),
        ]
        report = resolve_symbols(
            rows,
            answers={},
            resolver=StubResolver({"NL0000000001": ("EXA.AS",)}),
            prices=StubPrices({"EXA.AS": self.RIGHT}),
        )
        assert report.resolved == {"NL0000000001": "EXA.AS"}

def _answer(isin: str, symbol: str | None):
    from app.ingest.symbols import SymbolAnswer

    return SymbolAnswer(isin=isin, symbol=symbol, note="")
```

- [ ] **Step 2: Write the failing integration test**

Create `backend/tests/integration/test_symbol_review.py`:

```python
"""The symbol quarantine as a projection, not a queue.

The same shape as `corporate_action_review`, and for the same reason: a queue
that accumulated would keep asking questions the operator has already answered,
which is the fastest way to train someone to ignore it. Every `fetch-prices` run
rewrites this table to describe the export as it stands.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
import json

from sqlmodel import Session, select

from app.db import create_engine_and_tables
from app.domain.symbols import OUT_OF_BAND, Verdict
from app.ingest.symbols import PendingSymbol, ResolutionReport, write_symbol_review
from app.models.market import SymbolReview

D = Decimal
DETECTED = datetime(2026, 9, 6, 12, 0, 0)

def pending(isin: str) -> PendingSymbol:
    return PendingSymbol(
        isin=isin,
        product_name="Example Holdings",
        trade_currency="EUR",
        verdicts=(
            Verdict(
                symbol="EXA2S.DE",
                accepted=False,
                reason=OUT_OF_BAND,
                ratios=(D("3.33"), D("4.80")),
            ),
        ),
    )

def test_writes_one_row_per_unresolved_instrument() -> None:
    engine = create_engine_and_tables("sqlite://")
    written = write_symbol_review(
        engine,
        ResolutionReport(resolved={}, pending=(pending("NL0000000001"),)),
        detected_at=DETECTED,
    )
    assert written == 1
    with Session(engine) as session:
        row = session.exec(select(SymbolReview)).one()
    assert row.isin == "NL0000000001"
    assert row.trade_currency == "EUR"
    assert row.resolved is False

def test_keeps_the_candidates_and_their_ratios_readable() -> None:
    """The operator answering this needs the evidence, and JSON in one column is
    how a projection carries a variable-length list without a second table that
    would also have to be swept."""
    engine = create_engine_and_tables("sqlite://")
    write_symbol_review(
        engine,
        ResolutionReport(resolved={}, pending=(pending("NL0000000001"),)),
        detected_at=DETECTED,
    )
    with Session(engine) as session:
        row = session.exec(select(SymbolReview)).one()
    candidates = json.loads(row.candidates)
    assert candidates[0]["symbol"] == "EXA2S.DE"
    assert candidates[0]["accepted"] is False
    assert candidates[0]["ratios"] == ["3.33", "4.80"]
    assert OUT_OF_BAND in candidates[0]["reason"]

def test_a_rerun_replaces_the_queue_rather_than_appending_to_it() -> None:
    engine = create_engine_and_tables("sqlite://")
    write_symbol_review(
        engine,
        ResolutionReport(resolved={}, pending=(pending("NL0000000001"),)),
        detected_at=DETECTED,
    )
    write_symbol_review(
        engine,
        ResolutionReport(resolved={}, pending=(pending("NL0000000002"),)),
        detected_at=DETECTED,
    )
    with Session(engine) as session:
        rows = session.exec(select(SymbolReview)).all()
    assert [row.isin for row in rows] == ["NL0000000002"]

def test_an_answered_export_leaves_an_empty_queue() -> None:
    engine = create_engine_and_tables("sqlite://")
    write_symbol_review(
        engine,
        ResolutionReport(resolved={}, pending=(pending("NL0000000001"),)),
        detected_at=DETECTED,
    )
    written = write_symbol_review(
        engine, ResolutionReport(resolved={"NL0000000001": "EXA.AS"}, pending=()), detected_at=DETECTED
    )
    assert written == 0
    with Session(engine) as session:
        assert session.exec(select(SymbolReview)).all() == []
```

- [ ] **Step 3: Run both to verify they fail**

Run: `cd backend && python -m pytest tests/unit/test_ingest_symbols.py tests/integration/test_symbol_review.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.ingest.symbols'`

- [ ] **Step 4: Write the implementation**

Create `backend/app/ingest/symbols.py`:

```python
"""Which ticker is this instrument, and who decides. M2 spec section 6.

Deliberately the same shape as `ingest/corporate_actions.py`: candidates are
detected, the ones that can be proved are accepted, the rest go to a review table
that is a PROJECTION rather than a queue, and the answers live in a gitignored,
hand-edited YAML file beside the ledger. One pattern for "the machine is unsure,
a human decides", not two.

What differs is the burden of proof. A corporate action is quarantined because
the export is ambiguous; a symbol is quarantined because a confident, correct-
looking answer can still be the wrong instrument. M2 spec section 3.2: roughly
one ISIN in nine resolved to a leveraged or inverse ETF on the same underlying,
and every one of those passed the checks an implementation would naturally apply.
So nothing here accepts a candidate on the strength of the lookup. The ledger
decides, in `domain/symbols.py`, and this module only carries rows to it.

An answer file entry wins outright and is never probed. A human has decided, and
spending a provider call to second-guess them would also quarantine their answer
on any day the network disagreed.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Protocol
from uuid import uuid4

import yaml
from sqlalchemy import Engine
from sqlmodel import Session, select

from app.domain.orders import LedgerRow
from app.domain.splits import Split, derive_splits
from app.domain.symbols import (
    CandidateSeries,
    TradeObservation,
    Verdict,
    accepted_symbol,
    judge,
)
from app.models.market import SymbolReview
from app.providers.base import PriceProvider, SymbolResolver

#: What an operator writes to route an instrument to `manual_prices.csv`.
MANUAL_ANSWER = "manual"

_ANSWER_FIELDS = frozenset({"isin", "symbol", "note"})

class MalformedSymbolAnswers(ValueError):
    """The answers file exists but cannot be trusted. Names the file and entry."""

class PricedRow(LedgerRow, Protocol):
    """`LedgerRow` plus the two fields symbol resolution reads.

    `currency_local` because a provider quotes a series in its own currency and
    the comparison has to happen there. `product_name` because
    `models.ledger.Instrument` is declared but nothing populates it, so the
    ledger row is where an instrument's name actually lives.
    """

    currency_local: str | None
    product_name: str | None

@dataclass(frozen=True, slots=True)
class SymbolAnswer:
    """One human answer. `symbol is None` means "route this to the price file"."""

    isin: str
    symbol: str | None
    note: str = ""

@dataclass(frozen=True, slots=True)
class PendingSymbol:
    """One instrument still waiting on a human, with the evidence attached."""

    isin: str
    product_name: str
    trade_currency: str
    verdicts: tuple[Verdict, ...]

@dataclass(frozen=True, slots=True)
class ResolutionReport:
    #: ISIN -> symbol, or None for "answered `manual`".
    resolved: dict[str, str | None]
    pending: tuple[PendingSymbol, ...]

    @property
    def ok(self) -> bool:
        return not self.pending

def load_symbol_answers(path: Path) -> dict[str, SymbolAnswer]:
    """Read the operator's answers. A missing file answers nothing.

    Strict about shape for the reason `load_resolutions` is, and then some. A
    corporate-action typo produces a suppressed trade, which the reconciliation
    invariants catch. A symbol typo produces a plausible price series for the
    wrong instrument, and nothing downstream can tell.
    """
    if not path.exists():
        return {}

    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as broken:
        raise MalformedSymbolAnswers(f"{path}: not valid YAML -- {broken}") from broken
    if not isinstance(document, dict):
        raise MalformedSymbolAnswers(f"{path}: expected a mapping at the top level")

    answers: dict[str, SymbolAnswer] = {}
    for position, entry in enumerate(document.get("symbols") or [], start=1):
        where = f"{path}: entry {position}"
        if not isinstance(entry, dict):
            raise MalformedSymbolAnswers(f"{where} is not a mapping")

        unknown = sorted(set(entry) - _ANSWER_FIELDS)
        if unknown:
            raise MalformedSymbolAnswers(
                f"{where} has unknown field(s) {unknown}; expected {sorted(_ANSWER_FIELDS)}"
            )

        isin = entry.get("isin")
        if not isinstance(isin, str) or not isin.strip():
            raise MalformedSymbolAnswers(f"{where} needs a non-empty string 'isin'")
        if isin in answers:
            raise MalformedSymbolAnswers(f"{path}: {isin} is answered twice")

        symbol = entry.get("symbol")
        if not isinstance(symbol, str) or not symbol.strip():
            raise MalformedSymbolAnswers(
                f"{where} ({isin}) needs a non-empty 'symbol', or {MANUAL_ANSWER!r}"
            )
        cleaned = symbol.strip()
        answers[isin] = SymbolAnswer(
            isin=isin,
            symbol=None if cleaned == MANUAL_ANSWER else cleaned,
            note=str(entry.get("note", "")),
        )
    return answers

def _is_priced_trade(row: PricedRow) -> bool:
    return (
        row.is_economic
        and row.isin is not None
        and row.quantity is not None
        and row.quantity != 0
        and row.price_local is not None
        and row.currency_local is not None
    )

def observations(rows: Sequence[PricedRow]) -> dict[str, list[TradeObservation]]:
    """Every executed price the ledger holds, per instrument, oldest first.

    Sells as well as buys: a sale is an executed price too, and an instrument
    whose buys predate the export window has nothing else to check against.
    """
    found: dict[str, list[TradeObservation]] = defaultdict(list)
    for row in rows:
        if not _is_priced_trade(row):
            continue
        found[str(row.isin)].append(
            TradeObservation(
                trade_date=row.trade_date,
                price_local=abs(row.price_local or Decimal("0")),
                currency=str(row.currency_local),
            )
        )
    return {
        isin: sorted(seen, key=lambda o: o.trade_date) for isin, seen in found.items()
    }

def instrument_names(rows: Sequence[PricedRow]) -> dict[str, str]:
    """ISIN -> the name the broker printed beside it."""
    names: dict[str, str] = {}
    for row in rows:
        if row.isin and row.product_name:
            names.setdefault(row.isin, row.product_name)
    return names

def _candidate_series(
    isin: str, resolver: SymbolResolver, prices: PriceProvider
) -> list[CandidateSeries]:
    """Fetch a series for each candidate ticker, so the ledger can judge it.

    A candidate with no series is dropped rather than judged: the discriminator
    would report NO_CLOSE_NEAR_TRADE, which reads as "the wrong instrument" when
    the truth is "nothing came back at all".
    """
    found: list[CandidateSeries] = []
    for candidate in resolver.candidates(isin):
        series = prices.full_series(candidate.symbol)
        if series is None or not series.points:
            continue
        found.append(
            CandidateSeries(
                symbol=candidate.symbol,
                currency=series.currency,
                closes={point.on: point.close_unadjusted for point in series.points},
            )
        )
    return found

def resolve_symbols(
    rows: Sequence[PricedRow],
    *,
    answers: Mapping[str, SymbolAnswer],
    resolver: SymbolResolver,
    prices: PriceProvider,
) -> ResolutionReport:
    """Decide a symbol for every instrument the ledger ever held.

    An answered instrument is taken as answered. Everything else is probed, and
    accepted only if exactly one candidate clears all three conditions of M2 spec
    section 6.2 against the ledger's own executed prices.
    """
    by_isin = observations(rows)
    names = instrument_names(rows)
    splits = derive_splits(rows)

    resolved: dict[str, str | None] = {}
    pending: list[PendingSymbol] = []

    for isin in sorted(by_isin):
        if isin in answers:
            resolved[isin] = answers[isin].symbol
            continue

        trades = by_isin[isin]
        for_this: Sequence[Split] = [s for s in splits if s.isin == isin]
        verdicts = judge(_candidate_series(isin, resolver, prices), trades, for_this)

        chosen = accepted_symbol(verdicts)
        if chosen is not None:
            resolved[isin] = chosen
            continue

        pending.append(
            PendingSymbol(
                isin=isin,
                product_name=names.get(isin, ""),
                trade_currency=trades[0].currency if trades else "",
                verdicts=verdicts,
            )
        )

    return ResolutionReport(resolved=resolved, pending=tuple(pending))

def _as_json(verdicts: Sequence[Verdict]) -> str:
    return json.dumps(
        [
            {
                "symbol": verdict.symbol,
                "accepted": verdict.accepted,
                "reason": verdict.reason,
                # Strings, not floats: a ratio is money-derived and the operator
                # is reading it to decide, so it must be the number measured.
                "ratios": [str(ratio) for ratio in verdict.ratios],
            }
            for verdict in verdicts
        ]
    )

def write_symbol_review(
    engine: Engine, report: ResolutionReport, *, detected_at: datetime
) -> int:
    """Rewrite the review table to describe this run. Returns rows written.

    A replacement, not an append: the queue holds no state of its own, the
    answers do. A queue that accumulated would keep asking questions already
    answered, which is how a quarantine trains people to ignore it.
    """
    with Session(engine) as session:
        for stale in session.exec(select(SymbolReview)).all():
            session.delete(stale)
        session.flush()
        for item in report.pending:
            session.add(
                SymbolReview(
                    id=uuid4(),
                    isin=item.isin,
                    product_name=item.product_name,
                    trade_currency=item.trade_currency,
                    candidates=_as_json(item.verdicts),
                    detected_at=detected_at,
                )
            )
        session.commit()
    return len(report.pending)
```

- [ ] **Step 5: Write the config example**

Create `config/instrument_symbols.example.yaml`:

```yaml
# Symbol answers (M2 spec section 6.3). Copy to instrument_symbols.yaml and edit.
#
# `fetch-prices` resolves what it can prove against the ledger and asks about the
# rest. It refuses to complete while anything is unanswered, and prints a
# paste-ready block for this file when it does -- so you never have to invent a
# key by hand.
#
# The question being answered is always "which of these tickers is the instrument
# I actually traded". A candidate can resolve from a real ISIN, return years of
# real daily bars, and be a leveraged or inverse product on the same underlying:
# the quarantine shows you the measured ratio of your own executed prices against
# each candidate's closes, and a correct symbol sits near 1.00 on every trade.
#
# `symbol: manual` routes an instrument to config/manual_prices.csv instead. Use
# it when no public series exists -- a private company, a microcap nobody indexes.
symbols:
  - isin: NL0000000001
    symbol: EXA.AS
    note: Euronext Amsterdam listing; ratios 0.99-1.01 across every trade
  - isin: US0000000404
    symbol: manual
    note: no public series on any free tier
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd backend && python -m pytest tests/unit/test_ingest_symbols.py tests/integration/test_symbol_review.py -q`
Expected: PASS, 24 tests.

- [ ] **Step 7: Run the whole gate**

Expected: 432 backend passed, 35 realdata, clean.

- [ ] **Step 8: Commit**

```bash
git add backend/app/ingest/symbols.py backend/tests/unit/test_ingest_symbols.py \
        backend/tests/integration/test_symbol_review.py config/instrument_symbols.example.yaml
git commit -m "feat(ingest): symbol resolution, answers file and quarantine

Same shape as M0's corporate-action flow: prove what the ledger can prove,
quarantine the rest with the measured ratios attached, answer it in a gitignored
YAML file beside the ledger.

Claude-Session: https://claude.ai/code/session_01UzfynZY2Rqs9oMAtCivdSF"
```

---
### Task 7: `fetch-prices` — five years deep, incremental, and refusing while anything is unresolved

The only writer of `price_daily` and `fx_daily`. It resolves symbols first and **refuses to complete** while anything is unanswered (M2 spec section 6.3), backfills a fixed five years on a first run (M2-7), and afterwards fetches only the days the cache lacks (M2-9).

The five-year depth is what makes a five-year lookback possible in Task 9 and Task 12. It is asserted here rather than assumed.

**Files:**
- Create: `backend/app/ingest/prices.py`
- Modify: `backend/app/cli.py` (add `fetch-prices` and `symbols` commands)
- Test: `backend/tests/integration/test_fetch_prices.py`
- Test: `backend/tests/integration/test_cli.py` (existing — add a `TestFetchPrices` class)

**Interfaces:**
- Consumes: `PriceChain`, `ManualPrices`, `FxProvider`, `PriceProvider`, `SymbolResolver`, `BACKFILL_YEARS`, `ProviderError` from `app.providers.*`; `resolve_symbols`, `load_symbol_answers`, `write_symbol_review`, `ResolutionReport` from `app.ingest.symbols`; `PriceDaily`, `FxDaily` from `app.models.market`.
- Produces:
  - `UnresolvedSymbols(RuntimeError)` carrying `.report: ResolutionReport`
  - `FetchResult(instruments: int, price_rows: int, fx_rows: int, sources: dict[str, int], earliest: date | None)`
  - `Providers(resolver, prices, chain, fx)` frozen dataclass
  - `build_providers(settings) -> Providers`
  - `fetch_prices(engine, providers, *, answers, now: datetime, full: bool) -> FetchResult`

- [ ] **Step 1: Write the failing integration test**

Create `backend/tests/integration/test_fetch_prices.py`:

```python
"""Filling the cache: what gets written, what gets refused, and how deep it goes.

Three properties this suite exists to hold:

* **It refuses while a symbol is unresolved, having written nothing.** A partial
  cache is worse than an empty one -- the chart would draw, with some
  instruments silently absent, and `coverage` would say `partial` as though the
  provider were at fault.
* **It backfills a fixed five years.** M2-7 makes the cache depth a constant and
  the chart start a separate question, which is what lets a reader ask to look
  back five years and actually be answered.
* **It is incremental afterwards.** A five-year refetch on every run is a
  request no free provider has a reason to keep serving.

Everything runs against stub providers. CI never touches the network.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlmodel import Session, select

from app.db import create_engine_and_tables
from app.ingest.prices import FetchResult, Providers, UnresolvedSymbols, fetch_prices
from app.ingest.symbols import SymbolAnswer
from app.models.ledger import Account, ImportBatch, Transaction
from app.models.market import FxDaily, PriceDaily
from app.providers.base import FxPoint, FxSeries, PricePoint, PriceSeries, SymbolCandidate
from app.providers.chain import PriceChain
from app.providers.manual import ManualPrices

D = Decimal
ZERO = D("0.00")
NOW = datetime(2026, 9, 6, 12, 0, 0, tzinfo=UTC)
TODAY = NOW.date()

# --------------------------------------------------------------------------
# Stubs
# --------------------------------------------------------------------------
class StubResolver:
    name = "stub"

    def __init__(self, by_isin: dict[str, tuple[str, ...]]) -> None:
        self._by_isin = by_isin

    def candidates(self, isin: str) -> tuple[SymbolCandidate, ...]:
        return tuple(
            SymbolCandidate(symbol=s, name="Example", exchange_code="NA", source="stub")
            for s in self._by_isin.get(isin, ())
        )

class StubPrices:
    name = "stub"

    def __init__(self, by_symbol: dict[str, PriceSeries]) -> None:
        self._by_symbol = by_symbol
        self.full_calls: list[str] = []
        self.since_calls: list[tuple[str, date]] = []

    def full_series(self, symbol: str) -> PriceSeries | None:
        self.full_calls.append(symbol)
        return self._by_symbol.get(symbol)

    def series_since(self, symbol: str, since: date) -> PriceSeries | None:
        self.since_calls.append((symbol, since))
        found = self._by_symbol.get(symbol)
        if found is None:
            return None
        return PriceSeries(
            symbol=found.symbol,
            currency=found.currency,
            source=found.source,
            points=tuple(p for p in found.points if p.on >= since),
        )

class StubFx:
    name = "stub-fx"

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, date, date]] = []

    def series(self, from_ccy: str, to_ccy: str, *, start: date, end: date) -> FxSeries | None:
        self.calls.append((from_ccy, to_ccy, start, end))
        if from_ccy == to_ccy:
            return None
        day = start
        points: list[FxPoint] = []
        while day <= end:
            if day.weekday() < 5:
                points.append(FxPoint(on=day, rate=D("1.04")))
            day += timedelta(days=1)
        return FxSeries(from_ccy=from_ccy, to_ccy=to_ccy, source="stub-fx", points=tuple(points))

def five_years_of(symbol: str, currency: str, close: str) -> PriceSeries:
    """A daily series reaching the full backfill depth."""
    points: list[PricePoint] = []
    day = TODAY - timedelta(days=365 * 5)
    while day <= TODAY:
        if day.weekday() < 5:
            points.append(
                PricePoint(on=day, close_unadjusted=D(close), close_adjusted=D(close))
            )
        day += timedelta(days=1)
    return PriceSeries(symbol=symbol, currency=currency, source="stub", points=tuple(points))

def providers(
    *,
    resolver: StubResolver,
    prices: StubPrices,
    fx: StubFx,
    manual: ManualPrices | None = None,
) -> Providers:
    return Providers(
        resolver=resolver,
        prices=prices,
        chain=PriceChain([prices], manual or ManualPrices(_by_isin={})),
        fx=fx,
    )

# --------------------------------------------------------------------------
# A tiny ledger
# --------------------------------------------------------------------------
def ledger(engine, rows: list[tuple[str, date, str, str, str]]) -> None:
    """`rows` are (isin, trade_date, quantity, price_local, currency)."""
    account_id, batch_id = uuid4(), uuid4()
    with Session(engine) as session:
        session.add(Account(id=account_id, broker="degiro", name="test", base_currency="EUR"))
        session.add(
            ImportBatch(
                id=batch_id,
                source="degiro",
                filename="Transactions.csv",
                file_sha256="0" * 64,
                parser_version="1",
                imported_at=NOW.replace(tzinfo=None),
                row_count=len(rows),
                inserted_count=len(rows),
            )
        )
        for index, (isin, on, quantity, price, currency) in enumerate(rows):
            value = -(D(quantity) * D(price))
            session.add(
                Transaction(
                    id=uuid4(),
                    account_id=account_id,
                    import_batch_id=batch_id,
                    source="degiro",
                    source_ref=f"ref-{index}",
                    txn_type="BUY" if D(quantity) > 0 else "SELL",
                    trade_date=on,
                    trade_time="10:00",
                    isin=isin,
                    product_name="Example Holdings",
                    quantity=D(quantity),
                    price_local=D(price),
                    currency_local=currency,
                    fee_base=ZERO,
                    tax_base=ZERO,
                    autofx_fee_base=ZERO,
                    value_base=value,
                    net_base=value,
                    order_ref=f"ord-{index}",
                    raw_json="{}",
                )
            )
        session.commit()

@pytest.fixture(name="engine")
def _engine():
    return create_engine_and_tables("sqlite://")

BOUGHT_ON = TODAY - timedelta(days=400)

# --------------------------------------------------------------------------
class TestRefusal:
    def test_refuses_while_a_symbol_is_unresolved(self, engine) -> None:
        ledger(engine, [("NL0000000001", BOUGHT_ON, "10", "20.00", "EUR")])
        # The only candidate trades at a third of the ledger's executed price:
        # the leveraged-product shape from the provider spike.
        stub = StubPrices({"EXA2S.DE": five_years_of("EXA2S.DE", "EUR", "6.00")})

        with pytest.raises(UnresolvedSymbols) as refused:
            fetch_prices(
                engine,
                providers(
                    resolver=StubResolver({"NL0000000001": ("EXA2S.DE",)}),
                    prices=stub,
                    fx=StubFx(),
                ),
                answers={},
                now=NOW,
                full=False,
            )

        assert [p.isin for p in refused.value.report.pending] == ["NL0000000001"]

    def test_writes_no_prices_when_it_refuses(self, engine) -> None:
        """A partial cache is worse than an empty one: the chart draws, some
        instruments are silently absent, and `coverage` blames the provider."""
        ledger(
            engine,
            [
                ("NL0000000001", BOUGHT_ON, "10", "20.00", "EUR"),
                ("NL0000000002", BOUGHT_ON, "5", "40.00", "EUR"),
            ],
        )
        stub = StubPrices(
            {
                "EXA.AS": five_years_of("EXA.AS", "EUR", "20.00"),
                "EXB2S.DE": five_years_of("EXB2S.DE", "EUR", "6.00"),
            }
        )
        with pytest.raises(UnresolvedSymbols):
            fetch_prices(
                engine,
                providers(
                    resolver=StubResolver(
                        {"NL0000000001": ("EXA.AS",), "NL0000000002": ("EXB2S.DE",)}
                    ),
                    prices=stub,
                    fx=StubFx(),
                ),
                answers={},
                now=NOW,
                full=False,
            )
        with Session(engine) as session:
            assert session.exec(select(PriceDaily)).all() == []

class TestBackfill:
    def test_stores_a_five_year_history_on_a_first_run(self, engine) -> None:
        """M2-7's fixed depth, asserted rather than assumed. This is what lets a
        reader ask to look back five years and be answered."""
        ledger(engine, [("NL0000000001", BOUGHT_ON, "10", "20.00", "EUR")])
        result = fetch_prices(
            engine,
            providers(
                resolver=StubResolver({"NL0000000001": ("EXA.AS",)}),
                prices=StubPrices({"EXA.AS": five_years_of("EXA.AS", "EUR", "20.00")}),
                fx=StubFx(),
            ),
            answers={},
            now=NOW,
            full=False,
        )
        assert isinstance(result, FetchResult)
        assert result.earliest is not None
        assert result.earliest <= TODAY - timedelta(days=365 * 4 + 180)

    def test_fetches_fx_over_the_same_five_years(self, engine) -> None:
        """A price five years deep with FX two years deep would value the older
        half of the chart at `missing`, which reads as a provider outage."""
        ledger(engine, [("US0000000404", BOUGHT_ON, "10", "20.00", "USD")])
        fx = StubFx()
        fetch_prices(
            engine,
            providers(
                resolver=StubResolver({"US0000000404": ("EXU",)}),
                prices=StubPrices({"EXU": five_years_of("EXU", "USD", "20.00")}),
                fx=fx,
            ),
            answers={},
            now=NOW,
            full=False,
        )
        with Session(engine) as session:
            earliest = min(row.rate_date for row in session.exec(select(FxDaily)).all())
        assert earliest <= TODAY - timedelta(days=365 * 4 + 180)

    def test_asks_for_fx_only_for_currencies_that_are_not_the_base(self, engine) -> None:
        ledger(engine, [("NL0000000001", BOUGHT_ON, "10", "20.00", "EUR")])
        fx = StubFx()
        fetch_prices(
            engine,
            providers(
                resolver=StubResolver({"NL0000000001": ("EXA.AS",)}),
                prices=StubPrices({"EXA.AS": five_years_of("EXA.AS", "EUR", "20.00")}),
                fx=fx,
            ),
            answers={},
            now=NOW,
            full=False,
        )
        assert fx.calls == []

class TestIncremental:
    def test_a_second_run_asks_only_for_what_the_cache_lacks(self, engine) -> None:
        ledger(engine, [("NL0000000001", BOUGHT_ON, "10", "20.00", "EUR")])
        stub = StubPrices({"EXA.AS": five_years_of("EXA.AS", "EUR", "20.00")})
        kit = providers(
            resolver=StubResolver({"NL0000000001": ("EXA.AS",)}), prices=stub, fx=StubFx()
        )

        fetch_prices(engine, kit, answers={}, now=NOW, full=False)
        stub.full_calls.clear()
        stub.since_calls.clear()
        fetch_prices(engine, kit, answers={}, now=NOW, full=False)

        assert stub.since_calls, "the second run must be incremental"
        assert stub.full_calls == []

    def test_full_refetches_the_whole_history(self, engine) -> None:
        """M2-9's escape hatch, for when a provider revises its history."""
        ledger(engine, [("NL0000000001", BOUGHT_ON, "10", "20.00", "EUR")])
        stub = StubPrices({"EXA.AS": five_years_of("EXA.AS", "EUR", "20.00")})
        kit = providers(
            resolver=StubResolver({"NL0000000001": ("EXA.AS",)}), prices=stub, fx=StubFx()
        )

        fetch_prices(engine, kit, answers={}, now=NOW, full=False)
        stub.full_calls.clear()
        fetch_prices(engine, kit, answers={}, now=NOW, full=True)

        assert stub.full_calls == ["EXA.AS"]

    def test_a_revised_close_replaces_the_cached_one(self, engine) -> None:
        """Refetching must update, not duplicate: the unique constraint would
        otherwise abort the run, and swallowing it would keep a stale price for
        ever."""
        ledger(engine, [("NL0000000001", BOUGHT_ON, "10", "20.00", "EUR")])
        kit = providers(
            resolver=StubResolver({"NL0000000001": ("EXA.AS",)}),
            prices=StubPrices({"EXA.AS": five_years_of("EXA.AS", "EUR", "20.00")}),
            fx=StubFx(),
        )
        fetch_prices(engine, kit, answers={}, now=NOW, full=False)

        revised = providers(
            resolver=StubResolver({"NL0000000001": ("EXA.AS",)}),
            prices=StubPrices({"EXA.AS": five_years_of("EXA.AS", "EUR", "21.00")}),
            fx=StubFx(),
        )
        fetch_prices(engine, revised, answers={}, now=NOW, full=True)

        with Session(engine) as session:
            rows = session.exec(
                select(PriceDaily).where(PriceDaily.price_date == BOUGHT_ON)
            ).all()
        assert len(rows) == 1
        assert rows[0].close_unadjusted == D("21.00")

class TestAnsweredInstruments:
    def test_an_answered_symbol_is_fetched_without_being_probed(self, engine) -> None:
        ledger(engine, [("NL0000000001", BOUGHT_ON, "10", "20.00", "EUR")])
        stub = StubPrices({"EXA.AS": five_years_of("EXA.AS", "EUR", "20.00")})
        fetch_prices(
            engine,
            providers(resolver=StubResolver({}), prices=stub, fx=StubFx()),
            answers={"NL0000000001": SymbolAnswer("NL0000000001", "EXA.AS", "")},
            now=NOW,
            full=False,
        )
        with Session(engine) as session:
            assert session.exec(select(PriceDaily)).first() is not None

    def test_an_instrument_answered_manual_is_priced_from_the_file(
        self, engine, tmp_path
    ) -> None:
        """And the stored row says `manual`, which is the whole mechanism behind
        `coverage: "manual"`."""
        ledger(engine, [("US0000000404", BOUGHT_ON, "10", "20.00", "USD")])
        csv_path = tmp_path / "manual_prices.csv"
        csv_path.write_text(
            f"isin,date,close,currency\nUS0000000404,{BOUGHT_ON.isoformat()},20.00,USD\n",
            encoding="utf-8",
        )
        result = fetch_prices(
            engine,
            providers(
                resolver=StubResolver({}),
                prices=StubPrices({}),
                fx=StubFx(),
                manual=ManualPrices.load(csv_path),
            ),
            answers={"US0000000404": SymbolAnswer("US0000000404", None, "")},
            now=NOW,
            full=False,
        )
        assert result.sources.get("manual") == 1
        with Session(engine) as session:
            assert session.exec(select(PriceDaily)).one().source == "manual"

    def test_an_instrument_nothing_can_price_leaves_no_row_and_no_refusal(
        self, engine
    ) -> None:
        """Answered, and still unpriceable. That is `coverage: "missing"`, which
        is a result the API reports -- not an error that stops the run."""
        ledger(engine, [("US0000000404", BOUGHT_ON, "10", "20.00", "USD")])
        result = fetch_prices(
            engine,
            providers(resolver=StubResolver({}), prices=StubPrices({}), fx=StubFx()),
            answers={"US0000000404": SymbolAnswer("US0000000404", None, "")},
            now=NOW,
            full=False,
        )
        assert result.price_rows == 0
        with Session(engine) as session:
            assert session.exec(select(PriceDaily)).all() == []
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend && python -m pytest tests/integration/test_fetch_prices.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.ingest.prices'`

- [ ] **Step 3: Write the cache writer**

Create `backend/app/ingest/prices.py`:

```python
"""Filling the price and FX cache. The only writer of `price_daily` and `fx_daily`.

M2 spec section 4 puts this on the fetched side of the determinism line, and the
line is not a metaphor: nothing in `rebuild/` or `analytics/` may write a row
here, and this module never writes a derived one. That separation is what lets
`rebuild()` still claim to be a function of the ledger while the chart it feeds
depends on a network call.

Three behaviours are load-bearing.

**It refuses while a symbol is unresolved** (M2 spec section 6.3), having written
nothing. A partial cache is worse than an empty one: the chart draws, some
instruments are silently absent, and `coverage` reports `partial` as though a
provider were at fault rather than a question being unanswered.

**It backfills a fixed five years** (M2-7). One call per instrument, no date
arithmetic per instrument, and the same depth for FX -- a price series five years
deep with FX two years deep would value the older half of every foreign position
as `missing`, which reads as an outage. The cache depth is deliberately deeper
than the chart's default start, so a reader who asks to look back five years is
answered from the cache rather than told the data does not exist.

**It is incremental afterwards** (M2-9). A five-year refetch on every run is a
request no free provider has a reason to keep serving. `--full` exists for the
day a provider revises its history.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
from sqlalchemy import Engine
from sqlmodel import Session, select

from app.ingest.symbols import (
    ResolutionReport,
    SymbolAnswer,
    resolve_symbols,
    write_symbol_review,
)
from app.models.ledger import Transaction
from app.models.market import FxDaily, PriceDaily
from app.providers.base import (
    BACKFILL_YEARS,
    FxProvider,
    PriceProvider,
    PriceSeries,
    SymbolResolver,
)
from app.providers.chain import PriceChain
from app.providers.ecb import EcbRates
from app.providers.manual import ManualPrices
from app.providers.openfigi import OpenFigiResolver
from app.providers.yahoo import YahooPrices
from app.settings import Settings

#: How far back an incremental fetch reaches beyond the newest cached day.
#: Providers revise recent bars; without the overlap a stale close would stay
#: cached for ever, because the incremental window would never cover it again.
_OVERLAP = timedelta(days=5)

class UnresolvedSymbols(RuntimeError):
    """A symbol is still waiting on a human. Nothing was written."""

    def __init__(self, report: ResolutionReport) -> None:
        super().__init__(f"{len(report.pending)} instrument(s) must be answered")
        self.report = report

@dataclass(frozen=True, slots=True)
class FetchResult:
    instruments: int
    price_rows: int
    fx_rows: int
    #: Provider name -> rows written. This is how the operator sees that an
    #: instrument fell through to `manual`.
    sources: dict[str, int]
    #: The oldest day now in the price cache. The five-year backfill, measured.
    earliest: date | None

@dataclass(frozen=True, slots=True)
class Providers:
    """Everything `fetch_prices` needs from the outside world, in one bag.

    Injected rather than constructed inside, so the tests can run the whole flow
    without a network and the CLI can build the real thing in one place.
    """

    resolver: SymbolResolver
    #: Used to probe candidates during resolution, where the manual fallback
    #: must NOT apply: a hand-typed price would "confirm" any ticker offered.
    prices: PriceProvider
    #: Used to fetch the accepted symbol, where the manual fallback does apply.
    chain: PriceChain
    fx: FxProvider

def build_providers(settings: Settings) -> Providers:
    """The real stack: OpenFIGI, Yahoo, ECB, and the hand-maintained file."""
    client = httpx.Client(follow_redirects=True)
    yahoo = YahooPrices(client)
    manual = ManualPrices.load(Path(settings.manual_prices_path))
    return Providers(
        resolver=OpenFigiResolver(client),
        prices=yahoo,
        chain=PriceChain([yahoo], manual),
        fx=EcbRates(client),
    )

def _ledger_rows(engine: Engine) -> list[Transaction]:
    with Session(engine) as session:
        return list(session.exec(select(Transaction)).all())

def _newest_price(session: Session, isin: str) -> date | None:
    rows = session.exec(select(PriceDaily).where(PriceDaily.isin == isin)).all()
    return max((row.price_date for row in rows), default=None)

def _newest_rate(session: Session, from_ccy: str, to_ccy: str) -> date | None:
    rows = session.exec(
        select(FxDaily).where(FxDaily.from_ccy == from_ccy, FxDaily.to_ccy == to_ccy)
    ).all()
    return max((row.rate_date for row in rows), default=None)

def _store_prices(
    session: Session, isin: str, series: PriceSeries, *, fetched_at: datetime
) -> int:
    """Upsert one instrument's points. Update in place, never a second row.

    A duplicate would make "the price that day" a question with two answers and
    the join would pick one arbitrarily; letting the unique constraint abort the
    run instead would mean a provider revising a single bar broke every fetch.
    """
    existing = {
        row.price_date: row
        for row in session.exec(select(PriceDaily).where(PriceDaily.isin == isin)).all()
    }
    for point in series.points:
        row = existing.get(point.on)
        if row is None:
            session.add(
                PriceDaily(
                    id=uuid4(),
                    isin=isin,
                    price_date=point.on,
                    close_unadjusted=point.close_unadjusted,
                    close_adjusted=point.close_adjusted,
                    currency=series.currency,
                    source=series.source,
                    fetched_at=fetched_at,
                )
            )
        else:
            row.close_unadjusted = point.close_unadjusted
            row.close_adjusted = point.close_adjusted
            row.currency = series.currency
            row.source = series.source
            row.fetched_at = fetched_at
            session.add(row)
    return len(series.points)

def _store_rates(
    session: Session, from_ccy: str, to_ccy: str, points, *, source: str, fetched_at: datetime
) -> int:
    existing = {
        row.rate_date: row
        for row in session.exec(
            select(FxDaily).where(FxDaily.from_ccy == from_ccy, FxDaily.to_ccy == to_ccy)
        ).all()
    }
    for point in points:
        row = existing.get(point.on)
        if row is None:
            session.add(
                FxDaily(
                    id=uuid4(),
                    from_ccy=from_ccy,
                    to_ccy=to_ccy,
                    rate_date=point.on,
                    rate=point.rate,
                    source=source,
                    fetched_at=fetched_at,
                )
            )
        else:
            row.rate = point.rate
            row.source = source
            row.fetched_at = fetched_at
            session.add(row)
    return len(points)

def fetch_prices(
    engine: Engine,
    providers: Providers,
    *,
    answers: Mapping[str, SymbolAnswer],
    now: datetime,
    full: bool,
) -> FetchResult:
    """Resolve symbols, then fill the cache. Refuses while anything is unanswered."""
    rows: Sequence[Transaction] = _ledger_rows(engine)

    report = resolve_symbols(
        rows, answers=answers, resolver=providers.resolver, prices=providers.prices
    )
    write_symbol_review(engine, report, detected_at=now.replace(tzinfo=None))
    if report.pending:
        # Deliberately before any price is written. See the module docstring.
        raise UnresolvedSymbols(report)

    today = now.date()
    # A fixed depth, computed once rather than per instrument (M2-7).
    backfill_start = today - timedelta(days=365 * BACKFILL_YEARS)

    sources: Counter[str] = Counter()
    price_rows = 0
    with Session(engine) as session:
        for isin, symbol in sorted(report.resolved.items()):
            since = None if full else _newest_price(session, isin)
            series = providers.chain.series(
                isin, symbol, since=None if since is None else since - _OVERLAP
            )
            if series is None or not series.points:
                # Answered, and still unpriceable. That is `coverage: "missing"`,
                # a result the API reports rather than an error that stops here.
                continue
            written = _store_prices(session, isin, series, fetched_at=now.replace(tzinfo=None))
            price_rows += written
            sources[series.source] += written
        session.commit()

    fx_rows = 0
    with Session(engine) as session:
        currencies = {
            row.currency
            for row in session.exec(select(PriceDaily)).all()
            if row.currency
        }
        base = _base_currency(session)
        for currency in sorted(currencies - {base}):
            newest = None if full else _newest_rate(session, currency, base)
            start = backfill_start if newest is None else newest - _OVERLAP
            series = providers.fx.series(currency, base, start=start, end=today)
            if series is None:
                continue
            fx_rows += _store_rates(
                session,
                currency,
                base,
                series.points,
                source=series.source,
                fetched_at=now.replace(tzinfo=None),
            )
        session.commit()

    with Session(engine) as session:
        earliest = min(
            (row.price_date for row in session.exec(select(PriceDaily)).all()), default=None
        )

    return FetchResult(
        instruments=len(report.resolved),
        price_rows=price_rows,
        fx_rows=fx_rows,
        sources=dict(sources),
        earliest=earliest,
    )

def _base_currency(session: Session) -> str:
    """The account's base currency, read from the ledger rather than settings.

    The rows being valued belong to an account, and that account states its own
    base. Reading an environment variable here would let a changed `.env` silently
    reinterpret a cache that was fetched against a different one.
    """
    from app.models.ledger import Account

    account = session.exec(select(Account)).first()
    return account.base_currency if account is not None else "EUR"
```

- [ ] **Step 4: Add the CLI commands**

Modify `backend/app/cli.py`. Add the imports:

```python
from datetime import UTC, datetime

from app.ingest.prices import UnresolvedSymbols, build_providers, fetch_prices
from app.ingest.symbols import MANUAL_ANSWER, load_symbol_answers
from app.models.market import SymbolReview
```

Add the two commands:

```python
def _report_symbol_quarantine(report, answers_path: Path) -> None:
    """Print the open questions and a paste-ready answer for them.

    The same shape as `_report_quarantine`, and for the same reason: an operator
    retyping a key from memory produces an answer that parses cleanly and applies
    to nothing. Here the key is the ISIN, and the evidence is the measured ratio
    of their own executed prices against each candidate -- which is the only thing
    that distinguishes the right ticker from a leveraged product on the same
    underlying.
    """
    typer.echo(
        f"{len(report.pending)} instrument(s) must be answered before prices can be fetched."
    )
    typer.echo(f"No prices were written. Add each ISIN to {answers_path} and run this again.\n")
    for item in report.pending:
        typer.echo(f"  {item.isin}  {item.product_name}  (trades in {item.trade_currency})")
        if not item.verdicts:
            typer.echo("      no candidate ticker was found at all")
        for verdict in item.verdicts:
            ratios = ", ".join(f"{ratio:.2f}" for ratio in verdict.ratios) or "none measured"
            typer.echo(f"      {verdict.symbol}: {verdict.reason}")
            typer.echo(f"          executed price / provider close: {ratios}")
    typer.echo("\nA correct ticker sits near 1.00 on every trade.")
    typer.echo(f"Answer with a ticker, or with {MANUAL_ANSWER!r} to price it from the CSV.\n")
    typer.echo("symbols:")
    for item in report.pending:
        typer.echo(f"  - isin: {item.isin}")
        typer.echo("    symbol: ")

@app.command("fetch-prices")
def fetch_prices_command(
    full: Annotated[
        bool,
        typer.Option("--full", help="Refetch the whole five-year history, not only what is missing."),
    ] = False,
) -> None:
    """Fill the price and FX cache (M2 spec section 4).

    Refuses, writing nothing, while any instrument's symbol is unanswered. Exits
    1 on a refusal and 2 when a provider could not be reached, so this can gate a
    script.
    """
    settings = get_settings()
    answers_path = Path(settings.instrument_symbols_path)
    engine = create_engine_and_tables(settings.database_url)

    try:
        result = fetch_prices(
            engine,
            build_providers(settings),
            answers=load_symbol_answers(answers_path),
            now=datetime.now(tz=UTC),
            full=full,
        )
    except UnresolvedSymbols as refused:
        _report_symbol_quarantine(refused.report, answers_path)
        raise typer.Exit(code=1) from refused
    except ProviderError as unreachable:
        typer.echo(str(unreachable), err=True)
        raise typer.Exit(code=2) from unreachable

    breakdown = ", ".join(f"{name}={count}" for name, count in sorted(result.sources.items()))
    typer.echo(
        f"{result.instruments} instruments: {result.price_rows} price rows "
        f"({breakdown or 'none'}), {result.fx_rows} FX rows"
    )
    if result.earliest is not None:
        typer.echo(f"cache reaches back to {result.earliest.isoformat()}")

@app.command("symbols")
def symbols_command() -> None:
    """Show the symbol review queue (M2 spec section 6.3).

    Rebuilt by every `fetch-prices` run, so this always describes the export as
    it stands rather than a history of what was once asked.
    """
    engine = create_engine_and_tables(get_settings().database_url)
    with Session(engine) as session:
        rows = session.exec(
            select(SymbolReview).order_by(SymbolReview.isin)  # type: ignore[arg-type]
        ).all()

    if not rows:
        typer.echo("no unresolved symbols")
        return

    for row in rows:
        typer.echo(f"OPEN  {row.isin}  {row.product_name}  ({row.trade_currency})")
        for candidate in json.loads(row.candidates):
            ratios = ", ".join(candidate["ratios"]) or "none measured"
            typer.echo(f"        {candidate['symbol']}: {candidate['reason']}  [{ratios}]")

    typer.echo(f"\n{len(rows)} instrument(s) still open")
```

Add `import json` and `from app.providers.base import ProviderError` to the CLI imports.

- [ ] **Step 5: Add the CLI test**

Append to `backend/tests/integration/test_cli.py`:

```python
class TestFetchPrices:
    """The operator's half of the symbol quarantine.

    The message has to carry the ISIN verbatim and the measured ratios, because
    those two are what the operator answers with and what they answer from. A
    refusal that said only "could not resolve 4 instruments" would be a dead end.
    """

    def _stub(self, monkeypatch: pytest.MonkeyPatch, *, resolves_to: str | None) -> None:
        from app.ingest import prices as prices_module
        from app.ingest.prices import Providers
        from app.providers.base import PricePoint, PriceSeries, SymbolCandidate
        from app.providers.chain import PriceChain
        from app.providers.manual import ManualPrices

        class Resolver:
            name = "stub"

            def candidates(self, isin: str) -> tuple[SymbolCandidate, ...]:
                return (
                    SymbolCandidate(
                        symbol="EXA2S.DE", name="Example 2x Short", exchange_code="GY",
                        source="stub",
                    ),
                )

        class Prices:
            name = "stub"

            def full_series(self, symbol: str) -> PriceSeries | None:
                close = "20.00" if resolves_to == symbol else "6.00"
                return PriceSeries(
                    symbol=symbol,
                    currency="EUR",
                    source="stub",
                    points=tuple(
                        PricePoint(
                            on=date(2025, 1, day),
                            close_unadjusted=Decimal(close),
                            close_adjusted=Decimal(close),
                        )
                        for day in (6, 7, 8)
                    ),
                )

            def series_since(self, symbol: str, since: date) -> PriceSeries | None:
                return self.full_series(symbol)

        class Fx:
            name = "stub-fx"

            def series(self, from_ccy, to_ccy, *, start, end):
                return None

        prices = Prices()
        monkeypatch.setattr(
            prices_module,
            "build_providers",
            lambda settings: Providers(
                resolver=Resolver(),
                prices=prices,
                chain=PriceChain([prices], ManualPrices(_by_isin={})),
                fx=Fx(),
            ),
        )

    def test_refuses_and_exits_non_zero_while_a_symbol_is_open(
        self, export: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner.invoke(
            app, ["import", str(export), "--resolutions", str(_resolutions(tmp_path, RESOLVE_BOTH))]
        )
        self._stub(monkeypatch, resolves_to=None)
        monkeypatch.setenv("INSTRUMENT_SYMBOLS_PATH", str(tmp_path / "absent.yaml"))
        get_settings.cache_clear()

        result = runner.invoke(app, ["fetch-prices"])

        assert result.exit_code == 1
        assert "must be answered" in result.stdout
        assert "EXA2S.DE" in result.stdout
        assert "executed price / provider close" in result.stdout

    def test_the_refusal_prints_a_paste_ready_answer_block(
        self, export: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner.invoke(
            app, ["import", str(export), "--resolutions", str(_resolutions(tmp_path, RESOLVE_BOTH))]
        )
        self._stub(monkeypatch, resolves_to=None)
        monkeypatch.setenv("INSTRUMENT_SYMBOLS_PATH", str(tmp_path / "absent.yaml"))
        get_settings.cache_clear()

        result = runner.invoke(app, ["fetch-prices"])

        assert "symbols:" in result.stdout
        assert "  - isin: " in result.stdout

    def test_symbols_says_so_when_nothing_is_waiting(self) -> None:
        assert runner.invoke(app, ["symbols"]).stdout.strip() == "no unresolved symbols"
```

Add `from datetime import date` and `from decimal import Decimal` to that file's imports.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd backend && python -m pytest tests/integration/test_fetch_prices.py tests/integration/test_cli.py -q`
Expected: PASS, 13 new tests plus the existing CLI tests.

- [ ] **Step 7: Run the whole gate**

Expected: 448 backend passed, 35 realdata, clean.

- [ ] **Step 8: Commit**

```bash
git add backend/app/ingest/prices.py backend/app/cli.py \
        backend/tests/integration/test_fetch_prices.py backend/tests/integration/test_cli.py
git commit -m "feat(ingest): fetch-prices -- five years deep, incremental, refusing while unresolved

Claude-Session: https://claude.ai/code/session_01UzfynZY2Rqs9oMAtCivdSF"
```

---

### Task 8: `rebuild()` gains `position_daily` and `cash_daily` — and still cannot touch the cache

M2-5. `rebuild()` writes the two derived daily tables and nothing else changes about it. The interesting test is the negative one: a rebuild must leave `price_daily` and `fx_daily` untouched, because the moment it does not, the determinism claim is gone.

`through` becomes a parameter, defaulting to the ledger's last trade date. That keeps `rebuild(engine, method)` a pure function of the ledger — every M1 determinism test keeps passing unchanged — while letting the CLI say "carry the series up to today", which is what a reader who last traded three months ago actually wants.

**Files:**
- Modify: `backend/app/analytics/rebuild.py`
- Modify: `backend/app/cli.py` (`rebuild` gains `--through`)
- Test: `backend/tests/integration/test_rebuild.py` (existing — add `TestDailySeries`)

**Interfaces:**
- Consumes: `daily_series`, `DailySeries` from `app.domain.positions`; `PositionDaily`, `CashDaily` from `app.models.ledger`.
- Produces:
  - `rebuild(engine: Engine, method: LotMethod, *, through: date | None = None) -> RebuildResult`
  - `RebuildResult` gains `position_days: int` and `cash_days: int`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/integration/test_rebuild.py`:

```python
class TestDailySeries:
    """`position_daily` and `cash_daily`: derived, deterministic, and no more.

    The most important test in this class is the one that asserts what `rebuild()`
    does NOT do. M2 spec section 4 splits the schema on the determinism line, and
    a rebuild that quietly rewrote a fetched price would put the two halves back
    together -- the same ledger rebuilt on two days would then produce different
    rows from identical facts, and M1's determinism tests would be asserting
    something false.
    """

    def test_writes_a_row_per_weekday_per_held_instrument(self, engine) -> None:
        rebuild(engine, "FIFO")
        with Session(engine) as session:
            rows = session.exec(select(PositionDaily)).all()
        assert rows
        assert all(row.position_date.weekday() < 5 for row in rows)

    def test_writes_one_cash_balance_per_weekday(self, engine) -> None:
        rebuild(engine, "FIFO")
        with Session(engine) as session:
            days = [row.cash_date for row in session.exec(select(CashDaily)).all()]
        assert len(days) == len(set(days))

    def test_the_share_series_is_identical_under_every_method(self, engine) -> None:
        """The schema decision, proved rather than asserted in a comment. M1
        showed share counts are method-independent; this shows the table that
        drops the `method` column is entitled to."""
        snapshots = {}
        for method in ("FIFO", "LIFO", "HIFO"):
            rebuild(engine, method)
            with Session(engine) as session:
                snapshots[method] = sorted(
                    (row.position_date, row.isin, str(row.quantity))
                    for row in session.exec(select(PositionDaily)).all()
                )
        assert snapshots["FIFO"] == snapshots["LIFO"] == snapshots["HIFO"]

    def test_rebuilding_twice_produces_identical_rows(self, engine) -> None:
        def snapshot() -> list[tuple[date, str, str]]:
            with Session(engine) as session:
                return sorted(
                    (row.position_date, row.isin, str(row.quantity))
                    for row in session.exec(select(PositionDaily)).all()
                )

        rebuild(engine, "FIFO")
        first = snapshot()
        rebuild(engine, "FIFO")
        assert snapshot() == first

    def test_replaces_rather_than_accumulates(self, engine) -> None:
        rebuild(engine, "FIFO")
        with Session(engine) as session:
            before = len(session.exec(select(PositionDaily)).all())
        rebuild(engine, "FIFO")
        with Session(engine) as session:
            assert len(session.exec(select(PositionDaily)).all()) == before

    def test_defaults_the_window_end_to_the_last_ledger_day(self, engine) -> None:
        """A default that read `date.today()` would make two rebuilds on two days
        produce different tables from one ledger, which is precisely the property
        M1 spends a whole test class on."""
        with Session(engine) as session:
            last_trade = max(row.trade_date for row in session.exec(select(Transaction)).all())
        rebuild(engine, "FIFO")
        with Session(engine) as session:
            assert max(row.cash_date for row in session.exec(select(CashDaily)).all()) == (
                _last_weekday_on_or_before(last_trade)
            )

    def test_through_carries_the_series_past_the_last_trade(self, engine) -> None:
        """What the CLI passes. A position held for three months since the last
        trade is still held, and a chart that stopped at the last trade would say
        otherwise."""
        with Session(engine) as session:
            last_trade = max(row.trade_date for row in session.exec(select(Transaction)).all())
        later = last_trade + timedelta(days=30)
        rebuild(engine, "FIFO", through=later)
        with Session(engine) as session:
            assert max(row.cash_date for row in session.exec(select(CashDaily)).all()) == (
                _last_weekday_on_or_before(later)
            )

    def test_reports_what_it_wrote(self, engine) -> None:
        result = rebuild(engine, "FIFO")
        assert result.position_days > 0
        assert result.cash_days > 0

    def test_does_not_touch_the_price_cache(self, engine) -> None:
        """The determinism line, asserted. `rebuild()` is a function of the
        ledger; a fetched price is not in the ledger, and a rebuild that dropped
        one would silently require a network call to recover a chart."""
        with Session(engine) as session:
            session.add(
                PriceDaily(
                    id=uuid4(),
                    isin="NL0000000001",
                    price_date=date(2025, 3, 3),
                    close_unadjusted=Decimal("12.30"),
                    close_adjusted=Decimal("11.80"),
                    currency="EUR",
                    source="yahoo",
                    fetched_at=datetime(2026, 9, 6, 12, 0, 0),
                )
            )
            session.add(
                FxDaily(
                    id=uuid4(),
                    from_ccy="USD",
                    to_ccy="EUR",
                    rate_date=date(2025, 3, 3),
                    rate=Decimal("1.04"),
                    source="ecb",
                    fetched_at=datetime(2026, 9, 6, 12, 0, 0),
                )
            )
            session.commit()

        rebuild(engine, "FIFO")

        with Session(engine) as session:
            assert len(session.exec(select(PriceDaily)).all()) == 1
            assert len(session.exec(select(FxDaily)).all()) == 1

def _last_weekday_on_or_before(day: date) -> date:
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day
```

Add to that file's imports: `from datetime import date, datetime, timedelta`, `from decimal import Decimal`, `from uuid import uuid4`, `from app.models.ledger import CashDaily, PositionDaily, Transaction`, `from app.models.market import FxDaily, PriceDaily`.

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest tests/integration/test_rebuild.py -q`
Expected: FAIL — `ImportError: cannot import name 'PositionDaily'` is already satisfied by Task 1, so the first real failure is `assert rows` finding none, because `rebuild()` writes no daily rows yet.

- [ ] **Step 3: Extend `rebuild()`**

Modify `backend/app/analytics/rebuild.py`. Extend the module docstring with a paragraph:

```python
"""...

M2 adds two more derived tables, `position_daily` and `cash_daily`, and
deliberately adds no third. There is no `valuation_daily`: valuation depends on a
fetched price, and a table that did would make this function's answer depend on
when it ran. Everything written here stays a pure function of the ledger, which
is the entire reason the price cache lives on the other side of the line and is
never touched from this module (M2 spec section 4).

`through` is why that still holds with a daily series in it. The ledger says when
you last traded, not when you last held, so the series needs an end date the
ledger cannot supply -- and taking it as a parameter, defaulting to the last
trade date, keeps `rebuild(engine, method)` a function of its inputs while
letting the CLI ask for "up to today".
"""
```

Add the imports and extend `RebuildResult`:

```python
from datetime import date

from app.domain.positions import daily_series
from app.models.ledger import CashDaily, Lot, LotClosure, PositionDaily, Transaction

@dataclass(frozen=True, slots=True)
class RebuildResult:
    method: LotMethod
    lots: int
    closures: int
    charges_attributed: Decimal
    charges_in_ledger: Decimal
    position_days: int
    cash_days: int
```

Change the signature and add the daily pass. Inside `rebuild`, after `splits = derive_splits(rows)`:

```python
def rebuild(
    engine: Engine, method: LotMethod, *, through: date | None = None
) -> RebuildResult:
    """Recompute the derived tables for one method, replacing what is there.

    `through` is the last day of the daily series. `None` means the ledger's own
    last trade date, which keeps this deterministic; the CLI passes today's date
    so a position held since the last trade keeps appearing on the chart.
    """
    with Session(engine) as session:
        rows = list(session.exec(select(Transaction)).all())

    window_end = through or max((row.trade_date for row in rows), default=date.min)
    series = daily_series(rows, through=window_end)
```

and in the write block, after the lot and closure rows are added:

```python
        for stale_position in session.exec(select(PositionDaily)).all():
            session.delete(stale_position)
        for stale_cash in session.exec(select(CashDaily)).all():
            session.delete(stale_cash)
        session.flush()
        for point in series.positions:
            session.add(
                PositionDaily(
                    id=uuid4(),
                    position_date=point.on,
                    isin=point.isin,
                    quantity=point.quantity,
                )
            )
        for cash_point in series.cash:
            session.add(
                CashDaily(
                    id=uuid4(),
                    cash_date=cash_point.on,
                    balance_base=cash_point.balance_base,
                )
            )
        session.commit()
```

and extend the returned result with `position_days=len(series.positions), cash_days=len(series.cash)`.

- [ ] **Step 4: Give the CLI a `--through`**

Modify `rebuild_command` in `backend/app/cli.py`:

```python
@app.command("rebuild")
def rebuild_command(
    method: Annotated[
        str, typer.Option("--method", help="FIFO, LIFO or HIFO. Defaults to the configured one.")
    ] = "",
    through: Annotated[
        str,
        typer.Option(
            "--through",
            help="Last day of the daily position and cash series. Defaults to today.",
        ),
    ] = "",
) -> None:
    """Recompute the derived tables from the ledger (design doc Sec 11.2).

    Derived tables only -- the ledger and the price cache are both untouched.
    Exits non-zero if attributed charges do not equal the ledger's, having
    written nothing.

    The daily series runs to `--through`, defaulting to today rather than to the
    last trade: a position held since the last trade is still held, and a chart
    that stopped there would say otherwise.
    """
    settings = get_settings()
    chosen = (method or settings.lot_method).upper()
    if chosen not in LOT_METHODS:
        typer.echo(f"unknown method {chosen!r}; expected one of {list(LOT_METHODS)}", err=True)
        raise typer.Exit(code=2)

    try:
        window_end = date.fromisoformat(through) if through else date.today()
    except ValueError as bad:
        typer.echo(f"--through {through!r} is not YYYY-MM-DD", err=True)
        raise typer.Exit(code=2) from bad

    engine = create_engine_and_tables(settings.database_url)
    try:
        result = rebuild(engine, chosen, through=window_end)
    except ChargeMismatch as mismatch:
        typer.echo(str(mismatch), err=True)
        raise typer.Exit(code=1) from mismatch

    typer.echo(
        f"{result.method}: {result.lots} open lots, {result.closures} closures, "
        f"charges {result.charges_attributed} == ledger {result.charges_in_ledger}"
    )
    typer.echo(
        f"daily series: {result.position_days} position rows, "
        f"{result.cash_days} cash days through {window_end.isoformat()}"
    )
```

Add `from datetime import date` to the CLI imports if it is not already there.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend && python -m pytest tests/integration/test_rebuild.py -q`
Expected: PASS, 9 new tests plus every existing one.

- [ ] **Step 6: Run the whole gate**

Expected: 457 backend passed, 35 realdata, clean. **If any M1 determinism test now fails, stop.** That means `through` reached a wall clock, and the fix is the default, not the test.

- [ ] **Step 7: Commit**

```bash
git add backend/app/analytics/rebuild.py backend/app/cli.py \
        backend/tests/integration/test_rebuild.py
git commit -m "feat(rebuild): write position_daily and cash_daily, and nothing on the fetched side

Claude-Session: https://claude.ai/code/session_01UzfynZY2Rqs9oMAtCivdSF"
```

---
### Task 9: `analytics/valuation.py` — the join, carry-forward, and coverage as a result

The read-time join of the two halves. There is no table here and there must not be one: it would have two independent triggers, a ledger change and a price refresh, either of which would silently invalidate it (M2 section 4).

Four decisions this task settles, all of them stated in the module docstring because they are the difference between a number and a claim:

1. **Carry-forward is not separate machinery.** It is what "the latest price dated on or before this day" means. The gap between that price's date and the day being valued *is* the staleness M2-3 requires to be recorded.
2. **Coverage is weighted by value, not by instrument count.** One large holding going dark matters more than three small ones; a count would say the opposite.
3. **A manual component's staleness is not measured.** A hand-maintained CSV is sparse by design, so measuring it would pin every manual day at `partial` and make `manual` unreachable — and §8.1 requires all four values to be reachable. `partial` stays reachable because it is about *provider* staleness.
4. **`covered_pct` is `null` when coverage is `missing`.** The day has no total, so "how much of the total is fresh" has no denominator. Reporting a fraction of a number that does not exist is the same mistake as reporting €0.

**Files:**
- Create: `backend/app/analytics/valuation.py`
- Test: `backend/tests/integration/test_valuation.py`

**Interfaces:**
- Consumes: `PositionDaily`, `CashDaily`, `Lot`, `Account`, `Transaction` from `app.models.ledger`; `PriceDaily`, `FxDaily` from `app.models.market`; `MANUAL` from `app.providers.base`; `LotMethod` from `app.domain.lots`.
- Produces:
  - `STALE_DAYS = 4`
  - `FULL`, `PARTIAL`, `MANUAL`, `MISSING` coverage constants and `worst_coverage(values) -> str`
  - `ValuationPoint(on, holdings_base, cash_base, value_base, coverage, covered_pct)`
  - `ValuationSeries(points, start, end, requested_from, clamped, coverage, base_currency)`
  - `PositionValue(isin, product_name, currency, quantity, cost_basis, charges_base, price, price_date, source, market_value_base, gross_unrealised_base, unrealised_base, unrealised_pct, coverage)`
  - `PositionsSnapshot(as_of, items, total_cost_basis, total_market_value_base, total_unrealised_base, coverage, base_currency)`
  - `value_series(engine, *, start: date | None = None, end: date | None = None) -> ValuationSeries`
  - `current_positions(engine, method: LotMethod) -> PositionsSnapshot`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/integration/test_valuation.py`:

```python
"""The join, and every way a day can fail to be worth a number.

The tests are seeded directly into the four tables rather than driven through
`fetch-prices` and `rebuild()`, because the join is what is under test and going
through both would make a coverage failure ambiguous between three modules.

Each of the four coverage values is reached deliberately here, not incidentally.
That matters more than it sounds: `missing` is the one parent doc Sec 8.1 actually
turns on, and a total that quietly drops an unpriceable position looks exactly
like a total that includes it. If `missing` is only ever reached by accident, the
first time it matters will be the first time it is exercised.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlmodel import Session

from app.analytics.valuation import (
    FULL,
    MANUAL,
    MISSING,
    PARTIAL,
    current_positions,
    value_series,
)
from app.db import create_engine_and_tables
from app.models.ledger import Account, CashDaily, ImportBatch, Lot, PositionDaily, Transaction
from app.models.market import FxDaily, PriceDaily

D = Decimal
ZERO = D("0.00")
FETCHED = datetime(2026, 9, 6, 12, 0, 0)

MON = date(2025, 3, 3)
TUE = date(2025, 3, 4)
WED = date(2025, 3, 5)
THU = date(2025, 3, 6)
FRI = date(2025, 3, 7)
NEXT_MON = date(2025, 3, 10)

A = "NL0000000001"
B = "US0000000404"

@pytest.fixture(name="engine")
def _engine():
    return create_engine_and_tables("sqlite://")

class Seed:
    """A tiny world: whichever rows a test needs, and nothing else."""

    def __init__(self, engine) -> None:
        self.engine = engine
        with Session(engine) as session:
            session.add(
                Account(id=uuid4(), broker="degiro", name="test", base_currency="EUR")
            )
            session.commit()

    def held(self, on: date, isin: str, quantity: str) -> "Seed":
        with Session(self.engine) as session:
            session.add(
                PositionDaily(
                    id=uuid4(), position_date=on, isin=isin, quantity=D(quantity)
                )
            )
            session.commit()
        return self

    def cash(self, on: date, balance: str) -> "Seed":
        with Session(self.engine) as session:
            session.add(CashDaily(id=uuid4(), cash_date=on, balance_base=D(balance)))
            session.commit()
        return self

    def priced(
        self, on: date, isin: str, close: str, *, currency: str = "EUR", source: str = "yahoo"
    ) -> "Seed":
        with Session(self.engine) as session:
            session.add(
                PriceDaily(
                    id=uuid4(),
                    isin=isin,
                    price_date=on,
                    close_unadjusted=D(close),
                    # Deliberately different, so a join that read the wrong
                    # column would produce a wrong number rather than the same one.
                    close_adjusted=D(close) / 2,
                    currency=currency,
                    source=source,
                    fetched_at=FETCHED,
                )
            )
            session.commit()
        return self

    def rate(self, on: date, from_ccy: str, value: str) -> "Seed":
        with Session(self.engine) as session:
            session.add(
                FxDaily(
                    id=uuid4(),
                    from_ccy=from_ccy,
                    to_ccy="EUR",
                    rate_date=on,
                    rate=D(value),
                    source="ecb",
                    fetched_at=FETCHED,
                )
            )
            session.commit()
        return self

    def lot(self, isin: str, *, method: str = "FIFO", quantity: str, cost: str,
            commission: str = "0.00") -> "Seed":
        with Session(self.engine) as session:
            session.add(
                Lot(
                    id=uuid4(),
                    method=method,
                    isin=isin,
                    source_ref=f"ref-{uuid4()}",
                    opened_on=MON,
                    quantity=D(quantity),
                    price=D(cost) / D(quantity),
                    cost_basis=D(cost),
                    commission=D(commission),
                    autofx=ZERO,
                    tax=ZERO,
                )
            )
            session.commit()
        return self

    def named(self, isin: str, name: str, currency: str) -> "Seed":
        """A ledger row, purely so the positions endpoint can name the instrument.

        `models.ledger.Instrument` is declared and nothing populates it, so the
        name and trade currency live on the transaction that carried them.
        """
        account_id, batch_id = uuid4(), uuid4()
        with Session(self.engine) as session:
            session.add(
                ImportBatch(
                    id=batch_id, source="degiro", filename="t.csv", file_sha256="0" * 64,
                    parser_version="1", imported_at=FETCHED, row_count=1, inserted_count=1,
                )
            )
            account = session.query(Account).first()
            account_id = account.id  # type: ignore[union-attr]
            session.add(
                Transaction(
                    id=uuid4(), account_id=account_id, import_batch_id=batch_id,
                    source="degiro", source_ref=f"n-{uuid4()}", txn_type="BUY",
                    trade_date=MON, isin=isin, product_name=name, quantity=D("1"),
                    price_local=D("1"), currency_local=currency, fee_base=ZERO,
                    tax_base=ZERO, autofx_fee_base=ZERO, value_base=D("-1"),
                    net_base=D("-1"), raw_json="{}",
                )
            )
            session.commit()
        return self

class TestTheJoin:
    def test_values_a_holding_at_quantity_times_close_plus_cash(self, engine) -> None:
        Seed(engine).held(MON, A, "10").cash(MON, "100.00").priced(MON, A, "20.00")
        point = value_series(engine).points[0]
        assert point.holdings_base == D("200.00")
        assert point.cash_base == D("100.00")
        assert point.value_base == D("300.00")

    def test_a_debit_balance_reduces_the_total(self, engine) -> None:
        """M2-4: value is NET. Leaving the overdraft out would overstate the
        portfolio by exactly the amount the broker is owed."""
        Seed(engine).held(MON, A, "10").cash(MON, "-50.00").priced(MON, A, "20.00")
        assert value_series(engine).points[0].value_base == D("150.00")

    def test_reads_the_unadjusted_close_not_the_adjusted_one(self, engine) -> None:
        """Parent doc Sec 7.5. The fixture stores an adjusted close of half the
        plain one, so a join reading the wrong column halves the portfolio."""
        Seed(engine).held(MON, A, "10").cash(MON, "0.00").priced(MON, A, "20.00")
        assert value_series(engine).points[0].holdings_base == D("200.00")

    def test_divides_by_the_fx_rate_to_reach_the_base_currency(self, engine) -> None:
        """1.04 USD per 1 EUR: 10 shares at USD 20.80 is EUR 200.00. Multiplying
        instead gives EUR 216.32 -- wrong by 8% and entirely believable."""
        (
            Seed(engine)
            .held(MON, B, "10")
            .cash(MON, "0.00")
            .priced(MON, B, "20.80", currency="USD")
            .rate(MON, "USD", "1.04")
        )
        assert value_series(engine).points[0].holdings_base == D("200.00")

    def test_a_base_currency_holding_needs_no_rate_row(self, engine) -> None:
        """EUR to EUR is 1 by arithmetic, not by a fetched fact."""
        Seed(engine).held(MON, A, "10").cash(MON, "0.00").priced(MON, A, "20.00")
        assert value_series(engine).points[0].coverage == FULL

    def test_sums_every_held_instrument(self, engine) -> None:
        (
            Seed(engine)
            .held(MON, A, "10")
            .held(MON, B, "5")
            .cash(MON, "0.00")
            .priced(MON, A, "20.00")
            .priced(MON, B, "10.40", currency="USD")
            .rate(MON, "USD", "1.04")
        )
        assert value_series(engine).points[0].holdings_base == D("250.00")

class TestCarryForward:
    def test_carries_the_last_close_across_a_venue_holiday(self, engine) -> None:
        """M2-3: every weekday is valued, with no holes and no silent inference.
        Carry-forward is not extra machinery -- it is what "the latest price on
        or before this day" means."""
        seed = Seed(engine).priced(MON, A, "20.00")
        for day in (MON, TUE, WED):
            seed.held(day, A, "10").cash(day, "0.00")
        assert [p.holdings_base for p in value_series(engine).points] == [
            D("200.00"),
            D("200.00"),
            D("200.00"),
        ]

    def test_absorbs_a_weekend_without_reporting_staleness(self, engine) -> None:
        """Friday's close valuing Monday is three calendar days old, which is
        within the four-day threshold. Crying wolf every Monday would train the
        reader to ignore the badge."""
        seed = Seed(engine).priced(FRI, A, "20.00")
        seed.held(FRI, A, "10").cash(FRI, "0.00")
        seed.held(NEXT_MON, A, "10").cash(NEXT_MON, "0.00")
        assert [p.coverage for p in value_series(engine).points] == [FULL, FULL]

    def test_never_uses_a_price_from_after_the_day_being_valued(self, engine) -> None:
        """A future close is not carry-forward, it is hindsight -- and it would
        make yesterday's chart change every time prices were fetched."""
        Seed(engine).held(MON, A, "10").cash(MON, "0.00").priced(TUE, A, "20.00")
        point = value_series(engine).points[0]
        assert point.coverage == MISSING
        assert point.value_base is None

class TestCoverage:
    def test_full_when_every_holding_is_freshly_priced(self, engine) -> None:
        Seed(engine).held(MON, A, "10").cash(MON, "0.00").priced(MON, A, "20.00")
        point = value_series(engine).points[0]
        assert point.coverage == FULL
        assert point.covered_pct == D("1")

    def test_partial_when_a_price_is_staler_than_four_days(self, engine) -> None:
        stale_from = MON - timedelta(days=10)
        Seed(engine).held(MON, A, "10").cash(MON, "0.00").priced(stale_from, A, "20.00")
        point = value_series(engine).points[0]
        assert point.coverage == PARTIAL
        # Still valued: a stale price is a worse answer, not no answer.
        assert point.value_base == D("200.00")

    def test_covered_pct_is_weighted_by_value_not_by_instrument_count(self, engine) -> None:
        """One large holding going dark matters more than three small ones, and
        a count would say the opposite. Here the stale instrument is 80% of the
        value and 50% of the instruments."""
        stale_from = MON - timedelta(days=10)
        (
            Seed(engine)
            .held(MON, A, "40")
            .held(MON, B, "10")
            .cash(MON, "0.00")
            .priced(stale_from, A, "20.00")   # 800.00, stale
            .priced(MON, B, "20.80", currency="USD")  # 200.00, fresh
            .rate(MON, "USD", "1.04")
        )
        point = value_series(engine).points[0]
        assert point.coverage == PARTIAL
        assert point.covered_pct == D("0.2")

    def test_manual_when_a_component_came_from_the_csv(self, engine) -> None:
        Seed(engine).held(MON, A, "10").cash(MON, "0.00").priced(
            MON, A, "20.00", source="manual"
        )
        assert value_series(engine).points[0].coverage == MANUAL

    def test_a_manual_price_carried_a_long_way_stays_manual(self, engine) -> None:
        """The rule that keeps all four values reachable. A hand-maintained CSV
        is sparse by design; measuring its staleness would pin every manual day
        at `partial` and `manual` would never be reached at all."""
        long_ago = MON - timedelta(days=60)
        Seed(engine).held(MON, A, "10").cash(MON, "0.00").priced(
            long_ago, A, "20.00", source="manual"
        )
        point = value_series(engine).points[0]
        assert point.coverage == MANUAL
        assert point.covered_pct == D("1")

    def test_missing_when_a_held_instrument_has_no_price_at_all(self, engine) -> None:
        """The rule parent doc Sec 8.1 actually turns on. A total that quietly
        drops an unpriceable position looks exactly like a total that includes
        it, so it must not be computed. Never EUR 0, never a silent omission."""
        Seed(engine).held(MON, A, "10").held(MON, B, "5").cash(MON, "100.00").priced(
            MON, A, "20.00"
        )
        point = value_series(engine).points[0]
        assert point.coverage == MISSING
        assert point.value_base is None
        assert point.holdings_base is None

    def test_covered_pct_is_null_on_a_missing_day(self, engine) -> None:
        """There is no total, so "how much of the total is fresh" has no
        denominator. A fraction of a number that does not exist is the same
        mistake as reporting zero."""
        Seed(engine).held(MON, A, "10").cash(MON, "0.00")
        assert value_series(engine).points[0].covered_pct is None

    def test_missing_when_the_fx_rate_is_absent(self, engine) -> None:
        """A foreign holding needs two facts. Having one of them is not most of
        the way there -- it is no answer at all."""
        Seed(engine).held(MON, B, "10").cash(MON, "0.00").priced(
            MON, B, "20.80", currency="USD"
        )
        assert value_series(engine).points[0].coverage == MISSING

    def test_missing_beats_partial(self, engine) -> None:
        stale_from = MON - timedelta(days=10)
        Seed(engine).held(MON, A, "10").held(MON, B, "5").cash(MON, "0.00").priced(
            stale_from, A, "20.00"
        )
        assert value_series(engine).points[0].coverage == MISSING

    def test_a_cash_only_day_is_fully_covered(self, engine) -> None:
        """Nothing needs a price, so nothing can be stale. The cash balance is
        ledger arithmetic and is as certain as any number in the app."""
        Seed(engine).cash(MON, "500.00")
        point = value_series(engine).points[0]
        assert point.coverage == FULL
        assert point.value_base == D("500.00")

    def test_the_series_reports_the_worst_day_it_contains(self, engine) -> None:
        """An envelope claiming `full` over a series with a missing day would be
        exactly the omission Sec 8.1 forbids, one level up."""
        seed = Seed(engine).priced(MON, A, "20.00")
        seed.held(MON, A, "10").cash(MON, "0.00")
        seed.held(TUE, B, "5").cash(TUE, "0.00")
        assert value_series(engine).coverage == MISSING

class TestWindow:
    def test_starts_at_the_first_day_a_position_existed(self, engine) -> None:
        """M2-7. The cache reaches further back, but valuing days on which
        nothing was held would draw a flat line and invite the reader to wonder
        what broke."""
        seed = Seed(engine).priced(MON, A, "20.00")
        seed.cash(MON, "1000.00")
        seed.cash(TUE, "1000.00")
        seed.held(WED, A, "10").cash(WED, "800.00")
        assert value_series(engine).points[0].on == WED

    def test_an_explicit_earlier_start_is_honoured_back_to_the_first_ledger_day(
        self, engine
    ) -> None:
        """What "look back five years if you want" means. The days before the
        first position are not flat zero -- they are the cash that was sitting
        in the account, which is a real number the ledger knows exactly."""
        seed = Seed(engine).priced(WED, A, "20.00")
        seed.cash(MON, "1000.00")
        seed.cash(TUE, "1000.00")
        seed.held(WED, A, "10").cash(WED, "800.00")

        series = value_series(engine, start=MON)

        assert [p.on for p in series.points] == [MON, TUE, WED]
        assert series.points[0].value_base == D("1000.00")
        assert series.points[0].coverage == FULL
        assert series.clamped is False

    def test_a_start_before_the_ledger_is_clamped_and_says_so(self, engine) -> None:
        """Never padded with zeros. A zero portfolio value is a claim, and on a
        day before the account existed it is a false one."""
        seed = Seed(engine).priced(MON, A, "20.00")
        seed.held(MON, A, "10").cash(MON, "0.00")

        five_years_back = MON - timedelta(days=365 * 5)
        series = value_series(engine, start=five_years_back)

        assert series.requested_from == five_years_back
        assert series.clamped is True
        assert series.start == MON
        assert [p.on for p in series.points] == [MON]

    def test_an_explicit_end_truncates_without_inventing_days(self, engine) -> None:
        seed = Seed(engine).priced(MON, A, "20.00")
        for day in (MON, TUE, WED):
            seed.held(day, A, "10").cash(day, "0.00")
        assert [p.on for p in value_series(engine, end=TUE).points] == [MON, TUE]

    def test_an_empty_ledger_yields_an_empty_series(self, engine) -> None:
        series = value_series(engine)
        assert series.points == ()
        assert series.start is None

class TestPositions:
    def test_values_an_open_position_at_the_latest_close(self, engine) -> None:
        (
            Seed(engine)
            .named(A, "Example Holdings", "EUR")
            .held(MON, A, "10")
            .cash(MON, "0.00")
            .priced(MON, A, "20.00")
            .lot(A, quantity="10", cost="150.00", commission="2.00")
        )
        snapshot = current_positions(engine, "FIFO")
        row = snapshot.items[0]
        assert row.quantity == D("10")
        assert row.market_value_base == D("200.00")
        assert row.cost_basis == D("150.00")

    def test_reports_gross_and_net_unrealised_separately(self, engine) -> None:
        """Sec 6.4's three answers, carried into the open position: what the
        stock did, what the broker charged, and what is left. Rolling the
        commission into one figure would hide it."""
        (
            Seed(engine)
            .named(A, "Example Holdings", "EUR")
            .held(MON, A, "10")
            .cash(MON, "0.00")
            .priced(MON, A, "20.00")
            .lot(A, quantity="10", cost="150.00", commission="2.00")
        )
        row = current_positions(engine, "FIFO").items[0]
        assert row.gross_unrealised_base == D("50.00")
        assert row.charges_base == D("2.00")
        assert row.unrealised_base == D("48.00")

    def test_an_unpriceable_position_reports_no_value_rather_than_zero(self, engine) -> None:
        (
            Seed(engine)
            .named(A, "Example Holdings", "EUR")
            .held(MON, A, "10")
            .cash(MON, "0.00")
            .lot(A, quantity="10", cost="150.00")
        )
        row = current_positions(engine, "FIFO").items[0]
        assert row.market_value_base is None
        assert row.unrealised_base is None
        assert row.coverage == MISSING

    def test_the_total_is_withheld_when_any_position_is_unpriceable(self, engine) -> None:
        """Sec 8.1 at the aggregate level. A total that silently omitted one
        holding reads identically to one that included it."""
        (
            Seed(engine)
            .named(A, "Example Holdings", "EUR")
            .named(B, "Other Holdings", "USD")
            .held(MON, A, "10")
            .held(MON, B, "5")
            .cash(MON, "0.00")
            .priced(MON, A, "20.00")
            .lot(A, quantity="10", cost="150.00")
            .lot(B, quantity="5", cost="80.00")
        )
        snapshot = current_positions(engine, "FIFO")
        assert snapshot.total_market_value_base is None
        assert snapshot.coverage == MISSING
        # The cost side is ledger arithmetic and is still exact.
        assert snapshot.total_cost_basis == D("230.00")

    def test_reads_the_cost_basis_of_the_method_it_was_asked_for(self, engine) -> None:
        (
            Seed(engine)
            .named(A, "Example Holdings", "EUR")
            .held(MON, A, "10")
            .cash(MON, "0.00")
            .priced(MON, A, "20.00")
            .lot(A, method="FIFO", quantity="10", cost="150.00")
            .lot(A, method="HIFO", quantity="10", cost="180.00")
        )
        assert current_positions(engine, "HIFO").items[0].cost_basis == D("180.00")

    def test_holds_no_row_for_an_instrument_no_longer_held(self, engine) -> None:
        seed = Seed(engine).named(A, "Example Holdings", "EUR").priced(MON, A, "20.00")
        seed.held(MON, A, "10").cash(MON, "0.00")
        seed.cash(TUE, "200.00")  # sold out on Tuesday: no position row
        assert current_positions(engine, "FIFO").items == ()

    def test_names_the_instrument_from_the_ledger(self, engine) -> None:
        (
            Seed(engine)
            .named(A, "Example Holdings", "EUR")
            .held(MON, A, "10")
            .cash(MON, "0.00")
            .priced(MON, A, "20.00")
            .lot(A, quantity="10", cost="150.00")
        )
        assert current_positions(engine, "FIFO").items[0].product_name == "Example Holdings"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend && python -m pytest tests/integration/test_valuation.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.analytics.valuation'`

- [ ] **Step 3: Write the implementation**

Create `backend/app/analytics/valuation.py`:

```python
"""Value the portfolio by joining the ledger's half to the network's. No table.

M2 spec section 4. `position_daily` and `cash_daily` are derived and
deterministic; `price_daily` and `fx_daily` are fetched and dated. Valuation is
where the two meet, and it meets them at READ time.

There is deliberately no `valuation_daily`. A table would have two independent
triggers -- a ledger change and a price refresh -- either of which would silently
invalidate it, and there is no way to look at a stored valuation and tell whether
it is still true. The cost is a join per read rather than a lookup, which at this
portfolio's size is tens of thousands of rows in SQLite. M2 has no scale problem
to solve and should not pre-solve one.

Four rules decide what a day is worth, and each is a claim rather than a
convenience:

**Carry-forward is not separate machinery.** "The latest price dated on or before
this day" already carries a Friday close into Monday. The gap between that
price's date and the day being valued IS the staleness M2-3 requires recorded --
so nothing infers a price, it only says how old the one it used is. A price dated
AFTER the day is never used: that is not carry-forward, it is hindsight, and it
would make yesterday's chart move every time prices were fetched.

**Coverage is weighted by value, not by instrument count.** One large holding
going dark matters more than three small ones, and a count would say the
opposite.

**A manual component's staleness is not measured.** `config/manual_prices.csv` is
sparse by design -- an operator types a price when they have one -- so measuring
its age would pin every manual day at `partial` and `manual` would never be
reached at all, which parent doc Sec 8.1 forbids by requiring all four values to
mean something distinct. `partial` stays reachable because it is a statement
about PROVIDER staleness, and a stale provider price is a different problem from
a hand-maintained one.

**A day with an unpriceable holding has no value.** Not EUR 0, not the sum of
what happens to be priced. A total that quietly drops a position looks exactly
like a total that includes it, and `covered_pct` is `null` there too: there is no
denominator to take a fraction of.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import Engine
from sqlmodel import Session, select

from app.domain.lots import LotMethod
from app.models.ledger import Account, CashDaily, Lot, PositionDaily, Transaction
from app.models.market import FxDaily, PriceDaily
from app.providers.base import MANUAL as MANUAL_SOURCE

#: How old a provider price may be before the day is `partial`. Four calendar
#: days absorbs a weekend plus one holiday (M2 spec section 7.2). A venue closing
#: for longer would show as `partial`, which is arguably correct and has not been
#: observed in the current data.
STALE_DAYS = 4

FULL = "full"
PARTIAL = "partial"
MANUAL = "manual"
MISSING = "missing"

#: Worst news first. `missing` outranks everything because it is the only value
#: that withholds a number. `partial` outranks `manual` because a stale provider
#: price is a fault; a hand-typed one is a choice.
_SEVERITY = {FULL: 0, MANUAL: 1, PARTIAL: 2, MISSING: 3}

_ZERO = Decimal("0.00")
_ONE = Decimal("1")

def worst_coverage(values: Sequence[str]) -> str:
    """The most severe coverage among `values`. Empty means nothing to cover."""
    return max(values, key=lambda value: _SEVERITY[value], default=FULL)

@dataclass(frozen=True, slots=True)
class ValuationPoint:
    on: date
    #: `None` when any held instrument could not be priced. See the docstring.
    holdings_base: Decimal | None
    cash_base: Decimal
    value_base: Decimal | None
    coverage: str
    #: The share of the day's HOLDINGS value that is fresh from a provider or
    #: hand-supplied. `None` exactly when `coverage` is `missing`.
    covered_pct: Decimal | None

@dataclass(frozen=True, slots=True)
class ValuationSeries:
    points: tuple[ValuationPoint, ...]
    start: date | None
    end: date | None
    #: What the caller asked for, kept so the UI can say the window was clamped.
    requested_from: date | None
    clamped: bool
    #: The worst coverage in the series. An envelope claiming `full` over a
    #: series with a missing day would be Sec 8.1's omission one level up.
    coverage: str
    base_currency: str

@dataclass(frozen=True, slots=True)
class PositionValue:
    isin: str
    product_name: str
    currency: str
    quantity: Decimal
    cost_basis: Decimal
    charges_base: Decimal
    price: Decimal | None
    price_date: date | None
    source: str | None
    market_value_base: Decimal | None
    #: What the stock did. Sec 6.4 keeps these three separate all the way to the
    #: screen: gross, charges, net.
    gross_unrealised_base: Decimal | None
    unrealised_base: Decimal | None
    unrealised_pct: Decimal | None
    coverage: str

@dataclass(frozen=True, slots=True)
class PositionsSnapshot:
    as_of: date | None
    items: tuple[PositionValue, ...]
    total_cost_basis: Decimal
    #: `None` when ANY position is unpriceable. A total that silently omitted
    #: one reads identically to one that included it.
    total_market_value_base: Decimal | None
    total_unrealised_base: Decimal | None
    coverage: str
    base_currency: str

# ---------------------------------------------------------------------------
# Reading the four tables
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class _Quote:
    close: Decimal
    currency: str
    on: date
    source: str

def _base_currency(session: Session) -> str:
    account = session.exec(select(Account)).first()
    return account.base_currency if account is not None else "EUR"

def _price_history(session: Session) -> dict[str, list[PriceDaily]]:
    history: dict[str, list[PriceDaily]] = {}
    for row in session.exec(select(PriceDaily)).all():
        history.setdefault(row.isin, []).append(row)
    for rows in history.values():
        rows.sort(key=lambda row: row.price_date)
    return history

def _rate_history(session: Session) -> dict[tuple[str, str], list[FxDaily]]:
    history: dict[tuple[str, str], list[FxDaily]] = {}
    for row in session.exec(select(FxDaily)).all():
        history.setdefault((row.from_ccy, row.to_ccy), []).append(row)
    for rows in history.values():
        rows.sort(key=lambda row: row.rate_date)
    return history

def _latest_on_or_before(rows: Sequence[PriceDaily], on: date) -> PriceDaily | None:
    found: PriceDaily | None = None
    for row in rows:
        if row.price_date > on:
            break  # sorted, so nothing later can qualify
        found = row
    return found

def _latest_rate_on_or_before(rows: Sequence[FxDaily], on: date) -> FxDaily | None:
    found: FxDaily | None = None
    for row in rows:
        if row.rate_date > on:
            break
        found = row
    return found

def _quote_for(
    isin: str, on: date, history: Mapping[str, list[PriceDaily]]
) -> _Quote | None:
    row = _latest_on_or_before(history.get(isin, ()), on)
    if row is None:
        return None
    return _Quote(
        close=row.close_unadjusted,
        currency=row.currency,
        on=row.price_date,
        source=row.source,
    )

def _in_base(
    quote: _Quote,
    quantity: Decimal,
    on: date,
    base: str,
    rates: Mapping[tuple[str, str], list[FxDaily]],
) -> tuple[Decimal, int] | None:
    """Value one holding in the base currency, plus the age of the older input.

    `None` when the currency cannot be converted: a foreign holding needs two
    facts, and having one of them is no answer at all.
    """
    gross = quantity * quote.close
    age = (on - quote.on).days
    if quote.currency == base:
        return gross, age

    rate_row = _latest_rate_on_or_before(rates.get((quote.currency, base), ()), on)
    if rate_row is None or rate_row.rate == 0:
        return None
    # `rate` is units of the quote currency per 1 unit of base -- the same
    # direction as `domain.money.FxRate`. Divide, never multiply.
    return gross / rate_row.rate, max(age, (on - rate_row.rate_date).days)

def _classify(source: str, age: int) -> str:
    if source == MANUAL_SOURCE:
        # Deliberately not aged. See the module docstring.
        return MANUAL
    return PARTIAL if age > STALE_DAYS else FULL

# ---------------------------------------------------------------------------
# The series
# ---------------------------------------------------------------------------
def value_series(
    engine: Engine, *, start: date | None = None, end: date | None = None
) -> ValuationSeries:
    """The daily net portfolio value: holdings at market plus cash.

    Without `start`, the series begins at the first day a position existed
    (M2-7). With one, it begins there -- clamped to the ledger's own first day,
    never padded before it, because a zero portfolio value on a day the account
    did not exist is a false claim rather than a missing one. That clamp is what
    lets a reader ask for five years and get an honest answer whatever the
    ledger's depth.
    """
    with Session(engine) as session:
        base = _base_currency(session)
        cash_rows = sorted(session.exec(select(CashDaily)).all(), key=lambda r: r.cash_date)
        position_rows = session.exec(select(PositionDaily)).all()
        prices = _price_history(session)
        rates = _rate_history(session)

    if not cash_rows:
        return ValuationSeries(
            points=(), start=None, end=None, requested_from=start,
            clamped=False, coverage=FULL, base_currency=base,
        )

    held: dict[date, list[PositionDaily]] = {}
    for row in position_rows:
        held.setdefault(row.position_date, []).append(row)

    floor = cash_rows[0].cash_date
    default_start = min(held) if held else floor
    window_start = max(start, floor) if start is not None else default_start
    window_end = end or cash_rows[-1].cash_date

    points: list[ValuationPoint] = []
    for cash_row in cash_rows:
        day = cash_row.cash_date
        if day < window_start or day > window_end:
            continue
        points.append(
            _value_day(day, held.get(day, []), cash_row.balance_base, base, prices, rates)
        )

    return ValuationSeries(
        points=tuple(points),
        start=points[0].on if points else None,
        end=points[-1].on if points else None,
        requested_from=start,
        clamped=start is not None and start < floor,
        coverage=worst_coverage([point.coverage for point in points]),
        base_currency=base,
    )

def _value_day(
    day: date,
    holdings: Sequence[PositionDaily],
    cash: Decimal,
    base: str,
    prices: Mapping[str, list[PriceDaily]],
    rates: Mapping[tuple[str, str], list[FxDaily]],
) -> ValuationPoint:
    total = _ZERO
    covered = _ZERO
    verdicts: list[str] = []
    missing = False

    for holding in sorted(holdings, key=lambda row: row.isin):
        quote = _quote_for(holding.isin, day, prices)
        converted = (
            None
            if quote is None
            else _in_base(quote, holding.quantity, day, base, rates)
        )
        if quote is None or converted is None:
            missing = True
            verdicts.append(MISSING)
            continue

        value, age = converted
        verdict = _classify(quote.source, age)
        verdicts.append(verdict)
        total += value
        if verdict != PARTIAL:
            covered += value

    coverage = worst_coverage(verdicts)
    if missing:
        # No total, so no fraction of one either.
        return ValuationPoint(
            on=day, holdings_base=None, cash_base=cash, value_base=None,
            coverage=MISSING, covered_pct=None,
        )

    return ValuationPoint(
        on=day,
        holdings_base=total,
        cash_base=cash,
        value_base=total + cash,
        coverage=coverage,
        covered_pct=_ONE if total == 0 else covered / total,
    )

# ---------------------------------------------------------------------------
# The current positions
# ---------------------------------------------------------------------------
def current_positions(engine: Engine, method: LotMethod) -> PositionsSnapshot:
    """Open holdings with cost basis, market value and unrealised P&L.

    `method` is required because the cost basis comes from `lot`, and FIFO, LIFO
    and HIFO disagree about it. The share COUNT does not depend on it -- that is
    why `position_daily` has no method column -- so the two halves of a row come
    from two tables with two different provenances, and the response envelope
    says which method it used.
    """
    with Session(engine) as session:
        base = _base_currency(session)
        position_rows = session.exec(select(PositionDaily)).all()
        if not position_rows:
            return PositionsSnapshot(
                as_of=None, items=(), total_cost_basis=_ZERO,
                total_market_value_base=None, total_unrealised_base=None,
                coverage=FULL, base_currency=base,
            )
        as_of = max(row.position_date for row in position_rows)
        held = [row for row in position_rows if row.position_date == as_of]

        lots = session.exec(select(Lot).where(Lot.method == method)).all()
        names: dict[str, str] = {}
        currencies: dict[str, str] = {}
        for row in session.exec(select(Transaction)).all():
            if row.isin:
                if row.product_name:
                    names.setdefault(row.isin, row.product_name)
                if row.currency_local:
                    currencies.setdefault(row.isin, row.currency_local)

        prices = _price_history(session)
        rates = _rate_history(session)

    basis: dict[str, Decimal] = {}
    charges: dict[str, Decimal] = {}
    for lot in lots:
        basis[lot.isin] = basis.get(lot.isin, _ZERO) + lot.cost_basis
        charges[lot.isin] = (
            charges.get(lot.isin, _ZERO) + lot.commission + lot.autofx + lot.tax
        )

    items: list[PositionValue] = []
    for holding in sorted(held, key=lambda row: row.isin):
        isin = holding.isin
        cost = basis.get(isin, _ZERO)
        charged = charges.get(isin, _ZERO)
        quote = _quote_for(isin, as_of, prices)
        converted = (
            None
            if quote is None
            else _in_base(quote, holding.quantity, as_of, base, rates)
        )

        if quote is None or converted is None:
            items.append(
                PositionValue(
                    isin=isin,
                    product_name=names.get(isin, ""),
                    currency=currencies.get(isin, base),
                    quantity=holding.quantity,
                    cost_basis=cost,
                    charges_base=charged,
                    price=None if quote is None else quote.close,
                    price_date=None if quote is None else quote.on,
                    source=None if quote is None else quote.source,
                    market_value_base=None,
                    gross_unrealised_base=None,
                    unrealised_base=None,
                    unrealised_pct=None,
                    coverage=MISSING,
                )
            )
            continue

        value, age = converted
        gross = value - cost
        net = gross - charged
        items.append(
            PositionValue(
                isin=isin,
                product_name=names.get(isin, ""),
                currency=quote.currency,
                quantity=holding.quantity,
                cost_basis=cost,
                charges_base=charged,
                price=quote.close,
                price_date=quote.on,
                source=quote.source,
                market_value_base=value,
                gross_unrealised_base=gross,
                unrealised_base=net,
                # `None` rather than zero on a zero basis: a return on nothing is
                # undefined, and 0% would read as "broke even".
                unrealised_pct=None if cost == 0 else net / cost,
                coverage=_classify(quote.source, age),
            )
        )

    coverage = worst_coverage([item.coverage for item in items])
    priced = [item.market_value_base for item in items if item.market_value_base is not None]
    complete = len(priced) == len(items)

    return PositionsSnapshot(
        as_of=as_of,
        items=tuple(items),
        total_cost_basis=sum(basis.get(item.isin, _ZERO) for item in items) or _ZERO,
        total_market_value_base=sum(priced, _ZERO) if complete else None,
        total_unrealised_base=(
            sum(
                (item.unrealised_base or _ZERO for item in items), _ZERO
            )
            if complete
            else None
        ),
        coverage=coverage,
        base_currency=base,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && python -m pytest tests/integration/test_valuation.py -q`
Expected: PASS, 30 tests.

- [ ] **Step 5: Run the whole gate**

Expected: 487 backend passed, 35 realdata, clean.

- [ ] **Step 6: Commit**

```bash
git add backend/app/analytics/valuation.py backend/tests/integration/test_valuation.py
git commit -m "feat(analytics): value the portfolio as a read-time join, with coverage as a result

No valuation table: it would go stale against either half. Carry-forward is what
'latest on or before' means, coverage is weighted by value, and a day with an
unpriceable holding is worth null rather than the sum of what happens to be there.

Claude-Session: https://claude.ai/code/session_01UzfynZY2Rqs9oMAtCivdSF"
```

---

### Task 10: The valuation and positions endpoints

Two endpoints, both carrying `method` and `coverage` in the envelope, which `Provenance` makes structurally impossible to omit (§9.2). Money crosses the wire as a string, as everywhere else.

The two envelopes disagree about `method`, and that disagreement is the point. `/api/valuation` reports `method: null` — share counts are method-independent, which is why `position_daily` has no method column, so claiming FIFO would name a computation that never ran. `/api/positions` requires `method`, because its cost basis comes from `lot` and FIFO, LIFO and HIFO genuinely disagree about it.

**Files:**
- Create: `backend/app/api/routes_valuation.py`
- Create: `backend/app/api/routes_positions.py`
- Modify: `backend/app/api/schemas.py` (append the M2 schemas)
- Modify: `backend/app/main.py` (register both routers)
- Test: `backend/tests/integration/test_valuation_api.py`

**Interfaces:**
- Consumes: `value_series`, `current_positions`, `ValuationSeries`, `PositionsSnapshot` from `app.analytics.valuation`; `get_engine` from `app.api.routes_transactions`; `Provenance`, `Coverage`, `LotMethod` from `app.api.schemas`.
- Produces:
  - `ValuationPointOut`, `ValuationSeriesOut`, `PositionOut`, `PositionsOut` in `app.api.schemas`
  - `GET /api/valuation?from=&to=` → `ValuationSeriesOut`
  - `GET /api/positions?method=` → `PositionsOut`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/integration/test_valuation_api.py`:

```python
"""What the two M2 endpoints put on the wire.

Two things are tested here that no unit test can reach. Money must arrive as a
STRING -- a JSON number is an IEEE double and would undo the exactness the
Decimal storage exists for -- and `null` must arrive as `null` rather than as
"0", because parent doc Sec 8.1's whole rule is that an unpriceable day says so
instead of reporting nothing as zero.

The envelopes disagree about `method` on purpose. The value series ran no lot
matching, so `null` is the honest answer; the positions table read a cost basis
out of `lot`, so it must name the method it read.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db import create_engine_and_tables
from app.main import create_app
from app.models.ledger import (
    Account,
    CashDaily,
    ImportBatch,
    Lot,
    PositionDaily,
    Transaction,
)
from app.models.market import FxDaily, PriceDaily

D = Decimal
ZERO = D("0.00")
FETCHED = datetime(2026, 9, 6, 12, 0, 0)

MON = date(2025, 3, 3)
TUE = date(2025, 3, 4)
A = "NL0000000001"
B = "US0000000404"

def seed(engine, *, price_b: bool = True) -> None:
    account_id, batch_id = uuid4(), uuid4()
    with Session(engine) as session:
        session.add(Account(id=account_id, broker="degiro", name="t", base_currency="EUR"))
        session.add(
            ImportBatch(
                id=batch_id, source="degiro", filename="t.csv", file_sha256="0" * 64,
                parser_version="1", imported_at=FETCHED, row_count=2, inserted_count=2,
            )
        )
        for isin, name, currency in ((A, "Example Holdings", "EUR"), (B, "Other Holdings", "USD")):
            session.add(
                Transaction(
                    id=uuid4(), account_id=account_id, import_batch_id=batch_id,
                    source="degiro", source_ref=f"ref-{isin}", txn_type="BUY",
                    trade_date=MON, isin=isin, product_name=name, quantity=D("1"),
                    price_local=D("1"), currency_local=currency, fee_base=ZERO,
                    tax_base=ZERO, autofx_fee_base=ZERO, value_base=D("-1"),
                    net_base=D("-1"), raw_json="{}",
                )
            )
        for day in (MON, TUE):
            session.add(CashDaily(id=uuid4(), cash_date=day, balance_base=D("-50.00")))
            session.add(
                PositionDaily(id=uuid4(), position_date=day, isin=A, quantity=D("10"))
            )
            session.add(
                PositionDaily(id=uuid4(), position_date=day, isin=B, quantity=D("5"))
            )
            session.add(
                PriceDaily(
                    id=uuid4(), isin=A, price_date=day, close_unadjusted=D("20.00"),
                    close_adjusted=D("10.00"), currency="EUR", source="yahoo",
                    fetched_at=FETCHED,
                )
            )
            if price_b:
                session.add(
                    PriceDaily(
                        id=uuid4(), isin=B, price_date=day, close_unadjusted=D("20.80"),
                        close_adjusted=D("20.80"), currency="USD", source="yahoo",
                        fetched_at=FETCHED,
                    )
                )
                session.add(
                    FxDaily(
                        id=uuid4(), from_ccy="USD", to_ccy="EUR", rate_date=day,
                        rate=D("1.04"), source="ecb", fetched_at=FETCHED,
                    )
                )
        for method, cost in (("FIFO", "150.00"), ("HIFO", "180.00")):
            session.add(
                Lot(
                    id=uuid4(), method=method, isin=A, source_ref=f"lot-{method}",
                    opened_on=MON, quantity=D("10"), price=D(cost) / D("10"),
                    cost_basis=D(cost), commission=D("2.00"), autofx=ZERO, tax=ZERO,
                )
            )
            session.add(
                Lot(
                    id=uuid4(), method=method, isin=B, source_ref=f"lotb-{method}",
                    opened_on=MON, quantity=D("5"), price=D("16.00"),
                    cost_basis=D("80.00"), commission=ZERO, autofx=ZERO, tax=ZERO,
                )
            )
        session.commit()

@pytest.fixture(name="client")
def _client():
    engine = create_engine_and_tables("sqlite://")
    seed(engine)
    return TestClient(create_app(engine=engine))

class TestValuationEnvelope:
    def test_reports_no_lot_method_because_none_was_applied(self, client) -> None:
        """Share counts are method-independent -- that is why `position_daily`
        has no method column. Naming FIFO here would claim a computation that
        never ran."""
        body = client.get("/api/valuation").json()
        assert body["method"] is None

    def test_carries_the_worst_coverage_in_the_series(self, client) -> None:
        assert client.get("/api/valuation").json()["coverage"] == "full"

    def test_states_the_base_currency(self, client) -> None:
        assert client.get("/api/valuation").json()["base_currency"] == "EUR"

class TestValuationPoints:
    def test_money_crosses_the_wire_as_a_string(self, client) -> None:
        point = client.get("/api/valuation").json()["items"][0]
        assert point["value_base"] == "250.00"
        assert isinstance(point["cash_base"], str)

    def test_reports_the_cash_component_separately(self, client) -> None:
        """M2-4: value is net of cash, and cash is reported as its own component
        so a reader can see which half moved."""
        point = client.get("/api/valuation").json()["items"][0]
        assert point["cash_base"] == "-50.00"
        assert point["holdings_base"] == "300.00"

    def test_covered_pct_is_a_string_too(self, client) -> None:
        assert isinstance(client.get("/api/valuation").json()["items"][0]["covered_pct"], str)

    def test_an_unpriceable_day_sends_null_not_zero(self) -> None:
        engine = create_engine_and_tables("sqlite://")
        seed(engine, price_b=False)
        body = TestClient(create_app(engine=engine)).get("/api/valuation").json()
        point = body["items"][0]
        assert point["value_base"] is None
        assert point["holdings_base"] is None
        assert point["covered_pct"] is None
        assert point["coverage"] == "missing"
        assert body["coverage"] == "missing"

class TestValuationWindow:
    def test_defaults_to_the_first_day_a_position_existed(self, client) -> None:
        body = client.get("/api/valuation").json()
        assert body["start"] == MON.isoformat()
        assert body["clamped"] is False
        assert body["requested_from"] is None

    def test_honours_an_explicit_window(self, client) -> None:
        body = client.get(f"/api/valuation?from={TUE}&to={TUE}").json()
        assert [p["date"] for p in body["items"]] == [TUE.isoformat()]

    def test_a_five_year_request_on_a_short_ledger_is_clamped_and_says_so(
        self, client
    ) -> None:
        """The reader asked to look back five years. The honest answer is
        everything there is, plus a flag saying the window was shortened -- never
        five years of padded zeros, because a zero portfolio value on a day the
        account did not exist is a false claim."""
        five_years_back = (MON - timedelta(days=365 * 5)).isoformat()
        body = client.get(f"/api/valuation?from={five_years_back}").json()
        assert body["requested_from"] == five_years_back
        assert body["clamped"] is True
        assert body["start"] == MON.isoformat()
        assert len(body["items"]) == 2

    def test_refuses_a_window_that_ends_before_it_starts(self, client) -> None:
        assert client.get(f"/api/valuation?from={TUE}&to={MON}").status_code == 422

    def test_refuses_a_date_it_cannot_read(self, client) -> None:
        assert client.get("/api/valuation?from=03-03-2025").status_code == 422

class TestPositions:
    def test_requires_a_method(self, client) -> None:
        """The cost basis comes from `lot`, and the three methods disagree about
        it. Answering without being asked would put a number under a label it did
        not earn."""
        assert client.get("/api/positions").status_code == 422

    def test_reports_the_method_it_used(self, client) -> None:
        body = client.get("/api/positions?method=HIFO").json()
        assert body["method"] == "HIFO"

    def test_reads_the_cost_basis_of_that_method(self, client) -> None:
        fifo = client.get("/api/positions?method=FIFO").json()
        hifo = client.get("/api/positions?method=HIFO").json()
        assert fifo["items"][0]["cost_basis"] == "150.00"
        assert hifo["items"][0]["cost_basis"] == "180.00"

    def test_the_share_count_is_the_same_under_both(self, client) -> None:
        fifo = client.get("/api/positions?method=FIFO").json()
        hifo = client.get("/api/positions?method=HIFO").json()
        assert [i["quantity"] for i in fifo["items"]] == [i["quantity"] for i in hifo["items"]]

    def test_reports_gross_charges_and_net_as_three_figures(self, client) -> None:
        row = client.get("/api/positions?method=FIFO").json()["items"][0]
        assert row["gross_unrealised_base"] == "50.00"
        assert row["charges_base"] == "2.00"
        assert row["unrealised_base"] == "48.00"

    def test_names_the_instrument_and_says_who_priced_it(self, client) -> None:
        row = client.get("/api/positions?method=FIFO").json()["items"][0]
        assert row["product_name"] == "Example Holdings"
        assert row["source"] == "yahoo"
        assert row["price_date"] == TUE.isoformat()

    def test_withholds_the_total_when_a_position_cannot_be_priced(self) -> None:
        engine = create_engine_and_tables("sqlite://")
        seed(engine, price_b=False)
        body = TestClient(create_app(engine=engine)).get("/api/positions?method=FIFO").json()
        assert body["total_market_value_base"] is None
        assert body["total_unrealised_base"] is None
        assert body["coverage"] == "missing"
        # Cost is ledger arithmetic and stays exact.
        assert body["total_cost_basis"] == "230.00"

    def test_an_unpriceable_row_says_missing_rather_than_zero(self) -> None:
        engine = create_engine_and_tables("sqlite://")
        seed(engine, price_b=False)
        body = TestClient(create_app(engine=engine)).get("/api/positions?method=FIFO").json()
        unpriced = [row for row in body["items"] if row["isin"] == B][0]
        assert unpriced["market_value_base"] is None
        assert unpriced["coverage"] == "missing"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest tests/integration/test_valuation_api.py -q`
Expected: FAIL — every request 404s, because neither router is registered.

- [ ] **Step 3: Add the schemas**

Append to `backend/app/api/schemas.py`:

```python
class ValuationPointOut(BaseModel):
    """One day of the net portfolio value.

    `value_base` and `holdings_base` are `None` -- not zero -- when a held
    instrument could not be priced. Sec 8.1: a total that quietly dropped a
    position looks exactly like a total that included it.
    """

    date: date
    holdings_base: Decimal | None
    cash_base: Decimal
    value_base: Decimal | None
    coverage: Coverage
    #: The share of the day's holdings value that is fresh or hand-supplied.
    #: `None` exactly when `coverage` is "missing": there is no total, so there
    #: is no denominator to take a fraction of.
    covered_pct: Decimal | None

    @field_serializer("cash_base")
    def _decimal_as_string(self, value: Decimal) -> str:
        return str(value)

    @field_serializer("holdings_base", "value_base", "covered_pct")
    def _optional_decimal_as_string(self, value: Decimal | None) -> str | None:
        return None if value is None else str(value)

class ValuationSeriesOut(Provenance):
    """The daily series.

    `method` is `None` on this envelope and that is a real answer, not a missing
    one: no lot matching was applied, and M1 proved share counts are
    method-independent. Filling it with the configured default would claim a
    computation that never ran.
    """

    items: list[ValuationPointOut]
    start: date | None
    end: date | None
    #: What the caller asked for, echoed back. With `clamped`, this is how the UI
    #: can say "you asked for five years and the account is two years old"
    #: instead of silently drawing a shorter chart.
    requested_from: date | None
    clamped: bool
    base_currency: str

class PositionOut(BaseModel):
    isin: str
    product_name: str
    #: The currency the PRICE is quoted in, which is the one the coverage story
    #: is about. It is normally the trade currency too; where a provider quotes
    #: elsewhere the symbol would not have been accepted at all.
    currency: str
    quantity: Decimal
    cost_basis: Decimal
    charges_base: Decimal
    price: Decimal | None
    price_date: date | None
    #: Which provider supplied the price. "manual" is what a reader needs to see
    #: beside a figure somebody typed.
    source: str | None
    market_value_base: Decimal | None
    gross_unrealised_base: Decimal | None
    unrealised_base: Decimal | None
    unrealised_pct: Decimal | None
    coverage: Coverage

    @field_serializer("quantity", "cost_basis", "charges_base")
    def _decimal_as_string(self, value: Decimal) -> str:
        return str(value)

    @field_serializer(
        "price", "market_value_base", "gross_unrealised_base",
        "unrealised_base", "unrealised_pct",
    )
    def _optional_decimal_as_string(self, value: Decimal | None) -> str | None:
        return None if value is None else str(value)

class PositionsOut(Provenance):
    items: list[PositionOut]
    as_of: date | None
    total_cost_basis: Decimal
    #: `None` when ANY position is unpriceable (Sec 8.1 at the aggregate level).
    total_market_value_base: Decimal | None
    total_unrealised_base: Decimal | None
    base_currency: str

    @field_serializer("total_cost_basis")
    def _decimal_as_string(self, value: Decimal) -> str:
        return str(value)

    @field_serializer("total_market_value_base", "total_unrealised_base")
    def _optional_decimal_as_string(self, value: Decimal | None) -> str | None:
        return None if value is None else str(value)
```

- [ ] **Step 4: Write the two routers**

Create `backend/app/api/routes_valuation.py`:

```python
"""The daily net portfolio value.

`from` and `to` are optional. Without them the series starts at the first day a
position existed (M2-7), which is the answer to "show me my portfolio". With
them it starts where the caller asked, clamped to the ledger's own first day --
so a reader who selects five years is answered with everything the ledger has
and told the window was shortened, rather than being handed five years of padded
zeros. A zero portfolio value on a day the account did not exist is a false
claim, not a missing one.

`method` is `None` in the envelope. See `ValuationSeriesOut`.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Engine

from app.analytics.valuation import value_series
from app.api.routes_transactions import get_engine
from app.api.schemas import ValuationPointOut, ValuationSeriesOut

router = APIRouter(prefix="/api", tags=["valuation"])

@router.get("/valuation", response_model=ValuationSeriesOut)
def get_valuation(
    engine: Engine = Depends(get_engine),
    # `from` is a Python keyword, so the parameter is aliased rather than
    # renamed: the query string is what the API's readers see.
    from_: date | None = Query(default=None, alias="from"),
    to: date | None = Query(default=None),
) -> ValuationSeriesOut:
    if from_ is not None and to is not None and from_ > to:
        raise HTTPException(
            status_code=422, detail=f"from {from_} is after to {to}"
        )

    series = value_series(engine, start=from_, end=to)
    return ValuationSeriesOut(
        items=[
            ValuationPointOut(
                date=point.on,
                holdings_base=point.holdings_base,
                cash_base=point.cash_base,
                value_base=point.value_base,
                coverage=point.coverage,  # type: ignore[arg-type]
                covered_pct=point.covered_pct,
            )
            for point in series.points
        ],
        start=series.start,
        end=series.end,
        requested_from=series.requested_from,
        clamped=series.clamped,
        base_currency=series.base_currency,
        method=None,
        coverage=series.coverage,  # type: ignore[arg-type]
    )
```

Create `backend/app/api/routes_positions.py`:

```python
"""Open holdings with market value and unrealised P&L.

`method` is required, exactly as on `/api/lots`: the cost basis comes from `lot`,
and FIFO, LIFO and HIFO produce three different ones from identical rows. The
share COUNT does not depend on it -- `position_daily` has no method column
because M1 proved it cannot -- so a row here joins one method-dependent figure to
one method-independent one, and the envelope says which method it used.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import Engine

from app.analytics.valuation import current_positions
from app.api.routes_transactions import get_engine
from app.api.schemas import LotMethod, PositionOut, PositionsOut

router = APIRouter(prefix="/api", tags=["positions"])

@router.get("/positions", response_model=PositionsOut)
def list_positions(
    method: LotMethod, engine: Engine = Depends(get_engine)
) -> PositionsOut:
    snapshot = current_positions(engine, method)
    return PositionsOut(
        items=[
            PositionOut(
                isin=item.isin,
                product_name=item.product_name,
                currency=item.currency,
                quantity=item.quantity,
                cost_basis=item.cost_basis,
                charges_base=item.charges_base,
                price=item.price,
                price_date=item.price_date,
                source=item.source,
                market_value_base=item.market_value_base,
                gross_unrealised_base=item.gross_unrealised_base,
                unrealised_base=item.unrealised_base,
                unrealised_pct=item.unrealised_pct,
                coverage=item.coverage,  # type: ignore[arg-type]
            )
            for item in snapshot.items
        ],
        as_of=snapshot.as_of,
        total_cost_basis=snapshot.total_cost_basis,
        total_market_value_base=snapshot.total_market_value_base,
        total_unrealised_base=snapshot.total_unrealised_base,
        base_currency=snapshot.base_currency,
        method=method,
        coverage=snapshot.coverage,  # type: ignore[arg-type]
    )
```

- [ ] **Step 5: Register both routers**

Modify `backend/app/main.py`:

```python
from app.api import routes_lots, routes_positions, routes_transactions, routes_valuation
```

and, after `app.include_router(routes_lots.router)`:

```python
    app.include_router(routes_valuation.router)
    app.include_router(routes_positions.router)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd backend && python -m pytest tests/integration/test_valuation_api.py -q`
Expected: PASS, 20 tests.

- [ ] **Step 7: Run the whole gate**

Expected: 507 backend passed, 35 realdata, ruff and mypy clean, 93 frontend.

- [ ] **Step 8: Commit**

```bash
git add backend/app/api/routes_valuation.py backend/app/api/routes_positions.py \
        backend/app/api/schemas.py backend/app/main.py \
        backend/tests/integration/test_valuation_api.py
git commit -m "feat(api): the valuation series and positions endpoints

The value series reports method=null because none was applied; positions require
one because their cost basis depends on it. An unpriceable day sends null, and a
window longer than the ledger comes back clamped rather than padded.

Claude-Session: https://claude.ai/code/session_01UzfynZY2Rqs9oMAtCivdSF"
```

---
### Task 11: The double-count, made structural

§7.5 requires that no call path reach both the adjusted and unadjusted close: total return computed from dividend-adjusted prices *plus* dividend income counts dividends twice. M2 makes that checkable by putting the two closes in two columns behind two modules, and this task writes the second module and the test that watches the boundary.

`total_return_series()` is not wired to an endpoint. That is deliberate: M3's instrument chart is what will consume it, and §7.5's guarantee only means something if both sides of it exist. A test asserting that valuation cannot reach a module that does not exist would pass vacuously — which is exactly the class of test M1 found nine of.

**Files:**
- Create: `backend/app/analytics/total_return.py`
- Test: `backend/tests/integration/test_no_double_count.py`
- Test: `backend/tests/unit/test_total_return.py`

**Interfaces:**
- Consumes: `PriceDaily` from `app.models.market`.
- Produces:
  - `TotalReturnPoint(on: date, close_adjusted: Decimal, index: Decimal)`
  - `total_return_series(engine, isin: str, *, start: date, end: date) -> tuple[TotalReturnPoint, ...]`

- [ ] **Step 1: Write the failing architectural test**

Create `backend/tests/integration/test_no_double_count.py`:

```python
"""No call path may reach both closes. Parent doc Sec 7.5, made checkable.

Total return computed from dividend-adjusted prices PLUS dividend income counts
dividends twice, and the resulting figure is wrong in a direction that flatters
the portfolio -- which is the worst possible direction for a number nobody will
question.

The guarantee is structural rather than conventional: two columns, two modules,
two accessors. This test watches the boundary between them by reading the
codebase's own syntax tree.

**What is exempt, and why.** The storage and fetch layers legitimately handle
both closes: `models/market.py` declares the columns, `providers/` parses them
off a response, `ingest/prices.py` writes them. Handling both once, at the
boundary, is what makes two columns possible at all. The rule Sec 7.5 states is
about the READ side -- the modules that turn a price into a number a reader
sees -- so the assertion is scoped to `analytics/` and `api/`, where it is not
vacuous.
"""

from __future__ import annotations

import ast
from pathlib import Path

APP = Path(__file__).parents[2] / "app"

UNADJUSTED = "close_unadjusted"
ADJUSTED = "close_adjusted"

#: The read side. Everything here turns a price into a figure someone reads.
READ_SIDE = ("analytics", "api")

def _identifiers(path: Path) -> set[str]:
    """Every name and attribute the module actually USES.

    An AST walk rather than a text search on purpose: a docstring explaining the
    rule mentions both column names, and a test that could be broken by writing
    down why it exists is not a test worth having.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.keyword) and node.arg:
            found.add(node.arg)
    return found

def _read_side_modules() -> list[Path]:
    return sorted(
        path
        for package in READ_SIDE
        for path in (APP / package).rglob("*.py")
        if path.name != "__init__.py"
    )

def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found

def _reachable_from(start: str) -> set[str]:
    """Every `app.*` module reachable by following imports from `start`."""
    seen: set[str] = set()
    queue = [start]
    while queue:
        module = queue.pop()
        if module in seen or not module.startswith("app."):
            continue
        seen.add(module)
        path = APP.parent / (module.replace(".", "/") + ".py")
        if not path.exists():
            continue
        queue.extend(_imports(path))
    return seen

def test_the_read_side_modules_exist_so_this_is_not_vacuous() -> None:
    """The guard M1 learned to write. A call-path assertion over an empty set of
    modules passes for the wrong reason."""
    modules = _read_side_modules()
    assert modules
    assert any(path.name == "valuation.py" for path in modules)
    assert any(path.name == "total_return.py" for path in modules)

def test_no_read_side_module_names_both_closes() -> None:
    both = [
        path.relative_to(APP).as_posix()
        for path in _read_side_modules()
        if {UNADJUSTED, ADJUSTED} <= _identifiers(path)
    ]
    assert not both, (
        "these modules reach both the adjusted and unadjusted close, which is how "
        "a dividend gets counted twice: " + ", ".join(both)
    )

def test_valuation_reads_only_the_unadjusted_close() -> None:
    names = _identifiers(APP / "analytics" / "valuation.py")
    assert UNADJUSTED in names
    assert ADJUSTED not in names

def test_total_return_reads_only_the_adjusted_close() -> None:
    names = _identifiers(APP / "analytics" / "total_return.py")
    assert ADJUSTED in names
    assert UNADJUSTED not in names

def test_the_valuation_endpoints_cannot_reach_the_total_return_module() -> None:
    """The call-path half. Two modules that each read one column would still
    double-count if one called the other."""
    for endpoint in ("app.api.routes_valuation", "app.api.routes_positions"):
        assert "app.analytics.total_return" not in _reachable_from(endpoint)

def test_the_total_return_module_does_not_reach_valuation() -> None:
    assert "app.analytics.valuation" not in _reachable_from("app.analytics.total_return")
```

- [ ] **Step 2: Write the failing unit test for the series itself**

Create `backend/tests/unit/test_total_return.py`:

```python
"""The total-return series: the only reader of `close_adjusted`.

Not wired to an endpoint yet -- M3's instrument chart is what will consume it.
It exists now because parent doc Sec 7.5's guarantee is that no call path reaches
both closes, and a test asserting that valuation cannot reach a module that does
not exist would pass for the wrong reason.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlmodel import Session

from app.analytics.total_return import total_return_series
from app.db import create_engine_and_tables
from app.models.market import PriceDaily

D = Decimal
FETCHED = datetime(2026, 9, 6, 12, 0, 0)

@pytest.fixture(name="engine")
def _engine():
    engine = create_engine_and_tables("sqlite://")
    with Session(engine) as session:
        for day, plain, adjusted in (
            (date(2025, 3, 3), "20.00", "10.00"),
            (date(2025, 3, 4), "20.00", "11.00"),
            (date(2025, 3, 5), "20.00", "12.00"),
        ):
            session.add(
                PriceDaily(
                    id=uuid4(),
                    isin="NL0000000001",
                    price_date=day,
                    close_unadjusted=D(plain),
                    close_adjusted=D(adjusted),
                    currency="EUR",
                    source="yahoo",
                    fetched_at=FETCHED,
                )
            )
        session.commit()
    return engine

def test_reads_the_adjusted_close(engine) -> None:
    """The fixture holds a flat plain close and a rising adjusted one, so a
    series reading the wrong column would be flat -- and a flat total return on
    a dividend payer is exactly the bug Sec 7.5 is about, seen from the other
    side."""
    points = total_return_series(
        engine, "NL0000000001", start=date(2025, 3, 3), end=date(2025, 3, 5)
    )
    assert [p.close_adjusted for p in points] == [D("10.00"), D("11.00"), D("12.00")]

def test_rebases_to_one_at_the_start_of_the_window(engine) -> None:
    points = total_return_series(
        engine, "NL0000000001", start=date(2025, 3, 3), end=date(2025, 3, 5)
    )
    assert points[0].index == D("1")
    assert points[-1].index == D("1.2")

def test_the_window_is_inclusive_and_excludes_nothing_else(engine) -> None:
    points = total_return_series(
        engine, "NL0000000001", start=date(2025, 3, 4), end=date(2025, 3, 4)
    )
    assert [p.on for p in points] == [date(2025, 3, 4)]

def test_an_instrument_with_no_prices_yields_an_empty_series(engine) -> None:
    assert total_return_series(
        engine, "NL0000000009", start=date(2025, 3, 3), end=date(2025, 3, 5)
    ) == ()
```

- [ ] **Step 3: Run both to verify they fail**

Run: `cd backend && python -m pytest tests/integration/test_no_double_count.py tests/unit/test_total_return.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.analytics.total_return'`, and the architectural test failing on the missing file too.

- [ ] **Step 4: Write the total-return module**

Create `backend/app/analytics/total_return.py`:

```python
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
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend && python -m pytest tests/integration/test_no_double_count.py tests/unit/test_total_return.py -q`
Expected: PASS, 10 tests.

If `test_the_valuation_endpoints_cannot_reach_the_total_return_module` fails with a missing module, Task 10 has not landed — do it first. **Do not weaken the assertion to make it pass.** A call-path test that skips the module it is supposed to watch is exactly the vacuous test this task exists to avoid; M1 found nine of those.

- [ ] **Step 6: Run the whole gate**

Expected: 517 backend passed, 35 realdata, clean.

- [ ] **Step 7: Commit**

```bash
git add backend/app/analytics/total_return.py backend/tests/unit/test_total_return.py \
        backend/tests/integration/test_no_double_count.py
git commit -m "feat(analytics): total_return_series, and the call-path test that keeps it apart

Two closes, two modules, one syntax-tree assertion that no read-side module
reaches both and no valuation endpoint can reach the total-return one. Nothing
calls it yet -- M3 will -- but a test that valuation cannot reach a module that
does not exist would pass for the wrong reason.

Claude-Session: https://claude.ai/code/session_01UzfynZY2Rqs9oMAtCivdSF"
```

---

### Task 12: The Positions screen — value chart, positions table, coverage strip

M2's visible outcome, all three surfaces on one screen per M2-8. `Positions` joins `LEDGER_BACKED` in `navigation.ts`, which removes its `MODELLED` badge automatically — and adding it there without pointing it at a real endpoint is the one way to make the UI lie about its own provenance, so the two changes belong in the same commit.

The range control is where "look back five years if you want" becomes real. `MAX` omits `from` and the server answers from the first day a position existed (M2-7); every other preset sends an explicit `from`, which the server honours back to the ledger's own first day and clamps beyond it. A clamped window says so on screen rather than silently drawing a shorter chart.

**Files:**
- Create: `frontend/src/lib/valuation.ts`
- Create: `frontend/src/lib/valuation.test.ts`
- Create: `frontend/src/components/charts/EChart.tsx`
- Create: `frontend/src/components/ui/CoverageStrip.tsx`
- Create: `frontend/src/screens/Positions.test.tsx`
- Modify: `frontend/src/api/types.ts`
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/screens/Positions.tsx` (rewritten against the live API)
- Modify: `frontend/src/navigation.ts`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/package.json` (add `echarts`)

**Interfaces:**
- Consumes: `decimalEur`, `decimal`, `decimalPercent`, `decimalIsNegative`, `shortDate` from `lib/format`; `MethodBadge`, `Notice`, `Panel`, `SegmentedControl`, `Table`/`HeadRow`/`Td`/`TableFrame` from `components/`.
- Produces:
  - `api/types.ts`: `ValuationPoint`, `ValuationSeries`, `LivePosition`, `PositionsPage`
  - `api/client.ts`: `fetchValuation(query)`, `fetchPositions(method)`
  - `lib/valuation.ts`: `RANGE_PRESETS`, `RangePreset`, `rangeStart(preset, today)`, `toPlotValue(value)`, `tallyCoverage(points)`, `CoverageTally`, `buildValueOption(points, options)`
  - `components/charts/EChart.tsx`: `EChart({ option, height })`
  - `components/ui/CoverageStrip.tsx`: `CoverageStrip({ points, clamped, requestedFrom })`

- [ ] **Step 1: Add the dependency**

```bash
cd frontend && npm install echarts@^5.5.1
```

§9.1 chose ECharts on four specifics — native candlestick, `markArea` for holding bands, `markPoint` with per-point `symbolSize`, and `dataZoom` with canvas rendering for multi-year daily series. M2 uses the last of those; M3 uses the rest. The library is confined to `components/charts/EChart.tsx` so the rest of the app stays plain React.

- [ ] **Step 2: Write the failing pure-logic test**

Create `frontend/src/lib/valuation.test.ts`:

```ts
/** The chart's shape, decided before any pixel is drawn.
 *
 *  Everything here is a pure function over the API's own strings, which is what
 *  lets the chart be tested in node rather than through a canvas that jsdom does
 *  not implement.
 *
 *  The one place a money string becomes a number is `toPlotValue`, and it is
 *  deliberately the only one. A pixel position is inherently floating point, so
 *  converting at the chart boundary is honest; converting anywhere a figure is
 *  DISPLAYED would undo the exactness the backend's Decimal storage exists for,
 *  which is why every cell in the table below goes through `decimalEur`.
 */

import { describe, expect, it } from "vitest";

import {
  RANGE_PRESETS,
  buildValueOption,
  rangeStart,
  tallyCoverage,
  toPlotValue,
} from "./valuation";
import type { ValuationPoint } from "../api/types";

function point(overrides: Partial<ValuationPoint> = {}): ValuationPoint {
  return {
    date: "2025-03-03",
    holdings_base: "300.00",
    cash_base: "-50.00",
    value_base: "250.00",
    coverage: "full",
    covered_pct: "1",
    ...overrides,
  };
}

describe("range presets", () => {
  it("offers five years and a maximum", () => {
    expect(RANGE_PRESETS).toContain("5Y");
    expect(RANGE_PRESETS).toContain("MAX");
  });

  it("asks for exactly five years back", () => {
    expect(rangeStart("5Y", new Date("2026-09-06T00:00:00Z"))).toBe("2021-09-06");
  });

  it("asks for one year back", () => {
    expect(rangeStart("1Y", new Date("2026-09-06T00:00:00Z"))).toBe("2025-09-06");
  });

  it("sends no start at all for MAX", () => {
    // The server then answers from the first day a position existed, which is
    // the M2-7 default. Computing a start here would second-guess it.
    expect(rangeStart("MAX", new Date("2026-09-06T00:00:00Z"))).toBeNull();
  });
});

describe("plotting values", () => {
  it("turns a money string into a number only for the chart", () => {
    expect(toPlotValue("250.00")).toBe(250);
  });

  it("keeps null as null so the line breaks instead of dropping to zero", () => {
    // A day that could not be fully priced has no value. Plotting it as 0 would
    // draw a cliff to the axis and read as a portfolio that lost everything.
    expect(toPlotValue(null)).toBeNull();
  });
});

describe("the value chart option", () => {
  const points = [
    point({ date: "2025-03-03" }),
    point({ date: "2025-03-04", coverage: "partial", covered_pct: "0.2" }),
    point({ date: "2025-03-05", value_base: null, holdings_base: null, coverage: "missing", covered_pct: null }),
    point({ date: "2025-03-06", coverage: "manual" }),
  ];

  it("puts one x-axis category per day", () => {
    const option = buildValueOption(points, { label: "Portfolio value" });
    expect(option.xAxis).toMatchObject({
      data: ["2025-03-03", "2025-03-04", "2025-03-05", "2025-03-06"],
    });
  });

  it("leaves a gap where a day could not be valued", () => {
    const option = buildValueOption(points, { label: "Portfolio value" });
    const line = (option.series as Array<Record<string, unknown>>)[0]!;
    expect(line.data).toEqual([250, 250, null, 250]);
    // Without this ECharts joins across the gap and the missing day disappears.
    expect(line.connectNulls).toBe(false);
  });

  it("marks every point below full coverage distinctly", () => {
    // Sec 8.1's instinct, on the chart: a stale stretch has to be visible
    // without consulting a legend, the same way MODELLED is.
    const option = buildValueOption(points, { label: "Portfolio value" });
    const marks = (option.series as Array<Record<string, unknown>>)[1]!;
    expect(marks.data).toEqual([
      [1, 250],
      [3, 250],
    ]);
  });

  it("does not mark a day it could not value at all", () => {
    // There is no y for a null value. The gap in the line is what says so.
    const option = buildValueOption(points, { label: "Portfolio value" });
    const marks = (option.series as Array<Record<string, unknown>>)[1]!;
    expect((marks.data as unknown[]).length).toBe(2);
  });

  it("survives an empty series without throwing", () => {
    const option = buildValueOption([], { label: "Portfolio value" });
    expect((option.series as unknown[]).length).toBe(2);
  });
});

describe("counting coverage", () => {
  it("counts each day once, by its own verdict", () => {
    const tally = tallyCoverage([
      point(),
      point({ coverage: "partial" }),
      point({ coverage: "partial" }),
      point({ coverage: "missing" }),
      point({ coverage: "manual" }),
    ]);
    expect(tally).toEqual({ full: 1, partial: 2, manual: 1, missing: 1, total: 5 });
  });

  it("reports zeroes rather than throwing on an empty series", () => {
    expect(tallyCoverage([])).toEqual({
      full: 0,
      partial: 0,
      manual: 0,
      missing: 0,
      total: 0,
    });
  });
});
```

- [ ] **Step 3: Write the API types and client**

Append to `frontend/src/api/types.ts`:

```ts
/** One day of the net portfolio value: holdings at market plus cash, so a debit
 *  balance reduces it. `value_base` and `holdings_base` are `null` -- never "0"
 *  -- when a held instrument could not be priced (design doc 8.1). */
export interface ValuationPoint {
  date: string;
  holdings_base: string | null;
  cash_base: string;
  value_base: string | null;
  coverage: Coverage;
  /** The share of the day's holdings value that is fresh or hand-supplied.
   *  `null` exactly when coverage is "missing": there is no total, so there is
   *  no denominator. */
  covered_pct: string | null;
}

export interface ValuationSeries extends Provenance {
  items: ValuationPoint[];
  start: string | null;
  end: string | null;
  /** Echoed back with `clamped`, so a window longer than the ledger can say it
   *  was shortened rather than silently drawing a shorter chart. */
  requested_from: string | null;
  clamped: boolean;
  base_currency: string;
}

/** Named `LivePosition` rather than `Position` because `portfolio/aggregate.ts`
 *  already exports a `PositionRow` from the modelled dataset, and a screen that
 *  imported the wrong one would compile and be wrong. */
export interface LivePosition {
  isin: string;
  product_name: string;
  currency: string;
  quantity: string;
  cost_basis: string;
  charges_base: string;
  price: string | null;
  price_date: string | null;
  /** Which provider answered. "manual" belongs beside a figure somebody typed. */
  source: string | null;
  market_value_base: string | null;
  /** Gross, charges and net kept apart all the way to the screen (design doc 6.4). */
  gross_unrealised_base: string | null;
  unrealised_base: string | null;
  unrealised_pct: string | null;
  coverage: Coverage;
}

export interface PositionsPage extends Provenance {
  items: LivePosition[];
  as_of: string | null;
  total_cost_basis: string;
  /** `null` when ANY position is unpriceable. Render "—", never a partial sum. */
  total_market_value_base: string | null;
  total_unrealised_base: string | null;
  base_currency: string;
}
```

Append to `frontend/src/api/client.ts` (and extend its import list with `LotMethodTag`, `PositionsPage`, `ValuationSeries`):

```ts
export interface ValuationQuery {
  /** ISO date. Omitted entirely for MAX, so the server answers from the first
   *  day a position existed rather than from a start this client guessed. */
  from?: string | null;
  to?: string | null;
}

export async function fetchValuation(query: ValuationQuery = {}): Promise<ValuationSeries> {
  const params = new URLSearchParams();
  if (query.from) params.set("from", query.from);
  if (query.to) params.set("to", query.to);
  const suffix = params.toString() ? `?${params}` : "";

  const response = await fetch(`${BASE}/api/valuation${suffix}`);
  if (!response.ok) {
    throw new Error(`Failed to load valuation: ${response.status} ${response.statusText}`);
  }
  return (await response.json()) as ValuationSeries;
}

export async function fetchPositions(method: LotMethodTag): Promise<PositionsPage> {
  const response = await fetch(`${BASE}/api/positions?method=${method}`);
  if (!response.ok) {
    throw new Error(`Failed to load positions: ${response.status} ${response.statusText}`);
  }
  return (await response.json()) as PositionsPage;
}
```

- [ ] **Step 4: Write the pure chart logic**

Create `frontend/src/lib/valuation.ts`:

```ts
/** Everything the value chart decides before a pixel is drawn.
 *
 *  Kept pure and separate from the ECharts wrapper for one practical reason:
 *  jsdom has no canvas, so a component test cannot render a chart -- but it can
 *  assert on the option object, and that object is where every decision worth
 *  testing lives.
 *
 *  `toPlotValue` is the only place in the app where a money string becomes a
 *  JavaScript number, and it is deliberately narrow. A pixel position is
 *  inherently floating point, so converting at the chart boundary is honest.
 *  Converting anywhere a figure is DISPLAYED would undo the exactness the
 *  backend's Decimal storage exists for -- which is why every cell of the
 *  positions table goes through `decimalEur` on the original string instead.
 */

import type { EChartsOption } from "echarts";

import type { Coverage, ValuationPoint } from "../api/types";
import { c } from "./theme";

export const RANGE_PRESETS = ["1M", "3M", "6M", "1Y", "2Y", "5Y", "MAX"] as const;
export type RangePreset = (typeof RANGE_PRESETS)[number];

const MONTHS_BACK: Record<Exclude<RangePreset, "MAX">, number> = {
  "1M": 1,
  "3M": 3,
  "6M": 6,
  "1Y": 12,
  "2Y": 24,
  "5Y": 60,
};

/** The `from` to send, or `null` to send none.
 *
 *  `MAX` sends none on purpose. The server then answers from the first day a
 *  position existed (M2-7); computing a start here would be this client
 *  second-guessing a decision the ledger already knows the answer to.
 *
 *  Every other preset sends an explicit date, which the server honours back to
 *  the ledger's own first day and clamps beyond it. That is what makes "show me
 *  five years" answerable on a two-year-old account: everything there is, plus a
 *  flag saying the window was shortened.
 */
export function rangeStart(preset: RangePreset, today: Date): string | null {
  if (preset === "MAX") return null;
  const start = new Date(
    Date.UTC(today.getUTCFullYear(), today.getUTCMonth() - MONTHS_BACK[preset], today.getUTCDate()),
  );
  return start.toISOString().slice(0, 10);
}

/** A money string to a chart coordinate. `null` stays `null` so the line breaks
 *  rather than dropping to the axis -- a zero there reads as a portfolio that
 *  lost everything, which is exactly the claim design doc 8.1 forbids. */
export function toPlotValue(value: string | null): number | null {
  return value === null ? null : Number(value);
}

export interface CoverageTally {
  full: number;
  partial: number;
  manual: number;
  missing: number;
  total: number;
}

export function tallyCoverage(points: readonly ValuationPoint[]): CoverageTally {
  const tally: CoverageTally = { full: 0, partial: 0, manual: 0, missing: 0, total: 0 };
  for (const point of points) {
    tally[point.coverage] += 1;
    tally.total += 1;
  }
  return tally;
}

const BELOW_FULL: ReadonlySet<Coverage> = new Set<Coverage>(["partial", "manual"]);

export interface ValueOptionConfig {
  label: string;
}

/** The ECharts option for the portfolio value chart.
 *
 *  Two series, not one. The line carries every day including the nulls, with
 *  `connectNulls: false` so an unpriceable day is a visible gap rather than a
 *  straight segment drawn over it. The scatter carries only the days below full
 *  coverage that still have a value, so a stale stretch is visible without
 *  consulting a legend -- the same instinct as the MODELLED badge.
 *
 *  A `missing` day appears in neither: it has no y-coordinate, and the gap in
 *  the line is the honest way to say so.
 */
export function buildValueOption(
  points: readonly ValuationPoint[],
  config: ValueOptionConfig,
): EChartsOption {
  const marks: Array<[number, number]> = [];
  points.forEach((point, index) => {
    const value = toPlotValue(point.value_base);
    if (value !== null && BELOW_FULL.has(point.coverage)) marks.push([index, value]);
  });

  return {
    animation: false,
    grid: { left: 64, right: 16, top: 16, bottom: 44 },
    xAxis: {
      type: "category",
      data: points.map((point) => point.date),
      axisLine: { lineStyle: { color: c.borderSoft } },
      axisLabel: { color: c.textFaint, fontSize: 10 },
    },
    yAxis: {
      type: "value",
      scale: true,
      axisLabel: { color: c.textFaint, fontSize: 10 },
      splitLine: { lineStyle: { color: c.borderSoft } },
    },
    // Canvas plus dataZoom is why design doc 9.1 chose this library: a
    // five-year daily series is well past where SVG rendering degrades.
    dataZoom: [{ type: "inside" }, { type: "slider", height: 18, bottom: 8 }],
    tooltip: { trigger: "axis" },
    series: [
      {
        name: config.label,
        type: "line",
        showSymbol: false,
        connectNulls: false,
        data: points.map((point) => toPlotValue(point.value_base)),
        lineStyle: { color: c.accent, width: 1.5 },
      },
      {
        name: "Below full coverage",
        type: "scatter",
        symbolSize: 5,
        data: marks,
        itemStyle: { color: c.modelled },
      },
    ],
  };
}
```

- [ ] **Step 5: Run the pure test to verify it passes**

Run: `cd frontend && npx vitest run src/lib/valuation.test.ts`
Expected: PASS, 15 tests.

- [ ] **Step 6: Write the chart wrapper and the coverage strip**

Create `frontend/src/components/charts/EChart.tsx`:

```tsx
/** The only place in the app that imports ECharts.
 *
 *  Design doc 9.1 accepted an imperative options object in exchange for native
 *  candlesticks, declarative `markArea` bands, quantity-scaled `markPoint`s and
 *  canvas rendering with `dataZoom` -- and confined the cost to one wrapper so
 *  the rest of the app stays plain React.
 *
 *  Every decision about WHAT to draw lives in `lib/valuation.ts` as a pure
 *  function. This file only mounts, resizes and disposes, which is also why
 *  component tests can mock it away: jsdom implements no canvas, and a test that
 *  needed one would be testing the library rather than the screen.
 */

import { useEffect, useRef } from "react";
import * as echarts from "echarts";
import type { EChartsOption } from "echarts";

export interface EChartProps {
  option: EChartsOption;
  height?: number;
}

export function EChart({ option, height = 320 }: EChartProps) {
  const host = useRef<HTMLDivElement | null>(null);
  const chart = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    if (!host.current) return undefined;
    chart.current = echarts.init(host.current, undefined, { renderer: "canvas" });
    const resize = () => chart.current?.resize();
    window.addEventListener("resize", resize);
    return () => {
      window.removeEventListener("resize", resize);
      chart.current?.dispose();
      chart.current = null;
    };
  }, []);

  useEffect(() => {
    // `true` replaces the option rather than merging it. Merging would keep the
    // previous series' data alive under a new one, so switching from a five-year
    // range to a one-month one would leave the old tail on screen.
    chart.current?.setOption(option, true);
  }, [option]);

  return <div ref={host} style={{ width: "100%", height }} />;
}
```

Create `frontend/src/components/ui/CoverageStrip.tsx`:

```tsx
/** How much of the chart above is actually measured.
 *
 *  Design doc 8.1 makes coverage a first-class result, and a result nobody
 *  displays is only half a guarantee. The strip is one cell per day, coloured by
 *  that day's verdict, so a stale stretch is a visible band rather than a number
 *  in a corner -- and the sentence under it says what the colours mean without
 *  requiring a legend.
 *
 *  `clamped` is here rather than on the chart because it is a statement about
 *  the question, not the answer: the reader asked for a window longer than the
 *  ledger, and the honest reply is everything there is plus a note.
 */

import type { ValuationPoint } from "../../api/types";
import { tallyCoverage } from "../../lib/valuation";
import { c, mono } from "../../lib/theme";
import { shortDate } from "../../lib/format";

const TONE: Record<string, string> = {
  full: c.accent,
  partial: c.modelled,
  manual: c.modelled,
  missing: c.negative,
};

export interface CoverageStripProps {
  points: readonly ValuationPoint[];
  clamped: boolean;
  requestedFrom: string | null;
  start: string | null;
}

export function CoverageStrip({ points, clamped, requestedFrom, start }: CoverageStripProps) {
  const tally = tallyCoverage(points);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <div
        style={{ display: "flex", gap: 1, height: 8, width: "100%" }}
        role="img"
        aria-label={`Coverage across ${tally.total} days`}
      >
        {points.map((point) => (
          <span
            key={point.date}
            title={`${point.date}: ${point.coverage}`}
            style={{ flex: "1 1 0", background: TONE[point.coverage] ?? c.borderSoft }}
          />
        ))}
      </div>
      <div style={{ fontFamily: mono, fontSize: 10, color: c.textFaint }}>
        {tally.full} of {tally.total} days fully priced · {tally.partial} stale ·{" "}
        {tally.manual} hand-supplied · {tally.missing} unpriceable
      </div>
      {clamped && requestedFrom && start && (
        <div style={{ fontSize: 11, color: c.modelled }}>
          Asked for {shortDate(requestedFrom)}; the ledger begins {shortDate(start)}. Showing
          everything there is rather than padding the difference.
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 7: Write the failing component test**

Create `frontend/src/screens/Positions.test.tsx`:

```tsx
/**
 * @vitest-environment jsdom
 */

/** What the Positions screen puts on screen, now that it reads the ledger.
 *
 *  The chart wrapper is mocked: jsdom implements no canvas, and every decision
 *  worth testing about the chart is already covered as a pure function in
 *  `lib/valuation.test.ts`. What is left here is the part only a rendered screen
 *  can be wrong about -- whether an unpriceable position says "no data" or
 *  quietly reads zero, and whether a total that cannot be computed is withheld.
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fetchPositions, fetchValuation } from "../api/client";
import type { LivePosition, PositionsPage, ValuationPoint, ValuationSeries } from "../api/types";
import { Positions } from "./Positions";

vi.mock("../api/client", () => ({
  fetchValuation: vi.fn(),
  fetchPositions: vi.fn(),
}));

vi.mock("../components/charts/EChart", () => ({
  EChart: ({ option }: { option: unknown }) => (
    <div data-testid="chart" data-series={JSON.stringify(option)} />
  ),
}));

const mockValuation = vi.mocked(fetchValuation);
const mockPositions = vi.mocked(fetchPositions);

function point(overrides: Partial<ValuationPoint> = {}): ValuationPoint {
  return {
    date: "2025-03-03",
    holdings_base: "300.00",
    cash_base: "-50.00",
    value_base: "250.00",
    coverage: "full",
    covered_pct: "1",
    ...overrides,
  };
}

function series(overrides: Partial<ValuationSeries> = {}): ValuationSeries {
  return {
    items: [point()],
    start: "2025-03-03",
    end: "2025-03-03",
    requested_from: null,
    clamped: false,
    base_currency: "EUR",
    method: null,
    coverage: "full",
    ...overrides,
  };
}

function position(overrides: Partial<LivePosition> = {}): LivePosition {
  return {
    isin: "NL0000000001",
    product_name: "Example Holdings",
    currency: "EUR",
    quantity: "10",
    cost_basis: "150.00",
    charges_base: "2.00",
    price: "20.00",
    price_date: "2025-03-03",
    source: "yahoo",
    market_value_base: "200.00",
    gross_unrealised_base: "50.00",
    unrealised_base: "48.00",
    unrealised_pct: "0.32",
    coverage: "full",
    ...overrides,
  };
}

function positionsPage(
  items: LivePosition[],
  overrides: Partial<PositionsPage> = {},
): PositionsPage {
  return {
    items,
    as_of: "2025-03-03",
    total_cost_basis: "150.00",
    total_market_value_base: "200.00",
    total_unrealised_base: "48.00",
    base_currency: "EUR",
    method: "FIFO",
    coverage: "full",
    ...overrides,
  };
}

function serve(valuation: ValuationSeries, positions: PositionsPage): void {
  mockValuation.mockResolvedValue(valuation);
  mockPositions.mockResolvedValue(positions);
}

beforeEach(() => {
  mockValuation.mockReset();
  mockPositions.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("the positions table", () => {
  it("shows market value and unrealised P&L", async () => {
    serve(series(), positionsPage([position()]));
    render(<Positions method="FIFO" onOpenInstrument={() => {}} />);

    const row = (await screen.findByText("Example Holdings")).closest("tr");
    const text = (row as HTMLElement).textContent ?? "";
    expect(text).toContain("€ 200,00");
    expect(text).toContain("€ 48,00");
  });

  it("keeps gross, charges and net as three separate figures", async () => {
    // Design doc 6.4. Rolling the commission into one number would hide what
    // the broker charged, which is one of the three answers a position owes.
    serve(series(), positionsPage([position()]));
    render(<Positions method="FIFO" onOpenInstrument={() => {}} />);

    const row = (await screen.findByText("Example Holdings")).closest("tr");
    const text = (row as HTMLElement).textContent ?? "";
    expect(text).toContain("€ 50,00");
    expect(text).toContain("€ 2,00");
  });

  it("says no data rather than € 0 for a position it cannot price", async () => {
    // Design doc 8.1. A zero market value is a claim, and here it is a false one.
    serve(
      series(),
      positionsPage(
        [
          position({
            isin: "US0000000404",
            product_name: "Other Holdings",
            price: null,
            price_date: null,
            source: null,
            market_value_base: null,
            gross_unrealised_base: null,
            unrealised_base: null,
            unrealised_pct: null,
            coverage: "missing",
          }),
        ],
        { total_market_value_base: null, total_unrealised_base: null, coverage: "missing" },
      ),
    );
    render(<Positions method="FIFO" onOpenInstrument={() => {}} />);

    const row = (await screen.findByText("Other Holdings")).closest("tr");
    const text = (row as HTMLElement).textContent ?? "";
    expect(text).not.toContain("€ 0,00");
    expect(text).toContain("—");
  });

  it("withholds the total when a position cannot be priced, and says why", async () => {
    serve(
      series(),
      positionsPage([position(), position({ isin: "US0000000404", coverage: "missing", market_value_base: null, unrealised_base: null })], {
        total_market_value_base: null,
        total_unrealised_base: null,
        coverage: "missing",
      }),
    );
    render(<Positions method="FIFO" onOpenInstrument={() => {}} />);

    expect(await screen.findByText(/cannot be priced/i)).toBeInTheDocument();
  });

  it("shows where a hand-supplied price came from", async () => {
    serve(series(), positionsPage([position({ source: "manual", coverage: "manual" })]));
    render(<Positions method="FIFO" onOpenInstrument={() => {}} />);

    const row = (await screen.findByText("Example Holdings")).closest("tr");
    expect(within(row as HTMLElement).getByText(/manual/i)).toBeInTheDocument();
  });
});

describe("the value chart and its coverage", () => {
  it("renders the chart from the API's own points", async () => {
    serve(series({ items: [point(), point({ date: "2025-03-04" })] }), positionsPage([position()]));
    render(<Positions method="FIFO" onOpenInstrument={() => {}} />);

    const chart = await screen.findByTestId("chart");
    expect(chart.getAttribute("data-series")).toContain("2025-03-04");
  });

  it("counts how many days were fully priced", async () => {
    serve(
      series({
        items: [point(), point({ date: "2025-03-04", coverage: "partial", covered_pct: "0.2" })],
        coverage: "partial",
      }),
      positionsPage([position()]),
    );
    render(<Positions method="FIFO" onOpenInstrument={() => {}} />);

    expect(await screen.findByText(/1 of 2 days fully priced/i)).toBeInTheDocument();
  });

  it("says when the requested window was longer than the ledger", async () => {
    // The five-year case on a younger account. Silently drawing a shorter chart
    // would leave the reader thinking five years is all there ever was.
    serve(
      series({ requested_from: "2021-03-03", clamped: true }),
      positionsPage([position()]),
    );
    render(<Positions method="FIFO" onOpenInstrument={() => {}} />);

    expect(await screen.findByText(/the ledger begins/i)).toBeInTheDocument();
  });
});

describe("the range control", () => {
  it("asks for five years back when the reader selects 5Y", async () => {
    const { default: userEvent } = await import("@testing-library/user-event");
    serve(series(), positionsPage([position()]));
    render(<Positions method="FIFO" onOpenInstrument={() => {}} />);
    await screen.findByText("Example Holdings");

    await userEvent.click(screen.getByRole("button", { name: "5Y" }));

    await waitFor(() =>
      expect(mockValuation).toHaveBeenCalledWith(
        expect.objectContaining({ from: expect.stringMatching(/^\d{4}-\d{2}-\d{2}$/) }),
      ),
    );
  });

  it("sends no start at all for MAX, so the server picks it", async () => {
    serve(series(), positionsPage([position()]));
    render(<Positions method="FIFO" onOpenInstrument={() => {}} />);

    await waitFor(() => expect(mockValuation).toHaveBeenCalledWith({ from: null }));
  });
});

describe("provenance", () => {
  it("reports the method the API said it used, not the one asked for", async () => {
    serve(series(), positionsPage([position()], { method: "HIFO" }));
    render(<Positions method="FIFO" onOpenInstrument={() => {}} />);

    expect(await screen.findByText("HIFO")).toBeInTheDocument();
  });

  it("asks the API for the method it was given", async () => {
    serve(series(), positionsPage([position()]));
    render(<Positions method="LIFO" onOpenInstrument={() => {}} />);

    await waitFor(() => expect(mockPositions).toHaveBeenCalledWith("LIFO"));
  });

  it("does not render a slow response under the method that replaced it", async () => {
    let release: (page: PositionsPage) => void = () => {};
    const slow = new Promise<PositionsPage>((resolve) => {
      release = resolve;
    });
    mockValuation.mockResolvedValue(series());
    mockPositions.mockImplementation((method) =>
      method === "FIFO"
        ? slow
        : Promise.resolve(positionsPage([position({ product_name: "Hifo Holdings" })], { method: "HIFO" })),
    );

    const { rerender } = render(<Positions method="FIFO" onOpenInstrument={() => {}} />);
    rerender(<Positions method="HIFO" onOpenInstrument={() => {}} />);
    expect(await screen.findByText("HIFO")).toBeInTheDocument();

    release(positionsPage([position({ product_name: "Fifo Holdings" })], { method: "FIFO" }));

    await waitFor(() => expect(screen.getByText("HIFO")).toBeInTheDocument());
    expect(screen.queryByText("Fifo Holdings")).not.toBeInTheDocument();
  });
});

describe("what the screen says when there is nothing to show", () => {
  it("distinguishes a dead API from an empty portfolio", async () => {
    mockValuation.mockRejectedValue(new Error("500 Server Error"));
    mockPositions.mockRejectedValue(new Error("500 Server Error"));
    render(<Positions method="FIFO" onOpenInstrument={() => {}} />);

    expect(await screen.findByText(/Could not reach the API/i)).toBeInTheDocument();
  });

  it("tells the reader to fetch prices when the cache is empty", async () => {
    // An empty chart and an unfetched cache look identical. One is a fact about
    // the portfolio and the other is a step nobody has run yet.
    serve(series({ items: [], start: null, end: null }), positionsPage([]));
    render(<Positions method="FIFO" onOpenInstrument={() => {}} />);

    expect(await screen.findByText(/fetch-prices/i)).toBeInTheDocument();
  });
});
```

If `@testing-library/user-event` is not installed, add it: `cd frontend && npm install -D @testing-library/user-event@^14.5.2`.

- [ ] **Step 8: Rewrite the Positions screen**

Replace `frontend/src/screens/Positions.tsx` entirely. The modelled version goes; `Dashboard`, `StockDetail`, `WhatIf`, `Dividends` and `Benchmarks` keep reading `portfolio/provider.ts` and keep their `MODELLED` badge.

```tsx
/** What I hold right now, priced. The third screen reading the live ledger.
 *
 *  Three surfaces on one screen, per M2-8: the net value chart, the coverage
 *  strip under it, and the positions table. They go live together so no screen
 *  mixes live and modelled figures.
 *
 *  Every figure here is formatted from the exact decimal string the API sent.
 *  Nothing on this screen is passed through `Number()` -- the two places a
 *  string becomes a number, `toPlotValue` for a pixel coordinate and
 *  `positionWeight` for a display ratio, both live in `lib/valuation.ts` with
 *  their reasons written down. See `api/types.ts` for why that matters.
 *
 *  `null` renders "—", never "€ 0,00" and never an empty cell. A zero is a
 *  claim and a blank is indistinguishable from a screen that forgot; design doc
 *  8.1 rules out both. The same applies to the portfolio total, which is
 *  withheld entirely rather than partially summed when any position is
 *  unpriceable.
 */

import { useEffect, useMemo, useState } from "react";

import { fetchPositions, fetchValuation } from "../api/client";
import type { LotMethodTag, PositionsPage, ValuationSeries } from "../api/types";
import { EChart } from "../components/charts/EChart";
import { SegmentedControl } from "../components/ui/Controls";
import { CoverageStrip } from "../components/ui/CoverageStrip";
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
import {
  decimal,
  decimalEur,
  decimalIsNegative,
  decimalPercent,
  shortDate,
} from "../lib/format";
import { c, mono } from "../lib/theme";
import {
  RANGE_PRESETS,
  buildValueOption,
  positionWeight,
  rangeStart,
  type RangePreset,
} from "../lib/valuation";

const COLUMNS: readonly ColumnDef[] = [
  { label: "INSTRUMENT" },
  { label: "QTY", align: "right" },
  { label: "COST BASIS", align: "right" },
  { label: "PRICE", align: "right" },
  { label: "AS OF", align: "right" },
  { label: "MARKET VALUE", align: "right" },
  // Gross, charges and net as three columns, not one. Sec 6.4: a position owes
  // three answers -- what the stock did, what the broker took, what is left.
  { label: "GROSS", align: "right" },
  { label: "CHARGES", align: "right" },
  { label: "UNREALISED", align: "right" },
  { label: "%", align: "right" },
  { label: "WEIGHT", align: "right" },
  { label: "CCY", align: "right" },
  { label: "SOURCE", align: "right" },
];

export interface PositionsProps {
  method: LotMethodTag;
  onOpenInstrument: (isin: string) => void;
}

/** Both responses, or neither. They are fetched together and rendered together,
 *  so "the chart arrived but the table did not" is unrepresentable rather than
 *  merely unlikely -- the same argument `Lots.tsx` makes about its two pages. */
interface LivePages {
  series: ValuationSeries;
  positions: PositionsPage;
}

function signColour(value: string | null): string {
  if (value == null) return c.textMuted;
  return decimalIsNegative(value) ? c.negative : c.positive;
}

export function Positions({ method, onOpenInstrument }: PositionsProps) {
  const [range, setRange] = useState<RangePreset>("MAX");
  const [pages, setPages] = useState<LivePages | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // The cancellation guard. A slow FIFO response landing after a fast HIFO one
    // would otherwise put FIFO's numbers under a HIFO badge.
    let cancelled = false;
    setLoading(true);
    setError(null);

    Promise.all([
      fetchValuation({ from: rangeStart(range, new Date()) }),
      fetchPositions(method),
    ])
      .then(([series, positions]) => {
        if (cancelled) return;
        setPages({ series, positions });
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
  }, [method, range]);

  const option = useMemo(
    () => buildValueOption(pages?.series.items ?? [], { label: "Portfolio value" }),
    [pages],
  );

  if (error) {
    return (
      <Notice tone="danger">
        Could not reach the API. {error}. Start it with{" "}
        <code style={{ fontFamily: mono }}>
          python -m uvicorn app.main:create_app --factory
        </code>
        , and check that CORS_ORIGINS in backend/.env lists this port.
      </Notice>
    );
  }

  if (loading || pages === null) {
    return <div style={{ fontSize: 12, color: c.textFaint }}>Loading…</div>;
  }

  const { series, positions } = pages;
  const total = positions.total_market_value_base;

  // An unfetched cache and an empty portfolio look identical on screen, and only
  // one of them is a fact about the portfolio.
  if (series.items.length === 0 && positions.items.length === 0) {
    return (
      <Notice tone="modelled">
        Nothing to value yet. Import an export, then run{" "}
        <code style={{ fontFamily: mono }}>python -m app.cli fetch-prices</code> and{" "}
        <code style={{ fontFamily: mono }}>python -m app.cli rebuild</code>.
      </Notice>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
        {/* The envelope's method, never the prop. If the two ever disagree the
            response is the truth and the badge has to say so. */}
        <MethodBadge method={positions.method} coverage={positions.coverage} />
        <span style={{ fontSize: 11, color: c.textFaint }}>
          {positions.items.length} open positions
          {positions.as_of ? ` · as of ${shortDate(positions.as_of)}` : ""}
        </span>
        <div style={{ marginLeft: "auto" }}>
          <SegmentedControl
            options={RANGE_PRESETS}
            value={range}
            onChange={setRange}
            label="RANGE"
            size="sm"
          />
        </div>
      </div>

      <Panel
        title="Portfolio value"
        subtitle="Net of cash — holdings at market plus the cash balance, so a debit balance reduces it"
      >
        <EChart option={option} height={320} />
        <div style={{ marginTop: 10 }}>
          <CoverageStrip
            points={series.items}
            clamped={series.clamped}
            requestedFrom={series.requested_from}
            start={series.start}
          />
        </div>
      </Panel>

      {total === null && (
        <Notice tone="danger">
          One or more positions cannot be priced, so the portfolio total is withheld
          rather than partially summed. Run{" "}
          <code style={{ fontFamily: mono }}>python -m app.cli symbols</code> to see what
          is unanswered.
        </Notice>
      )}

      <TableFrame>
        <Table minWidth={1180}>
          <HeadRow columns={COLUMNS} />
          <tbody>
            {positions.items.map((row, index) => (
              <tr
                key={row.isin}
                style={{
                  background: rowBackground(index),
                  borderBottom: `1px solid ${c.borderSoft}`,
                }}
              >
                <Td>
                  <button
                    type="button"
                    onClick={() => onOpenInstrument(row.isin)}
                    style={{
                      all: "unset",
                      cursor: "pointer",
                      display: "block",
                      color: c.text,
                      fontWeight: 500,
                    }}
                  >
                    {row.product_name || row.isin}
                  </button>
                  <span
                    style={{
                      display: "block",
                      fontFamily: mono,
                      fontSize: 9.5,
                      color: c.textFaint,
                      marginTop: 2,
                    }}
                  >
                    {row.isin}
                  </span>
                </Td>
                <Td align="right" numeric>{decimal(row.quantity, 0, 4)}</Td>
                <Td align="right" numeric color={c.textMuted}>
                  {decimalEur(row.cost_basis)}
                </Td>
                <Td align="right" numeric>{decimal(row.price, 2, 4)}</Td>
                <Td align="right" numeric color={c.textFaint}>
                  {row.price_date ? shortDate(row.price_date) : "—"}
                </Td>
                <Td align="right" numeric color={c.text}>
                  {decimalEur(row.market_value_base)}
                </Td>
                <Td
                  align="right"
                  numeric
                  color={signColour(row.gross_unrealised_base)}
                >
                  {decimalEur(row.gross_unrealised_base)}
                </Td>
                <Td align="right" numeric color={c.textMuted}>
                  {decimalEur(row.charges_base)}
                </Td>
                <Td align="right" numeric color={signColour(row.unrealised_base)}>
                  {decimalEur(row.unrealised_base)}
                </Td>
                <Td align="right" numeric color={signColour(row.unrealised_base)}>
                  {decimalPercent(row.unrealised_pct)}
                </Td>
                <Td align="right" numeric color={c.textMuted}>
                  {decimalPercent(positionWeight(row.market_value_base, total))}
                </Td>
                <Td align="right" numeric color={c.textFaint}>{row.currency}</Td>
                <Td
                  align="right"
                  numeric
                  color={row.coverage === "full" ? c.textFaint : c.modelled}
                >
                  {row.source ?? "—"}
                </Td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr style={{ background: c.panelAlt, borderTop: `1px solid ${c.borderStrong}` }}>
              <Td padding="10px 11px" style={{ fontSize: 11, fontWeight: 600, color: c.text }}>
                Total
              </Td>
              <td />
              <Td padding="10px 11px" align="right" numeric color={c.textMuted}>
                {decimalEur(positions.total_cost_basis)}
              </Td>
              <td />
              <td />
              <Td padding="10px 11px" align="right" numeric color={c.text}>
                {decimalEur(total)}
              </Td>
              <td />
              <td />
              <Td
                padding="10px 11px"
                align="right"
                numeric
                color={signColour(positions.total_unrealised_base)}
              >
                {decimalEur(positions.total_unrealised_base)}
              </Td>
              <td />
              <td />
              <td />
              <td />
            </tr>
          </tfoot>
        </Table>
      </TableFrame>
    </div>
  );
}
```

`positionWeight` belongs beside `toPlotValue` in `lib/valuation.ts`, for the same reason: it is a conversion out of exact decimal strings, and both conversions should be in one place with their justification written once. Add it there:

```ts
/** One position's share of the portfolio, as a ratio string for `decimalPercent`.
 *
 *  The second and last place a money string becomes a number. A weight is a
 *  display ratio rounded to one decimal place, so float precision cannot reach
 *  the rendered figure -- unlike a cost basis, where it would.
 *
 *  `null` when the total is `null`: a share of a total that does not exist is
 *  not a smaller number, it is not a number. That is design doc 8.1 one level
 *  below the withheld total itself.
 */
export function positionWeight(value: string | null, total: string | null): string | null {
  if (value === null || total === null) return null;
  const denominator = Number(total);
  if (!denominator) return null;
  return String(Number(value) / denominator);
}
```

and cover it in `lib/valuation.test.ts`:

```ts
describe("position weight", () => {
  it("is the position's share of the portfolio", () => {
    expect(positionWeight("250.00", "1000.00")).toBe("0.25");
  });

  it("is null when the total was withheld", () => {
    // A share of a total that does not exist is not a smaller number.
    expect(positionWeight("250.00", null)).toBeNull();
  });

  it("is null rather than infinite on a zero total", () => {
    expect(positionWeight("250.00", "0.00")).toBeNull();
  });
});
```

Add `positionWeight` to that file's import list.

- [ ] **Step 9: Take the MODELLED badge off Positions**

Modify `frontend/src/navigation.ts`:

```ts
/** The screens currently backed by a real endpoint. Everything else renders
 *  from `portfolio/provider.ts` and is badged MODELLED. */
export const LEDGER_BACKED: ReadonlySet<TabId> = new Set<TabId>(["pos", "tx", "lots"]);
```

Modify `frontend/src/App.tsx` to pass the new props:

```tsx
          {tab === "pos" && <Positions method={method} onOpenInstrument={openInstrument} />}
```

and update the honesty banner's sentence, which currently names two live screens:

```tsx
                Prices, dividends and benchmarks are computed from a modelled dataset.{" "}
                <button ... onClick={() => setTab("pos")} ...>Positions</button>,{" "}
                <button ... onClick={() => setTab("tx")} ...>Transactions</button> and{" "}
                <button ... onClick={() => setTab("lots")} ...>Lots</button>{" "}
                read the live ledger.
```

If `agg` or `instrumentIndex` becomes unused by any remaining call site, leave them — `Dashboard`, `StockDetail`, `WhatIf`, `Dividends` and `Benchmarks` all still take them.

- [ ] **Step 10: Run the frontend suite**

Run: `cd frontend && npx tsc --noEmit && npx vitest run`
Expected: PASS. 93 existing + 15 in `valuation.test.ts` + 15 in `Positions.test.tsx` = 123.

- [ ] **Step 11: Run the whole gate, and look at it in a browser**

```bash
cd backend && python -m pytest -q && python -m pytest -q -m realdata \
  && python -m ruff check . && python -m mypy app
cd ../frontend && npx tsc --noEmit && npx vitest run
```

Then, with a populated database:

```bash
cd backend && python -m uvicorn app.main:create_app --factory
cd frontend && npm run dev
```

Check by eye: the chart starts at the first day a position existed, selecting 5Y reaches further back or says the window was clamped, and the Positions tab carries no `MODELLED` badge. If Vite reports a port other than 5173, add it to `CORS_ORIGINS` in `backend/.env` — otherwise every request fails as a bare "Failed to fetch".

- [ ] **Step 12: Commit**

```bash
git add frontend/src/lib/valuation.ts frontend/src/lib/valuation.test.ts \
        frontend/src/components/charts/EChart.tsx frontend/src/components/ui/CoverageStrip.tsx \
        frontend/src/screens/Positions.tsx frontend/src/screens/Positions.test.tsx \
        frontend/src/api/types.ts frontend/src/api/client.ts \
        frontend/src/navigation.ts frontend/src/App.tsx \
        frontend/package.json frontend/package-lock.json
git commit -m "feat(ui): the Positions screen reads the ledger -- value chart, table and coverage strip

Positions joins LEDGER_BACKED in the same commit that points it at a real
endpoint, because doing one without the other is the one way to make the UI lie
about its own provenance. The range control reaches five years back; a window
longer than the ledger comes back clamped and says so.

Claude-Session: https://claude.ai/code/session_01UzfynZY2Rqs9oMAtCivdSF"
```

---
### Task 13: The real-data acceptance

M2's definition of done, measured against the owner's own export. Every expected value is derived at run time from the gitignored files — never written down, per the standing rule and `test_no_real_data_committed.py`.

The suite splits in two, and the split is about the network rather than about convenience. One half needs only the export and runs the whole pipeline in a temporary database. The other needs a populated cache, so it reads the operator's *own* local database and skips when there is nothing there — which keeps the whole opt-in suite network-free, exactly as CI is.

**Files:**
- Modify: `backend/tests/integration/realdata_subject.py` (add the price derivations)
- Create: `backend/tests/integration/test_realdata_valuation.py`
- Create: `backend/tests/integration/test_realdata_prices.py`

**Interfaces:**
- Produces, in `realdata_subject`:
  - `TradeRow` gains `price_local: Decimal | None` and `price_ccy: str`
  - `traded_isins() -> set[str]`
  - `trade_currency(isin: str) -> str`
  - `executed_prices(isin: str) -> list[tuple[date, Decimal]]`
  - `first_trade_date() -> date`
  - `local_database_url() -> str | None`

- [ ] **Step 1: Extend the subject module**

Modify `backend/tests/integration/realdata_subject.py`. Add the two column positions beside the existing ones:

```python
_DATE, _TIME, _PRODUCT, _ISIN, _QTY = 0, 1, 2, 3, 6
#: The executed price and the currency it was executed in. Sec 3.1: the currency
#: column PRECEDES its amount, and the header's blank placeholder sits on the
#: wrong side -- so these are mapped by position like everything else here.
_PRICE, _PRICE_CCY = 7, 8
_LOCAL_VALUE, _VALUE_EUR, _AUTOFX, _FEE, _TOTAL_EUR, _ORDER_ID = 9, 11, 13, 14, 15, 16
```

Add the two fields to `TradeRow` and populate them in `trades()`:

```python
    #: The executed price per share, in the trade currency. This is what the
    #: symbol discriminator compares against a provider's close -- NOT the
    #: base-currency price the lot matcher uses, because a provider quotes in
    #: its own currency and converting first would make the FX rate a third
    #: unknown in a check that already has two.
    price_local: Decimal | None
    price_ccy: str
```

```python
                    price_local=_decimal(row[_PRICE]),
                    price_ccy=row[_PRICE_CCY].strip(),
```

Then append the new derivations:

```python
def traded_isins() -> set[str]:
    """Every instrument the account ever moved shares in."""
    return {row.isin for row in trades() if row.isin and row.quantity != 0}

def trade_currency(isin: str) -> str:
    """The currency this instrument's fills were priced in.

    Raises if the export priced one instrument in two currencies -- which the
    symbol discriminator also refuses, because there is then no single trade
    currency for a candidate series to match.
    """
    found = {row.price_ccy for row in trades() if row.isin == isin and row.price_ccy}
    if len(found) != 1:
        raise AssertionError(f"{len(found)} trade currencies for one instrument")
    return found.pop()

def executed_prices(isin: str) -> list[tuple[date, Decimal]]:
    """(date, price) for every fill in this instrument, oldest first.

    The oracle the symbol check is measured against: what the account actually
    paid, on the days it paid it.
    """
    return sorted(
        (row.trade_date, row.price_local)
        for row in trades()
        if row.isin == isin and row.price_local is not None and row.quantity != 0
    )

def first_trade_date() -> date:
    return min(row.trade_date for row in trades())

def local_database_url() -> str | None:
    """The operator's own database, from `backend/.env`, if it exists.

    The price-cache tests read it rather than fetching, so the opt-in suite stays
    as network-free as CI. Returns None when there is no .env, no DATABASE_URL,
    or the file it names is absent -- all of which are ordinary states, not
    failures.
    """
    env = REPO / "backend" / ".env"
    if not env.exists():
        return None
    for line in env.read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition("=")
        if key.strip() != "DATABASE_URL":
            continue
        url = value.strip()
        prefix = "sqlite:///"
        if url.startswith(prefix):
            path = Path(url[len(prefix) :])
            candidate = path if path.is_absolute() else (REPO / "backend" / path)
            return url if candidate.exists() else None
        return url or None
    return None
```

Add `from pathlib import Path` to that module's imports if it is not already there (it is).

- [ ] **Step 2: Write the ledger-side acceptance**

Create `backend/tests/integration/test_realdata_valuation.py`:

```python
"""Opt-in: the ledger half of M2, against the owner's real export.

No network and no price cache. Everything here is derivable from the export
alone, which is why it is separated from `test_realdata_prices.py`: a failure in
this file is a bug in the pipeline, where a failure there could equally be a
provider that changed its mind.

The two numbers worth being judged on are both the broker's own. The daily share
series must reproduce every position in `Portfolio.csv` on its last day, and the
daily cash series must land on the broker's own cash line -- which is the harder
of the two, because it is the sum of 785 rows across four currencies with 256
internal transfers that look exactly like deposits and are not.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlmodel import Session, select

from app.analytics.rebuild import rebuild
from app.db import create_engine_and_tables
from app.domain.splits import derive_splits
from app.domain.symbols import CandidateSeries, TradeObservation, assess
from app.ingest.degiro.portfolio_csv import parse_portfolio_csv
from app.ingest.importer import ensure_default_account, import_degiro_export
from app.ingest.symbols import observations
from app.models.ledger import CashDaily, PositionDaily, Transaction
from tests.integration import realdata_subject as subject

EXPORT = Path(__file__).parents[3] / "degiro-export"
ANSWERS = Path(__file__).parents[3] / "config" / "corporate_actions.yaml"

pytestmark = [
    pytest.mark.realdata,
    pytest.mark.skipif(
        not ((EXPORT / "Transactions.csv").exists() and ANSWERS.exists()),
        reason="real DeGiro export or answers file not present",
    ),
]

D = Decimal
#: Sec 5.4's per-portfolio reconciliation tolerance.
AGGREGATE_TOLERANCE = D("0.50")

@pytest.fixture(scope="module")
def rebuilt() -> Engine:
    engine = create_engine_and_tables("sqlite://")
    import_degiro_export(engine, EXPORT, ensure_default_account(engine), ANSWERS)
    rebuild(engine, "FIFO")
    return engine

def _last_position_day(engine: Engine) -> date:
    with Session(engine) as session:
        rows = session.exec(select(PositionDaily)).all()
    assert rows, "the daily series is empty; there is nothing to check"
    return max(row.position_date for row in rows)

class TestTheDailyShareSeries:
    def test_reproduces_every_position_in_the_brokers_statement(self, rebuilt) -> None:
        """The M1 acceptance, carried into the daily series. The split
        instrument only reaches its share count if the corporate action was
        detected, suppressed, its ratio derived and applied -- and every other
        position has to agree too, or the split logic is right about one of them
        by luck."""
        snapshot = parse_portfolio_csv(EXPORT / "Portfolio.csv")
        assert snapshot.positions, "the broker's statement lists no positions"

        last = _last_position_day(rebuilt)
        with Session(rebuilt) as session:
            held = {
                row.isin: row.quantity
                for row in session.exec(
                    select(PositionDaily).where(PositionDaily.position_date == last)
                ).all()
            }

        for position in snapshot.positions:
            assert held.get(position.isin) == position.quantity, position.isin

    def test_holds_nothing_the_broker_does_not_report(self, rebuilt) -> None:
        """The other direction. A phantom position would inflate the chart and
        never show up in a one-way comparison."""
        snapshot = parse_portfolio_csv(EXPORT / "Portfolio.csv")
        reported = {position.isin for position in snapshot.positions}
        last = _last_position_day(rebuilt)
        with Session(rebuilt) as session:
            held = {
                row.isin
                for row in session.exec(
                    select(PositionDaily).where(PositionDaily.position_date == last)
                ).all()
            }
        assert held == reported

    def test_the_series_is_the_same_under_every_method(self, rebuilt) -> None:
        """Why `position_daily` has no method column, proved on real data."""
        def snapshot() -> list[tuple[date, str, str]]:
            with Session(rebuilt) as session:
                return sorted(
                    (row.position_date, row.isin, str(row.quantity))
                    for row in session.exec(select(PositionDaily)).all()
                )

        rebuild(rebuilt, "FIFO")
        fifo = snapshot()
        rebuild(rebuilt, "HIFO")
        assert snapshot() == fifo
        rebuild(rebuilt, "FIFO")  # leave the fixture as the module found it

class TestTheDailyCashSeries:
    def test_lands_on_the_brokers_own_cash_balance(self, rebuilt) -> None:
        """The hardest reconciliation in the project, on a daily series.

        785 account rows, four currencies, and 256 internal transfers that carry
        real signed euro amounts and plausible running balances while being
        neither deposits nor withdrawals. Classify one of them wrong and this
        misses by its amount.
        """
        expected = subject.broker_cash_balance()
        with Session(rebuilt) as session:
            rows = session.exec(select(CashDaily)).all()
        assert rows, "the cash series is empty"
        final = max(rows, key=lambda row: row.cash_date).balance_base
        assert abs(final - expected) <= AGGREGATE_TOLERANCE

    def test_starts_before_the_first_position(self, rebuilt) -> None:
        """Money sits in the account before it buys anything. A cash series that
        began at the first BUY would hide the deposits that funded it -- and
        would make an explicit five-year window start later than the ledger
        actually reaches."""
        with Session(rebuilt) as session:
            first_cash = min(row.cash_date for row in session.exec(select(CashDaily)).all())
            first_position = min(
                row.position_date for row in session.exec(select(PositionDaily)).all()
            )
        assert first_cash <= first_position

    def test_covers_every_weekday_without_a_hole(self, rebuilt) -> None:
        """M2-3: every weekday is valued. A hole would be a day the chart could
        not draw, and there is no honest way to draw one."""
        with Session(rebuilt) as session:
            days = sorted(row.cash_date for row in session.exec(select(CashDaily)).all())
        expected = [
            day
            for day in _weekdays_between(days[0], days[-1])
        ]
        assert days == expected

def _weekdays_between(start: date, end: date) -> list[date]:
    from datetime import timedelta

    days: list[date] = []
    day = start
    while day <= end:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days

class TestTheDiscriminatorOnRealTrades:
    """The check that would have caught the leveraged-ETF match, run against the
    owner's own executed prices rather than an invented series.

    The impostor is BUILT from the real trades at run time -- a series a third of
    the real price with a drift in it -- so no figure is written down and the
    fixture cannot go stale against a re-export.
    """

    def _subject_isin(self) -> str:
        for isin in sorted(subject.traded_isins()):
            if len(subject.executed_prices(isin)) >= 2:
                return isin
        pytest.skip("no instrument in the export has two priced trades")

    def test_a_series_matching_the_ledger_is_accepted(self, rebuilt) -> None:
        isin = self._subject_isin()
        with Session(rebuilt) as session:
            rows = list(session.exec(select(Transaction)).all())
        trades = observations(rows)[isin]
        splits = [s for s in derive_splits(rows) if s.isin == isin]

        # A "provider" that agrees exactly: the split-adjusted executed price.
        from app.domain.symbols import split_factor

        closes = {
            trade.trade_date: trade.price_local / split_factor(splits, trade.trade_date)
            for trade in trades
        }
        verdict = assess(
            CandidateSeries(
                symbol="RIGHT", currency=subject.trade_currency(isin), closes=closes
            ),
            trades,
            splits,
        )
        assert verdict.accepted, verdict.reason

    def test_a_leveraged_lookalike_is_rejected(self, rebuilt) -> None:
        """Same ISIN, same currency, years of bars, a third of the price and
        drifting. Every naive check passes; this one does not."""
        isin = self._subject_isin()
        with Session(rebuilt) as session:
            rows = list(session.exec(select(Transaction)).all())
        trades = observations(rows)[isin]
        splits = [s for s in derive_splits(rows) if s.isin == isin]

        from app.domain.symbols import split_factor

        closes = {
            trade.trade_date: (
                trade.price_local / split_factor(splits, trade.trade_date) / D("3")
            )
            for trade in trades
        }
        verdict = assess(
            CandidateSeries(
                symbol="WRONG2S", currency=subject.trade_currency(isin), closes=closes
            ),
            trades,
            splits,
        )
        assert not verdict.accepted

    def test_the_right_answer_needs_the_split_correction(self, rebuilt) -> None:
        """The control that makes the accept above mean something. On the
        instrument that split, comparing an unadjusted executed price against a
        split-adjusted close disagrees by the ratio."""
        isin = subject.split().isin
        with Session(rebuilt) as session:
            rows = list(session.exec(select(Transaction)).all())
        trades = observations(rows).get(isin)
        if not trades:
            pytest.skip("the split instrument has no priced trades in the export")
        splits = [s for s in derive_splits(rows) if s.isin == isin]

        from app.domain.symbols import split_factor

        closes = {
            trade.trade_date: trade.price_local / split_factor(splits, trade.trade_date)
            for trade in trades
        }
        currency = subject.trade_currency(isin)
        with_correction = assess(
            CandidateSeries("RIGHT", currency, closes), trades, splits
        )
        without_correction = assess(CandidateSeries("RIGHT", currency, closes), trades, [])

        assert with_correction.accepted
        assert not without_correction.accepted
```

- [ ] **Step 3: Write the cache-side acceptance**

Create `backend/tests/integration/test_realdata_prices.py`:

```python
"""Opt-in: the fetched half of M2, against the operator's own populated cache.

Reads the local database rather than fetching, so the opt-in suite stays as
network-free as CI. It skips when there is no database or the cache is empty,
which is the ordinary state before `fetch-prices` has been run -- and the skip
message says so, because a silently green suite that checked nothing is worse
than a red one.

To run it for real:

    cd backend
    python -m app.cli import ../degiro-export
    python -m app.cli fetch-prices
    python -m app.cli rebuild
    python -m pytest -q -m realdata
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlmodel import Session, select

from app.analytics.valuation import MISSING, current_positions, value_series
from app.db import create_engine_and_tables
from app.ingest.degiro.portfolio_csv import parse_portfolio_csv
from app.models.ledger import PositionDaily
from app.models.market import FxDaily, PriceDaily, SymbolReview
from app.providers.base import BACKFILL_YEARS
from tests.integration import realdata_subject as subject

EXPORT = Path(__file__).parents[3] / "degiro-export"
URL = subject.local_database_url()

D = Decimal

pytestmark = [
    pytest.mark.realdata,
    pytest.mark.skipif(
        not (EXPORT / "Transactions.csv").exists() or URL is None,
        reason="no real export, or no local database in backend/.env",
    ),
]

#: A listing younger than five years cannot have five years of history, and the
#: ECB publishes no rate on a holiday. Six months of slack turns "the backfill
#: ran" into a testable claim without asserting a calendar.
SLACK = timedelta(days=180)

@pytest.fixture(scope="module")
def engine() -> Engine:
    assert URL is not None
    built = create_engine_and_tables(URL)
    with Session(built) as session:
        if not session.exec(select(PriceDaily)).first():
            pytest.skip("price cache is empty; run `python -m app.cli fetch-prices` first")
        if not session.exec(select(PositionDaily)).first():
            pytest.skip("no daily series; run `python -m app.cli rebuild` first")
    return built

class TestTheQuarantineIsAnswered:
    def test_no_symbol_is_still_open(self, engine) -> None:
        """`fetch-prices` refuses while one is, so a populated cache with an open
        question means something wrote prices it should not have."""
        with Session(engine) as session:
            open_rows = session.exec(select(SymbolReview)).all()
        assert [row.isin for row in open_rows] == []

class TestTheFiveYearBackfill:
    def test_the_fx_cache_reaches_five_years_back(self, engine) -> None:
        """The cleanest proof the backfill ran to its stated depth. The ECB
        publishes continuously, so unlike a listing there is no honest reason for
        a currency pair to be short."""
        with Session(engine) as session:
            rows = session.exec(select(FxDaily)).all()
        if not rows:
            pytest.skip("the portfolio is entirely base-currency; no FX to check")

        floor = date.today() - timedelta(days=365 * BACKFILL_YEARS) + SLACK
        for pair in {(row.from_ccy, row.to_ccy) for row in rows}:
            earliest = min(
                row.rate_date
                for row in rows
                if (row.from_ccy, row.to_ccy) == pair
            )
            assert earliest <= floor, pair

    def test_the_price_cache_reaches_five_years_back_for_something(self, engine) -> None:
        """Per-instrument depth is not assertable -- a listing may be younger
        than the window. That at least one instrument reaches it is."""
        with Session(engine) as session:
            earliest = min(row.price_date for row in session.exec(select(PriceDaily)).all())
        assert earliest <= date.today() - timedelta(days=365 * BACKFILL_YEARS) + SLACK

    def test_a_five_year_window_is_answered_rather_than_refused(self, engine) -> None:
        """What the reader actually does. Asking for five years must come back
        with everything the ledger has -- clamped and flagged if the account is
        younger, never empty and never padded."""
        series = value_series(engine, start=date.today() - timedelta(days=365 * 5))
        assert series.points
        assert series.start is not None
        assert series.start >= subject.first_trade_date() - timedelta(days=7)

class TestCoverage:
    def test_every_day_a_position_was_held_can_be_priced(self, engine) -> None:
        """The acceptance for the whole provider stack. A `missing` day means an
        instrument the cache cannot reach on a day the account held it, and the
        chart has a hole in it."""
        series = value_series(engine)
        unpriceable = [point.on for point in series.points if point.coverage == MISSING]
        assert not unpriceable, (
            f"{len(unpriceable)} day(s) could not be valued, first "
            f"{unpriceable[0] if unpriceable else ''}"
        )

    def test_the_series_starts_at_the_first_day_a_position_existed(self, engine) -> None:
        with Session(engine) as session:
            first_held = min(
                row.position_date for row in session.exec(select(PositionDaily)).all()
            )
        assert value_series(engine).start == first_held

    def test_every_open_position_reports_a_market_value(self, engine) -> None:
        snapshot = current_positions(engine, "FIFO")
        assert snapshot.items, "no open positions to value"
        assert snapshot.total_market_value_base is not None

class TestAgainstTheBrokersOwnValuation:
    def test_the_total_is_the_right_order_of_magnitude(self, engine) -> None:
        """Deliberately loose, and worth having anyway.

        `Portfolio.csv` states the broker's own market value per position, but as
        of the export date -- while the cache reaches to whenever prices were last
        fetched. Those are different days, so the honest tolerance is wide.

        What a wide tolerance still catches is the failure this whole milestone
        is built around: a position priced from a leveraged or inverse product is
        wrong by a factor of tens, not by a market move. A 15% band separates
        "prices moved" from "we priced the wrong instrument".
        """
        snapshot = parse_portfolio_csv(EXPORT / "Portfolio.csv")
        broker_total = sum((p.value_base for p in snapshot.positions), D("0"))
        if broker_total == 0:
            pytest.skip("the broker's statement values nothing")

        ours = current_positions(engine, "FIFO").total_market_value_base
        assert ours is not None
        assert abs(ours - broker_total) / broker_total < D("0.15")

    def test_no_single_position_is_out_by_an_order_of_magnitude(self, engine) -> None:
        """The per-instrument version, which is what actually catches an
        impostor: one wrong symbol among many can hide inside a portfolio total."""
        snapshot = parse_portfolio_csv(EXPORT / "Portfolio.csv")
        ours = {
            item.isin: item.market_value_base
            for item in current_positions(engine, "FIFO").items
        }
        for position in snapshot.positions:
            mine = ours.get(position.isin)
            assert mine is not None, position.isin
            if position.value_base == 0:
                continue
            assert abs(mine - position.value_base) / abs(position.value_base) < D("0.30"), (
                position.isin
            )
```

- [ ] **Step 4: Run the opt-in suite**

```bash
cd backend
python -m app.cli import ../degiro-export
python -m app.cli fetch-prices
python -m app.cli rebuild
python -m pytest -q -m realdata
```

Expected: the ledger-side tests pass; the cache-side tests pass once `fetch-prices` has completed. **If `fetch-prices` refuses, answer the quarantine** in `config/instrument_symbols.yaml` using the block it printed — that is the flow working, not a bug. A correct ticker sits near 1.00 on every measured ratio.

If a cache-side test skips, read the skip reason before moving on. A silently green suite that checked nothing is worse than a red one.

- [ ] **Step 5: Run the leak scanner explicitly**

Run: `cd backend && python -m pytest tests/integration/test_no_real_data_committed.py -q -m realdata`
Expected: PASS. This scans **every tracked file**, including this plan document and every fixture added in M2. If it fails, the fix is to remove the leaked value — never to relax the scanner.

- [ ] **Step 6: Run the whole gate**

```bash
cd backend && python -m pytest -q && python -m pytest -q -m realdata \
  && python -m ruff check . && python -m mypy app
cd ../frontend && npx tsc --noEmit && npx vitest run
```
Expected: everything green.

- [ ] **Step 7: Commit**

```bash
git add backend/tests/integration/realdata_subject.py \
        backend/tests/integration/test_realdata_valuation.py \
        backend/tests/integration/test_realdata_prices.py
git commit -m "test(realdata): M2's acceptance against the owner's own export

The daily share series reproduces every position in the broker's statement and
the cash series lands on its cash line; the discriminator accepts a series built
from the real executed prices and rejects a leveraged lookalike built from the
same ones. Every expectation is derived at run time -- no figure is committed.

Claude-Session: https://claude.ai/code/session_01UzfynZY2Rqs9oMAtCivdSF"
```

---

## Definition of done for M2

Taken from the M2 spec, and checkable rather than felt.

**The pipeline**

- `python -m app.cli fetch-prices` completes, or refuses and names the instruments to answer. It never half-completes.
- The cache holds a **fixed five years**: every FX pair reaches back five years, and at least one instrument's price series does.
- A second `fetch-prices` run is incremental; `--full` refetches.
- `python -m app.cli rebuild` writes `position_daily` and `cash_daily` and leaves `price_daily` and `fx_daily` byte-identical.
- Rebuilding twice produces identical derived rows, and the share series is the same under FIFO, LIFO and HIFO.

**The numbers**

- The daily share series reproduces **every** position in the broker's own `Portfolio.csv`, on both sides of the comparison — including the one that only reaches its count through the split.
- The daily cash series lands on the broker's own cash line within €0.50, negative balance included.
- Every day a position was held can be priced: no `missing` day in the real series.
- No single position's market value differs from the broker's own by more than 30%, and the portfolio total by more than 15% — the band that separates "prices moved between two dates" from "we priced the wrong instrument".

**The guarantees**

- No read-side module names both `close_unadjusted` and `close_adjusted`, and neither valuation endpoint can reach `analytics/total_return.py` by any import path.
- The symbol discriminator, run against the real executed prices, accepts an agreeing series and rejects a leveraged lookalike built from the same trades — and the right answer needs M1's derived split ratio to be accepted at all.
- Every response carries `method` and `coverage`. `/api/valuation` reports `method: null` because none was applied; `/api/positions` reports the one it used.
- A day that cannot be fully priced sends `value_base: null` and `covered_pct: null`. Never €0, never a partial sum.
- A portfolio total is withheld entirely when any position is unpriceable.

**The screen**

- The Positions screen shows a **net portfolio value chart** — holdings at market plus cash, so the debit balance reduces it — starting at the first day a position existed.
- The range control reaches **five years back**, and a window longer than the ledger comes back clamped with a line on screen saying so, never padded with zeros.
- Points below full coverage render distinctly, so a stale stretch is visible without consulting a legend.
- The positions table shows quantity, cost basis, price, market value, gross/charges/net unrealised, and per-row coverage — with `—` wherever a figure is genuinely absent.
- The coverage strip counts the days.
- `Positions` is in `LEDGER_BACKED` and carries no `MODELLED` badge.

**The rules**

- `pytest`, `pytest -m realdata`, `ruff check`, `mypy app`, `tsc --noEmit` and `vitest run` are all green.
- `test_no_real_data_committed.py` passes, having scanned this plan, every fixture and every new module.
- `git check-ignore -v config/instrument_symbols.yaml config/manual_prices.csv` matches both.
- `domain/` imports no ORM, opens no file, and calls no `datetime.now()`.
- `providers/` is the only package that makes an HTTP call.
- Coverage 80%+, with `domain/` held higher.

## What M3 adds next

The instrument chart: markers for buys and sells scaled by quantity, holding-period bands, in-market and out-of-market intervals, and a benchmark rebased at entry with excess return over the holding period. All of it needs the price series M2 introduces — and `total_return_series()`, written here and called by nothing yet, is what the benchmark comparison will read. That is why M3 comes after rather than alongside.
