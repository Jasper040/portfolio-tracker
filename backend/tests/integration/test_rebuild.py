"""Rebuilding lots from the ledger (design doc Sec 11.2 #5).

`rebuild()` is the claim that every derived number in this app can be recomputed
from the ledger alone. That claim is only worth something if it is checked, so the
determinism tests here are not decoration: they are the property.
"""

from __future__ import annotations

import shutil
from collections.abc import Sequence
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import Engine
from sqlmodel import Session, select

from app.analytics.rebuild import ChargeMismatch, rebuild
from app.db import create_engine_and_tables
from app.domain.charges import Charges
from app.ingest.importer import ensure_default_account, import_degiro_export
from app.models.ledger import Account, ImportBatch, Lot, LotClosure, Transaction

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


SYNTHETIC_ISIN = "NL0000000009"

#: `(source_ref, trade_date, quantity, value_base, fee_base)`. A positive quantity
#: is a purchase and a negative one a sale, which is the export's own convention;
#: `value_base` is DeGiro's "Value EUR" for the movement and `fee_base` is a debit,
#: so it is negative.
SyntheticFill = tuple[str, str, str, str, str]


def _synthetic_ledger(fills: Sequence[SyntheticFill]) -> Engine:
    """An engine holding exactly `fills`, inserted straight into the session.

    Written in code rather than as another golden CSV deliberately: the golden
    files' row counts are asserted in a dozen places by M0's suite, so a fixture
    that can only be extended by moving those is a fixture nobody extends. The
    style follows `tests/unit/test_models.py`, which already builds an Account, an
    ImportBatch and Transaction rows by hand.
    """
    engine = _engine()
    account_id = uuid4()
    batch_id = uuid4()
    with Session(engine) as session:
        session.add(
            Account(
                id=account_id, broker="degiro", name="Synthetic", base_currency="EUR"
            )
        )
        session.add(
            ImportBatch(
                id=batch_id,
                source="degiro",
                filename="synthetic",
                file_sha256="synthetic",
                parser_version="1",
                imported_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                row_count=len(fills),
                inserted_count=len(fills),
            )
        )
        for ref, day, quantity, value, fee in fills:
            session.add(
                Transaction(
                    id=uuid4(),
                    account_id=account_id,
                    import_batch_id=batch_id,
                    source="degiro",
                    source_ref=ref,
                    txn_type="SELL" if quantity.startswith("-") else "BUY",
                    trade_date=date.fromisoformat(day),
                    isin=SYNTHETIC_ISIN,
                    quantity=D(quantity),
                    value_base=D(value),
                    fee_base=D(fee),
                    tax_base=D("0.00"),
                    net_base=D(value) + D(fee),
                    is_economic=True,
                    closure_reason="DECISION",
                    raw_json="{}",
                )
            )
        session.commit()
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


class TestASaleWithNoMatchingBuy:
    """An instrument whose purchases predate the export window.

    The sale closes no lot, so no closure exists to carry its charges. Before
    `MatchResult.unmatched_charges` they were silently discarded: `attributed` fell
    short of the ledger and `rebuild()` refused EVERY method for the WHOLE ledger,
    with a message blaming apportionment. One such instrument would have bricked
    the application, and neither dataset happens to contain one.
    """

    FILLS: tuple[SyntheticFill, ...] = (
        ("syn-orphan-sell", "2024-04-01", "-10", "400.00", "-1.50"),
        ("syn-later-buy", "2024-05-01", "4", "-80.00", "-0.50"),
    )

    def test_the_rebuild_completes_rather_than_refusing(self) -> None:
        result = rebuild(_synthetic_ledger(self.FILLS), "FIFO")
        assert result.closures == 0
        assert result.lots == 1
        assert result.charges_attributed == result.charges_in_ledger
        assert result.charges_in_ledger == D("2.00")

    def test_the_orphaned_sales_charges_reach_the_attributed_side(self) -> None:
        """1.50 of the 2.00 belongs to no lot and no closure. It is counted through
        `unmatched_charges`; without that channel `attributed` would come to 0.50
        against a ledger of 2.00 and the rebuild would refuse."""
        engine = _synthetic_ledger(self.FILLS)
        result = rebuild(engine, "FIFO")
        with Session(engine) as session:
            lots = session.exec(select(Lot)).all()
        assert sum((lot.commission for lot in lots), D("0")) == D("0.50")
        assert result.charges_attributed == D("2.00")


class TestMethodDivergenceSurvivesThePipeline:
    """Sec 7.1's premise, end to end rather than at the matcher.

    `tests/unit/test_lots.py` proves the three algorithms disagree and
    `test_lots_api.py` proves each method is served from its own stored rows.
    Neither proves the disagreement SURVIVES `to_lot_transactions` ->
    `apply_splits` -> `rebuild`, and both real datasets are blind to it: the golden
    fixture gives every instrument exactly one buy lot and the owner's export closes
    each position in full, so all three methods produce identical closures on both.
    A regrouping bug that collapsed multi-lot structure would pass everything else
    in this suite.
    """

    #: Three purchases and one sale of one instrument. Three lots, not two: HIFO
    #: picks the dearest, which with only two lots is necessarily also the oldest or
    #: the newest, so two methods would tie however the prices were chosen. Here the
    #: dearest lot (30) is the middle one, so FIFO, LIFO and HIFO each pick a
    #: different lot and the sale can consume exactly one of them.
    FILLS: tuple[SyntheticFill, ...] = (
        ("syn-buy-at-20", "2024-01-01", "10", "-200.00", "-1.00"),
        ("syn-buy-at-30", "2024-02-01", "10", "-300.00", "-1.00"),
        ("syn-buy-at-10", "2024-03-01", "10", "-100.00", "-1.00"),
        ("syn-sell-at-40", "2024-04-01", "-10", "400.00", "-1.00"),
    )

    def test_the_three_methods_realise_three_different_totals(self) -> None:
        engine = _synthetic_ledger(self.FILLS)
        realised: dict[str, Decimal] = {}
        for method in ("FIFO", "LIFO", "HIFO"):
            result = rebuild(engine, method)  # type: ignore[arg-type]
            assert result.charges_attributed == result.charges_in_ledger
            with Session(engine) as session:
                closures = session.exec(
                    select(LotClosure).where(LotClosure.method == method)
                ).all()
            assert len(closures) == 1
            realised[method] = sum((c.pnl for c in closures), D("0"))

        # Gross 200 / 300 / 100 on the 20, 10 and 30 lots respectively, each less
        # the 2.00 the round trip cost: 1.00 from the buy leg (the lot is fully
        # consumed, so it keeps none of it) and the sale's whole 1.00.
        assert realised["FIFO"] == D("198.00")
        assert realised["LIFO"] == D("298.00")
        assert realised["HIFO"] == D("98.00")
        assert len(set(realised.values())) == 3

    def test_each_method_leaves_a_different_pair_of_lots_open(self) -> None:
        """The other half of the same fact: the method decides which basis was
        consumed, so the basis left behind differs too -- while the quantity, which
        the method must never change, does not."""
        engine = _synthetic_ledger(self.FILLS)
        remaining: dict[str, Decimal] = {}
        for method in ("FIFO", "LIFO", "HIFO"):
            rebuild(engine, method)  # type: ignore[arg-type]
            with Session(engine) as session:
                lots = session.exec(select(Lot).where(Lot.method == method)).all()
            assert sum((lot.quantity for lot in lots), D("0")) == D("20")
            remaining[method] = sum((lot.cost_basis for lot in lots), D("0"))

        assert remaining["FIFO"] == D("400.00")  # the 30 and 10 lots remain
        assert remaining["LIFO"] == D("500.00")  # the 20 and 30 lots remain
        assert remaining["HIFO"] == D("300.00")  # the 20 and 10 lots remain


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
