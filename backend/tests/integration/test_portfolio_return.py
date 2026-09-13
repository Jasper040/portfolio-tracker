"""The claims about trades and sweeps, driven through `rebuild()`.

`tests/unit/test_portfolio_return.py` proves the arithmetic over hand-built
closes. It cannot prove that `V` is continuous across a trade: that is a claim
about how `cash_daily` and `position_daily` are derived from a fill's
`net_base`, and the only honest test of it goes through the derivation.
"""

from __future__ import annotations

import csv
import shutil
from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pytest
from sqlalchemy import Engine

from app.analytics.flows import external_flows
from app.analytics.portfolio_return import PortfolioReturn, portfolio_returns
from app.analytics.rebuild import rebuild
from app.analytics.valuation import value_series
from app.db import create_engine_and_tables
from app.ingest.importer import ensure_default_account, import_degiro_export
from tests.integration.synthetic_ledger import Ledger

MON = date(2025, 3, 3)
TUE = date(2025, 3, 4)
WED = date(2025, 3, 5)

GOLDEN = Path(__file__).parents[1] / "golden"
RESOLVE_BOTH = """\
resolutions:
  - key: NL0000000003:2025-01-17:100.00
    treatment: corporate_action
  - key: US0000000002:2025-01-18:100.00
    treatment: corporate_action
"""
#: Account.csv descriptions that move money within the account and never across
#: its boundary (Sec 3.3). The parser drops every one of them.
INTERNAL_TRANSFERS = ("degiro cash sweep transfer", "overboeking", "reservation ideal")


def _returns(engine: Engine) -> PortfolioReturn:
    """What the performance route computes, from the ledger's first day."""
    return portfolio_returns(
        value_series(engine, start=date.min).points, external_flows(engine)
    )


def _wednesday(engine: Engine) -> D | None:
    return next(step.daily_return for step in _returns(engine).links if step.on == WED)


def _held_since_monday() -> Ledger:
    """1000.00 deposited and 10 shares bought at Monday's close of 20.00. Flat
    into Tuesday, up 10% on Wednesday: the holding alone earns 20.00 on the
    1000.00 there at Tuesday's close, which is 0.02."""
    return (
        Ledger()
        .deposit(MON, "1000.00")
        .buy(MON, "10", "20.00")
        .close(MON, "20.00")
        .close(TUE, "20.00")
        .close(WED, "22.00")
    )


class TestADepositThroughTheLedger:
    def test_a_deposit_into_a_flat_market_returns_zero(self) -> None:
        """The load-bearing test again, through `rebuild()`, so the deposit
        reaches `V` the way a real one does -- as a row in `cash_daily`."""
        engine = (
            Ledger()
            .deposit(MON, "1000.00")
            .buy(MON, "10", "20.00")
            .deposit(WED, "5000.00")
            .close(MON, "20.00")
            .close(TUE, "20.00")
            .close(WED, "20.00")
            .rebuilt(through=WED)
        )
        assert [step.daily_return for step in _returns(engine).links] == [D("0"), D("0")]


class TestATradeIsNotAFlow:
    def test_without_a_trade_the_holding_earns_two_percent(self) -> None:
        """The control. Without it the next test passes for an implementation
        that returns 0.02 on every Wednesday."""
        assert _wednesday(_held_since_monday().rebuilt(through=WED)) == D("0.02")

    @pytest.mark.parametrize("quantity", ["50", "500"])
    def test_a_buy_at_the_close_with_no_fee_leaves_the_day_unchanged(
        self, quantity: str
    ) -> None:
        """M6a-5: a buy moves value from cash to holdings, both inside `V`. At
        the close and free of charge nothing survives the netting, at any size
        -- including one that drives cash deeply negative. This is the assertion
        that fails the moment cash is dropped from the denominator."""
        engine = _held_since_monday().buy(WED, quantity, "22.00").rebuilt(through=WED)
        assert _wednesday(engine) == D("0.02")

    def test_a_buy_below_the_close_adds_the_gap_less_the_fee(self) -> None:
        """50 shares at 21.00 against a 22.00 close gain 50.00 on the day, less a
        2.00 fee: 48.00 on the 1000.00 there at Tuesday's close. By that and by
        nothing else. Stated separately because the test above passes for an
        implementation that ignores trades altogether."""
        engine = (
            _held_since_monday().buy(WED, "50", "21.00", fee="2.00").rebuilt(through=WED)
        )
        assert _wednesday(engine) == D("0.02") + D("48.00") / D("1000.00")


def _is_internal_transfer(line: str) -> bool:
    description = next(csv.reader([line]))[5]
    return description.strip().casefold().startswith(INTERNAL_TRANSFERS)


def _golden(tmp_path: Path, *, with_transfers: bool) -> Engine:
    export = tmp_path / ("with" if with_transfers else "without")
    export.mkdir()
    shutil.copy(GOLDEN / "degiro_transactions_golden.csv", export / "Transactions.csv")
    header, *body = (GOLDEN / "degiro_account_golden.csv").read_text(
        encoding="utf-8"
    ).splitlines(keepends=True)
    kept = body if with_transfers else [line for line in body if not _is_internal_transfer(line)]
    (export / "Account.csv").write_text(header + "".join(kept), encoding="utf-8")
    answers = tmp_path / "corporate_actions.yaml"
    answers.write_text(RESOLVE_BOTH, encoding="utf-8")

    engine = create_engine_and_tables("sqlite://")
    import_degiro_export(engine, export, ensure_default_account(engine), answers)
    rebuild(engine, "FIFO")
    return engine


class TestSweepsAreNotFlows:
    def test_the_fixture_really_carries_internal_transfers(self) -> None:
        """The vacuity check: stripping nothing would pass the next test for the
        wrong reason."""
        lines = (GOLDEN / "degiro_account_golden.csv").read_text(encoding="utf-8").splitlines()
        assert sum(1 for line in lines[1:] if _is_internal_transfer(line)) == 4

    def test_the_return_series_is_identical_with_and_without_them(
        self, tmp_path: Path
    ) -> None:
        """Sec 3.3's finding, made checkable (M6a section 8)."""
        with_them = _golden(tmp_path, with_transfers=True)
        without_them = _golden(tmp_path, with_transfers=False)
        assert external_flows(with_them) == external_flows(without_them)
        assert _returns(with_them) == _returns(without_them)

    def test_the_golden_flows_are_the_genuine_ones(self, tmp_path: Path) -> None:
        """One deposit, one withdrawal, and a flatex pair that crosses the
        boundary both ways on one day and nets to zero."""
        assert external_flows(_golden(tmp_path, with_transfers=True)) == {
            date(2025, 1, 8): D("0.00"),
            date(2025, 1, 9): D("-75.00"),
            date(2025, 1, 10): D("1000.00"),
        }
