"""Opt-in suite: M1's definition of done against the owner's real exports.

Design doc Sec 10 states M1's outcome as one number: the split instrument's share
count, matching Portfolio.csv. It is the right number to be judged on because
nothing else in the pipeline can be wrong while it is right -- the split has to be
detected, suppressed, its ratio derived and applied, and the ordinary trades around
it left alone.

**Every expected value here is read from the export at run time**, never written
down. Sec 13 keeps the owner's holdings out of the repo and Sec 1 leaves the door
open to open-sourcing this. Deriving them also makes the assertions stronger: they
say the pipeline reproduces the broker's own statement, where a hardcoded figure
only said it reproduces what somebody typed. See `realdata_subject`.

Never runs in CI: the exports are gitignored, so these tests skip themselves when
the directory is absent.

PYTEST_DONT_REWRITE -- pytest's assertion rewriting prints both operands of a
failing assert, and the operands here are derived from the gitignored export:
identifiers, balances, dates, and model reprs that carry all three. That output
reaches a terminal, and from there agent transcripts, pasted reports and issue
comments. The marker turns the rewriting off, so a failure reports only what
its own message says.
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
from app.models.ledger import Lot, LotClosure, Transaction
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


def _charges_the_broker_took(engine: Engine) -> Decimal:
    """The ledger's charge total, computed here rather than by the production code.

    `rebuild()` raises whenever its two totals differ, so
    `charges_attributed == charges_in_ledger` cannot fail -- it is only reachable
    once it is already true. This restates the predicate and the sign flip in full
    rather than calling `is_share_movement` or `charges_of`, so the assertion below
    compares two independently derived numbers instead of one number with itself.
    """
    with Session(engine) as session:
        rows = session.exec(select(Transaction)).all()

    total = D("0.00")
    for row in rows:
        counts = (
            row.is_economic
            and row.isin is not None
            and row.quantity is not None
            and row.quantity != 0
            and row.value_base is not None
        )
        if not counts:
            continue
        total += -row.fee_base - (row.autofx_fee_base or D("0.00")) - row.tax_base
    return total


def test_the_split_instrument_matches_the_brokers_share_count(rebuilt: Engine) -> None:
    """M1's headline, and the sharpest test in the project.

    The count is only reachable if the split was applied as a corporate action:
    book its legs as ordinary trades instead and the position ends short by every
    share the split created.
    """
    split = subject.split()
    held = sum((lot.quantity for lot in _lots(rebuilt, split.isin)), D("0"))
    assert held == parse_portfolio_csv(EXPORT / "Portfolio.csv").quantity_of(split.isin)
    # Strictly more than the restated opening lot: later purchases are in there too,
    # so a pipeline that dropped them would still fail this.
    assert held > split.post_split_quantity


def test_the_pre_split_lot_was_restated_not_repriced(rebuilt: Engine) -> None:
    """Sec 11.2 #1. The opening lot survives the split with its cost basis intact:
    more shares, proportionally cheaper, the same money paid for them. The basis is
    the trade value with charges excluded (Sec 6.4)."""
    split = subject.split()
    lot = next(
        lot for lot in _lots(rebuilt, split.isin) if lot.opened_on == split.opened_on
    )
    assert lot.quantity == split.post_split_quantity
    assert lot.cost_basis == split.cost_basis
    assert lot.price == split.post_split_price
    # The property that makes restating safe rather than merely convenient.
    assert lot.quantity * lot.price == split.cost_basis


def test_the_split_realised_nothing(rebuilt: Engine) -> None:
    """A split is not a sale. If its legs had been matched, its effective date
    would carry a realised profit that never happened."""
    split = subject.split()
    # The instrument was processed at all -- otherwise "no closure on that date"
    # would be true of an instrument the rebuild never looked at.
    assert _lots(rebuilt, split.isin), "the split instrument produced no lots"
    with Session(rebuilt) as session:
        closures = session.exec(
            select(LotClosure).where(LotClosure.isin == split.isin)
        ).all()
    assert [c for c in closures if c.closed_on == split.effective_on] == []


def test_the_charges_on_that_lot_are_visible_and_not_in_the_basis(rebuilt: Engine) -> None:
    """Commission and FX cost, reportable separately -- which is the whole reason
    Sec 6.4 stopped capitalising them into the basis."""
    split = subject.split()
    lot = next(
        lot for lot in _lots(rebuilt, split.isin) if lot.opened_on == split.opened_on
    )
    assert lot.commission == split.commission
    assert lot.autofx == split.autofx
    # The broker really did charge for this trade, so a change that zeroed both the
    # ledger and the lot would be caught rather than passing as 0 == 0.
    assert lot.commission + lot.autofx > D("0")
    # And the charges sit beside the basis rather than inside it.
    assert lot.cost_basis == split.cost_basis


def test_every_position_matches_the_brokers_own_statement(rebuilt: Engine) -> None:
    """Not just the split instrument. Every position the broker reports must agree,
    or the split logic happens to be right about one of them by luck."""
    snapshot = parse_portfolio_csv(EXPORT / "Portfolio.csv")
    assert snapshot.positions, "the broker's statement lists no positions to check"
    for position in snapshot.positions:
        held = sum((lot.quantity for lot in _lots(rebuilt, position.isin)), D("0"))
        assert held == snapshot.quantity_of(position.isin), position.isin


def test_the_standing_charge_invariant_holds_on_real_data(rebuilt: Engine) -> None:
    """Sec 11.2 #4 across every real trade, order id and currency in the export.

    This is the test the acceptance run cites for the invariant, so it must not be
    the tautology `rebuild()` guarantees. The expected total is summed from the
    ledger rows here and both of the rebuild's own figures are compared to it.
    """
    expected = _charges_the_broker_took(rebuilt)
    # The owner really did pay something across 112 trades, so a change that zeroed
    # both sides of the equality would be caught rather than passing as 0 == 0. The
    # figure itself is not hardcoded: it is the broker's, and it belongs in the
    # export rather than in git.
    assert expected > D("0")

    result = rebuild(rebuilt, "FIFO")
    assert result.charges_in_ledger == expected
    assert result.charges_attributed == expected


@pytest.mark.parametrize("method", ["FIFO", "LIFO", "HIFO"])
def test_every_method_holds_the_same_shares(rebuilt: Engine, method: str) -> None:
    """Method changes which lots a sale consumed and therefore realised P&L. It
    cannot change how many shares are left."""
    split = subject.split()
    expected = parse_portfolio_csv(EXPORT / "Portfolio.csv").quantity_of(split.isin)
    rebuild(rebuilt, method)  # type: ignore[arg-type]
    with Session(rebuilt) as session:
        lots = session.exec(
            select(Lot).where(Lot.method == method, Lot.isin == split.isin)
        ).all()
    assert sum((lot.quantity for lot in lots), D("0")) == expected
