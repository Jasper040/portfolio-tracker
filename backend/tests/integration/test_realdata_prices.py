"""Opt-in: the fetched half of M2, against the operator's own populated cache.

Reads the local database rather than fetching, so the opt-in suite stays as
network-free as CI. It skips when there is no database or the cache is empty,
which is the ordinary state before `fetch-prices` has been run -- and the skip
message says so, because a silently green suite that checked nothing is worse
than a red one.

To run it for real:

    cd backend
    python -m app.cli import ../degiro-export
    python -m app.cli fetch-prices
    python -m app.cli rebuild
    python -m pytest -q -m realdata

PYTEST_DONT_REWRITE -- pytest's assertion rewriting prints both operands of a
failing assert, and the operands here are derived from the gitignored export:
identifiers, balances, dates, and model reprs that carry all three. That output
reaches a terminal, and from there agent transcripts, pasted reports and issue
comments. The marker turns the rewriting off, so a failure reports only what
its own message says.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlmodel import Session, select

from app.analytics.positions_snapshot import current_positions
from app.analytics.quotes import MISSING
from app.analytics.valuation import value_series
from app.db import create_engine_and_tables
from app.ingest.degiro.portfolio_csv import parse_portfolio_csv
from app.models.ledger import PositionDaily
from app.models.market import FxDaily, PriceDaily, SymbolReview
from app.providers.base import BACKFILL_YEARS
from tests.integration import realdata_subject as subject

EXPORT = Path(__file__).parents[3] / "degiro-export"
URL = subject.local_database_url()

D = Decimal

pytestmark = [
    pytest.mark.realdata,
    pytest.mark.skipif(
        not (EXPORT / "Transactions.csv").exists() or URL is None,
        reason="no real export, or no local database in backend/.env",
    ),
]

#: A listing younger than five years cannot have five years of history, and the
#: ECB publishes no rate on a holiday. Six months of slack turns "the backfill
#: ran" into a testable claim without asserting a calendar.
SLACK = timedelta(days=180)


@pytest.fixture(scope="module")
def engine() -> Engine:
    assert URL is not None
    built = create_engine_and_tables(URL)
    with Session(built) as session:
        if not session.exec(select(PriceDaily)).first():
            pytest.skip("price cache is empty; run `python -m app.cli fetch-prices` first")
        if not session.exec(select(PositionDaily)).first():
            pytest.skip("no daily series; run `python -m app.cli rebuild` first")
    return built


class TestTheQuarantineIsAnswered:
    def test_no_symbol_is_still_open(self, engine) -> None:
        """`fetch-prices` refuses while one is, so a populated cache with an open
        question means something wrote prices it should not have."""
        with Session(engine) as session:
            open_rows = session.exec(select(SymbolReview)).all()
        assert [row.isin for row in open_rows] == []


class TestTheFiveYearBackfill:
    def test_the_fx_cache_reaches_five_years_back(self, engine) -> None:
        """The cleanest proof the backfill ran to its stated depth. The ECB
        publishes continuously, so unlike a listing there is no honest reason for
        a currency pair to be short."""
        with Session(engine) as session:
            rows = session.exec(select(FxDaily)).all()
        if not rows:
            pytest.skip("the portfolio is entirely base-currency; no FX to check")

        floor = date.today() - timedelta(days=365 * BACKFILL_YEARS) + SLACK
        for pair in {(row.from_ccy, row.to_ccy) for row in rows}:
            earliest = min(row.rate_date for row in rows if (row.from_ccy, row.to_ccy) == pair)
            assert earliest <= floor, pair

    def test_the_price_cache_reaches_five_years_back_for_something(self, engine) -> None:
        """Per-instrument depth is not assertable -- a listing may be younger
        than the window. That at least one instrument reaches it is."""
        with Session(engine) as session:
            earliest = min(row.price_date for row in session.exec(select(PriceDaily)).all())
        assert earliest <= date.today() - timedelta(days=365 * BACKFILL_YEARS) + SLACK

    def test_a_five_year_window_is_answered_rather_than_refused(self, engine) -> None:
        """What the reader actually does. Asking for five years must come back
        with everything the ledger has -- clamped and flagged if the account is
        younger, never empty and never padded."""
        series = value_series(engine, start=date.today() - timedelta(days=365 * 5))
        assert series.points
        assert series.start is not None
        assert series.start >= subject.first_trade_date() - timedelta(days=7)


class TestCoverage:
    def test_every_day_a_position_was_held_can_be_priced(self, engine) -> None:
        """The acceptance for the whole provider stack. A `missing` day means an
        instrument the cache cannot reach on a day the account held it, and the
        chart has a hole in it."""
        series = value_series(engine)
        unpriceable = [point.on for point in series.points if point.coverage == MISSING]
        assert not unpriceable, (
            f"{len(unpriceable)} day(s) could not be valued, first "
            f"{unpriceable[0] if unpriceable else ''}"
        )

    def test_the_series_starts_at_the_first_day_a_position_existed(self, engine) -> None:
        with Session(engine) as session:
            first_held = min(
                row.position_date for row in session.exec(select(PositionDaily)).all()
            )
        assert value_series(engine).start == first_held

    def test_every_open_position_reports_a_market_value(self, engine) -> None:
        snapshot = current_positions(engine, "FIFO")
        assert snapshot.items, "no open positions to value"
        assert snapshot.total_market_value_base is not None


class TestAgainstTheBrokersOwnValuation:
    def test_the_total_is_the_right_order_of_magnitude(self, engine) -> None:
        """Deliberately loose, and worth having anyway.

        `Portfolio.csv` states the broker's own market value per position, but as
        of the export date -- while the cache reaches to whenever prices were last
        fetched. Those are different days, so the honest tolerance is wide.

        What a wide tolerance still catches is the failure this whole milestone
        is built around: a position priced from a leveraged or inverse product is
        wrong by a factor of tens, not by a market move. A 15% band separates
        "prices moved" from "we priced the wrong instrument".
        """
        snapshot = parse_portfolio_csv(EXPORT / "Portfolio.csv")
        broker_total = sum((p.value_base for p in snapshot.positions), D("0"))
        if broker_total == 0:
            pytest.skip("the broker's statement values nothing")

        ours = current_positions(engine, "FIFO").total_market_value_base
        assert ours is not None
        assert abs(ours - broker_total) / broker_total < D("0.15")

    def test_no_single_position_is_out_by_an_order_of_magnitude(self, engine) -> None:
        """The per-instrument version, which is what actually catches an
        impostor: one wrong symbol among many can hide inside a portfolio total."""
        snapshot = parse_portfolio_csv(EXPORT / "Portfolio.csv")
        ours = {
            item.isin: item.market_value_base
            for item in current_positions(engine, "FIFO").items
        }
        for position in snapshot.positions:
            mine = ours.get(position.isin)
            assert mine is not None, position.isin
            if position.value_base == 0:
                continue
            assert abs(mine - position.value_base) / abs(position.value_base) < D("0.30"), (
                position.isin
            )
