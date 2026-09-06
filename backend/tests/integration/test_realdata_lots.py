"""Opt-in suite: M1's definition of done against the owner's real exports.

Design doc Sec 10 states M1's outcome as one number -- ORN = 32 shares matching
Portfolio.csv. It is the right number to be judged on because nothing else in the
pipeline can be wrong while it is right: the split has to be detected, suppressed,
derived and applied, and the ordinary trades around it left alone.

Never runs in CI: the exports are gitignored, so these tests skip themselves when
the directory is absent.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlmodel import Session, select

from app.analytics.rebuild import rebuild
from app.db import create_engine_and_tables
from app.ingest.degiro.portfolio_csv import parse_portfolio_csv
from app.ingest.importer import ensure_default_account, import_degiro_export
from app.models.ledger import Lot, LotClosure

EXPORT = Path(__file__).parents[3] / "degiro-export"
ANSWERS = Path(__file__).parents[3] / "config" / "corporate_actions.yaml"

pytestmark = [
    pytest.mark.realdata,
    pytest.mark.skipif(
        not ((EXPORT / "Transactions.csv").exists() and ANSWERS.exists()),
        reason="real DeGiro export or answers file not present",
    ),
]

ORION = "US0000000901"
D = Decimal


@pytest.fixture(scope="module")
def rebuilt() -> Engine:
    engine = create_engine_and_tables("sqlite://")
    import_degiro_export(engine, EXPORT, ensure_default_account(engine), ANSWERS)
    rebuild(engine, "FIFO")
    return engine


def _lots(engine: Engine, isin: str) -> list[Lot]:
    with Session(engine) as session:
        return list(
            session.exec(select(Lot).where(Lot.method == "FIFO", Lot.isin == isin)).all()
        )


def test_orion_holds_thirty_two_shares(rebuilt: Engine) -> None:
    """M1's headline. 23 shares were bought; the other 9 are the 10-for-1 split
    applied to the single share held on 2025-02-18."""
    held = sum((lot.quantity for lot in _lots(rebuilt, ORION)), D("0"))
    assert held == parse_portfolio_csv(EXPORT / "Portfolio.csv").quantity_of(ORION)
    assert held == D("32")


def test_the_pre_split_lot_was_restated_not_repriced(rebuilt: Engine) -> None:
    """Sec 11.2 #1: the 2025-01-30 lot, cost basis EUR 655.30, 10 shares after the
    split. The basis is the trade value with charges excluded (Sec 6.4)."""
    lot = next(lot for lot in _lots(rebuilt, ORION) if lot.opened_on.isoformat() == "2025-01-30")
    assert lot.quantity == D("10")
    assert lot.cost_basis == D("655.30")
    assert lot.price == D("65.530")


def test_the_split_realised_nothing(rebuilt: Engine) -> None:
    """A split is not a sale. If its legs had been matched, 2025-02-18 would carry
    a realised profit that never happened."""
    with Session(rebuilt) as session:
        closures = session.exec(
            select(LotClosure).where(LotClosure.isin == ORION)
        ).all()
    assert [c for c in closures if c.closed_on.isoformat() == "2025-02-18"] == []


def test_the_charges_on_that_lot_are_visible_and_not_in_the_basis(rebuilt: Engine) -> None:
    """EUR 2.00 commission and EUR 2.18 of FX cost, reportable separately -- which
    is the whole reason Sec 6.4 stopped capitalising them."""
    lot = next(lot for lot in _lots(rebuilt, ORION) if lot.opened_on.isoformat() == "2025-01-30")
    assert lot.commission == D("2.00")
    assert lot.autofx == D("2.18")
    assert lot.cost_basis == D("655.30")


def test_every_position_matches_the_brokers_own_statement(rebuilt: Engine) -> None:
    """Not just ORN. Portfolio.csv states six positions; all six must agree, or
    the split logic happens to be right about one instrument by luck."""
    snapshot = parse_portfolio_csv(EXPORT / "Portfolio.csv")
    for position in snapshot.positions:
        held = sum((lot.quantity for lot in _lots(rebuilt, position.isin)), D("0"))
        assert held == snapshot.quantity_of(position.isin), position.isin


def test_the_standing_charge_invariant_holds_on_real_data(rebuilt: Engine) -> None:
    """Sec 11.2 #4 across 112 real trades, 105 order ids and four currencies."""
    result = rebuild(rebuilt, "FIFO")
    assert result.charges_attributed == result.charges_in_ledger


@pytest.mark.parametrize("method", ["FIFO", "LIFO", "HIFO"])
def test_every_method_holds_the_same_shares(rebuilt: Engine, method: str) -> None:
    """Method changes which lots a sale consumed and therefore realised P&L. It
    cannot change how many shares are left."""
    rebuild(rebuilt, method)  # type: ignore[arg-type]
    with Session(rebuilt) as session:
        lots = session.exec(
            select(Lot).where(Lot.method == method, Lot.isin == ORION)
        ).all()
    assert sum((lot.quantity for lot in lots), D("0")) == D("32")
