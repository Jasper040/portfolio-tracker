"""Opt-in: the ledger half of M2, against the owner's real export.

No network and no price cache. Everything here is derivable from the export
alone, which is why it is separated from `test_realdata_prices.py`: a failure in
this file is a bug in the pipeline, where a failure there could equally be a
provider that changed its mind.

The two numbers worth being judged on are both the broker's own. The daily share
series must reproduce every position in `Portfolio.csv` on its last day, and the
daily cash series must land on the broker's own cash line -- which is the harder
of the two, because it is the sum of 785 rows across four currencies with 256
internal transfers that look exactly like deposits and are not.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlmodel import Session, select

from app.analytics.rebuild import rebuild
from app.db import create_engine_and_tables
from app.domain.splits import derive_splits
from app.domain.symbols import CandidateSeries, assess
from app.ingest.degiro.portfolio_csv import parse_portfolio_csv
from app.ingest.importer import ensure_default_account, import_degiro_export
from app.ingest.symbols import observations
from app.models.ledger import CashDaily, PositionDaily, Transaction
from tests.integration import realdata_subject as subject

EXPORT = Path(__file__).parents[3] / "degiro-export"
ANSWERS = Path(__file__).parents[3] / "config" / "corporate_actions.yaml"

pytestmark = [
    pytest.mark.realdata,
    pytest.mark.skipif(
        not ((EXPORT / "Transactions.csv").exists() and ANSWERS.exists()),
        reason="real DeGiro export or answers file not present",
    ),
]

D = Decimal
#: Sec 5.4's per-portfolio reconciliation tolerance.
AGGREGATE_TOLERANCE = D("0.50")


@pytest.fixture(scope="module")
def rebuilt() -> Engine:
    engine = create_engine_and_tables("sqlite://")
    import_degiro_export(engine, EXPORT, ensure_default_account(engine), ANSWERS)
    rebuild(engine, "FIFO")
    return engine


def _last_position_day(engine: Engine) -> date:
    with Session(engine) as session:
        rows = session.exec(select(PositionDaily)).all()
    assert rows, "the daily series is empty; there is nothing to check"
    return max(row.position_date for row in rows)


class TestTheDailyShareSeries:
    def test_reproduces_every_position_in_the_brokers_statement(self, rebuilt) -> None:
        """The M1 acceptance, carried into the daily series. The split
        instrument only reaches its share count if the corporate action was
        detected, suppressed, its ratio derived and applied -- and every other
        position has to agree too, or the split logic is right about one of them
        by luck."""
        snapshot = parse_portfolio_csv(EXPORT / "Portfolio.csv")
        assert snapshot.positions, "the broker's statement lists no positions"

        last = _last_position_day(rebuilt)
        with Session(rebuilt) as session:
            held = {
                row.isin: row.quantity
                for row in session.exec(
                    select(PositionDaily).where(PositionDaily.position_date == last)
                ).all()
            }

        for position in snapshot.positions:
            assert held.get(position.isin) == position.quantity, position.isin

    def test_holds_nothing_the_broker_does_not_report(self, rebuilt) -> None:
        """The other direction. A phantom position would inflate the chart and
        never show up in a one-way comparison."""
        snapshot = parse_portfolio_csv(EXPORT / "Portfolio.csv")
        reported = {position.isin for position in snapshot.positions}
        last = _last_position_day(rebuilt)
        with Session(rebuilt) as session:
            held = {
                row.isin
                for row in session.exec(
                    select(PositionDaily).where(PositionDaily.position_date == last)
                ).all()
            }
        assert held == reported

    def test_the_series_is_the_same_under_every_method(self, rebuilt) -> None:
        """Why `position_daily` has no method column, proved on real data."""

        def snapshot() -> list[tuple[date, str, str]]:
            with Session(rebuilt) as session:
                return sorted(
                    (row.position_date, row.isin, str(row.quantity))
                    for row in session.exec(select(PositionDaily)).all()
                )

        rebuild(rebuilt, "FIFO")
        fifo = snapshot()
        rebuild(rebuilt, "HIFO")
        assert snapshot() == fifo
        rebuild(rebuilt, "FIFO")  # leave the fixture as the module found it


class TestTheDailyCashSeries:
    def test_lands_on_the_brokers_own_cash_balance(self, rebuilt) -> None:
        """The hardest reconciliation in the project, on a daily series.

        785 account rows, four currencies, and 256 internal transfers that carry
        real signed euro amounts and plausible running balances while being
        neither deposits nor withdrawals. Classify one of them wrong and this
        misses by its amount.
        """
        expected = subject.broker_cash_balance()
        with Session(rebuilt) as session:
            rows = session.exec(select(CashDaily)).all()
        assert rows, "the cash series is empty"
        final = max(rows, key=lambda row: row.cash_date).balance_base
        assert abs(final - expected) <= AGGREGATE_TOLERANCE

    def test_starts_before_the_first_position(self, rebuilt) -> None:
        """Money sits in the account before it buys anything. A cash series that
        began at the first BUY would hide the deposits that funded it -- and
        would make an explicit five-year window start later than the ledger
        actually reaches."""
        with Session(rebuilt) as session:
            first_cash = min(row.cash_date for row in session.exec(select(CashDaily)).all())
            first_position = min(
                row.position_date for row in session.exec(select(PositionDaily)).all()
            )
        assert first_cash <= first_position

    def test_covers_every_weekday_without_a_hole(self, rebuilt) -> None:
        """M2-3: every weekday is valued. A hole would be a day the chart could
        not draw, and there is no honest way to draw one."""
        with Session(rebuilt) as session:
            days = sorted(row.cash_date for row in session.exec(select(CashDaily)).all())
        expected = [day for day in _weekdays_between(days[0], days[-1])]
        assert days == expected


def _weekdays_between(start: date, end: date) -> list[date]:
    from datetime import timedelta

    days: list[date] = []
    day = start
    while day <= end:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


class TestTheDiscriminatorOnRealTrades:
    """The check that would have caught the leveraged-ETF match, run against the
    owner's own executed prices rather than an invented series.

    The impostor is BUILT from the real trades at run time -- a series a third of
    the real price with a drift in it -- so no figure is written down and the
    fixture cannot go stale against a re-export.
    """

    def _subject_isin(self) -> str:
        for isin in sorted(subject.traded_isins()):
            if len(subject.executed_prices(isin)) >= 2:
                return isin
        pytest.skip("no instrument in the export has two priced trades")

    def test_a_series_matching_the_ledger_is_accepted(self, rebuilt) -> None:
        isin = self._subject_isin()
        with Session(rebuilt) as session:
            rows = list(session.exec(select(Transaction)).all())
        trades = observations(rows)[isin]
        splits = [s for s in derive_splits(rows) if s.isin == isin]

        # A "provider" that agrees exactly: the split-adjusted executed price.
        from app.domain.symbols import split_factor

        closes = {
            trade.trade_date: trade.price_local / split_factor(splits, trade.trade_date)
            for trade in trades
        }
        verdict = assess(
            CandidateSeries(
                symbol="RIGHT", currency=subject.trade_currency(isin), closes=closes
            ),
            trades,
            splits,
        )
        assert verdict.accepted, verdict.reason

    def test_a_leveraged_lookalike_is_rejected(self, rebuilt) -> None:
        """Same ISIN, same currency, years of bars, a third of the price and
        drifting. Every naive check passes; this one does not."""
        isin = self._subject_isin()
        with Session(rebuilt) as session:
            rows = list(session.exec(select(Transaction)).all())
        trades = observations(rows)[isin]
        splits = [s for s in derive_splits(rows) if s.isin == isin]

        from app.domain.symbols import split_factor

        closes = {
            trade.trade_date: (
                trade.price_local / split_factor(splits, trade.trade_date) / D("3")
            )
            for trade in trades
        }
        verdict = assess(
            CandidateSeries(
                symbol="WRONG2S", currency=subject.trade_currency(isin), closes=closes
            ),
            trades,
            splits,
        )
        assert not verdict.accepted

    def test_the_right_answer_needs_the_split_correction(self, rebuilt) -> None:
        """The control that makes the accept above mean something. On the
        instrument that split, comparing an unadjusted executed price against a
        split-adjusted close disagrees by the ratio."""
        isin = subject.split().isin
        with Session(rebuilt) as session:
            rows = list(session.exec(select(Transaction)).all())
        trades = observations(rows).get(isin)
        if not trades:
            pytest.skip("the split instrument has no priced trades in the export")
        splits = [s for s in derive_splits(rows) if s.isin == isin]

        from app.domain.symbols import split_factor

        closes = {
            trade.trade_date: trade.price_local / split_factor(splits, trade.trade_date)
            for trade in trades
        }
        currency = subject.trade_currency(isin)
        with_correction = assess(CandidateSeries("RIGHT", currency, closes), trades, splits)
        without_correction = assess(CandidateSeries("RIGHT", currency, closes), trades, [])

        assert with_correction.accepted
        assert not without_correction.accepted
