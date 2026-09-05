# M0a — Ledger Foundation and DeGiro Trade Import — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Import DeGiro `Transactions.csv` into an append-only ledger, idempotently and reversibly, and browse every row with its original source data in a browser.

**Architecture:** A pure `domain/` layer (Decimal money and FX value objects, no I/O) sits under a positional CSV parser that produces immutable `NormalisedRow` objects. An importer assigns deterministic dedupe keys, writes an `import_batch`, and inserts only unseen rows. FastAPI exposes the ledger; a small React table renders it. Nothing is mutable, nothing is recomputed at read time.

**Tech Stack:** Python 3.12+, FastAPI, SQLModel/SQLAlchemy, Pydantic Settings, Typer, pytest, React + Vite + TypeScript.

**Spec:** `docs/superpowers/specs/2026-09-05-portfolio-tracker-design.md`

## Global Constraints

- **The ledger is append-only and immutable.** Corrections happen by adding rows or re-importing, never by editing a row in place. Nothing is deleted except by undoing a whole batch.
- **Money is `Decimal`, never `float`.** Anywhere. Including in tests.
- **No SQLite-only features.** Schema must port to Postgres/D1. Explicit types, declared foreign keys, UUID primary keys.
- **`net_base` is broker truth.** Store DeGiro's `Total EUR` verbatim; never recompute it. Reconciliation tolerance: ±€0.02 per row, ±€0.50 per portfolio aggregate.
- **FX rate direction:** DeGiro's `Exchange rate` is *units of local currency per 1 EUR*. Convert by **dividing**. Blank for EUR rows.
- **CSV columns are mapped by position, never by header name.** The header is misaligned against the data in all three DeGiro files.
- **Real broker exports are gitignored.** Tests run against `backend/tests/golden/`. A `realdata` pytest marker gates the local-only suite.
- **Type hints on every function.** Files 200–400 lines typical, 800 maximum.
- **Do not implement:** corporate-action detection, `Account.csv` parsing, lot matching, prices, or reconciliation. Those are M0b and M1.

---

### Task 1: Project scaffolding, settings, and test harness

**Files:**
- Create: `backend/pyproject.toml`
- Create: `backend/app/__init__.py`
- Create: `backend/app/settings.py`
- Create: `.env.example`
- Test: `backend/tests/unit/test_settings.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Settings` (pydantic-settings class) with fields `database_url: str`, `base_currency: str = "EUR"`, `lot_method: str = "FIFO"`; and `get_settings() -> Settings` (cached).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_settings.py
import pytest
from app.settings import Settings


def test_settings_reads_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./test.sqlite")
    s = Settings()
    assert s.database_url == "sqlite:///./test.sqlite"
    assert s.base_currency == "EUR"
    assert s.lot_method == "FIFO"


def test_settings_fails_loudly_when_database_url_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValueError):
        Settings(_env_file=None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/unit/test_settings.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app'`

- [ ] **Step 3: Write the scaffolding and implementation**

```toml
# backend/pyproject.toml
[project]
name = "portfolio-tracker"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.32",
    "sqlmodel>=0.0.22",
    "pydantic-settings>=2.6",
    "typer>=0.15",
    "pandas>=2.2",
]

[project.optional-dependencies]
dev = ["pytest>=8.3", "httpx>=0.28", "ruff>=0.8", "mypy>=1.13"]

[tool.pytest.ini_options]
pythonpath = ["."]
testpaths = ["tests"]
markers = [
    "realdata: runs against the owner's real DeGiro exports; skipped unless they exist",
]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
# Pinned explicitly: ruff's default rule set varies by version, and an unpinned
# gate that changes under you is worse than no gate. E/F = pycodestyle+pyflakes,
# I = import order, B = bugbear.
# FURB is deliberately absent: FURB157 rewrites Decimal("1") to Decimal(1), and in
# a codebase whose first rule is "money is Decimal, never float", constructing from
# strings is the habit worth keeping.
select = ["E", "F", "I", "B"]

[tool.mypy]
python_version = "3.12"
strict = true
```

```python
# backend/app/settings.py
"""Application settings. Required secrets are validated at startup, not at use."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-backed configuration.

    `database_url` has no default on purpose: a missing database URL must fail at
    startup with a clear message rather than silently defaulting to a scratch file
    that quietly accumulates a second, wrong ledger.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    base_currency: str = "EUR"
    lot_method: str = "FIFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

```bash
# .env.example  (committed; .env is gitignored)
DATABASE_URL=sqlite:///./data/portfolio.sqlite
BASE_CURRENCY=EUR
LOT_METHOD=FIFO
```

Create empty `backend/app/__init__.py`, `backend/tests/__init__.py`, `backend/tests/unit/__init__.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/unit/test_settings.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add backend/pyproject.toml backend/app backend/tests .env.example
git commit -m "chore: scaffold backend package, settings, and test harness"
```

---

### Task 2: Money and FxRate value objects

**Files:**
- Create: `backend/app/domain/__init__.py`
- Create: `backend/app/domain/money.py`
- Test: `backend/tests/unit/test_money.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `Money(amount: Decimal, currency: str)` — frozen; `__add__`, `__sub__`, `__neg__` raise `CurrencyMismatch` across currencies.
  - `FxRate(from_currency: str, to_currency: str, rate: Decimal, as_of: date)` — frozen; `convert(money: Money) -> Money` **divides** by `rate`; raises `CurrencyMismatch` if `money.currency != from_currency`.
  - `CurrencyMismatch(Exception)`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_money.py
from dataclasses import FrozenInstanceError
from datetime import date
from decimal import Decimal

import pytest

from app.domain.money import CurrencyMismatch, FxRate, Money


def test_money_adds_within_one_currency() -> None:
    assert Money(Decimal("1.10"), "EUR") + Money(Decimal("2.20"), "EUR") == Money(
        Decimal("3.30"), "EUR"
    )


def test_money_refuses_to_add_across_currencies() -> None:
    with pytest.raises(CurrencyMismatch):
        Money(Decimal("1"), "EUR") + Money(Decimal("1"), "USD")


def test_fx_converts_by_dividing_degiro_style() -> None:
    """DeGiro quotes local-per-EUR, so converting divides: -1004.25 / 1.2150.

    The broker booked -826.55 for this row; exact division gives -826.54. That
    one-cent gap is the broker's own rounding, and it is precisely why `net_base`
    is stored as broker truth rather than recomputed from its components.
    """
    rate = FxRate("USD", "EUR", Decimal("1.2150"), date(2026, 7, 13))
    result = rate.convert(Money(Decimal("-1004.25"), "USD"))
    assert result.currency == "EUR"
    assert result.amount.quantize(Decimal("0.01")) == Decimal("-826.54")
    # The broker's own figure differs by exactly one cent: documented, not asserted away.
    assert abs(result.amount - Decimal("-826.55")) < Decimal("0.02")


def test_fx_refuses_wrong_direction() -> None:
    """A EUR->USD conversion through a USD->EUR rate must raise, not silently invert."""
    rate = FxRate("USD", "EUR", Decimal("1.2150"), date(2026, 7, 13))
    with pytest.raises(CurrencyMismatch):
        rate.convert(Money(Decimal("100"), "EUR"))


def test_money_is_immutable() -> None:
    """Frozen, and specifically frozen: a bare `Exception` here would also pass if
    the assignment raised NameError, so it would assert almost nothing."""
    m = Money(Decimal("1"), "EUR")
    with pytest.raises(FrozenInstanceError):
        m.amount = Decimal("2")  # type: ignore[misc]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/unit/test_money.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.domain'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/domain/money.py
"""Money and FX as value objects.

Two failure modes motivate this module:

1. Applying an FX rate in the wrong direction produces a number that is wrong but
   entirely plausible. `FxRate.convert` therefore refuses any input whose currency
   is not `from_currency`, turning a silent 35% error into an exception.
2. Floating point on money accumulates cent-level drift that is indistinguishable
   from the broker's own cent-level rounding. Everything here is `Decimal`.

DeGiro quotes its exchange rate as *units of local currency per 1 EUR*, so the
conversion divides. That is the opposite of what a field named `fx_rate_to_base`
suggests, which is exactly why the direction is encoded in the type.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


class CurrencyMismatch(Exception):
    """Raised when an operation mixes currencies that cannot be combined."""


@dataclass(frozen=True, slots=True)
class Money:
    amount: Decimal
    currency: str

    def _check(self, other: Money) -> None:
        if self.currency != other.currency:
            raise CurrencyMismatch(
                f"cannot combine {self.currency} with {other.currency}"
            )

    def __add__(self, other: Money) -> Money:
        self._check(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._check(other)
        return Money(self.amount - other.amount, self.currency)

    def __neg__(self) -> Money:
        return Money(-self.amount, self.currency)


@dataclass(frozen=True, slots=True)
class FxRate:
    """`rate` is units of `from_currency` per 1 unit of `to_currency`."""

    from_currency: str
    to_currency: str
    rate: Decimal
    as_of: date

    def convert(self, money: Money) -> Money:
        if money.currency != self.from_currency:
            raise CurrencyMismatch(
                f"rate converts {self.from_currency}->{self.to_currency}, "
                f"got {money.currency}"
            )
        return Money(money.amount / self.rate, self.to_currency)
```

Create empty `backend/app/domain/__init__.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/unit/test_money.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/domain backend/tests/unit/test_money.py
git commit -m "feat: Money and FxRate value objects with direction-safe conversion"
```

---

### Task 3: DeGiro dialect — locale parsing and positional column layout

**Files:**
- Create: `backend/app/ingest/__init__.py`
- Create: `backend/app/ingest/degiro/__init__.py`
- Create: `backend/app/ingest/degiro/dialect.py`
- Test: `backend/tests/unit/test_degiro_dialect.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `parse_decimal(raw: str) -> Decimal` — raises `ValueError` on blank.
  - `parse_optional_decimal(raw: str) -> Decimal | None` — blank becomes `None`.
  - `parse_dutch_date(raw: str) -> date`.
  - `TRANSACTIONS_HEADER: str`, `ACCOUNT_HEADER: str`, `PORTFOLIO_HEADER: str`.
  - `assert_header(actual: list[str], expected: str, filename: str) -> None` — raises `UnexpectedHeader`.
  - `class TxnCol` — integer column indices for `Transactions.csv`.
  - `UnexpectedHeader(Exception)`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_degiro_dialect.py
from datetime import date
from decimal import Decimal

import pytest

from app.ingest.degiro.dialect import (
    TRANSACTIONS_HEADER,
    TRANSACTIONS_RAW_FIELDS,
    TxnCol,
    UnexpectedHeader,
    assert_header,
    parse_decimal,
    parse_dutch_date,
    parse_optional_decimal,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("778,25", Decimal("778.25")),
        ("-1.322,44", Decimal("-1322.44")),
        ("155,6000", Decimal("155.6000")),
        ("1,2150", Decimal("1.2150")),
        ("0,00", Decimal("0.00")),
        ("10.623,75", Decimal("10623.75")),
    ],
)
def test_parses_dutch_decimals(raw: str, expected: Decimal) -> None:
    assert parse_decimal(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["778.25", "abc", "-", "(1.322,44)", "1.036.50", ".50", "1 036,50", "--1,00"],
)
def test_rejects_input_that_is_not_a_dutch_number(raw: str) -> None:
    """A silent hundredfold error is the worst outcome for a money parser:
    "778.25" must raise, not quietly become Decimal("103650")."""
    with pytest.raises(ValueError):
        parse_decimal(raw)


def test_parses_bare_integers() -> None:
    """Quantities arrive with no decimal part at all."""
    assert parse_decimal("2") == Decimal("2")
    assert parse_decimal("-5") == Decimal("-5")


def test_raw_fields_is_a_stable_17_column_map() -> None:
    """TRANSACTIONS_RAW_FIELDS exists to stop a column being silently dropped from
    raw_json. Nothing else in the suite would catch a reorder, duplicate or deletion."""
    assert len(TRANSACTIONS_RAW_FIELDS) == 17
    assert len(set(TRANSACTIONS_RAW_FIELDS)) == 17
    assert all(name.strip() for name in TRANSACTIONS_RAW_FIELDS)
    assert TRANSACTIONS_RAW_FIELDS[TxnCol.LOCAL_CCY] == "Local value currency"
    assert TRANSACTIONS_RAW_FIELDS[TxnCol.VALUE_EUR_CCY] == "Value EUR currency"
    assert TRANSACTIONS_RAW_FIELDS[TxnCol.TOTAL_EUR] == "Total EUR"
    assert TRANSACTIONS_RAW_FIELDS[TxnCol.ORDER_ID] == "Order ID"


def test_blank_decimal_is_none_not_zero() -> None:
    """A blank fee means 'the fee is on another row', not 'the fee was zero'."""
    assert parse_optional_decimal("") is None
    assert parse_optional_decimal("   ") is None
    assert parse_optional_decimal("0,00") == Decimal("0.00")


def test_parses_dutch_dates() -> None:
    assert parse_dutch_date("27-07-2026") == date(2026, 7, 27)


def test_header_guard_accepts_the_verified_header() -> None:
    assert_header(TRANSACTIONS_HEADER.split(","), TRANSACTIONS_HEADER, "Transactions.csv")


def test_header_guard_rejects_a_changed_header() -> None:
    """If DeGiro changes the export, fail loudly rather than shift every column."""
    changed = TRANSACTIONS_HEADER.replace("Order ID", "Order Reference").split(",")
    with pytest.raises(UnexpectedHeader):
        assert_header(changed, TRANSACTIONS_HEADER, "Transactions.csv")


def test_column_indices_match_the_data_not_the_header() -> None:
    """Header says index 8 is unnamed; the data puts the local currency there."""
    assert TxnCol.PRICE == 7
    assert TxnCol.LOCAL_CCY == 8
    assert TxnCol.LOCAL_VALUE == 9
    assert TxnCol.VALUE_EUR == 11
    assert TxnCol.TOTAL_EUR == 15
    assert TxnCol.ORDER_ID == 16
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/unit/test_degiro_dialect.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.ingest'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/ingest/degiro/dialect.py
"""DeGiro CSV dialect: Dutch locale plus verified positional column layouts.

The header row in every DeGiro export is misaligned against the data. A currency
column precedes its amount, but the header's blank placeholder sits on the wrong
side, so `Account.csv`'s header reads `...,FX,Change,,Balance,,Order Id` while the
data is `...,fx,change_ccy,change,balance_ccy,balance,order_id`.

Mapping by header name therefore shifts every column silently. Columns are mapped
positionally, and `assert_header` fails the import loudly if DeGiro ever changes
the export shape these indices were verified against.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal


class UnexpectedHeader(Exception):
    """The export header no longer matches the layout these indices assume."""


TRANSACTIONS_HEADER = (
    "Date,Time,Product,ISIN,Reference exchange,Venue,Quantity,Price,,"
    "Local value,,Value EUR,Exchange rate,AutoFX Fee,"
    "Transaction and/or third party fees EUR,Total EUR,Order ID"
)

ACCOUNT_HEADER = "Date,Time,Value date,Product,ISIN,Description,FX,Change,,Balance,,Order Id"

PORTFOLIO_HEADER = "Product,Symbol/ISIN,Amount,Closing,Local value,,Value in EUR"

# The header's two blank names would collide as dict keys and silently drop a column
# from `raw_json`. These are the same fields in the same order, with the unnamed
# currency columns given the names the data actually puts there.
TRANSACTIONS_RAW_FIELDS = [
    "Date",
    "Time",
    "Product",
    "ISIN",
    "Reference exchange",
    "Venue",
    "Quantity",
    "Price",
    "Local value currency",
    "Local value",
    "Value EUR currency",
    "Value EUR",
    "Exchange rate",
    "AutoFX Fee",
    "Transaction and/or third party fees EUR",
    "Total EUR",
    "Order ID",
]


class TxnCol:
    """Verified positional indices for Transactions.csv."""

    DATE = 0
    TIME = 1
    PRODUCT = 2
    ISIN = 3
    REF_EXCHANGE = 4
    VENUE = 5
    QUANTITY = 6
    PRICE = 7
    LOCAL_CCY = 8
    LOCAL_VALUE = 9
    VALUE_EUR_CCY = 10
    VALUE_EUR = 11
    FX_RATE = 12
    AUTOFX_FEE = 13
    TXN_FEE = 14
    TOTAL_EUR = 15
    ORDER_ID = 16


def assert_header(actual: list[str], expected: str, filename: str) -> None:
    joined = ",".join(actual)
    if joined != expected:
        raise UnexpectedHeader(
            f"{filename} header changed.\n  expected: {expected}\n  actual:   {joined}\n"
            "Column indices in dialect.py were verified against the expected header. "
            "Re-verify them before importing."
        )


# Optional sign; digits either ungrouped or grouped by "." in threes; optional ","
# decimal part. Both grouped ("10.623,75") and ungrouped ("778,25") forms occur in
# real exports, and quantities arrive as bare integers ("2", "-5").
_DUTCH_NUMBER = re.compile(r"^-?(?:\d+|\d{1,3}(?:\.\d{3})+)(?:,\d+)?$")


def parse_decimal(raw: str) -> Decimal:
    """Parse a Dutch-locale number: '.' groups thousands, ',' is the decimal point.

    The shape is validated before the separators are swapped. Without that check an
    English-formatted cell like "778.25" passes straight through the replacements
    and returns Decimal("103650") — a hundredfold error, silent, in a money parser.
    Failing loudly on an unexpected shape is the entire point of this function.

    One ambiguity necessarily remains: "1.036" is read as 1036, the Dutch reading.
    A file mixing English decimals with Dutch headers could still be misread there,
    which is what `assert_header` guards against upstream.
    """
    text = raw.strip()
    if not text:
        raise ValueError("cannot parse an empty string as a decimal")
    if not _DUTCH_NUMBER.match(text):
        raise ValueError(f"not a Dutch-formatted number: {raw!r}")
    return Decimal(text.replace(".", "").replace(",", "."))


def parse_optional_decimal(raw: str) -> Decimal | None:
    """Blank means absent, which is not the same as zero."""
    return parse_decimal(raw) if raw.strip() else None


def parse_dutch_date(raw: str) -> date:
    return datetime.strptime(raw.strip(), "%d-%m-%Y").date()
```

Create empty `backend/app/ingest/__init__.py` and `backend/app/ingest/degiro/__init__.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/unit/test_degiro_dialect.py -v`
Expected: 21 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/ingest backend/tests/unit/test_degiro_dialect.py
git commit -m "feat: DeGiro CSV dialect with positional column layout and header guard"
```

---

### Task 4: Database models and exact-Decimal storage

**Files:**
- Create: `backend/app/models/__init__.py`
- Create: `backend/app/models/types.py`
- Create: `backend/app/models/ledger.py`
- Create: `backend/app/db.py`
- Test: `backend/tests/unit/test_models.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `DecimalString` — SQLAlchemy `TypeDecorator` storing `Decimal` exactly.
  - `Account(id: UUID, broker: str, name: str, base_currency: str)`
  - `Instrument(id: UUID, isin: str, name: str, currency: str, instrument_type: str)`
  - `ImportBatch(id: UUID, source: str, filename: str, file_sha256: str, parser_version: str, imported_at: datetime, row_count: int, inserted_count: int)`
  - `Transaction(...)` — full field list below.
  - `create_engine_and_tables(database_url: str) -> Engine`, `get_session(engine) -> Session`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_models.py
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlmodel import Session, select

from app.db import create_engine_and_tables
from app.models.ledger import Account, ImportBatch, Transaction


def test_decimal_round_trips_exactly() -> None:
    """0.1 + 0.2 style drift must be impossible; SQLite must not see a float."""
    engine = create_engine_and_tables("sqlite://")
    account_id = uuid4()
    batch_id = uuid4()
    with Session(engine) as s:
        s.add(Account(id=account_id, broker="degiro", name="Main", base_currency="EUR"))
        s.add(
            ImportBatch(
                id=batch_id,
                source="degiro",
                filename="Transactions.csv",
                file_sha256="abc",
                parser_version="1",
                imported_at=datetime.now(timezone.utc),
                row_count=1,
                inserted_count=1,
            )
        )
        s.add(
            Transaction(
                id=uuid4(),
                account_id=account_id,
                import_batch_id=batch_id,
                source="degiro",
                source_ref="ref-1",
                txn_type="BUY",
                trade_date=date(2026, 7, 13),
                isin="US0000000903",
                quantity=Decimal("2"),
                price_local=Decimal("502.1500"),
                currency_local="USD",
                fx_rate=Decimal("1.2150"),
                fee_base=Decimal("-2.00"),
                tax_base=Decimal("0.00"),
                gross_local=Decimal("-1004.25"),
                net_base=Decimal("-883.10"),
                order_ref="6e89ea79",
                is_economic=True,
                closure_reason="DECISION",
                raw_json='{"a": 1}',
            )
        )
        s.commit()

    with Session(engine) as s:
        txn = s.exec(select(Transaction)).one()
        assert isinstance(txn.price_local, Decimal)
        # Value equality would NOT catch a lost scale: Decimal("502.15") equals
        # Decimal("502.1500"). Pin the representation instead, so a stray
        # .normalize() or a float round-trip in the read path fails loudly.
        assert str(txn.price_local) == "502.1500"
        assert txn.price_local.as_tuple().exponent == -4
        assert str(txn.net_base) == "-883.10"


def test_money_column_rejects_a_float() -> None:
    """The column type is the last boundary where "money is Decimal, never float"
    can still be enforced. A float must raise, not be silently coerced."""
    from sqlalchemy.dialects import sqlite

    from app.models.types import DecimalString

    col = DecimalString()
    with pytest.raises(TypeError):
        col.process_bind_param(0.1, sqlite.dialect())  # type: ignore[arg-type]
    assert col.process_bind_param(Decimal("0.10"), sqlite.dialect()) == "0.10"
    assert col.process_bind_param(None, sqlite.dialect()) is None


def test_orphan_foreign_keys_are_rejected() -> None:
    """SQLite ignores declared FKs unless the pragma is set. Without it the local
    suite accepts rows Postgres rejects, so a referential bug — a bad undo ordering,
    a stale account_id — would only ever appear in production."""
    from sqlalchemy.exc import IntegrityError

    engine = create_engine_and_tables("sqlite://")
    with Session(engine) as s:
        s.add(
            Transaction(
                id=uuid4(),
                account_id=uuid4(),  # no such account
                import_batch_id=uuid4(),  # no such batch
                source="degiro",
                source_ref="orphan",
                txn_type="BUY",
                trade_date=date(2026, 1, 1),
                fee_base=Decimal("0"),
                tax_base=Decimal("0"),
                net_base=Decimal("-1"),
                is_economic=True,
                closure_reason="DECISION",
                raw_json="{}",
            )
        )
        with pytest.raises(IntegrityError):
            s.commit()


def test_source_ref_is_unique() -> None:
    """Idempotency is enforced by the database, not only by application logic."""
    engine = create_engine_and_tables("sqlite://")
    from sqlalchemy.exc import IntegrityError

    account_id = uuid4()
    batch_id = uuid4()

    def make(ref: str) -> Transaction:
        return Transaction(
            id=uuid4(),
            account_id=account_id,
            import_batch_id=batch_id,
            source="degiro",
            source_ref=ref,
            txn_type="BUY",
            trade_date=date(2026, 1, 1),
            fee_base=Decimal("0"),
            tax_base=Decimal("0"),
            net_base=Decimal("-1"),
            is_economic=True,
            closure_reason="DECISION",
            raw_json="{}",
        )

    with Session(engine) as s:
        s.add(Account(id=account_id, broker="degiro", name="M", base_currency="EUR"))
        s.add(
            ImportBatch(
                id=batch_id,
                source="degiro",
                filename="f",
                file_sha256="h",
                parser_version="1",
                imported_at=datetime.now(timezone.utc),
                row_count=0,
                inserted_count=0,
            )
        )
        s.add(make("dupe"))
        s.commit()
        s.add(make("dupe"))
        with pytest.raises(IntegrityError):
            s.commit()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/unit/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.db'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/models/types.py
"""Exact-Decimal column type.

SQLAlchemy's `Numeric` on SQLite round-trips through float, which reintroduces the
cent-level drift the Decimal discipline exists to prevent. Storing the canonical
string keeps SQLite exact, while Postgres still gets a real NUMERIC column, so the
schema stays portable without giving up exactness locally.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import Dialect, Numeric, String, TypeDecorator


class DecimalString(TypeDecorator[Decimal]):
    impl = String
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> Any:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(Numeric(28, 10))
        return dialect.type_descriptor(String(40))

    def process_bind_param(self, value: Decimal | None, dialect: Dialect) -> Any:
        if value is None:
            return None
        if not isinstance(value, Decimal):
            raise TypeError(
                f"money columns take Decimal, got {type(value).__name__}: {value!r}. "
                "This column type is the last boundary where the no-float rule can "
                "still be enforced; coercing here would defeat its whole purpose."
            )
        return value if dialect.name == "postgresql" else str(value)

    def process_result_value(self, value: Any, dialect: Dialect) -> Decimal | None:
        if value is None:
            return None
        return value if isinstance(value, Decimal) else Decimal(str(value))
```

```python
# backend/app/models/ledger.py
"""The ledger. Append-only and immutable: see the design doc, section 3."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Column, UniqueConstraint
from sqlmodel import Field, SQLModel

from app.models.types import DecimalString


class Account(SQLModel, table=True):
    __tablename__ = "account"

    id: UUID = Field(primary_key=True)
    broker: str
    name: str
    base_currency: str


class Instrument(SQLModel, table=True):
    __tablename__ = "instrument"

    id: UUID = Field(primary_key=True)
    isin: str = Field(unique=True, index=True)
    name: str
    currency: str
    instrument_type: str = "equity"


class ImportBatch(SQLModel, table=True):
    __tablename__ = "import_batch"

    id: UUID = Field(primary_key=True)
    source: str
    filename: str
    file_sha256: str
    parser_version: str
    imported_at: datetime
    row_count: int
    inserted_count: int


class Transaction(SQLModel, table=True):
    """One economic event. Never updated in place; only inserted or batch-deleted."""

    __tablename__ = "transaction"
    __table_args__ = (UniqueConstraint("source", "source_ref", name="uq_txn_source_ref"),)

    id: UUID = Field(primary_key=True)
    account_id: UUID = Field(foreign_key="account.id", index=True)
    import_batch_id: UUID = Field(foreign_key="import_batch.id", index=True)

    source: str
    source_ref: str = Field(index=True)

    txn_type: str = Field(index=True)
    trade_date: date = Field(index=True)
    settle_date: date | None = None

    isin: str | None = Field(default=None, index=True)
    product_name: str | None = None

    quantity: Decimal | None = Field(default=None, sa_column=Column(DecimalString()))
    price_local: Decimal | None = Field(default=None, sa_column=Column(DecimalString()))
    currency_local: str | None = None
    fx_rate: Decimal | None = Field(default=None, sa_column=Column(DecimalString()))

    fee_base: Decimal = Field(sa_column=Column(DecimalString(), nullable=False))
    tax_base: Decimal = Field(sa_column=Column(DecimalString(), nullable=False))
    gross_local: Decimal | None = Field(default=None, sa_column=Column(DecimalString()))
    net_base: Decimal = Field(sa_column=Column(DecimalString(), nullable=False))

    order_ref: str | None = Field(default=None, index=True)
    is_economic: bool = True
    closure_reason: str = "DECISION"

    raw_json: str
    note: str | None = None
```

**Two deliberate deviations from design doc §5.2, recorded here so a reviewer does not
read them as omissions:**

- `source_file_hash` lives on `import_batch`, not on `transaction`. Every transaction
  already carries `import_batch_id`, so the file hash is reachable without duplicating
  it on 900 rows. The spec's requirement — tying a row to the exact file that produced
  it — is satisfied through the join.
- `order_group_id` is **not** added yet. It only has meaning once fees are attributed at
  order level, which is M1. Adding an unpopulated column now would invite something to
  depend on it while it is still null.

```python
# backend/app/db.py
"""Engine and schema creation."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Engine, event
from sqlmodel import Session, SQLModel, create_engine

import app.models.ledger  # noqa: F401  - registers tables on SQLModel.metadata


def create_engine_and_tables(database_url: str) -> Engine:
    engine = create_engine(database_url, echo=False)
    if engine.dialect.name == "sqlite":
        _enforce_sqlite_foreign_keys(engine)
    SQLModel.metadata.create_all(engine)
    return engine


def get_session(engine: Engine) -> Session:
    return Session(engine)


def _enforce_sqlite_foreign_keys(engine: Engine) -> None:
    """SQLite ignores declared foreign keys unless explicitly asked not to.

    Without this the local database accepts rows Postgres would reject — an orphan
    transaction pointing at a deleted import_batch, say — so the suite would be
    validating weaker semantics than the deployment target, and a referential bug
    would surface only in production. The schema is portable; the enforcement has
    to be too.
    """

    @event.listens_for(engine, "connect")
    def _set_pragma(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
```

Create empty `backend/app/models/__init__.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/unit/test_models.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/models backend/app/db.py backend/tests/unit/test_models.py
git commit -m "feat: ledger schema with exact-Decimal storage and unique source_ref"
```

---

### Task 5: Deterministic dedupe key

**Files:**
- Create: `backend/app/ingest/source_ref.py`
- Test: `backend/tests/unit/test_source_ref.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `RefInput` — frozen dataclass: `order_ref: str`, `trade_datetime: str`, `isin: str`, `quantity: str`, `price: str`.
  - `assign_source_refs(inputs: Sequence[RefInput]) -> list[str]` — returns refs positionally aligned to `inputs`, stable under row reordering, distinct for byte-identical rows.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_source_ref.py
import random

from app.ingest.source_ref import RefInput, assign_source_refs


def _row(order: str, qty: str, price: str, when: str = "2026-01-10T14:20") -> RefInput:
    return RefInput(
        order_ref=order, trade_datetime=when, isin="NL0000000002", quantity=qty, price=price
    )


def test_identical_rows_get_distinct_refs() -> None:
    """Two byte-identical partial fills are two real trades, not one."""
    rows = [_row("C1", "-5", "12.34"), _row("C1", "-5", "12.34")]
    refs = assign_source_refs(rows)
    assert len(set(refs)) == 2


def test_refs_are_stable_across_reimport() -> None:
    rows = [_row("C1", "-5", "12.34"), _row("C1", "-5", "12.34"), _row("A1", "10", "1.00")]
    assert assign_source_refs(rows) == assign_source_refs(list(rows))


def test_refs_are_stable_when_the_export_reorders_rows() -> None:
    """A re-export with rows in a different order must not create new transactions."""
    rows = [
        _row("C1", "-5", "12.34"),
        _row("C1", "-5", "12.34"),
        _row("A1", "10", "1.00"),
        _row("B1", "-3", "30.00"),
    ]
    baseline = set(assign_source_refs(rows))
    for seed in range(10):
        shuffled = list(rows)
        random.Random(seed).shuffle(shuffled)
        assert set(assign_source_refs(shuffled)) == baseline


def test_delimiter_in_a_field_cannot_forge_another_rows_ref() -> None:
    """A plain "|".join is not injective: ("X|Y", "T", ...) and ("X", "Y|T", ...)
    would produce the same payload and so the same ref, silently merging two distinct
    trades. Fields are raw CSV cell values; nothing guarantees they are delimiter-free."""
    a = RefInput(order_ref="X|Y", trade_datetime="T", isin="I", quantity="1", price="1")
    b = RefInput(order_ref="X", trade_datetime="Y|T", isin="I", quantity="1", price="1")
    refs = assign_source_refs([a, b])
    assert refs[0] != refs[1]


def test_ordinals_extend_past_a_pair() -> None:
    """Ordinal assignment must keep working for a duplicate group larger than two."""
    row = RefInput(
        order_ref="O", trade_datetime="T", isin="I", quantity="-5", price="1,00"
    )
    assert len(set(assign_source_refs([row] * 5))) == 5


def test_different_prices_give_different_refs() -> None:
    rows = [_row("C1", "-5", "145.3000"), _row("C1", "-5", "145.3050")]
    assert len(set(assign_source_refs(rows))) == 2


def test_blank_order_ref_still_yields_a_ref() -> None:
    rows = [_row("", "100", "1.00"), _row("", "-10", "10.00")]
    refs = assign_source_refs(rows)
    assert all(refs) and len(set(refs)) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/unit/test_source_ref.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.ingest.source_ref'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/ingest/source_ref.py
"""Deterministic dedupe keys.

Two constraints pull against each other:

* Re-importing the same file must change nothing, even if DeGiro re-exports the
  rows in a different order.
* Two partial fills that are byte-identical are two real trades and must survive
  as two rows.

The resolution is an ordinal assigned *within* each group of otherwise-identical
rows, after sorting the whole file by the identity fields. Because the ordinal is
derived from a sorted grouping rather than from file position, shuffling the input
produces the same set of refs, while duplicates still get 0, 1, 2, ...

`order_ref` alone is not a key: 105 distinct order ids cover 112 rows in the real
export, because partial fills share one id and synthetic rows have none.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import astuple, dataclass


@dataclass(frozen=True, slots=True)
class RefInput:
    order_ref: str
    trade_datetime: str
    isin: str
    quantity: str
    price: str


def assign_source_refs(inputs: Sequence[RefInput]) -> list[str]:
    order = sorted(range(len(inputs)), key=lambda i: astuple(inputs[i]))
    seen: dict[RefInput, int] = defaultdict(int)
    refs: list[str] = [""] * len(inputs)
    for i in order:
        row = inputs[i]
        ordinal = seen[row]
        seen[row] += 1
        refs[i] = hashlib.sha256(_payload(row, ordinal).encode("utf-8")).hexdigest()
    return refs


def _payload(row: RefInput, ordinal: int) -> str:
    """Length-prefix every field so the encoding is injective.

    A plain "|".join is not. RefInput(order_ref="X|Y", trade_datetime="T", ...) and
    RefInput(order_ref="X", trade_datetime="Y|T", ...) both join to "X|Y|T|...", so
    two distinct trades hash identically and one silently vanishes from the ledger.
    These are raw CSV cell values — nothing upstream guarantees they contain no
    delimiter — and a second source (SnapTrade) will feed this same key later.

    Length prefixes make the encoding unambiguous even when a field itself looks
    like one: "3:xy" encodes as "4:3:xy", which can only be read back one way.

    This format is effectively persistent schema. Changing it changes every ref, so
    an existing ledger would have to be rebuilt rather than re-imported.
    """
    return "".join(f"{len(field)}:{field}" for field in (*astuple(row), str(ordinal)))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/unit/test_source_ref.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/ingest/source_ref.py backend/tests/unit/test_source_ref.py
git commit -m "feat: deterministic source_ref stable under reordering, distinct for identical fills"
```

---

### Task 6: Golden fixture and the Transactions.csv parser

**Files:**
- Create: `backend/tests/golden/degiro_transactions_golden.csv`
- Create: `backend/app/ingest/base.py`
- Create: `backend/app/ingest/degiro/transactions_csv.py`
- Test: `backend/tests/unit/test_transactions_parser.py`

**Interfaces:**
- Consumes: dialect (Task 3), `RefInput`/`assign_source_refs` (Task 5). **Not** `Money`/`FxRate`: the parser stores the broker's raw Decimals verbatim; conversion happens in M1/M2, so importing them here would be unused indirection.
- Produces:
  - `NormalisedRow` — frozen dataclass with fields: `source`, `source_ref`, `txn_type`, `trade_date`, `settle_date`, `isin`, `product_name`, `quantity`, `price_local`, `currency_local`, `fx_rate`, `fee_base`, `tax_base`, `gross_local`, `net_base`, `order_ref`, `is_economic`, `closure_reason`, `raw`.
  - `parse_transactions_csv(path: Path) -> list[NormalisedRow]`.
  - `PARSER_VERSION: str`.

- [ ] **Step 1: Write the golden fixture and the failing test**

Create `backend/tests/golden/degiro_transactions_golden.csv` with exactly this content. Every value is invented; every *structure* is reproduced from the real export.

```csv
Date,Time,Product,ISIN,Reference exchange,Venue,Quantity,Price,,Local value,,Value EUR,Exchange rate,AutoFX Fee,Transaction and/or third party fees EUR,Total EUR,Order ID
06-01-2025,09:00,TEST EURO CORP NV,NL0000000001,EAM,XAMS,10,"10,0000",EUR,"-100,00",EUR,"-100,00",,"0,00","-2,00","-102,00",aaaa0001-0000-0000-0000-000000000001
07-01-2025,10:30,TEST DOLLAR CORP,US0000000001,NDQ,XNAS,5,"20,0000",USD,"-100,00",USD,"-90,91","1,1000","-0,23","-2,00","-93,14",aaaa0002-0000-0000-0000-000000000002
08-01-2025,11:15,TEST EURO CORP NV,NL0000000001,EAM,CDED,-3,"30,0000",EUR,"90,00",EUR,"90,00",,"0,00",,"90,00",bbbb0001-0000-0000-0000-000000000003
08-01-2025,11:15,TEST EURO CORP NV,NL0000000001,EAM,CDED,-7,"30,0000",EUR,"210,00",EUR,"210,00",,"0,00","-3,00","207,00",bbbb0001-0000-0000-0000-000000000003
09-01-2025,12:00,TEST IDENTICAL NV,NL0000000002,EAM,XAMS,10,"12,3400",EUR,"-123,40",EUR,"-123,40",,"0,00","-2,00","-125,40",cccc0001-0000-0000-0000-000000000004
10-01-2025,14:20,TEST IDENTICAL NV,NL0000000002,EAM,CDED,-5,"12,3400",EUR,"61,70",EUR,"61,70",,"0,00",,"61,70",cccc0002-0000-0000-0000-000000000005
10-01-2025,14:20,TEST IDENTICAL NV,NL0000000002,EAM,CDED,-5,"12,3400",EUR,"61,70",EUR,"61,70",,"0,00",,"61,70",cccc0002-0000-0000-0000-000000000005
13-01-2025,15:45,TEST DOLLAR CORP,US0000000001,NDQ,XNAS,-2,"50,0000",USD,"100,00",USD,"92,59","1,0800","-0,23","-2,00","90,35",aaaa0003-0000-0000-0000-000000000006
14-01-2025,09:05,TEST AUSSIE LTD,AU0000000001,ASX,ASXT,100,"0,5000",AUD,"-50,00",AUD,"-30,30","1,6500","-0,08","-2,00","-32,38",aaaa0004-0000-0000-0000-000000000007
15-01-2025,16:00,"TEST COMMA CORP, INC.",US0000000002,NSY,XNYS,1,"100,0000",USD,"-100,00",USD,"-90,91","1,1000","-0,23","-2,00","-93,14",aaaa0005-0000-0000-0000-000000000008
16-01-2025,09:00,TEST SPLIT NV,NL0000000003,EAM,XAMS,10,"10,0000",EUR,"-100,00",EUR,"-100,00",,"0,00","-2,00","-102,00",aaaa0006-0000-0000-0000-000000000009
17-01-2025,00:00,TEST SPLIT NV,NL0000000003,EAM,,100,"1,0000",EUR,"-100,00",EUR,"-100,00",,"0,00",,"-100,00",
17-01-2025,00:00,TEST SPLIT NV,NL0000000003,EAM,,-10,"10,0000",EUR,"100,00",EUR,"100,00",,"0,00",,"100,00",
```

Structures covered: a quoted product name containing a comma; partial fills sharing one order id with the commission on only one row; two byte-identical fill rows; three currencies plus blank FX for EUR; a deliberate one-cent arithmetic mismatch (row 8: −92.59 + −0.23 + −2.00 = 90.36, stated as 90.35); and a blank-order-id split pair.

```python
# backend/tests/unit/test_transactions_parser.py
from decimal import Decimal
from pathlib import Path

import pytest

from app.ingest.degiro.dialect import TRANSACTIONS_HEADER, UnexpectedHeader
from app.ingest.degiro.transactions_csv import MalformedRow, parse_transactions_csv

GOLDEN = Path(__file__).parents[1] / "golden" / "degiro_transactions_golden.csv"


@pytest.fixture(scope="module")
def rows() -> list:
    return parse_transactions_csv(GOLDEN)


def test_parses_every_data_row(rows: list) -> None:
    assert len(rows) == 13


def test_quoted_product_name_with_a_comma_survives(rows: list) -> None:
    row = next(r for r in rows if r.isin == "US0000000002")
    assert row.product_name == "TEST COMMA CORP, INC."


def test_buy_and_sell_are_derived_from_quantity_sign(rows: list) -> None:
    buy = next(r for r in rows if r.order_ref == "aaaa0001-0000-0000-0000-000000000001")
    sell = next(r for r in rows if r.order_ref == "bbbb0001-0000-0000-0000-000000000003")
    assert buy.txn_type == "BUY"
    assert sell.txn_type == "SELL"


def test_blank_fee_is_zero_not_none_on_the_row(rows: list) -> None:
    """A blank fee means the commission sits on a sibling fill; this row paid none."""
    fills = [r for r in rows if r.order_ref == "bbbb0001-0000-0000-0000-000000000003"]
    assert sorted(f.fee_base for f in fills) == [Decimal("-3.00"), Decimal("0.00")]


def test_net_base_is_broker_truth_even_when_arithmetic_disagrees(rows: list) -> None:
    """Row 8's components sum to 90.36; DeGiro says 90.35. Store 90.35."""
    row = next(r for r in rows if r.order_ref == "aaaa0003-0000-0000-0000-000000000006")
    assert row.net_base == Decimal("90.35")
    assert row.net_base != row.gross_base_components_sum


def test_eur_rows_have_no_fx_rate(rows: list) -> None:
    row = next(r for r in rows if r.isin == "NL0000000001")
    assert row.fx_rate is None
    assert row.currency_local == "EUR"


def test_foreign_currency_rows_keep_the_broker_rate_verbatim(rows: list) -> None:
    row = next(r for r in rows if r.isin == "AU0000000001")
    assert row.currency_local == "AUD"
    assert row.fx_rate == Decimal("1.6500")


def test_identical_fill_rows_become_two_distinct_transactions(rows: list) -> None:
    fills = [r for r in rows if r.order_ref == "cccc0002-0000-0000-0000-000000000005"]
    assert len(fills) == 2
    assert fills[0].source_ref != fills[1].source_ref


def test_blank_order_id_rows_are_parsed_not_dropped(rows: list) -> None:
    """The split pair is suppressed in M0b, but M0a must still ingest it."""
    split = [r for r in rows if r.isin == "NL0000000003" and not r.order_ref]
    assert len(split) == 2
    assert all(r.is_economic for r in split)


def test_raw_row_is_preserved(rows: list) -> None:
    row = rows[0]
    assert row.raw["Total EUR"] == "-102,00"


def test_raw_row_keeps_every_column_including_the_unnamed_ones(rows: list) -> None:
    """The header has two blank names. Keying the raw dict off it would collapse them
    into one entry and silently lose a column — the exact class of bug raw_json exists
    to catch. Both currency columns must survive under distinct names."""
    row = next(r for r in rows if r.isin == "AU0000000001")
    assert len(row.raw) == 17
    assert row.raw["Local value currency"] == "AUD"
    assert row.raw["Value EUR currency"] == "AUD"
    assert row.raw["Local value"] == "-50,00"
    assert row.raw["Value EUR"] == "-30,30"


def test_column_mapping_is_pinned_for_local_and_base_values(rows: list) -> None:
    """A transposed LOCAL_VALUE/VALUE_EUR index would pass every other test here: the
    broker-truth test asserts only an inequality, which survives a swap. Positional
    correctness is this parser's entire reason to exist, so pin it to real values."""
    aud = next(r for r in rows if r.isin == "AU0000000001")
    assert aud.gross_local == Decimal("-50.00")  # Local value, in AUD
    assert aud.value_base == Decimal("-30.30")  # Value EUR
    assert aud.net_base == Decimal("-32.38")  # Total EUR


def test_a_short_row_reports_its_line_number(tmp_path: Path) -> None:
    """A bare IndexError across 112 rows of 17 columns locates nothing."""
    bad = tmp_path / "short.csv"
    bad.write_text(TRANSACTIONS_HEADER + "
06-01-2025,09:00,X
", encoding="utf-8")
    with pytest.raises(MalformedRow) as exc:
        parse_transactions_csv(bad)
    assert "line 2" in str(exc.value)


def test_a_malformed_number_reports_its_line_number(tmp_path: Path) -> None:
    lines = GOLDEN.read_text(encoding="utf-8").strip().split("
")
    lines[1] = lines[1].replace('"10,0000"', '"10.0000"')  # English decimal
    bad = tmp_path / "badnum.csv"
    bad.write_text("
".join(lines) + "
", encoding="utf-8")
    with pytest.raises(MalformedRow) as exc:
        parse_transactions_csv(bad)
    assert "line 2" in str(exc.value)


def test_rejects_a_file_whose_header_changed(tmp_path: Path) -> None:
    bad = tmp_path / "bad.csv"
    bad.write_text("Date,Time,Product\n06-01-2025,09:00,X\n", encoding="utf-8")
    with pytest.raises(UnexpectedHeader):
        parse_transactions_csv(bad)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/unit/test_transactions_parser.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.ingest.degiro.transactions_csv'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/ingest/base.py
"""The normalised shape every source produces before it reaches the ledger."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class NormalisedRow:
    source: str
    source_ref: str
    txn_type: str
    trade_date: date
    net_base: Decimal
    fee_base: Decimal
    tax_base: Decimal
    raw: dict[str, str]
    settle_date: date | None = None
    isin: str | None = None
    product_name: str | None = None
    quantity: Decimal | None = None
    price_local: Decimal | None = None
    currency_local: str | None = None
    fx_rate: Decimal | None = None
    gross_local: Decimal | None = None
    value_base: Decimal | None = None
    autofx_fee_base: Decimal = Decimal("0.00")
    order_ref: str | None = None
    is_economic: bool = True
    closure_reason: str = "DECISION"

    @property
    def gross_base_components_sum(self) -> Decimal:
        """What `net_base` would be if the broker's own arithmetic were exact.

        Exposed only so reconciliation can measure the gap. Never stored: the
        design fixes `net_base` as broker truth because that is the amount that
        actually moved through the cash account.
        """
        return (self.value_base or Decimal("0")) + self.autofx_fee_base + self.fee_base
```

```python
# backend/app/ingest/degiro/transactions_csv.py
"""Parser for DeGiro's Transactions.csv (trades only).

Authoritative for trades. The same trades also appear in Account.csv as
`Koop`/`Verkoop` rows; those are dropped there in favour of these, because this
file carries the execution venue, the FX rate and the fee split.
"""

from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path

from app.ingest.base import NormalisedRow
from app.ingest.degiro.dialect import (
    TRANSACTIONS_HEADER,
    TxnCol,
    assert_header,
    parse_decimal,
    parse_dutch_date,
    parse_optional_decimal,
)
from app.ingest.source_ref import RefInput, assign_source_refs

PARSER_VERSION = "degiro-transactions-1"
SOURCE = "degiro"


class MalformedRow(Exception):
    """A row could not be parsed. Carries the file and line so it can be found.

    With 112 rows of 17 columns, an error that does not say *where* it failed turns
    a two-minute fix into a bisect.
    """


def parse_transactions_csv(path: Path) -> list[NormalisedRow]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        assert_header(header, TRANSACTIONS_HEADER, path.name)
        # Keep the physical line number: blank-row filtering makes a later enumerate()
        # disagree with the file, and a parse error must name the line you can go read.
        numbered = [
            (line_no, row)
            for line_no, row in enumerate(reader, start=2)
            if any(cell.strip() for cell in row)
        ]

    raw_rows = [row for _, row in numbered]
    ref_inputs = [
        RefInput(
            order_ref=row[TxnCol.ORDER_ID].strip(),
            trade_datetime=f"{row[TxnCol.DATE].strip()}T{row[TxnCol.TIME].strip()}",
            isin=row[TxnCol.ISIN].strip(),
            quantity=row[TxnCol.QUANTITY].strip(),
            price=row[TxnCol.PRICE].strip(),
        )
        for row in raw_rows
    ]
    refs = assign_source_refs(ref_inputs)

    parsed: list[NormalisedRow] = []
    for (line_no, row), ref in zip(numbered, refs, strict=True):
        try:
            parsed.append(_to_normalised(row, ref))
        except (ValueError, IndexError) as exc:
            raise MalformedRow(f"{path.name} line {line_no}: {exc}") from exc
    return parsed


def _to_normalised(row: list[str], source_ref: str) -> NormalisedRow:
    if len(row) != len(TRANSACTIONS_RAW_FIELDS):
        raise ValueError(
            f"expected {len(TRANSACTIONS_RAW_FIELDS)} columns, got {len(row)}"
        )
    quantity = parse_decimal(row[TxnCol.QUANTITY])
    order_ref = row[TxnCol.ORDER_ID].strip() or None

    # A blank fee cell means the commission was booked on a sibling fill of the same
    # order, so this row genuinely paid nothing. Order-level attribution happens in M1.
    fee = parse_optional_decimal(row[TxnCol.TXN_FEE]) or Decimal("0.00")
    autofx = parse_optional_decimal(row[TxnCol.AUTOFX_FEE]) or Decimal("0.00")

    return NormalisedRow(
        source=SOURCE,
        source_ref=source_ref,
        txn_type="BUY" if quantity > 0 else "SELL",
        trade_date=parse_dutch_date(row[TxnCol.DATE]),
        isin=row[TxnCol.ISIN].strip() or None,
        product_name=row[TxnCol.PRODUCT].strip() or None,
        quantity=quantity,
        price_local=parse_decimal(row[TxnCol.PRICE]),
        currency_local=row[TxnCol.LOCAL_CCY].strip() or None,
        fx_rate=parse_optional_decimal(row[TxnCol.FX_RATE]),
        gross_local=parse_optional_decimal(row[TxnCol.LOCAL_VALUE]),
        value_base=parse_optional_decimal(row[TxnCol.VALUE_EUR]),
        autofx_fee_base=autofx,
        fee_base=fee,
        tax_base=Decimal("0.00"),
        net_base=parse_decimal(row[TxnCol.TOTAL_EUR]),
        order_ref=order_ref,
        is_economic=True,
        closure_reason="DECISION",
        raw=dict(zip(TRANSACTIONS_RAW_FIELDS, row, strict=True)),
    )
```

Note the import line in this module must include `TRANSACTIONS_RAW_FIELDS`:

```python
from app.ingest.degiro.dialect import (
    TRANSACTIONS_HEADER,
    TRANSACTIONS_RAW_FIELDS,
    TxnCol,
    assert_header,
    parse_decimal,
    parse_dutch_date,
    parse_optional_decimal,
)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/unit/test_transactions_parser.py -v`
Expected: 15 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/ingest backend/tests/golden backend/tests/unit/test_transactions_parser.py
git commit -m "feat: Transactions.csv parser with golden fixture reproducing export quirks"
```

---

### Task 7: Idempotent importer with batch undo

**Files:**
- Create: `backend/app/ingest/importer.py`
- Create: `backend/app/cli.py`
- Test: `backend/tests/integration/__init__.py`
- Test: `backend/tests/integration/test_importer.py`
- Test: `backend/tests/integration/test_realdata.py`

**Interfaces:**
- Consumes: `parse_transactions_csv`, `PARSER_VERSION` (Task 6); models and `create_engine_and_tables` (Task 4).
- Produces:
  - `ImportResult(batch_id: UUID, rows_parsed: int, rows_inserted: int, rows_skipped: int)`
  - `import_transactions_file(engine: Engine, path: Path, account_id: UUID) -> ImportResult`
  - `undo_batch(engine: Engine, batch_id: UUID) -> int`
  - `ensure_default_account(engine: Engine) -> UUID`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/integration/test_importer.py
from pathlib import Path
from uuid import uuid4

import pytest
from sqlmodel import Session, select

from app.db import create_engine_and_tables
from app.ingest.degiro.transactions_csv import MalformedRow
from app.ingest.importer import ensure_default_account, import_transactions_file, undo_batch
from app.models.ledger import Account, ImportBatch, Transaction

GOLDEN = Path(__file__).parents[1] / "golden" / "degiro_transactions_golden.csv"


def _engine():
    return create_engine_and_tables("sqlite://")


def test_import_inserts_every_row() -> None:
    engine = _engine()
    account_id = ensure_default_account(engine)
    result = import_transactions_file(engine, GOLDEN, account_id)
    assert result.rows_parsed == 13
    assert result.rows_inserted == 13
    with Session(engine) as s:
        assert len(s.exec(select(Transaction)).all()) == 13


def test_reimporting_the_same_file_changes_nothing() -> None:
    """Source spec section 9.6."""
    engine = _engine()
    account_id = ensure_default_account(engine)
    import_transactions_file(engine, GOLDEN, account_id)
    second = import_transactions_file(engine, GOLDEN, account_id)
    assert second.rows_inserted == 0
    assert second.rows_skipped == 13
    with Session(engine) as s:
        assert len(s.exec(select(Transaction)).all()) == 13


def test_identical_fill_rows_both_survive_import() -> None:
    engine = _engine()
    account_id = ensure_default_account(engine)
    import_transactions_file(engine, GOLDEN, account_id)
    with Session(engine) as s:
        fills = s.exec(
            select(Transaction).where(
                Transaction.order_ref == "cccc0002-0000-0000-0000-000000000005"
            )
        ).all()
    assert len(fills) == 2


def test_undo_removes_exactly_one_batch() -> None:
    engine = _engine()
    account_id = ensure_default_account(engine)
    result = import_transactions_file(engine, GOLDEN, account_id)
    removed = undo_batch(engine, result.batch_id)
    assert removed == 13
    with Session(engine) as s:
        assert s.exec(select(Transaction)).all() == []
        assert s.exec(select(ImportBatch)).all() == []


def test_a_malformed_file_aborts_the_import_leaving_nothing_behind(tmp_path: Path) -> None:
    """Atomicity here is structural: parsing finishes before any Session is opened, so
    a bad row means the database is never touched. Nothing enforced that, though — a
    refactor moving the parse inside the session would allow a half-import with a
    committed batch row, and no existing test would notice. This locks it in."""
    engine = _engine()
    account_id = ensure_default_account(engine)

    lines = GOLDEN.read_text(encoding="utf-8").strip().split("
")
    lines[5] = "06-01-2025,09:00,TRUNCATED"  # 3 columns where 17 are required
    bad = tmp_path / "malformed.csv"
    bad.write_text("
".join(lines) + "
", encoding="utf-8")

    with pytest.raises(MalformedRow):
        import_transactions_file(engine, bad, account_id)

    with Session(engine) as s:
        assert s.exec(select(Transaction)).all() == []
        assert s.exec(select(ImportBatch)).all() == []


def test_undo_of_an_unknown_batch_is_a_no_op() -> None:
    engine = _engine()
    ensure_default_account(engine)
    assert undo_batch(engine, uuid4()) == 0


def test_ensure_default_account_is_idempotent() -> None:
    """It runs on every import, so a second call must not create a second account."""
    engine = _engine()
    first = ensure_default_account(engine)
    second = ensure_default_account(engine)
    assert first == second
    with Session(engine) as s:
        assert len(s.exec(select(Account)).all()) == 1


def test_batch_records_the_file_hash() -> None:
    engine = _engine()
    account_id = ensure_default_account(engine)
    result = import_transactions_file(engine, GOLDEN, account_id)
    with Session(engine) as s:
        batch = s.get(ImportBatch, result.batch_id)
    assert batch is not None
    assert len(batch.file_sha256) == 64
    assert batch.row_count == 13
```

```python
# backend/tests/integration/test_realdata.py
"""Opt-in suite against the owner's real exports. Never runs in CI: the files are
gitignored, so these tests skip themselves when the directory is absent."""

from pathlib import Path

import pytest
from sqlmodel import Session, select

from app.db import create_engine_and_tables
from app.ingest.importer import ensure_default_account, import_transactions_file
from app.models.ledger import Transaction

REAL = Path(__file__).parents[3] / "degiro-export" / "Transactions.csv"

pytestmark = [
    pytest.mark.realdata,
    pytest.mark.skipif(not REAL.exists(), reason="real DeGiro export not present"),
]


def test_real_file_imports_every_row_and_is_idempotent() -> None:
    engine = create_engine_and_tables("sqlite://")
    account_id = ensure_default_account(engine)

    first = import_transactions_file(engine, REAL, account_id)
    assert first.rows_inserted == first.rows_parsed

    second = import_transactions_file(engine, REAL, account_id)
    assert second.rows_inserted == 0

    with Session(engine) as s:
        assert len(s.exec(select(Transaction)).all()) == first.rows_parsed
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/integration -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.ingest.importer'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/ingest/importer.py
"""Import orchestration.

Every import writes an `import_batch` and is fully reversible. Idempotency comes
from the unique `(source, source_ref)` constraint plus a pre-read of existing refs,
so re-importing a file inserts nothing rather than raising.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import Engine
from sqlmodel import Session, select

from app.ingest.degiro.transactions_csv import PARSER_VERSION, SOURCE, parse_transactions_csv
from app.models.ledger import Account, ImportBatch, Transaction

DEFAULT_ACCOUNT_NAME = "DeGiro Main"


@dataclass(frozen=True, slots=True)
class ImportResult:
    batch_id: UUID
    rows_parsed: int
    rows_inserted: int
    rows_skipped: int


def ensure_default_account(engine: Engine) -> UUID:
    with Session(engine) as session:
        existing = session.exec(select(Account).where(Account.broker == SOURCE)).first()
        if existing is not None:
            return existing.id
        account = Account(
            id=uuid4(), broker=SOURCE, name=DEFAULT_ACCOUNT_NAME, base_currency="EUR"
        )
        session.add(account)
        session.commit()
        return account.id


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def import_transactions_file(engine: Engine, path: Path, account_id: UUID) -> ImportResult:
    rows = parse_transactions_csv(path)
    batch_id = uuid4()

    with Session(engine) as session:
        known = set(
            session.exec(select(Transaction.source_ref).where(Transaction.source == SOURCE)).all()
        )
        fresh = [row for row in rows if row.source_ref not in known]

        session.add(
            ImportBatch(
                id=batch_id,
                source=SOURCE,
                filename=path.name,
                file_sha256=_sha256(path),
                parser_version=PARSER_VERSION,
                imported_at=datetime.now(timezone.utc),
                row_count=len(rows),
                inserted_count=len(fresh),
            )
        )
        for row in fresh:
            session.add(
                Transaction(
                    id=uuid4(),
                    account_id=account_id,
                    import_batch_id=batch_id,
                    source=row.source,
                    source_ref=row.source_ref,
                    txn_type=row.txn_type,
                    trade_date=row.trade_date,
                    settle_date=row.settle_date,
                    isin=row.isin,
                    product_name=row.product_name,
                    quantity=row.quantity,
                    price_local=row.price_local,
                    currency_local=row.currency_local,
                    fx_rate=row.fx_rate,
                    fee_base=row.fee_base,
                    tax_base=row.tax_base,
                    gross_local=row.gross_local,
                    net_base=row.net_base,
                    order_ref=row.order_ref,
                    is_economic=row.is_economic,
                    closure_reason=row.closure_reason,
                    raw_json=json.dumps(row.raw, ensure_ascii=False),
                )
            )
        session.commit()

    return ImportResult(
        batch_id=batch_id,
        rows_parsed=len(rows),
        rows_inserted=len(fresh),
        rows_skipped=len(rows) - len(fresh),
    )


def undo_batch(engine: Engine, batch_id: UUID) -> int:
    """Remove one import batch and every transaction it created."""
    with Session(engine) as session:
        doomed = session.exec(
            select(Transaction).where(Transaction.import_batch_id == batch_id)
        ).all()
        for txn in doomed:
            session.delete(txn)
        batch = session.get(ImportBatch, batch_id)
        if batch is not None:
            session.delete(batch)
        session.commit()
        return len(doomed)
```

```python
# backend/app/cli.py
"""Command line entry points."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import typer

from app.db import create_engine_and_tables
from app.ingest.importer import ensure_default_account, import_transactions_file, undo_batch
from app.settings import get_settings

app = typer.Typer(help="Portfolio tracker maintenance commands.")


@app.command("import")
def import_command(path: Path) -> None:
    """Import a DeGiro Transactions.csv export."""
    engine = create_engine_and_tables(get_settings().database_url)
    account_id = ensure_default_account(engine)
    result = import_transactions_file(engine, path, account_id)
    typer.echo(
        f"batch {result.batch_id}: parsed {result.rows_parsed}, "
        f"inserted {result.rows_inserted}, skipped {result.rows_skipped}"
    )


@app.command("undo")
def undo_command(batch_id: str) -> None:
    """Undo one import batch."""
    engine = create_engine_and_tables(get_settings().database_url)
    removed = undo_batch(engine, UUID(batch_id))
    typer.echo(f"removed {removed} transactions")


if __name__ == "__main__":
    app()
```

Create empty `backend/tests/integration/__init__.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/ -v`
Expected: all pass; `test_realdata.py` passes locally (real export present) and skips elsewhere.

- [ ] **Step 5: Commit**

```bash
git add backend/app/ingest/importer.py backend/app/cli.py backend/tests/integration
git commit -m "feat: idempotent import with batch undo, plus opt-in real-data suite"
```

---

### Task 8: Transactions API

**Files:**
- Create: `backend/app/api/__init__.py`
- Create: `backend/app/api/schemas.py`
- Create: `backend/app/api/routes_transactions.py`
- Create: `backend/app/main.py`
- Test: `backend/tests/integration/test_api.py`

**Interfaces:**
- Consumes: models (Task 4), importer (Task 7).
- Produces:
  - `TransactionOut` — Pydantic response model; money fields serialise as strings to survive JSON without float drift.
  - `TransactionPage(items: list[TransactionOut], total: int, limit: int, offset: int)`
  - `GET /api/transactions?limit&offset&isin` and `GET /api/health`.
  - `create_app(engine: Engine | None = None) -> FastAPI`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/integration/test_api.py
from pathlib import Path

from fastapi.testclient import TestClient

from app.db import create_engine_and_tables
from app.ingest.importer import ensure_default_account, import_transactions_file
from app.main import create_app

GOLDEN = Path(__file__).parents[1] / "golden" / "degiro_transactions_golden.csv"


def _client() -> TestClient:
    engine = create_engine_and_tables("sqlite://")
    account_id = ensure_default_account(engine)
    import_transactions_file(engine, GOLDEN, account_id)
    return TestClient(create_app(engine))


def test_lists_transactions_newest_first() -> None:
    """Assert the whole sequence, not just its endpoints: a broken secondary sort key
    leaves the first and last rows correct while scrambling everything between them."""
    body = _client().get("/api/transactions", params={"limit": 1000}).json()
    dates = [r["trade_date"] for r in body["items"]]
    assert dates == sorted(dates, reverse=True)
    assert body["total"] == 13


def test_money_is_serialised_as_a_string_not_a_float() -> None:
    """JSON floats reintroduce the drift Decimal exists to prevent."""
    body = _client().get("/api/transactions").json()
    row = next(r for r in body["items"] if r["order_ref"] == "aaaa0003-0000-0000-0000-000000000006")
    assert row["net_base"] == "90.35"
    assert isinstance(row["net_base"], str)


def test_raw_source_row_is_available_for_inspection() -> None:
    body = _client().get("/api/transactions").json()
    assert body["items"][0]["raw"]["Total EUR"]


def test_filters_by_isin() -> None:
    body = _client().get("/api/transactions", params={"isin": "NL0000000003"}).json()
    assert body["total"] == 3


def test_paginates() -> None:
    body = _client().get("/api/transactions", params={"limit": 5, "offset": 0}).json()
    assert len(body["items"]) == 5
    assert body["total"] == 13


def test_paging_covers_every_row_exactly_once() -> None:
    """The real pagination risk is a non-total ordering: rows tying on the sort key can
    come back in a different order per request, so a page boundary silently skips one
    row and repeats another. Only walking the pages and comparing against a single full
    fetch detects that -- asserting one page's length never will."""
    client = _client()
    whole = client.get("/api/transactions", params={"limit": 1000}).json()
    expected = [r["id"] for r in whole["items"]]

    paged: list[str] = []
    for offset in range(0, whole["total"], 5):
        page = client.get(
            "/api/transactions", params={"limit": 5, "offset": offset}
        ).json()
        paged.extend(r["id"] for r in page["items"])

    assert paged == expected
    assert len(set(paged)) == len(paged)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/integration/test_api.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.main'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/api/schemas.py
"""API response models.

Money crosses the wire as a string. JSON numbers are IEEE doubles, so serialising a
Decimal as a number reintroduces exactly the drift the storage layer prevents.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, field_serializer

from app.models.ledger import Transaction


class TransactionOut(BaseModel):
    id: str
    trade_date: date
    txn_type: str
    isin: str | None
    product_name: str | None
    quantity: Decimal | None
    price_local: Decimal | None
    currency_local: str | None
    fx_rate: Decimal | None
    fee_base: Decimal
    tax_base: Decimal
    net_base: Decimal
    order_ref: str | None
    is_economic: bool
    closure_reason: str
    raw: dict[str, str]

    @field_serializer(
        "quantity", "price_local", "fx_rate", "fee_base", "tax_base", "net_base"
    )
    def _decimal_as_string(self, value: Decimal | None) -> str | None:
        return None if value is None else str(value)

    @classmethod
    def from_model(cls, txn: Transaction) -> "TransactionOut":
        return cls(
            id=str(txn.id),
            trade_date=txn.trade_date,
            txn_type=txn.txn_type,
            isin=txn.isin,
            product_name=txn.product_name,
            quantity=txn.quantity,
            price_local=txn.price_local,
            currency_local=txn.currency_local,
            fx_rate=txn.fx_rate,
            fee_base=txn.fee_base,
            tax_base=txn.tax_base,
            net_base=txn.net_base,
            order_ref=txn.order_ref,
            is_economic=txn.is_economic,
            closure_reason=txn.closure_reason,
            raw=json.loads(txn.raw_json),
        )


class TransactionPage(BaseModel):
    items: list[TransactionOut]
    total: int
    limit: int
    offset: int
```

```python
# backend/app/api/routes_transactions.py
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import Engine, func
from sqlmodel import Session, select

from app.api.schemas import TransactionOut, TransactionPage
from app.models.ledger import Transaction

router = APIRouter(prefix="/api", tags=["transactions"])


def get_engine(request: Request) -> Engine:
    """The engine is wired onto app.state at construction time.

    Deliberately not `dependency_overrides`: that is FastAPI's test-seam and using
    it for production wiring leaves no seam left for tests to use.
    """
    engine: Engine = request.app.state.engine
    return engine


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/transactions", response_model=TransactionPage)
def list_transactions(
    engine: Engine = Depends(get_engine),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    isin: str | None = None,
) -> TransactionPage:
    with Session(engine) as session:
        count_stmt = select(func.count()).select_from(Transaction)
        # Ordering must be a TOTAL order or offset/limit can skip or repeat a row at a
        # page boundary. trade_date is not unique across rows, and source_ref is only
        # unique per source -- the DB constraint is UNIQUE(source, source_ref), and a
        # second source (SnapTrade) is a planned milestone. The primary key settles it.
        page_stmt = select(Transaction).order_by(
            Transaction.trade_date.desc(), Transaction.source_ref, Transaction.id
        )
        if isin:
            count_stmt = count_stmt.where(Transaction.isin == isin)
            page_stmt = page_stmt.where(Transaction.isin == isin)

        total = session.exec(count_stmt).one()
        rows = session.exec(page_stmt.limit(limit).offset(offset)).all()

    return TransactionPage(
        items=[TransactionOut.from_model(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )
```

```python
# backend/app/main.py
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import Engine

from app.api import routes_transactions
from app.db import create_engine_and_tables
from app.settings import get_settings


def create_app(engine: Engine | None = None) -> FastAPI:
    app = FastAPI(title="Portfolio Tracker")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.engine = engine or create_engine_and_tables(get_settings().database_url)
    app.include_router(routes_transactions.router)
    return app
```

Create empty `backend/app/api/__init__.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/ -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add backend/app/api backend/app/main.py backend/tests/integration/test_api.py
git commit -m "feat: transactions API with string-serialised money and raw row inspection"
```

---

### Task 9: React transaction table

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/vite.config.ts`
- Create: `frontend/tsconfig.json`
- Create: `frontend/index.html`
- Create: `frontend/src/main.tsx`
- Create: `frontend/src/api/types.ts`
- Create: `frontend/src/api/client.ts`
- Create: `frontend/src/components/TransactionTable.tsx`
- Create: `frontend/src/App.tsx`

**Interfaces:**
- Consumes: `GET /api/transactions` (Task 8).
- Produces: a dev server on `http://localhost:5173` rendering every transaction with an expandable raw-source row.

- [ ] **Step 1: Scaffold the frontend**

```json
// frontend/package.json
{
  "name": "portfolio-tracker-frontend",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "typecheck": "tsc --noEmit"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1"
  },
  "devDependencies": {
    "@types/react": "^18.3.12",
    "@types/react-dom": "^18.3.1",
    "@vitejs/plugin-react": "^4.3.4",
    "typescript": "^5.7.2",
    "vite": "^6.0.5"
  }
}
```

```json
// frontend/tsconfig.json
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "moduleResolution": "bundler",
    "jsx": "react-jsx",
    "strict": true,
    "noUncheckedIndexedAccess": true,
    "noUnusedLocals": true,
    "noEmit": true,
    "skipLibCheck": true
  },
  "include": ["src"]
}
```

```ts
// frontend/vite.config.ts
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: { port: 5173 },
});
```

```html
<!-- frontend/index.html -->
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Portfolio Tracker</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

- [ ] **Step 2: Write the API client and types**

```ts
// frontend/src/api/types.ts
// Money arrives as a string on purpose: JSON numbers are IEEE doubles and would
// reintroduce the cent drift the backend's Decimal storage prevents. Format these
// for display; never parse them into a float for arithmetic.
export interface Transaction {
  id: string;
  trade_date: string;
  txn_type: string;
  isin: string | null;
  product_name: string | null;
  quantity: string | null;
  price_local: string | null;
  currency_local: string | null;
  fx_rate: string | null;
  fee_base: string;
  tax_base: string;
  net_base: string;
  order_ref: string | null;
  is_economic: boolean;
  closure_reason: string;
  raw: Record<string, string>;
}

export interface TransactionPage {
  items: Transaction[];
  total: number;
  limit: number;
  offset: number;
}
```

```ts
// frontend/src/api/client.ts
import type { TransactionPage } from "./types";

const BASE = "http://localhost:8000";

export async function fetchTransactions(limit = 500, offset = 0): Promise<TransactionPage> {
  const response = await fetch(`${BASE}/api/transactions?limit=${limit}&offset=${offset}`);
  if (!response.ok) {
    throw new Error(`Failed to load transactions: ${response.status} ${response.statusText}`);
  }
  return (await response.json()) as TransactionPage;
}
```

- [ ] **Step 3: Write the table component**

```tsx
// frontend/src/components/TransactionTable.tsx
import { useState } from "react";
import type { Transaction } from "../api/types";

interface Props {
  transactions: Transaction[];
}

/** One ledger row, plus a collapsible panel showing the original CSV cells.
 *  The raw panel exists so a parser bug can be diagnosed against the source
 *  without re-opening the export. */
function Row({ txn }: { txn: Transaction }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <tr>
        <td>{txn.trade_date}</td>
        <td>{txn.txn_type}</td>
        <td>{txn.product_name ?? "—"}</td>
        <td>{txn.isin ?? "—"}</td>
        <td style={{ textAlign: "right" }}>{txn.quantity ?? "—"}</td>
        <td style={{ textAlign: "right" }}>
          {txn.price_local ?? "—"} {txn.currency_local ?? ""}
        </td>
        <td style={{ textAlign: "right" }}>{txn.fee_base}</td>
        <td style={{ textAlign: "right" }}>{txn.net_base}</td>
        <td>
          <button type="button" onClick={() => setOpen(!open)}>
            {open ? "hide" : "raw"}
          </button>
        </td>
      </tr>
      {open && (
        <tr>
          <td colSpan={9}>
            <pre style={{ margin: 0, fontSize: 12, overflowX: "auto" }}>
              {JSON.stringify(txn.raw, null, 2)}
            </pre>
          </td>
        </tr>
      )}
    </>
  );
}

export function TransactionTable({ transactions }: Props) {
  return (
    <table style={{ borderCollapse: "collapse", width: "100%", fontSize: 14 }}>
      <thead>
        <tr>
          <th>Date</th>
          <th>Type</th>
          <th>Product</th>
          <th>ISIN</th>
          <th>Qty</th>
          <th>Price</th>
          <th>Fee (EUR)</th>
          <th>Net (EUR)</th>
          <th />
        </tr>
      </thead>
      <tbody>
        {transactions.map((txn) => (
          <Row key={txn.id} txn={txn} />
        ))}
      </tbody>
    </table>
  );
}
```

```tsx
// frontend/src/App.tsx
import { useEffect, useState } from "react";
import { fetchTransactions } from "./api/client";
import { TransactionTable } from "./components/TransactionTable";
import type { Transaction } from "./api/types";

export default function App() {
  const [transactions, setTransactions] = useState<Transaction[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchTransactions()
      .then((page) => setTransactions(page.items))
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <p style={{ padding: 24 }}>Loading…</p>;
  if (error) return <p style={{ padding: 24, color: "crimson" }}>{error}</p>;

  return (
    <main style={{ padding: 24, fontFamily: "system-ui, sans-serif" }}>
      <h1 style={{ fontSize: 20 }}>Ledger — {transactions.length} transactions</h1>
      <TransactionTable transactions={transactions} />
    </main>
  );
}
```

```tsx
// frontend/src/main.tsx
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";

const root = document.getElementById("root");
if (!root) throw new Error("root element missing");
createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
```

- [ ] **Step 4: Verify end to end**

```bash
cd backend && python -m uvicorn app.main:create_app --factory --port 8000 &
cd frontend && npm install && npm run typecheck && npm run dev
```

Expected: `npm run typecheck` exits 0. Opening `http://localhost:5173` after
`python -m app.cli import ../degiro-export/Transactions.csv` shows 112 rows, newest
first, with a working `raw` toggle on each.

- [ ] **Step 5: Commit**

```bash
git add frontend
git commit -m "feat: React transaction table with raw source row inspection"
```

---

## Definition of done for M0a

- `pytest` green, including the opt-in `realdata` suite locally.
- `python -m app.cli import degiro-export/Transactions.csv` inserts 112 rows; running it again inserts 0.
- `python -m app.cli undo <batch-id>` empties the ledger.
- The browser table shows all 112 rows with inspectable raw source data.
- No real export data is committed. Confirm with `git status --porcelain` and `git check-ignore -v degiro-export/Transactions.csv`.

## What M0b adds next

`Account.csv` classification (the 20-pattern taxonomy, the 256 non-economic cash rows), corporate-action detection and quarantine, `is_economic` suppression of the split and product-change pairs, and the cross-file reconciliation report that closes M0.
