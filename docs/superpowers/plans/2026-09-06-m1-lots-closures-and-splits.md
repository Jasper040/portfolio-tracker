# M1 — Lots, Closures, Splits and `rebuild()` — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the M0 ledger into lots and closures — FIFO/LIFO/HIFO, split-adjusted, rebuildable from scratch — and prove it by reproducing the broker's own ORN position of 32 shares.

**Architecture:** Three pure passes sit under a persistence layer that owns no arithmetic. `domain/orders.py` groups ledger rows into economic orders and attributes each order's charges across its fills. `domain/splits.py` derives a ratio from the corporate-action legs M0 already suppressed and rewrites the lots opened before it. `domain/lots.py` matches buys to sells. `analytics/rebuild.py` runs the three in order and writes `lot` and `lot_closure`, which are derived tables — dropped and rewritten on every rebuild, never edited.

**Tech Stack:** Python 3.12+, SQLModel/SQLAlchemy, FastAPI, Typer, pytest, React + Vite + TypeScript.

**Spec:** `docs/superpowers/specs/2026-09-05-portfolio-tracker-design.md`

## Global Constraints

- **The ledger is append-only and immutable.** `lot` and `lot_closure` are NOT the ledger — they are derived, and `rebuild()` drops and rewrites them. Nothing in `transaction` is ever updated.
- **Money is `Decimal`, never `float`.** Anywhere. Including in tests.
- **Cost basis excludes charges.** `quantity × price`, nothing added. Charges ride alongside as their own figure and are deducted from P&L (design doc §6.4, decided 2026-09-06). A closure must be able to report what the stock did, what the broker took, and what is left, as three separate numbers.
- **Charges stay in three parts** all the way to the lot: `commission`, `autofx`, `tax`. They answer different questions.
- **Charges are positive in the domain layer.** The ledger stores them negative (a debit). The sign flip happens exactly once, in `domain/orders.py`.
- **`is_economic = False` rows are skipped by lot matching.** That is what M0's quarantine bought; nothing here re-derives it.
- **The standing invariant: `Σ attributed charges == Σ ledger charges`,** asserted after every rebuild in production code, not only in tests (§11.2).
- **Purity:** `domain/` imports no ORM, opens no file, calls no `datetime.now()`. That is what makes `rebuild()` provably deterministic.
- **No SQLite-only features.** Explicit types, declared foreign keys, UUID primary keys.
- **Type hints on every function.** Files 200–400 lines typical, 800 maximum.
- **Do not implement:** prices, market value, unrealised P&L, benchmarks, or dividend analytics. A lot's *market* value needs a price series, which is M2. M1's UI shows cost basis, quantity and realised P&L only.

**Natural stopping point:** Tasks 1–6 are backend-only and produce provable software (ORN = 32 via `pytest -m realdata`). Tasks 7–9 make it visible. Stopping after Task 6 leaves the repo green and coherent.

## Deliberate deviations from the spec

Two columns §5.2 lists on `transaction` are **not** added, and the reasons are the
same in both cases: they are derived, and `transaction` is the fact table.

- **`order_group_id`** — "Derived: groups fill rows into one economic order". Task 2
  computes exactly this, in the pure layer, on every rebuild. Persisting it onto an
  append-only row would freeze a derivation into the facts, so a change to the
  grouping rule could no longer be applied by rebuilding — it would need a
  migration, or worse, would silently disagree with the code that computes it.
- **`source_file_hash`** — "Ties a row to the exact file that produced it".
  `import_batch` already carries the hash of both export files, and every
  transaction carries `import_batch_id`. The tie exists; duplicating it per row adds
  a second copy that can drift from the first.

If either turns out to be needed by M2 or later, adding it then is a schema change
plus a re-import — which the ledger supports by design. Adding it now, unused, is a
column nothing writes correctly because nothing reads it.

---

### Task 1: Cost basis excludes charges, and charges keep their three parts

`domain/lots.py` currently contradicts itself: `OpenLot.cost` adds fees into the basis, while `Closure.pnl` and `Closure.return_pct` both measure against a fee-free basis. This task settles it in favour of the fee-free basis and splits the single `fees` field into the three charge types.

**Files:**
- Create: `backend/app/domain/charges.py`
- Modify: `backend/app/domain/lots.py`
- Test: `backend/tests/unit/test_charges.py`
- Test: `backend/tests/unit/test_lots.py` (existing — update)

**Interfaces:**
- Consumes: `apportion(total: Decimal, weights: Sequence[Decimal], places: int = 2) -> list[Decimal]` from `app.domain.fees`.
- Produces:
  - `Charges(commission: Decimal, autofx: Decimal, tax: Decimal)` frozen dataclass, with `.total: Decimal`, `.__add__`, and `Charges.zero()`.
  - `apportion_charges(charges: Charges, weights: Sequence[Decimal]) -> list[Charges]`.
  - `LotTransaction(id, trade_date, side, quantity, price, charges: Charges)`.
  - `OpenLot(id, opened_on, quantity, price, charges)` with `.cost_basis: Decimal`.
  - `Closure(...)` with `.gross_pnl`, `.charges`, `.pnl`, `.return_pct`, `.annualised_return`, `.holding_days`.
  - `MatchResult` with `.quantity`, `.cost_basis`, `.charges`, `.realised`, `.gross_realised`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_charges.py`:

```python
"""What a trade cost beyond the price of the shares.

Three components rather than one total because they answer different questions:
a commission is negotiable and comparable between brokers, an FX cost is a
consequence of buying abroad, and tax is reportable. Rolling them into one number
answers none of those.
"""

from __future__ import annotations

from decimal import Decimal

from app.domain.charges import Charges, apportion_charges

D = Decimal


class TestCharges:
    def test_total_sums_the_three_components(self) -> None:
        assert Charges(D("2.00"), D("2.18"), D("0.50")).total == D("4.68")

    def test_zero_is_zero_in_every_component(self) -> None:
        assert Charges.zero() == Charges(D("0.00"), D("0.00"), D("0.00"))
        assert Charges.zero().total == D("0.00")

    def test_adds_component_wise(self) -> None:
        """Summing totals instead would lose the breakdown the owner asked for."""
        total = Charges(D("2.00"), D("1.00"), D("0.00")) + Charges(D("3.00"), D("0.50"), D("0.25"))
        assert total == Charges(D("5.00"), D("1.50"), D("0.25"))


class TestApportionCharges:
    def test_each_component_sums_exactly_to_its_whole(self) -> None:
        """The Sec 11.2 invariant, per component. Apportioning the TOTAL and then
        splitting it back into three would reintroduce the rounding that
        `apportion` exists to remove."""
        charges = Charges(D("0.10"), D("0.10"), D("0.10"))
        parts = apportion_charges(charges, [D(1), D(1), D(1)])
        assert sum(p.commission for p in parts) == D("0.10")
        assert sum(p.autofx for p in parts) == D("0.10")
        assert sum(p.tax for p in parts) == D("0.10")

    def test_returns_one_charge_per_weight(self) -> None:
        parts = apportion_charges(Charges(D("6.00"), D("0.00"), D("0.00")), [D(1), D(2)])
        assert [p.commission for p in parts] == [D("2.00"), D("4.00")]

    def test_a_zero_charge_apportions_to_zeros(self) -> None:
        parts = apportion_charges(Charges.zero(), [D(1), D(3)])
        assert parts == [Charges.zero(), Charges.zero()]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/unit/test_charges.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.domain.charges'`

- [ ] **Step 3: Write minimal implementation**

Create `backend/app/domain/charges.py`:

```python
"""What a trade cost beyond the price of the shares.

Design doc Sec 6.4. Three components rather than one total, because they answer
different questions and the owner asked to see them apart: `commission` is what the
broker charged to execute, `autofx` is what converting the currency cost, and `tax`
is tax paid and separately reportable.

**Positive means money paid.** The ledger stores these negative, as debits. The flip
happens exactly once, in `domain.orders`, so nothing downstream has to remember
which convention it is looking at -- and a sign error shows up as a P&L that moves
the wrong way rather than as a plausible number.

Charges are never added into a cost basis. A lot bought for EUR 655.30 with EUR 4.18
of charges has a basis of 655.30; the 4.18 stays visible and is deducted from P&L.
The arithmetic of net P&L is the same either way. What differs is whether you can
still see what you paid.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from app.domain.fees import apportion

_ZERO = Decimal("0.00")


@dataclass(frozen=True, slots=True)
class Charges:
    """The three costs a trade carries, kept apart. Positive means money paid."""

    commission: Decimal = _ZERO
    autofx: Decimal = _ZERO
    tax: Decimal = _ZERO

    @property
    def total(self) -> Decimal:
        return self.commission + self.autofx + self.tax

    def __add__(self, other: "Charges") -> "Charges":
        return Charges(
            commission=self.commission + other.commission,
            autofx=self.autofx + other.autofx,
            tax=self.tax + other.tax,
        )

    @classmethod
    def zero(cls) -> "Charges":
        return cls(_ZERO, _ZERO, _ZERO)


def apportion_charges(charges: Charges, weights: Sequence[Decimal]) -> list[Charges]:
    """Split each component across `weights` so each one sums back exactly.

    Component-wise, not total-then-redivide. `apportion` is exact per call, so three
    exact splits give an exact total; splitting the total and then reconstructing a
    breakdown from it would round twice and break the Sec 11.2 invariant on the
    component the owner is actually reading.
    """
    commissions = apportion(charges.commission, weights)
    autofx = apportion(charges.autofx, weights)
    taxes = apportion(charges.tax, weights)
    return [
        Charges(commission=c, autofx=a, tax=t)
        for c, a, t in zip(commissions, autofx, taxes, strict=True)
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/unit/test_charges.py -q`
Expected: PASS (6 tests)

- [ ] **Step 5: Write the failing test for the new lot semantics**

Append to `backend/tests/unit/test_lots.py`:

```python
class TestCostBasisExcludesCharges:
    """Design doc Sec 6.4, decided 2026-09-06.

    Capitalising hides the cost: a lot bought at 655.30 with 4.18 of charges reads
    as 659.48 and the 4.18 is gone. Net P&L is identical either way; what changes is
    whether the charge is still visible when you ask what you paid.
    """

    def test_an_open_lots_basis_is_quantity_times_price(self) -> None:
        result = match_lots([buy("1", "10", "10.00", "2.00")], "FIFO")
        assert result.open_lots[0].cost_basis == D("100.00")

    def test_the_charges_are_reported_beside_it_not_inside_it(self) -> None:
        result = match_lots([buy("1", "10", "10.00", "2.00")], "FIFO")
        assert result.open_lots[0].charges.total == D("2.00")
        assert result.cost_basis == D("100.00")
        assert result.charges.total == D("2.00")

    def test_a_closure_reports_stock_performance_apart_from_charges(self) -> None:
        """The three numbers the owner asked for: what the stock did, what the
        broker took, and what is left."""
        result = match_lots(
            [buy("1", "10", "10.00", "2.00"), sell("2", "5", "20.00", "4.00")], "FIFO"
        )
        closure = result.closures[0]
        assert closure.gross_pnl == D("50.00")
        assert closure.charges.total == D("5.00")
        assert closure.pnl == D("45.00")

    def test_return_is_measured_against_the_fee_free_basis(self) -> None:
        """5 shares at 10.00 tied up 50.00. Net P&L 45.00 on that is 90%."""
        result = match_lots(
            [buy("1", "10", "10.00", "2.00"), sell("2", "5", "20.00", "4.00")], "FIFO"
        )
        assert result.closures[0].return_pct == D("0.9")

    def test_commission_and_fx_cost_stay_distinguishable_after_matching(self) -> None:
        """The whole point of carrying three components through the matcher."""
        result = match_lots(
            [
                LotTransaction(
                    id="1",
                    trade_date=date(2020, 1, 1),
                    side="BUY",
                    quantity=D("10"),
                    price=D("10.00"),
                    charges=Charges(commission=D("2.00"), autofx=D("1.00"), tax=D("0.50")),
                )
            ],
            "FIFO",
        )
        lot = result.open_lots[0]
        assert lot.charges.commission == D("2.00")
        assert lot.charges.autofx == D("1.00")
        assert lot.charges.tax == D("0.50")
```

Update the `buy`/`sell` helpers at the top of the same file so the existing tests keep compiling — the `fees` string becomes a commission:

```python
from app.domain.charges import Charges
from app.domain.lots import LotTransaction, match_lots


def buy(ref: str, quantity: str, price: str, fees: str = "0.00", day: int = 1) -> LotTransaction:
    return LotTransaction(
        id=ref,
        trade_date=date(2020, 1, day),
        side="BUY",
        quantity=D(quantity),
        price=D(price),
        charges=Charges(commission=D(fees)),
    )


def sell(ref: str, quantity: str, price: str, fees: str = "0.00", day: int = 1) -> LotTransaction:
    return LotTransaction(
        id=ref,
        trade_date=date(2024, 1, day),
        side="SELL",
        quantity=D(quantity),
        price=D(price),
        charges=Charges(commission=D(fees)),
    )
```

Existing assertions of the form `result.closures[0].fees == D("5.00")` become
`result.closures[0].charges.total == D("5.00")`. Existing assertions on
`result.cost` become `result.cost_basis` and must drop the fee component:
a lot of 10 @ 10.00 with 2.00 of commission is now `cost_basis == D("100.00")`,
not `D("102.00")`.

- [ ] **Step 6: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/unit/test_lots.py -q`
Expected: FAIL — `TypeError: LotTransaction.__init__() got an unexpected keyword argument 'charges'`

- [ ] **Step 7: Change `domain/lots.py`**

Replace the `fees: Decimal` field with `charges: Charges` on `LotTransaction`, `OpenLot` and `Closure`; replace `apportion` with `apportion_charges`; and change the three properties. The edits, in order:

```python
from app.domain.charges import Charges, apportion_charges
```

```python
@dataclass(frozen=True, slots=True)
class LotTransaction:
    """One economic buy or sell, with its order-level charges already attributed.

    Grouping fill rows into orders and splitting an order's charges across its fills
    happens upstream in `domain.orders`; by the time a transaction reaches the
    matcher its `charges` are its own.
    """

    id: str
    trade_date: date
    side: Literal["BUY", "SELL"]
    quantity: Decimal
    price: Decimal
    charges: Charges


@dataclass(frozen=True, slots=True)
class OpenLot:
    """Purchase quantity still held, with the charges belonging to that quantity."""

    id: str
    opened_on: date
    quantity: Decimal
    price: Decimal
    charges: Charges

    @property
    def cost_basis(self) -> Decimal:
        """What the shares cost. Charges are NOT capitalised (Sec 6.4).

        Named `cost_basis` rather than `cost` deliberately: the old name meant
        price-plus-fees, and a silent change of meaning behind an unchanged name is
        how a wrong number survives a refactor.
        """
        return self.quantity * self.price
```

```python
@dataclass(frozen=True, slots=True)
class Closure:
    """One matched (buy lot, sale) pair.

    A sale spanning three lots produces three closures; a lot sold in three
    tranches also produces three.
    """

    lot_id: str
    #: The SALE's id. A closure is a pair, and carrying only the buy would leave
    #: half of it untraceable back to the ledger.
    sale_id: str
    opened_on: date
    open_price: Decimal
    closed_on: date
    close_price: Decimal
    quantity: Decimal
    #: Pro-rata charges from BOTH legs of the round trip.
    charges: Charges

    @property
    def gross_pnl(self) -> Decimal:
        """What the stock did, before the broker took anything."""
        return self.quantity * (self.close_price - self.open_price)

    @property
    def pnl(self) -> Decimal:
        """Realised P&L in base currency, net of both legs' charges."""
        return self.gross_pnl - self.charges.total

    @property
    def holding_days(self) -> int:
        return (self.closed_on - self.opened_on).days

    @property
    def return_pct(self) -> Decimal | None:
        """Return on the capital the lot tied up. `None` when that was zero."""
        basis = self.quantity * self.open_price
        if basis == 0:
            return None
        return self.pnl / basis
```

`annualised_return` is unchanged. Then `MatchResult`:

```python
@dataclass(frozen=True, slots=True)
class MatchResult:
    closures: list[Closure]
    open_lots: list[OpenLot]

    @property
    def quantity(self) -> Decimal:
        return sum((lot.quantity for lot in self.open_lots), Decimal(0))

    @property
    def cost_basis(self) -> Decimal:
        return sum((lot.cost_basis for lot in self.open_lots), Decimal(0))

    @property
    def charges(self) -> Charges:
        return sum((lot.charges for lot in self.open_lots), Charges.zero())

    @property
    def gross_realised(self) -> Decimal:
        return sum((closure.gross_pnl for closure in self.closures), Decimal(0))

    @property
    def realised(self) -> Decimal:
        return sum((closure.pnl for closure in self.closures), Decimal(0))
```

In `match_lots`, the attribution pass changes from `Decimal` to `Charges`:

```python
    closure_charges = [Charges.zero()] * len(matches)
    open_lots: list[OpenLot] = []

    for lot in working:
        weights = [*lot.consumed, lot.remaining]
        shares = apportion_charges(lot.transaction.charges, weights)
        for index, share in zip(lot.closure_indices, shares, strict=False):
            closure_charges[index] += share
        if lot.remaining > 0:
            open_lots.append(
                OpenLot(
                    id=lot.transaction.id,
                    opened_on=lot.transaction.trade_date,
                    quantity=lot.remaining,
                    price=lot.transaction.price,
                    charges=shares[-1],
                )
            )

    for sale, indices in sale_closures:
        if not indices:
            continue
        shares = apportion_charges(sale.charges, [matches[i].quantity for i in indices])
        for index, share in zip(indices, shares, strict=False):
            closure_charges[index] += share

    closures = [
        Closure(
            lot_id=match.lot.id,
            sale_id=match.sale.id,
            opened_on=match.lot.trade_date,
            open_price=match.lot.price,
            closed_on=match.sale.trade_date,
            close_price=match.sale.price,
            quantity=match.quantity,
            charges=closure_charges[i],
        )
        for i, match in enumerate(matches)
    ]
```

Also update the module docstring's fee paragraph to say "charges" and to state that
the basis excludes them.

- [ ] **Step 8: Run the tests**

Run: `cd backend && python -m pytest tests/unit/test_lots.py tests/unit/test_charges.py -q`
Expected: PASS

Then check nothing else referenced the old names:

Run: `cd backend && python -m pytest -q && python -m ruff check . && python -m mypy app`
Expected: all green

- [ ] **Step 9: Commit**

```bash
git add backend/app/domain/charges.py backend/app/domain/lots.py \
        backend/tests/unit/test_charges.py backend/tests/unit/test_lots.py
git commit -m "feat(domain): keep charges beside the cost basis, not inside it"
```

---

### Task 2: Economic orders and order-level charge attribution

DeGiro books a commission once per *order*, on one arbitrary fill row (§3.5: CASTOR 07-10-2026 is `+25` with a blank fee and `+75` with `−2.00` under one order id). Attributing that fee to the row it landed on would give one fill a 2.67% cost and the other zero. §6.4 says attribute at order level, then pro-rata across fills.

**Files:**
- Create: `backend/app/domain/orders.py`
- Test: `backend/tests/unit/test_orders.py`

**Interfaces:**
- Consumes: `Charges`, `apportion_charges` (Task 1); `LotTransaction` (Task 1).
- Produces: `to_lot_transactions(rows: Sequence[LedgerRow]) -> dict[str, list[LotTransaction]]`, keyed by ISIN, each list chronological; and `LedgerRow` — a Protocol matching the fields `to_lot_transactions` reads, so `domain/` stays ORM-free.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_orders.py`:

```python
"""Grouping fill rows into economic orders (design doc Sec 6.4).

DeGiro charges commission per ORDER and books it on one arbitrary fill. The real
export's CASTOR order is the case: `+25` with a blank fee and `+75` charged EUR 2.00.
Left on the row it landed on, one fill carries a 2.67% cost and the other none --
and a HIFO matcher picking between them would then pick on an artefact of DeGiro's
bookkeeping rather than on price.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.domain.orders import to_lot_transactions

D = Decimal


@dataclass(frozen=True)
class Row:
    """Stands in for a `Transaction`. `domain/` never imports the ORM."""

    source_ref: str
    isin: str | None
    trade_date: date
    trade_time: str | None
    txn_type: str
    quantity: Decimal | None
    price_local: Decimal | None
    value_base: Decimal | None
    fee_base: Decimal
    autofx_fee_base: Decimal | None
    tax_base: Decimal
    order_ref: str | None
    is_economic: bool = True


def row(**kwargs: object) -> Row:
    defaults: dict[str, object] = {
        "source_ref": "r1",
        "isin": "US0000000001",
        "trade_date": date(2026, 5, 28),
        "trade_time": "10:00",
        "txn_type": "BUY",
        "quantity": D("10"),
        "price_local": D("10.00"),
        "value_base": D("-100.00"),
        "fee_base": D("0.00"),
        "autofx_fee_base": D("0.00"),
        "tax_base": D("0.00"),
        "order_ref": "order-1",
    }
    return Row(**{**defaults, **kwargs})  # type: ignore[arg-type]


class TestOrderLevelCharges:
    def test_splits_one_orders_commission_across_its_fills_by_quantity(self) -> None:
        """The CASTOR case: EUR 2.00 on one order of 25 + 75 becomes 0.50 / 1.50."""
        rows = [
            row(source_ref="a", quantity=D("25"), value_base=D("-250.00"), fee_base=D("0.00")),
            row(source_ref="b", quantity=D("75"), value_base=D("-750.00"), fee_base=D("-2.00")),
        ]
        fills = to_lot_transactions(rows)["US0000000001"]
        assert [f.charges.commission for f in fills] == [D("0.50"), D("1.50")]

    def test_the_attributed_total_equals_the_ledger_total(self) -> None:
        """The Sec 11.2 standing invariant, at the smallest scale it can be seen."""
        rows = [
            row(source_ref="a", quantity=D("1"), value_base=D("-10.00")),
            row(source_ref="b", quantity=D("1"), value_base=D("-10.00")),
            row(source_ref="c", quantity=D("1"), value_base=D("-10.00"), fee_base=D("-0.10")),
        ]
        fills = to_lot_transactions(rows)["US0000000001"]
        assert sum(f.charges.commission for f in fills) == D("0.10")

    def test_charges_arrive_positive(self) -> None:
        """The ledger stores a debit; the domain layer counts money paid."""
        fills = to_lot_transactions([row(fee_base=D("-2.00"), tax_base=D("-0.50"))])
        charge = fills["US0000000001"][0].charges
        assert charge.commission == D("2.00")
        assert charge.tax == D("0.50")

    def test_the_three_charge_types_stay_apart(self) -> None:
        fills = to_lot_transactions(
            [row(fee_base=D("-2.00"), autofx_fee_base=D("-2.18"), tax_base=D("-0.50"))]
        )
        charge = fills["US0000000001"][0].charges
        assert (charge.commission, charge.autofx, charge.tax) == (D("2.00"), D("2.18"), D("0.50"))


class TestGrouping:
    def test_two_orders_on_one_day_do_not_share_a_commission(self) -> None:
        rows = [
            row(source_ref="a", order_ref="order-1", fee_base=D("-2.00")),
            row(source_ref="b", order_ref="order-2", fee_base=D("-3.00")),
        ]
        fills = to_lot_transactions(rows)["US0000000001"]
        assert sorted(f.charges.commission for f in fills) == [D("2.00"), D("3.00")]

    def test_a_blank_order_ref_falls_back_to_a_synthetic_key(self) -> None:
        """Sec 6.4. Two blank-id rows at different times are two orders, not one --
        grouping them would pool charges across unrelated trades."""
        rows = [
            row(source_ref="a", order_ref=None, trade_time="10:00", fee_base=D("-2.00")),
            row(source_ref="b", order_ref=None, trade_time="14:00", fee_base=D("-3.00")),
        ]
        fills = to_lot_transactions(rows)["US0000000001"]
        assert sorted(f.charges.commission for f in fills) == [D("2.00"), D("3.00")]

    def test_groups_by_isin(self) -> None:
        rows = [row(source_ref="a", isin="US0000000001"), row(source_ref="b", isin="NL0000000001")]
        assert set(to_lot_transactions(rows)) == {"US0000000001", "NL0000000001"}

    def test_returns_each_instrument_chronologically(self) -> None:
        """`match_lots` raises on unordered input, so ordering is this layer's job."""
        rows = [
            row(source_ref="a", trade_date=date(2026, 5, 30), order_ref="o2"),
            row(source_ref="b", trade_date=date(2026, 5, 28), order_ref="o1"),
        ]
        fills = to_lot_transactions(rows)["US0000000001"]
        assert [f.trade_date for f in fills] == [date(2026, 5, 28), date(2026, 5, 30)]


class TestDeterminism:
    def test_shuffling_the_input_changes_nothing(self) -> None:
        """Sec 11.2 #5. The ledger has no inherent order: rows arrive in whatever
        order the export listed them and SQLite returns them in whatever order it
        likes. This is the layer that imposes one, so this is where the property
        has to hold -- `match_lots` downstream raises on unordered input rather
        than sorting, precisely so the responsibility cannot drift.
        """
        rows = [
            row(source_ref="a", trade_date=date(2026, 5, 28), order_ref="o1"),
            row(source_ref="b", trade_date=date(2026, 5, 29), order_ref="o2"),
            row(source_ref="c", trade_date=date(2026, 5, 28), order_ref="o1"),
            row(source_ref="d", trade_date=date(2026, 5, 30), order_ref="o3"),
        ]
        shuffled = list(rows)
        random.Random(11).shuffle(shuffled)
        assert to_lot_transactions(shuffled) == to_lot_transactions(rows)

    def test_two_fills_of_one_order_keep_a_stable_order(self) -> None:
        """Byte-identical fills exist (Sec 3.5). Ordering them by date alone would
        leave their relative position to chance, and HIFO would then pick between
        two equal-priced lots differently on different runs."""
        rows = [
            row(source_ref="b", trade_date=date(2026, 5, 28), order_ref="o1"),
            row(source_ref="a", trade_date=date(2026, 5, 28), order_ref="o1"),
        ]
        fills = to_lot_transactions(rows)["US0000000001"]
        assert [f.id for f in fills] == ["a", "b"]


class TestSelection:
    def test_skips_non_economic_rows(self) -> None:
        """What M0's quarantine bought. A split's legs look exactly like a buy and
        a sell; matching them would realise a profit that never happened."""
        rows = [row(source_ref="a"), row(source_ref="b", is_economic=False)]
        assert len(to_lot_transactions(rows)["US0000000001"]) == 1

    def test_skips_cash_rows_that_move_no_shares(self) -> None:
        """A dividend has no quantity. It belongs to M5, not to lot matching."""
        rows = [row(source_ref="a"), row(source_ref="b", txn_type="DIVIDEND", quantity=None)]
        assert len(to_lot_transactions(rows)["US0000000001"]) == 1

    def test_a_negative_quantity_becomes_a_sell_with_a_positive_quantity(self) -> None:
        """`match_lots` reads `side` and expects magnitudes."""
        fills = to_lot_transactions([row(quantity=D("-5"), value_base=D("50.00"))])
        fill = fills["US0000000001"][0]
        assert fill.side == "SELL"
        assert fill.quantity == D("5")

    def test_price_is_the_base_currency_price_not_the_local_one(self) -> None:
        """Everything downstream is EUR. `price_local` is USD on a US trade, and
        mixing the two produces a cost basis that looks right and is not."""
        fills = to_lot_transactions(
            [row(quantity=D("10"), price_local=D("100.00"), value_base=D("-655.30"))]
        )
        assert fills["US0000000001"][0].price == D("65.530")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/unit/test_orders.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.domain.orders'`

- [ ] **Step 3: Write minimal implementation**

Create `backend/app/domain/orders.py`:

```python
"""Ledger rows into economic orders, and orders into matchable fills.

Design doc Sec 6.4. DeGiro charges commission once per ORDER and books it on one
arbitrary fill row: the real export's CASTOR order is `+25` with a blank fee beside
`+75` charged EUR 2.00. Left where it landed, one fill carries a 2.67% cost and the
other none -- and a HIFO matcher choosing between them would be choosing on an
artefact of DeGiro's bookkeeping rather than on price.

So charges are pooled per order and split back across the fills pro rata by
quantity. `apportion_charges` makes the split exact, which is what lets the Sec 11.2
invariant hold as an equality rather than a tolerance.

Two conversions happen here and nowhere else:

* **Sign.** The ledger stores a charge as a debit (negative). The domain layer
  counts money paid (positive).
* **Currency.** `price_local` is USD on a US trade. Everything downstream is EUR, so
  the price handed to the matcher is `value_base / quantity` -- the euro amount the
  broker itself recorded, divided by the shares it bought. Deriving it from
  `price_local` and `fx_rate` instead would recompute a number the export already
  states, which Sec 5.4 forbids.

Pure: rows in, fills out. `LedgerRow` is a Protocol, so `domain/` imports no ORM.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Protocol

from app.domain.charges import Charges, apportion_charges
from app.domain.lots import LotTransaction

_ZERO = Decimal("0.00")


class LedgerRow(Protocol):
    """Exactly the fields this module reads off a `Transaction`.

    A Protocol rather than an import: `domain/` is pure by design (Sec 4.1), and a
    structural type keeps it that way while still type-checking against the ORM row
    the caller actually passes.
    """

    source_ref: str
    isin: str | None
    trade_date: date
    trade_time: str | None
    txn_type: str
    quantity: Decimal | None
    price_local: Decimal | None
    value_base: Decimal | None
    fee_base: Decimal
    autofx_fee_base: Decimal | None
    tax_base: Decimal
    order_ref: str | None
    is_economic: bool


def _order_key(row: LedgerRow) -> tuple[str, str, str]:
    """`(order_ref, trade_datetime, isin)` with a synthetic fallback (Sec 6.4).

    A blank order id falls back to the row's own date and time. Two blank-id rows at
    different times are two orders: pooling them would spread one trade's commission
    over another's fills.
    """
    isin = row.isin or ""
    stamp = f"{row.trade_date.isoformat()}T{row.trade_time or ''}"
    return (row.order_ref or f"synthetic:{stamp}:{isin}", stamp, isin)


def _charges_of(row: LedgerRow) -> Charges:
    """Ledger debits into money paid."""
    return Charges(
        commission=-row.fee_base,
        autofx=-(row.autofx_fee_base or _ZERO),
        tax=-row.tax_base,
    )


def _is_share_movement(row: LedgerRow) -> bool:
    return (
        row.is_economic
        and row.isin is not None
        and row.quantity is not None
        and row.quantity != 0
        and row.value_base is not None
    )


def to_lot_transactions(rows: Sequence[LedgerRow]) -> dict[str, list[LotTransaction]]:
    """Group `rows` into orders, attribute charges, and return fills per ISIN.

    Each list is chronological, because `match_lots` raises on unordered input
    rather than silently producing a plausible wrong basis.
    """
    orders: dict[tuple[str, str, str], list[LedgerRow]] = defaultdict(list)
    for row in rows:
        if _is_share_movement(row):
            orders[_order_key(row)].append(row)

    by_isin: dict[str, list[LotTransaction]] = defaultdict(list)
    for fills in orders.values():
        pooled = Charges.zero()
        for row in fills:
            pooled = pooled + _charges_of(row)

        # Quantity, not value: the two fills of one order are the same instrument at
        # nearly the same price, and quantity is the thing DeGiro's own per-order
        # commission is indifferent to.
        weights = [abs(row.quantity or _ZERO) for row in fills]
        for row, share in zip(fills, apportion_charges(pooled, weights), strict=True):
            quantity = row.quantity or _ZERO
            value = row.value_base or _ZERO
            by_isin[str(row.isin)].append(
                LotTransaction(
                    id=row.source_ref,
                    trade_date=row.trade_date,
                    side="BUY" if quantity > 0 else "SELL",
                    quantity=abs(quantity),
                    price=abs(value) / abs(quantity),
                    charges=share,
                )
            )

    return {
        isin: sorted(fills, key=lambda fill: (fill.trade_date, fill.id))
        for isin, fills in by_isin.items()
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/unit/test_orders.py -q`
Expected: PASS (14 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/domain/orders.py backend/tests/unit/test_orders.py
git commit -m "feat(domain): attribute charges at order level, not to the fill they landed on"
```

---

### Task 3: Derive the split ratio from the legs M0 suppressed

The answers file records a decision, not a ratio (§6.3, decided 2026-09-06). The ratio is already in the export: ORN's suppressed legs are −1 share out and +10 in.

**Files:**
- Create: `backend/app/domain/splits.py`
- Test: `backend/tests/unit/test_splits.py`

**Interfaces:**
- Consumes: `LedgerRow` (Task 2).
- Produces: `Split(isin: str, effective_on: date, ratio: Decimal)` frozen dataclass, and `derive_splits(rows: Sequence[LedgerRow]) -> list[Split]`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_splits.py`:

```python
"""Deriving a split's ratio from the ledger rows M0 flagged non-economic.

Design doc Sec 6.3, decided 2026-09-06: the answers file records a decision -- is
this an event or a trade -- and nothing more. The ratio is already stated by the
broker. ORN's two suppressed legs are 1 share out and 10 in; that is 10:1, and
asking the operator to retype it beside a pair that already says so invites a typo
no test can catch, because both numbers would look plausible.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.domain.splits import Split, derive_splits
from tests.unit.test_orders import row  # the shared LedgerRow stand-in

D = Decimal


class TestDerivation:
    def test_reads_ten_for_one_off_the_orion_shape(self) -> None:
        rows = [
            row(source_ref="a", quantity=D("-1"), value_base=D("845.60"), is_economic=False),
            row(source_ref="b", quantity=D("10"), value_base=D("-845.60"), is_economic=False),
        ]
        assert derive_splits(rows) == [
            Split(isin="US0000000001", effective_on=date(2026, 5, 28), ratio=D("10"))
        ]

    def test_reads_a_reverse_split(self) -> None:
        """1-for-10: ten shares out, one in. The ratio is a tenth, not ten."""
        rows = [
            row(source_ref="a", quantity=D("-10"), value_base=D("100.00"), is_economic=False),
            row(source_ref="b", quantity=D("1"), value_base=D("-100.00"), is_economic=False),
        ]
        assert derive_splits(rows)[0].ratio == D("0.1")

    def test_reads_a_three_for_two(self) -> None:
        rows = [
            row(source_ref="a", quantity=D("-2"), value_base=D("100.00"), is_economic=False),
            row(source_ref="b", quantity=D("3"), value_base=D("-100.00"), is_economic=False),
        ]
        assert derive_splits(rows)[0].ratio == D("1.5")


class TestSelection:
    def test_ignores_economic_rows(self) -> None:
        """A genuine same-day round trip with a real order id is the Sec 11.2 decoy.
        M0 already decided it is a trade; deriving a ratio from it would invent a
        split out of an ordinary buy and sell."""
        rows = [
            row(source_ref="a", quantity=D("-1"), value_base=D("100.00")),
            row(source_ref="b", quantity=D("10"), value_base=D("-100.00")),
        ]
        assert derive_splits(rows) == []

    def test_ignores_a_suppressed_pair_that_does_not_change_the_share_count(self) -> None:
        """A product change swaps one instrument for another at the same count. It
        is suppressed, but it is not a split and adjusting lots by 1.0 is a no-op
        that would still show up in the audit trail as an event."""
        rows = [
            row(source_ref="a", quantity=D("-100"), value_base=D("845.75"), is_economic=False),
            row(source_ref="b", quantity=D("100"), value_base=D("-845.75"), is_economic=False),
        ]
        assert derive_splits(rows) == []

    def test_ignores_an_unpaired_suppressed_row(self) -> None:
        rows = [row(source_ref="a", quantity=D("-1"), value_base=D("100.00"), is_economic=False)]
        assert derive_splits(rows) == []


class TestOrdering:
    def test_returns_splits_oldest_first(self) -> None:
        """They are applied in sequence; two splits on one instrument compound."""
        rows = [
            row(source_ref="a", quantity=D("-1"), value_base=D("10.00"),
                trade_date=date(2026, 6, 1), is_economic=False),
            row(source_ref="b", quantity=D("2"), value_base=D("-10.00"),
                trade_date=date(2026, 6, 1), is_economic=False),
            row(source_ref="c", quantity=D("-1"), value_base=D("10.00"),
                trade_date=date(2026, 5, 1), is_economic=False),
            row(source_ref="d", quantity=D("3"), value_base=D("-10.00"),
                trade_date=date(2026, 5, 1), is_economic=False),
        ]
        assert [s.effective_on for s in derive_splits(rows)] == [
            date(2026, 5, 1),
            date(2026, 6, 1),
        ]


class TestGuards:
    def test_a_zero_share_leg_raises_rather_than_dividing_by_zero(self) -> None:
        rows = [
            row(source_ref="a", quantity=D("-0"), value_base=D("0.00"), is_economic=False),
            row(source_ref="b", quantity=D("10"), value_base=D("0.00"), is_economic=False),
        ]
        with pytest.raises(ValueError, match="zero"):
            derive_splits(rows)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/unit/test_splits.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.domain.splits'`

- [ ] **Step 3: Write minimal implementation**

Create `backend/app/domain/splits.py`:

```python
"""Share splits, derived from the ledger rather than declared in a config file.

Design doc Sec 6.3, decided 2026-09-06. M0 already identified each corporate action
and flagged both its legs `is_economic=False`. Those two legs state the ratio: ORN
went out 1 share and came back 10, which is 10:1. Reading it from the export keeps
the ratio broker truth and keeps the answers file to decisions only.

A suppressed pair whose share count does not change is a product change, not a
split. It is skipped: adjusting every lot by a factor of one is a no-op that would
still appear in the audit trail as an event that happened.

Pure: rows in, splits out.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.domain.orders import LedgerRow

_ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class Split:
    """A change in share count that left cost basis untouched.

    `ratio` is new shares per old share: 10 for a 10-for-1, `0.1` for a 1-for-10
    reverse. A holding of `q` becomes `q * ratio` at a price of `price / ratio`.
    """

    isin: str
    effective_on: date
    ratio: Decimal


def derive_splits(rows: Sequence[LedgerRow]) -> list[Split]:
    """Read every split out of the corporate-action legs M0 suppressed.

    Oldest first: two splits on one instrument compound, and applying them out of
    order gives a different -- wrong -- share count.
    """
    groups: dict[tuple[str, date], list[LedgerRow]] = defaultdict(list)
    for row in rows:
        if row.is_economic or row.isin is None or row.quantity is None or row.quantity == 0:
            continue
        groups[(row.isin, row.trade_date)].append(row)

    splits: list[Split] = []
    for (isin, effective_on), legs in groups.items():
        out = sum((leg.quantity or _ZERO for leg in legs if (leg.quantity or _ZERO) < 0), _ZERO)
        into = sum((leg.quantity or _ZERO for leg in legs if (leg.quantity or _ZERO) > 0), _ZERO)
        if out == 0 or into == 0:
            if out == 0 and into != 0:
                raise ValueError(
                    f"{isin} on {effective_on}: a corporate action with zero shares "
                    "surrendered has no derivable ratio"
                )
            continue
        ratio = into / abs(out)
        # 1.0 is a product change: the instrument changed, the share count did not.
        if ratio == 1:
            continue
        splits.append(Split(isin=isin, effective_on=effective_on, ratio=ratio))

    return sorted(splits, key=lambda split: (split.effective_on, split.isin))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/unit/test_splits.py -q`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/domain/splits.py backend/tests/unit/test_splits.py
git commit -m "feat(domain): derive split ratios from the suppressed legs"
```

---

### Task 4: Apply splits to the lots opened before them

§7.1: "Splits adjust `open_qty`, `remaining_qty` and `open_price` on every open lot, leaving cost basis unchanged." Applying the adjustment to the *transactions* before matching achieves this and leaves `match_lots` untouched — quantity × price is invariant under the adjustment, so the basis cannot drift.

**Files:**
- Modify: `backend/app/domain/splits.py`
- Test: `backend/tests/unit/test_splits.py` (extend)

**Interfaces:**
- Consumes: `Split` (Task 3), `LotTransaction` (Task 1).
- Produces: `apply_splits(fills: Sequence[LotTransaction], splits: Sequence[Split]) -> list[LotTransaction]`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/unit/test_splits.py`:

```python
class TestApplication:
    def _fill(self, ref: str, day: int, quantity: str, price: str) -> LotTransaction:
        return LotTransaction(
            id=ref,
            trade_date=date(2024, day, 1),
            side="BUY",
            quantity=D(quantity),
            price=D(price),
            charges=Charges(commission=D("2.00")),
        )

    SPLIT = Split(isin="X", effective_on=date(2024, 6, 10), ratio=D("10"))

    def test_multiplies_the_quantity_of_a_lot_opened_before_it(self) -> None:
        [fill] = apply_splits([self._fill("a", 5, "1", "655.30")], [self.SPLIT])
        assert fill.quantity == D("10")

    def test_divides_the_price_by_the_same_ratio(self) -> None:
        [fill] = apply_splits([self._fill("a", 5, "1", "655.30")], [self.SPLIT])
        assert fill.price == D("65.530")

    def test_leaves_the_cost_basis_exactly_unchanged(self) -> None:
        """The property that makes this safe. Sec 7.1: a split changes how many
        pieces the holding is cut into, never what was paid for it."""
        original = self._fill("a", 5, "1", "655.30")
        [adjusted] = apply_splits([original], [self.SPLIT])
        assert adjusted.quantity * adjusted.price == original.quantity * original.price

    def test_leaves_charges_alone(self) -> None:
        """A split costs nothing. Scaling charges would invent a cost."""
        [fill] = apply_splits([self._fill("a", 5, "1", "655.30")], [self.SPLIT])
        assert fill.charges.commission == D("2.00")

    def test_does_not_touch_a_lot_opened_after_it(self) -> None:
        """The 2026 ORN purchases were made in post-split shares already."""
        [fill] = apply_splits([self._fill("a", 8, "10", "143.50")], [self.SPLIT])
        assert fill.quantity == D("10")
        assert fill.price == D("143.50")

    def test_does_not_touch_a_lot_opened_on_the_day_itself(self) -> None:
        """DeGiro books the adjustment on the split date, so a trade that same day
        is already in new shares. Adjusting it would double-count the split."""
        [fill] = apply_splits([self._fill("a", 6, "10", "90.666")], [self.SPLIT])
        assert fill.quantity == D("10")

    def test_two_splits_compound_in_order(self) -> None:
        splits = [
            Split(isin="X", effective_on=date(2024, 3, 1), ratio=D("2")),
            Split(isin="X", effective_on=date(2024, 6, 10), ratio=D("10")),
        ]
        [fill] = apply_splits([self._fill("a", 1, "1", "100.00")], splits)
        assert fill.quantity == D("20")
        assert fill.quantity * fill.price == D("100.00")

    def test_a_sale_before_the_split_is_adjusted_too(self) -> None:
        """Otherwise a pre-split sale of 1 share would try to consume 1 of the 10
        the same split created, and the position would end 9 shares too high."""
        sale = LotTransaction(
            id="s", trade_date=date(2024, 5, 1), side="SELL",
            quantity=D("1"), price=D("900.00"), charges=Charges.zero(),
        )
        [adjusted] = apply_splits([sale], [self.SPLIT])
        assert adjusted.quantity == D("10")
```

Add to that file's imports:

```python
from app.domain.charges import Charges
from app.domain.lots import LotTransaction
from app.domain.splits import Split, apply_splits, derive_splits
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/unit/test_splits.py -q`
Expected: FAIL with `ImportError: cannot import name 'apply_splits'`

- [ ] **Step 3: Write minimal implementation**

Append to `backend/app/domain/splits.py`:

```python
from dataclasses import replace

from app.domain.lots import LotTransaction


def apply_splits(
    fills: Sequence[LotTransaction], splits: Sequence[Split]
) -> list[LotTransaction]:
    """Restate pre-split fills in post-split shares.

    Sec 7.1 phrases this as adjusting open lots. Doing it to the transactions before
    matching gets the same result and leaves the matcher untouched, because
    `quantity * price` is invariant under the adjustment -- the cost basis cannot
    drift no matter how many splits compound.

    Strictly before, never on the day: DeGiro books the adjustment on the split date
    itself, so a trade that day is already denominated in new shares and scaling it
    would count the split twice.

    Sales are adjusted as well as buys. A pre-split sale of one old share is a sale
    of ten new ones; leaving it alone would consume one of the ten the split just
    created and leave the position nine shares too high.

    `splits` must be for this instrument only, oldest first -- `derive_splits`
    returns them that way and the caller filters by ISIN.
    """
    adjusted = list(fills)
    for split in splits:
        adjusted = [
            replace(
                fill,
                quantity=fill.quantity * split.ratio,
                price=fill.price / split.ratio,
            )
            if fill.trade_date < split.effective_on
            else fill
            for fill in adjusted
        ]
    return adjusted
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/unit/test_splits.py -q`
Expected: PASS (16 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/domain/splits.py backend/tests/unit/test_splits.py
git commit -m "feat(domain): restate pre-split lots in post-split shares"
```

---

### Task 5: The `lot` and `lot_closure` tables

**Files:**
- Modify: `backend/app/models/ledger.py`
- Test: `backend/tests/unit/test_models.py` (extend)

**Interfaces:**
- Produces: `Lot` and `LotClosure` SQLModel tables.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/unit/test_models.py`:

```python
class TestDerivedTables:
    """`lot` and `lot_closure` are NOT the ledger.

    They are a projection of it under one lot method, dropped and rewritten by every
    `rebuild()`. The append-only rule protects facts; these are conclusions, and a
    conclusion that cannot be recomputed from scratch is a conclusion nobody can
    check.
    """

    def test_a_lot_stores_its_basis_and_charges_apart(self) -> None:
        engine = create_engine_and_tables("sqlite://")
        with Session(engine) as session:
            session.add(
                Lot(
                    id=uuid4(),
                    method="FIFO",
                    isin="US0000000901",
                    source_ref="abc",
                    opened_on=date(2024, 5, 22),
                    quantity=Decimal("10"),
                    price=Decimal("65.530"),
                    cost_basis=Decimal("655.30"),
                    commission=Decimal("2.00"),
                    autofx=Decimal("2.18"),
                    tax=Decimal("0.00"),
                )
            )
            session.commit()
            lot = session.exec(select(Lot)).one()
        assert lot.cost_basis == Decimal("655.30")
        assert lot.commission + lot.autofx == Decimal("4.18")

    def test_decimals_survive_the_round_trip_exactly(self) -> None:
        """`DecimalString`, same as the ledger. A float column would make 65.530
        come back as 65.53000000000001."""
        engine = create_engine_and_tables("sqlite://")
        with Session(engine) as session:
            session.add(
                Lot(
                    id=uuid4(), method="FIFO", isin="X", source_ref="a",
                    opened_on=date(2024, 5, 22), quantity=Decimal("10"),
                    price=Decimal("65.530"), cost_basis=Decimal("655.30"),
                    commission=Decimal("0.00"), autofx=Decimal("0.00"), tax=Decimal("0.00"),
                )
            )
            session.commit()
            assert session.exec(select(Lot)).one().price == Decimal("65.530")

    def test_a_closure_stores_gross_and_net_apart(self) -> None:
        """The three numbers the owner asked for, persisted rather than recomputed
        at read time -- so the API cannot disagree with the rebuild that wrote it."""
        engine = create_engine_and_tables("sqlite://")
        with Session(engine) as session:
            session.add(
                LotClosure(
                    id=uuid4(), method="FIFO", isin="X", lot_source_ref="a",
                    sale_source_ref="b", opened_on=date(2024, 1, 1),
                    closed_on=date(2024, 6, 1), quantity=Decimal("5"),
                    open_price=Decimal("10.00"), close_price=Decimal("20.00"),
                    gross_pnl=Decimal("50.00"), commission=Decimal("5.00"),
                    autofx=Decimal("0.00"), tax=Decimal("0.00"), pnl=Decimal("45.00"),
                    holding_days=152,
                )
            )
            session.commit()
            closure = session.exec(select(LotClosure)).one()
        assert closure.gross_pnl - closure.commission == closure.pnl
```

Add `Lot` and `LotClosure` to that file's imports from `app.models.ledger`.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/unit/test_models.py -q`
Expected: FAIL with `ImportError: cannot import name 'Lot'`

- [ ] **Step 3: Write minimal implementation**

Add to `backend/app/models/ledger.py`, after `CorporateActionReview`:

```python
class Lot(SQLModel, table=True):
    """One open purchase lot under one lot method. Derived, not ledger.

    Rewritten wholesale by every `rebuild()`, which is why it carries `method`: FIFO,
    LIFO and HIFO produce different lots from identical rows, and a table that could
    not say which one it holds would be a number without a provenance -- exactly
    what Sec 9.2 exists to prevent.

    Charges are stored in three columns rather than one total, and separately from
    `cost_basis`, because Sec 6.4 says a lot must be able to report what the shares
    cost and what the broker charged as two different answers.
    """

    __tablename__ = "lot"
    __table_args__ = (UniqueConstraint("method", "source_ref", name="uq_lot_method_ref"),)

    id: UUID = Field(primary_key=True)
    method: str = Field(index=True)
    isin: str = Field(index=True)
    #: The opening transaction's `source_ref`. Ties a derived lot to the fact it came
    #: from without a foreign key into a table that gets rewritten.
    source_ref: str = Field(index=True)

    opened_on: date = Field(index=True)
    #: Post-split. `price` is likewise split-adjusted, so `quantity * price`
    #: still equals `cost_basis`.
    quantity: Decimal = Field(sa_column=Column(DecimalString(), nullable=False))
    price: Decimal = Field(sa_column=Column(DecimalString(), nullable=False))
    cost_basis: Decimal = Field(sa_column=Column(DecimalString(), nullable=False))

    commission: Decimal = Field(sa_column=Column(DecimalString(), nullable=False))
    autofx: Decimal = Field(sa_column=Column(DecimalString(), nullable=False))
    tax: Decimal = Field(sa_column=Column(DecimalString(), nullable=False))


class LotClosure(SQLModel, table=True):
    """One matched (lot, sale) pair under one lot method. Derived, not ledger."""

    __tablename__ = "lot_closure"

    id: UUID = Field(primary_key=True)
    method: str = Field(index=True)
    isin: str = Field(index=True)
    lot_source_ref: str = Field(index=True)
    sale_source_ref: str = Field(index=True)

    opened_on: date
    closed_on: date = Field(index=True)
    quantity: Decimal = Field(sa_column=Column(DecimalString(), nullable=False))
    open_price: Decimal = Field(sa_column=Column(DecimalString(), nullable=False))
    close_price: Decimal = Field(sa_column=Column(DecimalString(), nullable=False))

    #: What the stock did, before charges. Stored rather than recomputed at read
    #: time so the API cannot disagree with the rebuild that wrote it.
    gross_pnl: Decimal = Field(sa_column=Column(DecimalString(), nullable=False))
    commission: Decimal = Field(sa_column=Column(DecimalString(), nullable=False))
    autofx: Decimal = Field(sa_column=Column(DecimalString(), nullable=False))
    tax: Decimal = Field(sa_column=Column(DecimalString(), nullable=False))
    pnl: Decimal = Field(sa_column=Column(DecimalString(), nullable=False))

    holding_days: int
    #: Sec 7.1 requires both on every closure. Nullable because both are genuinely
    #: undefined in cases the matcher meets: a zero basis has no return, and a
    #: same-day round trip or a total loss has no annualised one. NULL is the honest
    #: answer there -- a zero would read as "no gain", which is a different claim.
    return_pct: Decimal | None = Field(default=None, sa_column=Column(DecimalString()))
    annualised_return: Decimal | None = Field(
        default=None, sa_column=Column(DecimalString())
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/unit/test_models.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/models/ledger.py backend/tests/unit/test_models.py
git commit -m "feat(models): add the derived lot and lot_closure tables"
```

---

### Task 6: `rebuild()` — deterministic, with the standing charge invariant

**Files:**
- Create: `backend/app/analytics/__init__.py`
- Create: `backend/app/analytics/rebuild.py`
- Modify: `backend/app/cli.py`
- Test: `backend/tests/integration/test_rebuild.py`

**Interfaces:**
- Consumes: `to_lot_transactions` (Task 2), `derive_splits`/`apply_splits` (Tasks 3–4), `match_lots` (Task 1), `Lot`/`LotClosure` (Task 5).
- Produces: `rebuild(engine: Engine, method: LotMethod) -> RebuildResult`, where `RebuildResult(method, lots, closures, charges_attributed, charges_in_ledger)`; and `ChargeMismatch(RuntimeError)`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/integration/test_rebuild.py`:

```python
"""Rebuilding lots from the ledger (design doc Sec 11.2 #5).

`rebuild()` is the claim that every derived number in this app can be recomputed
from the ledger alone. That claim is only worth something if it is checked, so the
determinism tests here are not decoration: they are the property.
"""

from __future__ import annotations

import shutil
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlmodel import Session, select

from app.analytics.rebuild import ChargeMismatch, rebuild
from app.db import create_engine_and_tables
from app.domain.charges import Charges
from app.ingest.importer import ensure_default_account, import_degiro_export
from app.models.ledger import Lot, LotClosure

GOLDEN = Path(__file__).parents[1] / "golden"

SPLIT_KEY = "NL0000000003:2025-01-17:100.00"
PRODUCT_CHANGE_KEY = "US0000000002:2025-01-18:100.00"

RESOLVE_BOTH = f"""\
resolutions:
  - key: {SPLIT_KEY}
    treatment: corporate_action
  - key: {PRODUCT_CHANGE_KEY}
    treatment: corporate_action
"""

D = Decimal


def _engine() -> Engine:
    return create_engine_and_tables("sqlite://")


@pytest.fixture
def loaded(tmp_path: Path) -> Engine:
    export = tmp_path / "degiro-export"
    export.mkdir()
    shutil.copy(GOLDEN / "degiro_transactions_golden.csv", export / "Transactions.csv")
    shutil.copy(GOLDEN / "degiro_account_golden.csv", export / "Account.csv")
    answers = tmp_path / "corporate_actions.yaml"
    answers.write_text(RESOLVE_BOTH, encoding="utf-8")

    engine = _engine()
    import_degiro_export(engine, export, ensure_default_account(engine), answers)
    return engine


class TestTheStandingInvariant:
    def test_attributed_charges_equal_ledger_charges(self, loaded: Engine) -> None:
        """Sec 11.2 #4, asserted in production code rather than only in tests. If
        apportionment ever loses a cent, the rebuild refuses rather than writing a
        set of lots whose fees do not add up to what was actually paid."""
        result = rebuild(loaded, "FIFO")
        assert result.charges_attributed == result.charges_in_ledger

    def test_a_charge_the_matcher_cannot_see_stops_the_rebuild(
        self, loaded: Engine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The guard has to fire on a real discrepancy, or it is decoration.

        Dropping one fill's charges during attribution is exactly the failure it
        exists to catch: apportionment silently loses a cost, every other number
        still looks plausible, and no reader would ever spot it.
        """
        import app.analytics.rebuild as rebuild_module

        real = rebuild_module.to_lot_transactions

        def lossy(rows):  # type: ignore[no-untyped-def]
            grouped = real(rows)
            for fills in grouped.values():
                if fills:
                    fills[0] = replace(fills[0], charges=Charges.zero())
                    break
            return grouped

        monkeypatch.setattr(rebuild_module, "to_lot_transactions", lossy)
        with pytest.raises(ChargeMismatch, match="nothing was written"):
            rebuild(loaded, "FIFO")

    def test_a_refused_rebuild_writes_nothing(self, loaded: Engine) -> None:
        """A half-written set of lots is worse than none: it looks complete."""
        with Session(loaded) as session:
            assert session.exec(select(Lot)).all() == []


class TestDeterminism:
    def test_rebuilding_twice_produces_identical_rows(self, loaded: Engine) -> None:
        first = rebuild(loaded, "FIFO")
        second = rebuild(loaded, "FIFO")
        assert first.lots == second.lots
        assert first.closures == second.closures

    def test_a_rebuild_replaces_rather_than_appends(self, loaded: Engine) -> None:
        rebuild(loaded, "FIFO")
        rebuild(loaded, "FIFO")
        with Session(loaded) as session:
            lots = session.exec(select(Lot).where(Lot.method == "FIFO")).all()
        assert len({lot.source_ref for lot in lots}) == len(lots)

    def test_the_three_methods_coexist(self, loaded: Engine) -> None:
        """Switching method must not destroy the other two, or the UI switcher
        would silently recompute the whole portfolio on every click."""
        for method in ("FIFO", "LIFO", "HIFO"):
            rebuild(loaded, method)
        with Session(loaded) as session:
            methods = {lot.method for lot in session.exec(select(Lot)).all()}
        assert methods == {"FIFO", "LIFO", "HIFO"}


class TestSuppression:
    def test_the_split_legs_never_become_lots(self, loaded: Engine) -> None:
        """The golden split pair is NL0000000003. Its legs are non-economic, so
        matching must not see them -- and the position must reflect the split."""
        rebuild(loaded, "FIFO")
        with Session(loaded) as session:
            lots = session.exec(select(Lot).where(Lot.isin == "NL0000000003")).all()
        # 10 bought pre-split at 10.00, restated by the golden 10:1 into 100 at 1.00.
        assert sum(lot.quantity for lot in lots) == D("100")
        assert sum(lot.cost_basis for lot in lots) == D("100.00")

    def test_a_closure_carries_gross_charges_and_net(self, loaded: Engine) -> None:
        rebuild(loaded, "FIFO")
        with Session(loaded) as session:
            closures = session.exec(select(LotClosure)).all()
        assert closures
        for closure in closures:
            assert closure.pnl == closure.gross_pnl - (
                closure.commission + closure.autofx + closure.tax
            )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/integration/test_rebuild.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.analytics'`

- [ ] **Step 3: Write minimal implementation**

Create `backend/app/analytics/__init__.py` (empty) and `backend/app/analytics/rebuild.py`:

```python
"""Recompute lots and closures from the ledger.

Design doc Sec 11.2 #5. `rebuild()` is the claim that every derived number in this
app can be recomputed from the ledger alone -- which is what makes a parser fix
propagate to realised P&L with no manual step, and what makes a disagreement
between two figures resolvable rather than a matter of opinion.

The claim is only worth something if it is checked, so this module does two things
beyond the arithmetic. It DELETES the derived rows for the method it is rebuilding
before writing new ones, so a rebuild is a replacement rather than an accumulation.
And it asserts `Sigma attributed charges == Sigma ledger charges` (Sec 11.2 #4)
before committing -- in production code, not only in tests, because the failure it
guards against is a cent lost in apportionment, which no reader would ever spot.

All arithmetic lives in `domain/`. This module reads rows, calls three pure passes
and writes the answers.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import Engine
from sqlmodel import Session, select

from app.domain.charges import Charges
from app.domain.lots import LotMethod, match_lots
from app.domain.orders import to_lot_transactions
from app.domain.splits import apply_splits, derive_splits
from app.models.ledger import Lot, LotClosure, Transaction

_ZERO = Decimal("0.00")


class ChargeMismatch(RuntimeError):
    """Attributed charges did not equal the ledger's. Nothing was written."""


@dataclass(frozen=True, slots=True)
class RebuildResult:
    method: LotMethod
    lots: int
    closures: int
    charges_attributed: Decimal
    charges_in_ledger: Decimal


def _ledger_charges(rows: list[Transaction]) -> Decimal:
    """What the broker actually took, on the rows lot matching is allowed to see.

    Non-economic rows are excluded on both sides of the invariant: a corporate
    action's legs carry no charge, and including them would compare a total that
    matching never saw.
    """
    total = _ZERO
    for row in rows:
        if not row.is_economic or row.quantity is None or row.isin is None:
            continue
        total += -row.fee_base - (row.autofx_fee_base or _ZERO) - row.tax_base
    return total


def rebuild(engine: Engine, method: LotMethod) -> RebuildResult:
    """Recompute `lot` and `lot_closure` for one method, replacing what is there."""
    with Session(engine) as session:
        rows = list(session.exec(select(Transaction)).all())

    splits = derive_splits(rows)
    fills_by_isin = to_lot_transactions(rows)

    lots: list[Lot] = []
    closures: list[LotClosure] = []
    attributed = _ZERO

    for isin in sorted(fills_by_isin):
        adjusted = apply_splits(
            fills_by_isin[isin], [s for s in splits if s.isin == isin]
        )
        result = match_lots(adjusted, method)

        for open_lot in result.open_lots:
            attributed += open_lot.charges.total
            lots.append(
                Lot(
                    id=uuid4(),
                    method=method,
                    isin=isin,
                    source_ref=open_lot.id,
                    opened_on=open_lot.opened_on,
                    quantity=open_lot.quantity,
                    price=open_lot.price,
                    cost_basis=open_lot.cost_basis,
                    commission=open_lot.charges.commission,
                    autofx=open_lot.charges.autofx,
                    tax=open_lot.charges.tax,
                )
            )

        for closure in result.closures:
            attributed += closure.charges.total
            closures.append(
                LotClosure(
                    id=uuid4(),
                    method=method,
                    isin=isin,
                    lot_source_ref=closure.lot_id,
                    sale_source_ref=closure.sale_id,
                    opened_on=closure.opened_on,
                    closed_on=closure.closed_on,
                    quantity=closure.quantity,
                    open_price=closure.open_price,
                    close_price=closure.close_price,
                    gross_pnl=closure.gross_pnl,
                    commission=closure.charges.commission,
                    autofx=closure.charges.autofx,
                    tax=closure.charges.tax,
                    pnl=closure.pnl,
                    holding_days=closure.holding_days,
                    return_pct=closure.return_pct,
                    annualised_return=closure.annualised_return,
                )
            )

    in_ledger = _ledger_charges(rows)
    if attributed != in_ledger:
        raise ChargeMismatch(
            f"attributed {attributed} but the ledger holds {in_ledger}; "
            "nothing was written"
        )

    with Session(engine) as session:
        for stale_lot in session.exec(select(Lot).where(Lot.method == method)).all():
            session.delete(stale_lot)
        for stale in session.exec(
            select(LotClosure).where(LotClosure.method == method)
        ).all():
            session.delete(stale)
        session.flush()
        for lot in lots:
            session.add(lot)
        for closure in closures:
            session.add(closure)
        session.commit()

    return RebuildResult(
        method=method,
        lots=len(lots),
        closures=len(closures),
        charges_attributed=attributed,
        charges_in_ledger=in_ledger,
    )
```

Add a CLI command in `backend/app/cli.py`:

```python
@app.command("rebuild")
def rebuild_command(
    method: Annotated[
        str, typer.Option("--method", help="FIFO, LIFO or HIFO. Defaults to the configured one.")
    ] = "",
) -> None:
    """Recompute lots and closures from the ledger (design doc Sec 11.2).

    Derived tables only -- the ledger is untouched. Exits non-zero if attributed
    charges do not equal the ledger's, having written nothing.
    """
    settings = get_settings()
    chosen = (method or settings.lot_method).upper()
    if chosen not in LOT_METHODS:
        typer.echo(f"unknown method {chosen!r}; expected one of {list(LOT_METHODS)}", err=True)
        raise typer.Exit(code=2)

    engine = create_engine_and_tables(settings.database_url)
    try:
        result = rebuild(engine, chosen)  # type: ignore[arg-type]
    except ChargeMismatch as mismatch:
        typer.echo(str(mismatch), err=True)
        raise typer.Exit(code=1) from mismatch

    typer.echo(
        f"{result.method}: {result.lots} open lots, {result.closures} closures, "
        f"charges {result.charges_attributed} == ledger {result.charges_in_ledger}"
    )
```

with imports `from app.analytics.rebuild import ChargeMismatch, rebuild` and
`from app.domain.lots import LOT_METHODS`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/integration/test_rebuild.py -q`
Expected: PASS

Then the whole suite plus the gates:

Run: `cd backend && python -m pytest -q && python -m ruff check . && python -m mypy app`
Expected: all green

- [ ] **Step 5: Commit**

```bash
git add backend/app/analytics backend/app/cli.py backend/tests/integration/test_rebuild.py
git commit -m "feat(analytics): rebuild lots from the ledger, refusing on a charge mismatch"
```

---

### Task 7: The lots API and the method switcher

**Files:**
- Create: `backend/app/api/routes_lots.py`
- Modify: `backend/app/api/schemas.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/integration/test_lots_api.py`

**Interfaces:**
- Consumes: `Lot`, `LotClosure` (Task 5); `Provenance`, `LotMethod`, `Coverage` (existing `schemas.py`).
- Produces: `GET /api/lots?method=FIFO[&isin=]` → `LotPage`; `GET /api/closures?method=FIFO[&isin=]` → `ClosurePage`. Both envelopes carry `method` and `coverage`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/integration/test_lots_api.py`:

```python
"""The lot endpoints (design doc Sec 9.2).

The point of these is `method`. A realised figure without the method that produced
it is not a number, it is an opinion -- FIFO, LIFO and HIFO give three different
answers from identical rows -- so the envelope carries it and the response is the
only place the UI may learn it from.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.analytics.rebuild import rebuild
from app.db import create_engine_and_tables
from app.ingest.importer import ensure_default_account, import_degiro_export
from app.main import create_app

GOLDEN = Path(__file__).parents[1] / "golden"

RESOLVE_BOTH = """\
resolutions:
  - key: NL0000000003:2025-01-17:100.00
    treatment: corporate_action
  - key: US0000000002:2025-01-18:100.00
    treatment: corporate_action
"""


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    export = tmp_path / "degiro-export"
    export.mkdir()
    shutil.copy(GOLDEN / "degiro_transactions_golden.csv", export / "Transactions.csv")
    shutil.copy(GOLDEN / "degiro_account_golden.csv", export / "Account.csv")
    answers = tmp_path / "corporate_actions.yaml"
    answers.write_text(RESOLVE_BOTH, encoding="utf-8")

    engine: Engine = create_engine_and_tables("sqlite://")
    import_degiro_export(engine, export, ensure_default_account(engine), answers)
    for method in ("FIFO", "LIFO", "HIFO"):
        rebuild(engine, method)
    return TestClient(create_app(engine=engine))


class TestProvenance:
    def test_the_envelope_names_the_method_that_produced_the_numbers(
        self, client: TestClient
    ) -> None:
        body = client.get("/api/lots", params={"method": "HIFO"}).json()
        assert body["method"] == "HIFO"
        assert body["coverage"] == "full"

    def test_an_unknown_method_is_rejected_rather_than_defaulted(
        self, client: TestClient
    ) -> None:
        """Defaulting would answer a question the caller did not ask, under a label
        saying it did."""
        assert client.get("/api/lots", params={"method": "AVERAGE"}).status_code == 422


class TestLots:
    def test_returns_only_the_requested_method(self, client: TestClient) -> None:
        body = client.get("/api/lots", params={"method": "FIFO"}).json()
        assert body["items"]
        assert all(item["method"] == "FIFO" for item in body["items"])

    def test_money_crosses_the_wire_as_strings(self, client: TestClient) -> None:
        """JSON numbers are IEEE doubles. Serialising a Decimal as one reintroduces
        exactly the drift the storage layer prevents."""
        item = client.get("/api/lots", params={"method": "FIFO"}).json()["items"][0]
        assert isinstance(item["cost_basis"], str)
        assert isinstance(item["quantity"], str)

    def test_charges_are_reported_apart_from_the_basis(self, client: TestClient) -> None:
        item = client.get("/api/lots", params={"method": "FIFO"}).json()["items"][0]
        assert {"commission", "autofx", "tax"} <= set(item)
        assert "cost_basis" in item

    def test_filters_by_isin(self, client: TestClient) -> None:
        body = client.get(
            "/api/lots", params={"method": "FIFO", "isin": "NL0000000003"}
        ).json()
        assert {item["isin"] for item in body["items"]} == {"NL0000000003"}


class TestClosures:
    def test_reports_gross_charges_and_net(self, client: TestClient) -> None:
        """The three numbers the owner asked for, straight from the rebuild."""
        items = client.get("/api/closures", params={"method": "FIFO"}).json()["items"]
        assert items
        for item in items:
            assert {"gross_pnl", "commission", "autofx", "tax", "pnl"} <= set(item)

    def test_the_three_methods_give_different_answers(self, client: TestClient) -> None:
        """If they did not, the switcher would be decoration."""
        totals = {}
        for method in ("FIFO", "LIFO", "HIFO"):
            items = client.get("/api/closures", params={"method": method}).json()["items"]
            totals[method] = sorted(item["pnl"] for item in items)
        assert len({tuple(v) for v in totals.values()}) > 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/integration/test_lots_api.py -q`
Expected: FAIL — 404 on `/api/lots`

- [ ] **Step 3: Write minimal implementation**

Add to `backend/app/api/schemas.py`:

```python
class LotOut(BaseModel):
    id: str
    method: str
    isin: str
    source_ref: str
    opened_on: date
    quantity: Decimal
    price: Decimal
    cost_basis: Decimal
    commission: Decimal
    autofx: Decimal
    tax: Decimal

    @field_serializer("quantity", "price", "cost_basis", "commission", "autofx", "tax")
    def _decimal_as_string(self, value: Decimal) -> str:
        return str(value)

    @classmethod
    def from_model(cls, lot: "Lot") -> "LotOut":
        return cls(
            id=str(lot.id),
            method=lot.method,
            isin=lot.isin,
            source_ref=lot.source_ref,
            opened_on=lot.opened_on,
            quantity=lot.quantity,
            price=lot.price,
            cost_basis=lot.cost_basis,
            commission=lot.commission,
            autofx=lot.autofx,
            tax=lot.tax,
        )


class LotPage(Provenance):
    items: list[LotOut]
    total: int


class ClosureOut(BaseModel):
    id: str
    method: str
    isin: str
    lot_source_ref: str
    opened_on: date
    closed_on: date
    quantity: Decimal
    open_price: Decimal
    close_price: Decimal
    gross_pnl: Decimal
    commission: Decimal
    autofx: Decimal
    tax: Decimal
    pnl: Decimal
    holding_days: int
    return_pct: Decimal | None
    annualised_return: Decimal | None

    @field_serializer(
        "quantity", "open_price", "close_price", "gross_pnl",
        "commission", "autofx", "tax", "pnl",
    )
    def _decimal_as_string(self, value: Decimal) -> str:
        return str(value)

    @field_serializer("return_pct", "annualised_return")
    def _optional_decimal_as_string(self, value: Decimal | None) -> str | None:
        """`None` stays `None`, never becomes "0". A same-day round trip has no
        annualised return; reporting zero would claim it broke even."""
        return None if value is None else str(value)

    @classmethod
    def from_model(cls, closure: "LotClosure") -> "ClosureOut":
        return cls(
            id=str(closure.id),
            method=closure.method,
            isin=closure.isin,
            lot_source_ref=closure.lot_source_ref,
            opened_on=closure.opened_on,
            closed_on=closure.closed_on,
            quantity=closure.quantity,
            open_price=closure.open_price,
            close_price=closure.close_price,
            gross_pnl=closure.gross_pnl,
            commission=closure.commission,
            autofx=closure.autofx,
            tax=closure.tax,
            pnl=closure.pnl,
            holding_days=closure.holding_days,
            return_pct=closure.return_pct,
            annualised_return=closure.annualised_return,
        )


class ClosurePage(Provenance):
    items: list[ClosureOut]
    total: int
```

with `from app.models.ledger import Lot, LotClosure, Transaction` at the top.

Create `backend/app/api/routes_lots.py`:

```python
"""Lot and closure endpoints.

`method` is a required query parameter, not a defaulted one. FIFO, LIFO and HIFO
produce three different realised figures from identical rows (design doc Sec 7.1),
so answering without being asked which one would put a number under a label that
did not earn it -- the failure Sec 9.2 makes structurally impossible in the
response envelope.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Engine, func
from sqlmodel import Session, select

from app.api.routes_transactions import get_engine
from app.api.schemas import ClosureOut, ClosurePage, LotMethod, LotOut, LotPage
from app.models.ledger import Lot, LotClosure

router = APIRouter(prefix="/api", tags=["lots"])


@router.get("/lots", response_model=LotPage)
def list_lots(
    method: LotMethod,
    engine: Engine = Depends(get_engine),
    isin: str | None = None,
    limit: int = Query(default=500, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
) -> LotPage:
    with Session(engine) as session:
        count_stmt = select(func.count()).select_from(Lot).where(Lot.method == method)
        page_stmt = (
            select(Lot)
            .where(Lot.method == method)
            .order_by(Lot.isin, Lot.opened_on, Lot.source_ref)  # type: ignore[arg-type]
        )
        if isin:
            count_stmt = count_stmt.where(Lot.isin == isin)
            page_stmt = page_stmt.where(Lot.isin == isin)

        total = session.exec(count_stmt).one()
        rows = session.exec(page_stmt.limit(limit).offset(offset)).all()

    return LotPage(
        items=[LotOut.from_model(row) for row in rows],
        total=total,
        method=method,
        # Every lot came from the ledger and nothing here depends on an external
        # series that could be missing. Market value, which does, is M2.
        coverage="full",
    )


@router.get("/closures", response_model=ClosurePage)
def list_closures(
    method: LotMethod,
    engine: Engine = Depends(get_engine),
    isin: str | None = None,
    limit: int = Query(default=500, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
) -> ClosurePage:
    with Session(engine) as session:
        count_stmt = (
            select(func.count()).select_from(LotClosure).where(LotClosure.method == method)
        )
        page_stmt = (
            select(LotClosure)
            .where(LotClosure.method == method)
            .order_by(LotClosure.closed_on.desc(), LotClosure.id)  # type: ignore[attr-defined]
        )
        if isin:
            count_stmt = count_stmt.where(LotClosure.isin == isin)
            page_stmt = page_stmt.where(LotClosure.isin == isin)

        total = session.exec(count_stmt).one()
        rows = session.exec(page_stmt.limit(limit).offset(offset)).all()

    return ClosurePage(
        items=[ClosureOut.from_model(row) for row in rows],
        total=total,
        method=method,
        coverage="full",
    )
```

Register it in `backend/app/main.py`:

```python
from app.api import routes_lots, routes_transactions
...
    app.include_router(routes_transactions.router)
    app.include_router(routes_lots.router)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/integration/test_lots_api.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/api backend/app/main.py backend/tests/integration/test_lots_api.py
git commit -m "feat(api): serve lots and closures, with the method in the envelope"
```

---

### Task 8: The frontend lot table and a working method switcher

The switcher in the header currently re-runs the modelled fixture matcher. This points it at the API. Market value stays absent — it needs prices, which is M2.

**Files:**
- Modify: `frontend/src/api/types.ts`
- Modify: `frontend/src/api/client.ts`
- Create: `frontend/src/screens/Lots.tsx`
- Modify: `frontend/src/navigation.ts`
- Modify: `frontend/src/App.tsx`
- Test: `frontend/src/api/lots.test.ts`

**Interfaces:**
- Consumes: `GET /api/lots`, `GET /api/closures` (Task 7).
- Produces: `fetchLots(method, opts)`, `fetchClosures(method, opts)`; `Lot`, `LotPage`, `Closure`, `ClosurePage` types.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/api/lots.test.ts`:

```typescript
import { describe, expect, it, vi, afterEach } from "vitest";
import { fetchClosures, fetchLots } from "./client";

afterEach(() => vi.unstubAllGlobals());

function stub(body: unknown) {
  const json = vi.fn().mockResolvedValue(body);
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, json });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("fetchLots", () => {
  it("sends the method the caller asked for", async () => {
    const fetchMock = stub({ items: [], total: 0, method: "HIFO", coverage: "full" });
    await fetchLots("HIFO");
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain("method=HIFO");
  });

  it("surfaces a failed response rather than returning an empty page", async () => {
    // An empty page and a dead API look identical on screen; one is a fact and
    // the other is a bug, so they must not render the same.
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: false, status: 500, statusText: "Server Error" }),
    );
    await expect(fetchLots("FIFO")).rejects.toThrow(/500/);
  });

  it("passes an isin filter through", async () => {
    const fetchMock = stub({ items: [], total: 0, method: "FIFO", coverage: "full" });
    await fetchLots("FIFO", { isin: "US0000000901" });
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain("isin=US0000000901");
  });
});

describe("fetchClosures", () => {
  it("hits the closures endpoint", async () => {
    const fetchMock = stub({ items: [], total: 0, method: "FIFO", coverage: "full" });
    await fetchClosures("FIFO");
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain("/api/closures");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/api/lots.test.ts`
Expected: FAIL — `fetchLots` is not exported

- [ ] **Step 3: Write minimal implementation**

Add to `frontend/src/api/types.ts`:

```typescript
/** Money is a string for the same reason it is on TransactionOut: a JSON number
 *  is an IEEE double, and rounding a cost basis to one loses the exactness the
 *  backend's Decimal storage exists to keep. */
export interface Lot {
  id: string;
  method: LotMethod;
  isin: string;
  source_ref: string;
  opened_on: string;
  quantity: string;
  price: string;
  cost_basis: string;
  commission: string;
  autofx: string;
  tax: string;
}

export interface LotPage extends Provenance {
  items: Lot[];
  total: number;
}

export interface Closure {
  id: string;
  method: LotMethod;
  isin: string;
  lot_source_ref: string;
  opened_on: string;
  closed_on: string;
  quantity: string;
  open_price: string;
  close_price: string;
  gross_pnl: string;
  commission: string;
  autofx: string;
  tax: string;
  pnl: string;
  holding_days: number;
  /** `null` where the figure is genuinely undefined -- a zero basis has no return,
   *  a same-day round trip has no annualised one. Render "—", never "0%". */
  return_pct: string | null;
  annualised_return: string | null;
}

export interface ClosurePage extends Provenance {
  items: Closure[];
  total: number;
}
```

Add to `frontend/src/api/client.ts`:

```typescript
export interface LotQuery {
  isin?: string;
  limit?: number;
  offset?: number;
}

async function getPage<T>(path: string, method: LotMethod, query: LotQuery): Promise<T> {
  const { isin, limit = 500, offset = 0 } = query;
  const params = new URLSearchParams({
    method,
    limit: String(limit),
    offset: String(offset),
  });
  if (isin) params.set("isin", isin);

  const response = await fetch(`${BASE}${path}?${params}`);
  if (!response.ok) {
    throw new Error(`Failed to load ${path}: ${response.status} ${response.statusText}`);
  }
  return (await response.json()) as T;
}

export function fetchLots(method: LotMethod, query: LotQuery = {}): Promise<LotPage> {
  return getPage<LotPage>("/api/lots", method, query);
}

export function fetchClosures(method: LotMethod, query: LotQuery = {}): Promise<ClosurePage> {
  return getPage<ClosurePage>("/api/closures", method, query);
}
```

with `import type { ClosurePage, LotMethod, LotPage, TransactionPage } from "./types";`

Create `frontend/src/screens/Lots.tsx`:

```tsx
/** Open lots and closures, straight from the ledger. The second screen reading
 *  real data.
 *
 *  No market value here, and that is deliberate: a position's worth needs a price
 *  series, which is M2. Putting a cost basis under a heading that implied market
 *  value would be exactly the "plausible guess" the Transactions screen refuses
 *  to make.
 *
 *  Charges are three columns, not one. That is the point of Sec 6.4 -- a
 *  commission and an FX cost are different things to have paid.
 */

import { useEffect, useState } from "react";
import { fetchClosures, fetchLots } from "../api/client";
import type { ClosurePage, LotMethod, LotPage } from "../api/types";
import { MethodBadge } from "../components/ui/MethodBadge";
import { Notice } from "../components/ui/Notice";
import {
  HeadRow,
  Table,
  TableFrame,
  Td,
  rowBackground,
  type ColumnDef,
} from "../components/ui/Table";
import { c, mono } from "../lib/theme";
import { decimal, decimalEur, decimalIsNegative, shortDate } from "../lib/format";

const LOT_COLUMNS: readonly ColumnDef[] = [
  { label: "ISIN" },
  { label: "OPENED" },
  { label: "QTY", align: "right" },
  { label: "PRICE", align: "right" },
  { label: "COST BASIS", align: "right" },
  { label: "COMMISSION", align: "right" },
  { label: "FX COST", align: "right" },
  { label: "TAX", align: "right" },
];

const CLOSURE_COLUMNS: readonly ColumnDef[] = [
  { label: "ISIN" },
  { label: "OPENED" },
  { label: "CLOSED" },
  { label: "QTY", align: "right" },
  { label: "OPEN", align: "right" },
  { label: "CLOSE", align: "right" },
  { label: "GROSS P&L", align: "right" },
  { label: "CHARGES", align: "right" },
  { label: "NET P&L", align: "right" },
  { label: "RETURN", align: "right" },
  { label: "DAYS", align: "right" },
];

/** A ratio held as a decimal string, rendered as a percentage.
 *
 *  `null` renders an em-dash, never "0%": a same-day round trip has no annualised
 *  return, and a zero there would claim it broke even. This is the one place a
 *  ledger-sourced string is parsed, and it is safe because the result decides a
 *  label rather than a figure -- see `api/types.ts`.
 */
function percent(value: string | null): string {
  if (value === null) return "\u2014";
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return "\u2014";
  return `${(parsed * 100).toFixed(2)}%`;
}

export interface LotsProps {
  method: LotMethod;
}

export function Lots({ method }: LotsProps) {
  const [lots, setLots] = useState<LotPage | null>(null);
  const [closures, setClosures] = useState<ClosurePage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    Promise.all([fetchLots(method), fetchClosures(method)])
      .then(([lotPage, closurePage]) => {
        if (cancelled) return;
        setLots(lotPage);
        setClosures(closurePage);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    // Re-fetches whenever the method changes. The guard also stops a slow FIFO
    // response landing after a fast HIFO one and being labelled HIFO.
    return () => {
      cancelled = true;
    };
  }, [method]);

  if (loading) {
    return <div style={{ fontSize: 12, color: c.textMuted }}>Loading lots…</div>;
  }

  if (error) {
    return (
      <Notice tone="danger">
        <span style={{ fontFamily: mono, color: c.negative }}>Could not reach the API</span>{" "}
        — {error}
      </Notice>
    );
  }

  if (!lots?.items.length) {
    return (
      <Notice tone="neutral">
        No lots yet. Import an export, then run{" "}
        <span style={{ fontFamily: mono }}>
          python -m app.cli rebuild --method {method}
        </span>
        .
      </Notice>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 11 }}>
        {/* Provenance from the response envelope, never from local state: the
            screen reports what the API said it computed. */}
        <MethodBadge method={lots.method} coverage={lots.coverage} />
        <span style={{ color: c.textMuted }}>
          {lots.total} open lots · {closures?.total ?? 0} closures
        </span>
      </div>

      <Notice>
        Cost basis is what the shares cost. Commission, FX cost and tax sit beside it, never
        inside it, so a realised figure separates what the stock did from what the broker took.
        Market value arrives with prices in M2.
      </Notice>

      <TableFrame>
        <Table>
          <HeadRow columns={LOT_COLUMNS} />
          <tbody>
            {lots.items.map((lot, i) => (
              <tr key={lot.id} style={{ background: rowBackground(i) }}>
                <Td padding="8px 11px" nowrap>
                  {lot.isin}
                </Td>
                <Td padding="8px 11px" nowrap>
                  {shortDate(lot.opened_on)}
                </Td>
                <Td padding="8px 11px" align="right" numeric>
                  {decimal(lot.quantity)}
                </Td>
                <Td padding="8px 11px" align="right" numeric>
                  {decimal(lot.price, 2)}
                </Td>
                <Td padding="8px 11px" align="right" numeric>
                  {decimalEur(lot.cost_basis)}
                </Td>
                <Td padding="8px 11px" align="right" numeric color={c.textMuted}>
                  {decimalEur(lot.commission)}
                </Td>
                <Td padding="8px 11px" align="right" numeric color={c.textMuted}>
                  {decimalEur(lot.autofx)}
                </Td>
                <Td padding="8px 11px" align="right" numeric color={c.textMuted}>
                  {decimalEur(lot.tax)}
                </Td>
              </tr>
            ))}
          </tbody>
        </Table>
      </TableFrame>

      <TableFrame>
        <Table>
          <HeadRow columns={CLOSURE_COLUMNS} />
          <tbody>
            {(closures?.items ?? []).map((closure, i) => (
              <tr key={closure.id} style={{ background: rowBackground(i) }}>
                <Td padding="8px 11px" nowrap>
                  {closure.isin}
                </Td>
                <Td padding="8px 11px" nowrap>
                  {shortDate(closure.opened_on)}
                </Td>
                <Td padding="8px 11px" nowrap>
                  {shortDate(closure.closed_on)}
                </Td>
                <Td padding="8px 11px" align="right" numeric>
                  {decimal(closure.quantity)}
                </Td>
                <Td padding="8px 11px" align="right" numeric>
                  {decimal(closure.open_price, 2)}
                </Td>
                <Td padding="8px 11px" align="right" numeric>
                  {decimal(closure.close_price, 2)}
                </Td>
                <Td
                  padding="8px 11px"
                  align="right"
                  numeric
                  color={decimalIsNegative(closure.gross_pnl) ? c.negative : c.positive}
                >
                  {decimalEur(closure.gross_pnl)}
                </Td>
                <Td padding="8px 11px" align="right" numeric color={c.textMuted}>
                  {decimalEur(closure.commission)}
                </Td>
                <Td
                  padding="8px 11px"
                  align="right"
                  numeric
                  color={decimalIsNegative(closure.pnl) ? c.negative : c.positive}
                >
                  {decimalEur(closure.pnl)}
                </Td>
                <Td padding="8px 11px" align="right" numeric>
                  {percent(closure.return_pct)}
                </Td>
                <Td padding="8px 11px" align="right" numeric>
                  {closure.holding_days}
                </Td>
              </tr>
            ))}
          </tbody>
        </Table>
      </TableFrame>
    </div>
  );
}
```

Add the screen to `frontend/src/navigation.ts` alongside the existing entries, with
id `"lots"`, label `"Lots"` and subtitle `"Cost basis and realised P&L"`, and
dispatch it in `frontend/src/App.tsx` where the other screens are:

```tsx
      {tab === "lots" && <Lots method={method} />}
```

Pass `App.tsx`'s existing method state down rather than adding a second one. Two
sources of truth for the selected method would let the header switcher and this
screen disagree about which one is on screen -- and the number on screen would be
right for a method the header was not showing.

- [ ] **Step 4: Run the tests**

Run: `cd frontend && npx vitest run && npx tsc --noEmit`
Expected: PASS, no type errors

- [ ] **Step 5: Commit**

```bash
git add frontend/src
git commit -m "feat(ui): show lots and closures from the live ledger, per method"
```

---

### Task 9: The real-data acceptance — ORN = 32

**Files:**
- Create: `backend/tests/integration/test_realdata_lots.py`

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/integration/test_realdata_lots.py`:

```python
"""Opt-in suite: M1's definition of done against the owner's real exports.

Design doc Sec 10 states M1's outcome as one number -- ORN = 32 shares matching
Portfolio.csv. It is the right number to be judged on because nothing else in the
pipeline can be wrong while it is right: the split has to be detected, suppressed,
derived and applied, and the ordinary trades around it left alone.

Never runs in CI: the exports are gitignored, so these tests skip themselves when
the directory is absent.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlmodel import Session, select

from app.analytics.rebuild import rebuild
from app.db import create_engine_and_tables
from app.ingest.degiro.portfolio_csv import parse_portfolio_csv
from app.ingest.importer import ensure_default_account, import_degiro_export
from app.models.ledger import Lot, LotClosure

EXPORT = Path(__file__).parents[3] / "degiro-export"
ANSWERS = Path(__file__).parents[3] / "config" / "corporate_actions.yaml"

pytestmark = [
    pytest.mark.realdata,
    pytest.mark.skipif(
        not ((EXPORT / "Transactions.csv").exists() and ANSWERS.exists()),
        reason="real DeGiro export or answers file not present",
    ),
]

ORION = "US0000000901"
D = Decimal


@pytest.fixture(scope="module")
def rebuilt() -> Engine:
    engine = create_engine_and_tables("sqlite://")
    import_degiro_export(engine, EXPORT, ensure_default_account(engine), ANSWERS)
    rebuild(engine, "FIFO")
    return engine


def _lots(engine: Engine, isin: str) -> list[Lot]:
    with Session(engine) as session:
        return list(session.exec(select(Lot).where(Lot.isin == isin)).all())


def test_orion_holds_thirty_two_shares(rebuilt: Engine) -> None:
    """M1's headline. 23 shares were bought; the other 9 are the 10-for-1 split
    applied to the single share held on 2025-02-18."""
    held = sum((lot.quantity for lot in _lots(rebuilt, ORION)), D("0"))
    assert held == parse_portfolio_csv(EXPORT / "Portfolio.csv").quantity_of(ORION)
    assert held == D("32")


def test_the_pre_split_lot_was_restated_not_repriced(rebuilt: Engine) -> None:
    """Sec 11.2 #1: the 2025-01-30 lot, cost basis EUR 655.30, 10 shares after the
    split. The basis is the trade value with charges excluded (Sec 6.4)."""
    lot = next(lot for lot in _lots(rebuilt, ORION) if lot.opened_on.isoformat() == "2025-01-30")
    assert lot.quantity == D("10")
    assert lot.cost_basis == D("655.30")
    assert lot.price == D("65.530")


def test_the_split_realised_nothing(rebuilt: Engine) -> None:
    """A split is not a sale. If its legs had been matched, 2025-02-18 would carry
    a realised profit that never happened."""
    with Session(rebuilt) as session:
        closures = session.exec(
            select(LotClosure).where(LotClosure.isin == ORION)
        ).all()
    assert [c for c in closures if c.closed_on.isoformat() == "2025-02-18"] == []


def test_the_charges_on_that_lot_are_visible_and_not_in_the_basis(rebuilt: Engine) -> None:
    """EUR 2.00 commission and EUR 2.18 of FX cost, reportable separately -- which
    is the whole reason Sec 6.4 stopped capitalising them."""
    lot = next(lot for lot in _lots(rebuilt, ORION) if lot.opened_on.isoformat() == "2025-01-30")
    assert lot.commission == D("2.00")
    assert lot.autofx == D("2.18")
    assert lot.cost_basis == D("655.30")


def test_every_position_matches_the_brokers_own_statement(rebuilt: Engine) -> None:
    """Not just ORN. Portfolio.csv states six positions; all six must agree, or
    the split logic happens to be right about one instrument by luck."""
    snapshot = parse_portfolio_csv(EXPORT / "Portfolio.csv")
    for position in snapshot.positions:
        held = sum((lot.quantity for lot in _lots(rebuilt, position.isin)), D("0"))
        assert held == snapshot.quantity_of(position.isin), position.isin


def test_the_standing_charge_invariant_holds_on_real_data(rebuilt: Engine) -> None:
    """Sec 11.2 #4 across 112 real trades, 105 order ids and four currencies."""
    result = rebuild(rebuilt, "FIFO")
    assert result.charges_attributed == result.charges_in_ledger


@pytest.mark.parametrize("method", ["FIFO", "LIFO", "HIFO"])
def test_every_method_holds_the_same_shares(rebuilt: Engine, method: str) -> None:
    """Method changes which lots a sale consumed and therefore realised P&L. It
    cannot change how many shares are left."""
    rebuild(rebuilt, method)  # type: ignore[arg-type]
    with Session(rebuilt) as session:
        lots = session.exec(
            select(Lot).where(Lot.method == method, Lot.isin == ORION)
        ).all()
    assert sum((lot.quantity for lot in lots), D("0")) == D("32")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest -q -m realdata tests/integration/test_realdata_lots.py`
Expected: FAIL — before Tasks 1–7 land, on the import; after them, this is the acceptance run.

- [ ] **Step 3: Make it pass**

If a position disagrees, the fault is upstream in Tasks 2–4, not here. Diagnose with:

```bash
python -m app.cli rebuild --method FIFO
```

and compare per-instrument sums against `Portfolio.csv`. Do not adjust the expected
numbers — they are the broker's own.

- [ ] **Step 4: Run the full gates**

Run:
```bash
cd backend && python -m pytest -q && python -m pytest -q -m realdata \
  && python -m ruff check . && python -m mypy app
cd ../frontend && npx tsc --noEmit && npx vitest run
```
Expected: all green

- [ ] **Step 5: Commit**

```bash
git add backend/tests/integration/test_realdata_lots.py
git commit -m "test: prove ORN reaches 32 shares through the split"
```

---

## Definition of done for M1

- `pytest` green, including the opt-in `realdata` suite locally.
- `python -m app.cli rebuild --method FIFO` reports attributed charges equal to the ledger's.
- **ORN = 32 shares, matching `Portfolio.csv`** — and all six positions agree.
- The 2025-01-30 ORN lot reads 10 shares at €65.530, cost basis €655.30, with €2.00 commission and €2.18 FX cost shown separately.
- Switching FIFO/LIFO/HIFO in the browser changes realised P&L and leaves share counts alone.
- Rebuilding twice produces identical rows.
- No real export data committed: `git status --porcelain` clean, `git check-ignore -v degiro-export/Transactions.csv config/corporate_actions.yaml` both match.

## What M2 adds next

Prices, FX series and valuation — which is what turns a cost basis into a market value and a portfolio-value chart, and what makes `coverage` mean something other than `"full"`. M1 deliberately reports no market value at all: a position priced from a series that has gaps must say so (§8.1), and there is no series yet to have gaps.
