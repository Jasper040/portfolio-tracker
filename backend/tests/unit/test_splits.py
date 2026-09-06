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

from app.domain.charges import Charges
from app.domain.lots import LotTransaction
from app.domain.splits import Split, apply_splits, derive_splits
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
        is already in new shares. Adjusting it would double-count the split.

        `_fill`'s `day` parameter feeds the month slot of `date(2024, day, 1)`, so it
        cannot land on `SPLIT.effective_on` (the 10th) itself -- the fixture is
        built directly on that exact date instead.
        """
        same_day_fill = LotTransaction(
            id="a",
            trade_date=self.SPLIT.effective_on,
            side="BUY",
            quantity=D("10"),
            price=D("90.666"),
            charges=Charges(commission=D("2.00")),
        )
        [fill] = apply_splits([same_day_fill], [self.SPLIT])
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
