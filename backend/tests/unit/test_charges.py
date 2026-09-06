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
