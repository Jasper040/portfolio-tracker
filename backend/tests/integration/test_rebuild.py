"""Rebuilding lots from the ledger (design doc Sec 11.2 #5).

`rebuild()` is the claim that every derived number in this app can be recomputed
from the ledger alone. That claim is only worth something if it is checked, so the
determinism tests here are not decoration: they are the property.
"""

from __future__ import annotations

import shutil
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlmodel import Session, select

from app.analytics.rebuild import ChargeMismatch, rebuild
from app.db import create_engine_and_tables
from app.domain.charges import Charges
from app.ingest.importer import ensure_default_account, import_degiro_export
from app.models.ledger import Lot, LotClosure

GOLDEN = Path(__file__).parents[1] / "golden"

SPLIT_KEY = "NL0000000003:2025-01-17:100.00"
PRODUCT_CHANGE_KEY = "US0000000002:2025-01-18:100.00"

RESOLVE_BOTH = f"""\
resolutions:
  - key: {SPLIT_KEY}
    treatment: corporate_action
  - key: {PRODUCT_CHANGE_KEY}
    treatment: corporate_action
"""

D = Decimal


def _engine() -> Engine:
    return create_engine_and_tables("sqlite://")


@pytest.fixture
def loaded(tmp_path: Path) -> Engine:
    export = tmp_path / "degiro-export"
    export.mkdir()
    shutil.copy(GOLDEN / "degiro_transactions_golden.csv", export / "Transactions.csv")
    shutil.copy(GOLDEN / "degiro_account_golden.csv", export / "Account.csv")
    answers = tmp_path / "corporate_actions.yaml"
    answers.write_text(RESOLVE_BOTH, encoding="utf-8")

    engine = _engine()
    import_degiro_export(engine, export, ensure_default_account(engine), answers)
    return engine


def _drop_one_fills_charges(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch `to_lot_transactions` so one fill's charges vanish during attribution.

    Shared by the two tests below that need to actually trip the standing-invariant
    guard: dropping one fill's charges is exactly the failure the guard exists to
    catch, and a test that never reaches it proves nothing about what happens when
    it fires.
    """
    import app.analytics.rebuild as rebuild_module

    real = rebuild_module.to_lot_transactions

    def lossy(rows):  # type: ignore[no-untyped-def]
        grouped = real(rows)
        for fills in grouped.values():
            if fills:
                fills[0] = replace(fills[0], charges=Charges.zero())
                break
        return grouped

    monkeypatch.setattr(rebuild_module, "to_lot_transactions", lossy)


def _without_id(row: Lot | LotClosure) -> dict[str, object]:
    return {field: value for field, value in row.model_dump().items() if field != "id"}


def _lot_snapshot(engine: Engine) -> list[dict[str, object]]:
    with Session(engine) as session:
        rows = session.exec(select(Lot).order_by(Lot.method, Lot.source_ref)).all()
    return [_without_id(row) for row in rows]


def _closure_snapshot(engine: Engine) -> list[dict[str, object]]:
    with Session(engine) as session:
        rows = session.exec(
            select(LotClosure).order_by(
                LotClosure.method, LotClosure.lot_source_ref, LotClosure.sale_source_ref
            )
        ).all()
    return [_without_id(row) for row in rows]


class TestTheStandingInvariant:
    def test_attributed_charges_equal_ledger_charges(self, loaded: Engine) -> None:
        """Sec 11.2 #4, asserted in production code rather than only in tests. If
        apportionment ever loses a cent, the rebuild refuses rather than writing a
        set of lots whose fees do not add up to what was actually paid."""
        result = rebuild(loaded, "FIFO")
        assert result.charges_attributed == result.charges_in_ledger

    def test_a_charge_the_matcher_cannot_see_stops_the_rebuild(
        self, loaded: Engine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The guard has to fire on a real discrepancy, or it is decoration.

        Dropping one fill's charges during attribution is exactly the failure it
        exists to catch: apportionment silently loses a cost, every other number
        still looks plausible, and no reader would ever spot it.
        """
        _drop_one_fills_charges(monkeypatch)
        with pytest.raises(ChargeMismatch, match="nothing was written"):
            rebuild(loaded, "FIFO")

    def test_a_refused_rebuild_writes_nothing(
        self, loaded: Engine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A half-written set of lots is worse than none: it looks complete.

        Reaching that claim means actually tripping the guard first -- asserting
        the tables are empty on a fresh, never-rebuilt fixture would pass no
        matter what `rebuild()` does, and would not be testing a refusal at all.
        """
        _drop_one_fills_charges(monkeypatch)
        with pytest.raises(ChargeMismatch):
            rebuild(loaded, "FIFO")

        with Session(loaded) as session:
            assert session.exec(select(Lot)).all() == []
            assert session.exec(select(LotClosure)).all() == []


class TestDeterminism:
    def test_rebuilding_twice_produces_identical_rows(self, loaded: Engine) -> None:
        """Design doc Sec 11.2 #5: double-rebuild produces identical dumps.

        `RebuildResult.lots`/`.closures` are counts -- equal counts would still
        pass if two runs wrote the same number of rows with different contents.
        `rebuild()` assigns each row a fresh `uuid4()`, so `id` legitimately
        differs between runs; every other column must not.
        """
        first = rebuild(loaded, "FIFO")
        first_lots = _lot_snapshot(loaded)
        first_closures = _closure_snapshot(loaded)

        second = rebuild(loaded, "FIFO")
        second_lots = _lot_snapshot(loaded)
        second_closures = _closure_snapshot(loaded)

        assert first.lots == second.lots
        assert first.closures == second.closures
        assert first_lots == second_lots
        assert first_closures == second_closures

    def test_a_rebuild_replaces_rather_than_appends(self, loaded: Engine) -> None:
        rebuild(loaded, "FIFO")
        rebuild(loaded, "FIFO")
        with Session(loaded) as session:
            lots = session.exec(select(Lot).where(Lot.method == "FIFO")).all()
        assert len({lot.source_ref for lot in lots}) == len(lots)

    def test_the_three_methods_coexist(self, loaded: Engine) -> None:
        """Switching method must not destroy the other two, or the UI switcher
        would silently recompute the whole portfolio on every click."""
        for method in ("FIFO", "LIFO", "HIFO"):
            rebuild(loaded, method)
        with Session(loaded) as session:
            methods = {lot.method for lot in session.exec(select(Lot)).all()}
        assert methods == {"FIFO", "LIFO", "HIFO"}


class TestSuppression:
    def test_the_split_legs_never_become_lots(self, loaded: Engine) -> None:
        """The golden split pair is NL0000000003. Its legs are non-economic, so
        matching must not see them -- and the position must reflect the split."""
        rebuild(loaded, "FIFO")
        with Session(loaded) as session:
            lots = session.exec(select(Lot).where(Lot.isin == "NL0000000003")).all()
        # 10 bought pre-split at 10.00, restated by the golden 10:1 into 100 at 1.00.
        assert sum(lot.quantity for lot in lots) == D("100")
        assert sum(lot.cost_basis for lot in lots) == D("100.00")

    def test_a_closure_carries_gross_charges_and_net(self, loaded: Engine) -> None:
        rebuild(loaded, "FIFO")
        with Session(loaded) as session:
            closures = session.exec(select(LotClosure)).all()
        assert closures
        for closure in closures:
            assert closure.pnl == closure.gross_pnl - (
                closure.commission + closure.autofx + closure.tax
            )
