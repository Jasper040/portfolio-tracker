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
from sqlmodel import Session, select

from app.analytics.positions_snapshot import current_positions
from app.analytics.quotes import FULL, MANUAL, MISSING, PARTIAL, STALE_DAYS
from app.analytics.valuation import value_series
from app.db import create_engine_and_tables
from app.domain.symbols import NEAR_DAYS
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
            account = session.exec(select(Account)).first()
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


class TestWindowedLoadsCarryIn:
    """PT-49: the window is applied in SQL now, and the rows a window's first
    days reach back to lie OUTSIDE it.

    Carry-forward is what `partial` coverage means, so a plain
    `price_date >= start` would not merely make a figure arrive sooner -- it
    would turn a day the ledger CAN value into a `missing` one, and withhold a
    total that was previously reported. These pin that the bound carries in the
    last row before the window for prices and for rates alike.
    """

    def test_a_price_from_before_the_window_still_values_its_first_day(self, engine) -> None:
        seed = Seed(engine).priced(MON, A, "10.00")  # the only quote, before the window
        for day in (THU, FRI):
            seed.held(day, A, "10").cash(day, "0.00")

        series = value_series(engine, start=THU)

        assert [p.on for p in series.points] == [THU, FRI]
        assert [p.value_base for p in series.points] == [D("100.00"), D("100.00")]
        assert MISSING not in [p.coverage for p in series.points]

    def test_a_rate_from_before_the_window_still_converts_its_first_day(self, engine) -> None:
        seed = Seed(engine).priced(MON, B, "10.00", currency="USD").rate(MON, "USD", "2.0")
        for day in (THU, FRI):
            seed.held(day, B, "10").cash(day, "0.00")

        series = value_series(engine, start=THU)

        # 10 x 10.00 USD at 2.0 per euro. Without the carried rate the holding
        # is unconvertible and the day reports `missing` instead.
        assert [p.value_base for p in series.points] == [D("50.00"), D("50.00")]
        assert MISSING not in [p.coverage for p in series.points]

    def test_a_window_says_exactly_what_the_whole_ledger_says_for_those_days(
        self, engine
    ) -> None:
        """The general property, and the one worth keeping: bounding the loads
        is an optimisation, so a window must be indistinguishable from the same
        days of the unbounded answer -- coverage and covered_pct included."""
        seed = Seed(engine)
        for offset, day in enumerate((MON, TUE, WED, THU, FRI)):
            seed.held(day, A, "10").cash(day, "-50.00")
            # WED deliberately has no quote of its own, so at least one day in
            # every window is valued by carry-forward. Without it this property
            # would hold even with the carry-in removed, and prove nothing.
            if day is not WED:
                seed.priced(day, A, f"1{offset}.00")

        def shape(points):
            return [
                (p.on, p.holdings_base, p.cash_base, p.value_base, p.coverage, p.covered_pct)
                for p in points
            ]

        whole = value_series(engine)
        for first in (MON, TUE, WED, THU, FRI):
            windowed = value_series(engine, start=first)
            assert shape(windowed.points) == shape(
                [p for p in whole.points if p.on >= first]
            ), f"window from {first} disagrees with the whole ledger"

    def test_a_stale_carried_price_still_reports_itself_as_stale(self, engine) -> None:
        """The carry-in must not launder a price's age. MON to NEXT_MON is
        seven days, past the four-day threshold, so the day is `partial`
        whether or not the window starts after the quote."""
        seed = Seed(engine).priced(MON, A, "10.00")
        seed.held(NEXT_MON, A, "10").cash(NEXT_MON, "0.00")

        assert value_series(engine, start=NEXT_MON).points[0].coverage == PARTIAL


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


def test_stale_days_and_near_days_agree_on_how_old_fresh_may_be() -> None:
    """`valuation.STALE_DAYS` (above, driving `PARTIAL`) and
    `domain.symbols.NEAR_DAYS` (the split-adjustment join's own four-day
    carry-forward) are the same idea under two names: a weekend plus one
    holiday is how far a price may be carried before it stops counting as
    fresh. Their docstrings already say they mean the same thing, but nothing
    outside this test enforced it.

    Neither module may import the other -- `domain/` takes no dependency on
    `analytics/`, in either direction (Sec 4.1's layering) -- so the two
    constants cannot share a definition. This test is the enforcement that a
    shared import cannot be: change one without the other and half the app
    means something different by "fresh" than the other half does, silently.
    """
    assert STALE_DAYS == NEAR_DAYS


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
