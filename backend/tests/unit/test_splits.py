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
