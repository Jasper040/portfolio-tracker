"""Fee apportionment.

Design doc Sec 11.2 makes `Σ attributed fees == Σ ledger fees` a standing invariant
asserted after every rebuild, not merely a test. That is only achievable if the
split is exact by construction, so these tests are mostly about the residual cent
rather than about proportions.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.fees import apportion

D = Decimal


class TestApportionSumsExactly:
    def test_splits_the_worked_example_from_the_design_doc(self) -> None:
        """CASTOR 07-10-2026: EUR 2.00 across fills of 1 and 3 becomes 0.50 / 1.50."""
        assert apportion(D("2.00"), [D("1"), D("3")]) == [D("0.50"), D("1.50")]

    def test_sums_to_the_total_when_the_split_does_not_divide(self) -> None:
        # 0.10 / 3 is 0.0333... per part. Naive rounding gives 0.03 x 3 = 0.09 and
        # loses a cent, which would break the standing invariant on every rebuild.
        parts = apportion(D("0.10"), [D("1"), D("1"), D("1")])
        assert sum(parts) == D("0.10")
        assert sorted(parts) == [D("0.03"), D("0.03"), D("0.04")]

    def test_gives_the_residual_to_the_largest_remainder(self) -> None:
        # Exact shares are 0.8333 and 0.1667; the first has the larger remainder.
        parts = apportion(D("1.00"), [D("5"), D("1")])
        assert sum(parts) == D("1.00")
        assert parts == [D("0.83"), D("0.17")]

    @pytest.mark.parametrize(
        "total,weights",
        [
            (D("0.01"), [D("1"), D("1")]),
            (D("100.00"), [D("1"), D("1"), D("1"), D("1"), D("1"), D("1"), D("7")]),
            (D("3.33"), [D("1.5"), D("2.25"), D("0.75")]),
            (D("0.07"), [D("1"), D("2"), D("3"), D("4")]),
        ],
    )
    def test_always_sums_to_the_total(self, total: Decimal, weights: list[Decimal]) -> None:
        assert sum(apportion(total, weights)) == total

    def test_holds_for_a_negative_total(self) -> None:
        # Taxes can be credited back. ROUND_FLOOR keeps the residual non-negative
        # in both directions, so the same distribution logic covers this.
        parts = apportion(D("-0.10"), [D("1"), D("1"), D("1")])
        assert sum(parts) == D("-0.10")


class TestApportionEdges:
    def test_returns_nothing_for_no_weights(self) -> None:
        assert apportion(D("2.00"), []) == []

    def test_splits_equally_when_every_weight_is_zero(self) -> None:
        # A fee still has to land somewhere. Dropping it would silently break the
        # invariant; dividing by the zero total would raise inside a rebuild.
        parts = apportion(D("0.90"), [D("0"), D("0"), D("0")])
        assert parts == [D("0.30"), D("0.30"), D("0.30")]
        assert sum(parts) == D("0.90")

    def test_distributes_nothing_when_the_total_is_zero(self) -> None:
        assert apportion(D("0.00"), [D("1"), D("2")]) == [D("0.00"), D("0.00")]

    def test_gives_a_zero_weight_nothing_when_others_carry_weight(self) -> None:
        assert apportion(D("1.00"), [D("0"), D("1")]) == [D("0.00"), D("1.00")]

    def test_rejects_a_negative_weight(self) -> None:
        # A negative quantity in a fee split means the caller has passed a sell as
        # if it were a buy. Failing loudly beats attributing a negative fee.
        with pytest.raises(ValueError):
            apportion(D("1.00"), [D("2"), D("-1")])

    def test_honours_a_different_precision(self) -> None:
        parts = apportion(D("1.0000"), [D("1"), D("2")], places=4)
        assert sum(parts) == D("1.0000")
        assert parts == [D("0.3333"), D("0.6667")]
