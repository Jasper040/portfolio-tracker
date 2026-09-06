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
