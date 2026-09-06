"""Lot matching, ported from the tested TypeScript implementation with two upgrades:
Decimal instead of float, and charge attribution routed through `apportion_charges`
so `Σ attributed charges == Σ ledger charges` holds exactly (design doc Sec 11.2).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.domain.charges import Charges
from app.domain.lots import LotTransaction, match_lots

D = Decimal


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


class TestTheThreeMethodsDisagree:
    """Three lots at 20, 30 and 10 and one sale at 40, so every method picks a
    different lot. Two methods aliasing would pass any weaker fixture."""

    TRANSACTIONS = [
        buy("1", "10", "20", day=1),
        buy("2", "10", "30", day=2),
        buy("3", "10", "10", day=3),
        sell("4", "10", "40"),
    ]

    def test_fifo_closes_the_oldest_lot(self) -> None:
        assert match_lots(self.TRANSACTIONS, "FIFO").realised == D("200")

    def test_lifo_closes_the_newest_lot(self) -> None:
        assert match_lots(self.TRANSACTIONS, "LIFO").realised == D("300")

    def test_hifo_closes_the_dearest_lot_realising_the_smallest_gain(self) -> None:
        assert match_lots(self.TRANSACTIONS, "HIFO").realised == D("100")

    @pytest.mark.parametrize("method", ["FIFO", "LIFO", "HIFO"])
    def test_the_method_never_changes_how_much_stock_is_left(self, method: str) -> None:
        # The method decides WHICH cost basis is consumed, never the quantity. A
        # method that changed the quantity would be a bug, not a policy.
        assert match_lots(self.TRANSACTIONS, method).quantity == D("20")  # type: ignore[arg-type]

    def test_the_remaining_cost_basis_differs_per_method(self) -> None:
        assert match_lots(self.TRANSACTIONS, "FIFO").cost_basis == D("400")  # 30 and 10 remain
        assert match_lots(self.TRANSACTIONS, "LIFO").cost_basis == D("500")  # 20 and 30 remain


class TestFeeAttribution:
    def test_pro_rates_both_legs_by_each_side_own_quantity(self) -> None:
        # Buy 10 @ 10 costing 2.00; sell 5 @ 20 costing 4.00.
        # Sale leg: all 4.00, since this closure is the whole sale.
        # Buy leg: 2.00 x 5/10 = 1.00. Total 5.00.
        result = match_lots([buy("1", "10", "10", "2.00"), sell("2", "5", "20", "4.00")], "FIFO")
        assert len(result.closures) == 1
        assert result.closures[0].charges.total == D("5.00")
        assert result.closures[0].pnl == D("45.00")

    def test_charges_a_purchase_fee_once_across_tranches(self) -> None:
        result = match_lots(
            [
                buy("1", "10", "10", "2.00"),
                sell("2", "5", "20", "0.00", day=1),
                sell("3", "5", "20", "0.00", day=2),
            ],
            "FIFO",
        )
        assert len(result.closures) == 2
        assert sum(c.charges.total for c in result.closures) == D("2.00")

    def test_leaves_the_open_lot_only_its_share(self) -> None:
        result = match_lots([buy("1", "10", "10", "2.00"), sell("2", "5", "20")], "FIFO")
        assert result.open_lots[0].charges.total == D("1.00")
        assert result.cost_basis == D("50.00")

    @pytest.mark.parametrize(
        "transactions",
        [
            [buy("1", "3", "10", "0.10"), sell("2", "1", "20"), sell("3", "2", "20")],
            [
                buy("1", "7", "10", "2.00"),
                buy("2", "3", "12", "1.00"),
                sell("3", "9", "20", "3.00"),
            ],
            [buy("1", "1", "10", "0.01"), sell("2", "1", "20", "0.01")],
        ],
    )
    def test_attributed_fees_always_equal_ledger_fees(
        self, transactions: list[LotTransaction]
    ) -> None:
        """The standing invariant from Sec 11.2, checked on splits that do not divide."""
        result = match_lots(transactions, "FIFO")
        attributed = sum(c.charges.total for c in result.closures) + sum(
            lot.charges.total for lot in result.open_lots
        )
        assert attributed == sum(t.charges.total for t in transactions)


class TestEdges:
    def test_splits_one_sale_across_every_lot_it_consumes(self) -> None:
        result = match_lots(
            [buy("1", "5", "10", day=1), buy("2", "5", "20", day=2), sell("3", "8", "30")], "FIFO"
        )
        assert [c.quantity for c in result.closures] == [D("5"), D("3")]
        assert result.quantity == D("2")

    def test_terminates_and_drops_the_excess_when_overselling(self) -> None:
        # Shorts are not modelled. A hang here is the failure this test exists for.
        result = match_lots([buy("1", "5", "10"), sell("2", "10", "30")], "FIFO")
        assert len(result.closures) == 1
        assert result.closures[0].quantity == D("5")
        assert result.quantity == D("0")

    def test_a_fully_consumed_lot_leaves_nothing_open(self) -> None:
        # Decimal is exact, so this is 0 rather than 1e-17 -- no epsilon needed.
        result = match_lots(
            [buy("1", "0.3", "10"), sell("2", "0.1", "20", day=1), sell("3", "0.2", "20", day=2)],
            "FIFO",
        )
        assert result.open_lots == []
        assert result.quantity == D("0")

    def test_reports_nothing_realised_without_a_sale(self) -> None:
        result = match_lots([buy("1", "10", "10", "1.00")], "FIFO")
        assert result.realised == D("0")
        assert result.closures == []
        assert result.cost_basis == D("100.00")

    def test_a_sale_matching_no_lot_keeps_its_charges_rather_than_dropping_them(
        self,
    ) -> None:
        """An instrument whose buys predate the export window.

        Nothing in the ledger opens the position, so the sale closes no lot and
        there is no closure to carry its charges. They must still be accounted for:
        `rebuild()` counts `unmatched_charges` toward the attributed side of
        `Sum(attributed) == Sum(ledger)`, and dropping them would make attributed
        fall short and refuse every method for the entire ledger -- with a message
        blaming apportionment, which would not be the cause.
        """
        result = match_lots([sell("1", "10", "20", "3.50")], "FIFO")
        assert result.closures == []
        assert result.open_lots == []
        assert result.unmatched_charges.total == D("3.50")
        assert result.unmatched_charges.commission == D("3.50")

    def test_an_oversale_keeps_the_whole_charge_on_the_part_that_matched(self) -> None:
        """The other half of the same rule. A sale that matched SOMETHING carries
        all of its charges on the closures it produced, so nothing is left over --
        `unmatched_charges` collects only sales that matched nothing at all."""
        result = match_lots([buy("1", "5", "10"), sell("2", "10", "30", "2.00")], "FIFO")
        assert result.unmatched_charges.total == D("0")
        assert sum(c.charges.total for c in result.closures) == D("2.00")

    def test_the_invariant_holds_when_a_sale_matches_nothing(self) -> None:
        """Sec 11.2 #4 across the union of both channels: attributing a sale with
        no lot must still add up to what the ledger says was paid."""
        # The `buy` helper dates its fills in 2020 and `sell` in 2024, so the later
        # purchase is built here rather than reordering the fixture into an input
        # `match_lots` would rightly reject as unchronological.
        later_buy = LotTransaction(
            id="2",
            trade_date=date(2024, 1, 2),
            side="BUY",
            quantity=D("5"),
            price=D("10"),
            charges=Charges(commission=D("1.00")),
        )
        transactions = [sell("1", "10", "20", "3.50", day=1), later_buy]
        result = match_lots(transactions, "FIFO")
        attributed = (
            sum((c.charges.total for c in result.closures), D("0"))
            + sum((lot.charges.total for lot in result.open_lots), D("0"))
            + result.unmatched_charges.total
        )
        assert attributed == sum((t.charges.total for t in transactions), D("0"))
        assert attributed == D("4.50")

    def test_rejects_transactions_out_of_chronological_order(self) -> None:
        # A SELL cannot match a BUY it has not seen. Silently mismatching would
        # produce a plausible, wrong cost basis.
        with pytest.raises(ValueError):
            match_lots([sell("1", "5", "20", day=2), buy("2", "5", "10", day=1)], "FIFO")


class TestClosureMetrics:
    """Sec 7.1: each closure carries holding days, P&L, % return and annualised return."""

    def test_reports_holding_days_and_percentage_return(self) -> None:
        result = match_lots([buy("1", "10", "10"), sell("2", "10", "12")], "FIFO")
        closure = result.closures[0]
        assert closure.holding_days == (date(2024, 1, 1) - date(2020, 1, 1)).days
        assert closure.return_pct == D("0.2")

    def test_annualises_over_the_holding_period(self) -> None:
        result = match_lots([buy("1", "10", "10"), sell("2", "10", "12")], "FIFO")
        annualised = result.closures[0].annualised_return
        assert annualised is not None
        # 20% over roughly four years is a few percent a year, not 20.
        assert D("0.04") < annualised < D("0.05")

    def test_declines_to_annualise_a_same_day_round_trip(self) -> None:
        result = match_lots(
            [
                LotTransaction("1", date(2024, 1, 1), "BUY", D("10"), D("10"), Charges.zero()),
                LotTransaction("2", date(2024, 1, 1), "SELL", D("10"), D("11"), Charges.zero()),
            ],
            "FIFO",
        )
        assert result.closures[0].holding_days == 0
        assert result.closures[0].annualised_return is None

    def test_declines_to_annualise_a_total_loss(self) -> None:
        # A -100% return has no real annualised equivalent; reporting one would
        # require a root of a negative number.
        result = match_lots([buy("1", "10", "10"), sell("2", "10", "0")], "FIFO")
        assert result.closures[0].return_pct == D("-1")
        assert result.closures[0].annualised_return is None


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
