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
