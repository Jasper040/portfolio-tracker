"""The corporate-action quarantine gate (design doc Sec 6.3).

"Import refuses to complete until each is resolved" is the whole point: a split
that slips through does not announce itself, it just quietly makes every realised
figure for that instrument wrong. So the refusal has to be absolute -- no rows at
all, not a partial ledger with a warning printed above it.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlmodel import Session, select

from app.db import create_engine_and_tables
from app.ingest.importer import QuarantineError, ensure_default_account, import_degiro_export
from app.models.ledger import CorporateActionReview, ImportBatch, Transaction

GOLDEN = Path(__file__).parents[1] / "golden"

SPLIT_KEY = "NL0000000003:2025-01-17:100.00"
PRODUCT_CHANGE_KEY = "US0000000002:2025-01-18:100.00"

RESOLVE_BOTH = f"""\
resolutions:
  - key: {SPLIT_KEY}
    treatment: corporate_action
    note: 10-for-1 split
  - key: {PRODUCT_CHANGE_KEY}
    treatment: corporate_action
    note: product change
"""


def _engine() -> Engine:
    return create_engine_and_tables("sqlite://")


@pytest.fixture
def export(tmp_path: Path) -> Path:
    directory = tmp_path / "degiro-export"
    directory.mkdir()
    shutil.copy(GOLDEN / "degiro_transactions_golden.csv", directory / "Transactions.csv")
    shutil.copy(GOLDEN / "degiro_account_golden.csv", directory / "Account.csv")
    return directory


def _resolutions(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "corporate_actions.yaml"
    path.write_text(body, encoding="utf-8")
    return path


class TestRefusal:
    def test_refuses_an_import_with_an_unresolved_corporate_action(
        self, export: Path, tmp_path: Path
    ) -> None:
        engine = _engine()
        with pytest.raises(QuarantineError) as raised:
            import_degiro_export(
                engine, export, ensure_default_account(engine), tmp_path / "absent.yaml"
            )
        assert {c.key for c in raised.value.pending} == {SPLIT_KEY, PRODUCT_CHANGE_KEY}

    def test_a_refused_import_leaves_no_rows_and_no_batch(
        self, export: Path, tmp_path: Path
    ) -> None:
        """A partial ledger is worse than none: it looks complete."""
        engine = _engine()
        with pytest.raises(QuarantineError):
            import_degiro_export(
                engine, export, ensure_default_account(engine), tmp_path / "absent.yaml"
            )
        with Session(engine) as session:
            assert session.exec(select(Transaction)).all() == []
            assert session.exec(select(ImportBatch)).all() == []

    def test_a_partially_resolved_export_is_still_refused(
        self, export: Path, tmp_path: Path
    ) -> None:
        engine = _engine()
        body = f"resolutions:\n  - key: {SPLIT_KEY}\n    treatment: corporate_action\n"
        with pytest.raises(QuarantineError) as raised:
            import_degiro_export(
                engine, export, ensure_default_account(engine), _resolutions(tmp_path, body)
            )
        assert [c.key for c in raised.value.pending] == [PRODUCT_CHANGE_KEY]


class TestReviewQueue:
    def test_writes_every_candidate_to_the_review_table(
        self, export: Path, tmp_path: Path
    ) -> None:
        """The refusal has to say what to answer, and it has to survive the process
        that raised it -- the operator edits the YAML in a different terminal."""
        engine = _engine()
        with pytest.raises(QuarantineError):
            import_degiro_export(
                engine, export, ensure_default_account(engine), tmp_path / "absent.yaml"
            )
        with Session(engine) as session:
            reviews = session.exec(select(CorporateActionReview)).all()
        assert {r.key for r in reviews} == {SPLIT_KEY, PRODUCT_CHANGE_KEY}
        assert {r.kind for r in reviews} == {"SPLIT", "PRODUCT_CHANGE"}
        assert all(r.resolved is False for r in reviews)

    def test_a_resolved_candidate_is_recorded_as_resolved(
        self, export: Path, tmp_path: Path
    ) -> None:
        engine = _engine()
        import_degiro_export(
            engine,
            export,
            ensure_default_account(engine),
            _resolutions(tmp_path, RESOLVE_BOTH),
        )
        with Session(engine) as session:
            reviews = session.exec(select(CorporateActionReview)).all()
        assert len(reviews) == 2
        assert all(r.resolved is True for r in reviews)

    def test_the_queue_is_rebuilt_rather_than_appended_to(
        self, export: Path, tmp_path: Path
    ) -> None:
        """It is a projection of (export, resolutions), not an accumulating log. A
        stale row would send the operator to answer a question already answered."""
        engine = _engine()
        account_id = ensure_default_account(engine)
        with pytest.raises(QuarantineError):
            import_degiro_export(engine, export, account_id, tmp_path / "absent.yaml")
        import_degiro_export(engine, export, account_id, _resolutions(tmp_path, RESOLVE_BOTH))
        with Session(engine) as session:
            reviews = session.exec(select(CorporateActionReview)).all()
        assert len(reviews) == 2


class TestSuppression:
    def _import(self, engine: Engine, export: Path, tmp_path: Path, body: str) -> None:
        import_degiro_export(
            engine, export, ensure_default_account(engine), _resolutions(tmp_path, body)
        )

    def test_a_resolved_corporate_action_lands_as_non_economic(
        self, export: Path, tmp_path: Path
    ) -> None:
        """The rows stay in the ledger -- it is append-only and they really are in
        the export -- but they are flagged so lot matching skips them."""
        engine = _engine()
        self._import(engine, export, tmp_path, RESOLVE_BOTH)
        with Session(engine) as session:
            rows = session.exec(select(Transaction)).all()
        non_economic = [r for r in rows if not r.is_economic]
        assert len(rows) == 15
        assert len(non_economic) == 4

    def test_a_suppressed_row_records_why_it_was_suppressed(
        self, export: Path, tmp_path: Path
    ) -> None:
        """`closure_reason` is deliberately left alone: its vocabulary describes why
        a LOT closed, and a suppressed row never closes one. The note carries the
        event key instead, so a row flagged non-economic can be traced back to the
        resolution that flagged it without joining anything."""
        engine = _engine()
        self._import(engine, export, tmp_path, RESOLVE_BOTH)
        with Session(engine) as session:
            rows = session.exec(select(Transaction)).all()
        non_economic = [r for r in rows if not r.is_economic]
        notes = [row.note or "" for row in non_economic]
        assert {r.closure_reason for r in non_economic} == {"DECISION"}
        assert sum(1 for note in notes if SPLIT_KEY in note) == 2
        assert sum(1 for note in notes if PRODUCT_CHANGE_KEY in note) == 2

    def test_the_suppressed_rows_are_exactly_the_blank_order_id_pairs(
        self, export: Path, tmp_path: Path
    ) -> None:
        engine = _engine()
        self._import(engine, export, tmp_path, RESOLVE_BOTH)
        with Session(engine) as session:
            rows = session.exec(select(Transaction)).all()
        assert {r.order_ref for r in rows if not r.is_economic} == {None}
        assert all(r.is_economic for r in rows if r.order_ref)

    def test_a_candidate_resolved_as_a_trade_stays_economic(
        self, export: Path, tmp_path: Path
    ) -> None:
        body = (
            f"resolutions:\n"
            f"  - key: {SPLIT_KEY}\n    treatment: trade\n"
            f"  - key: {PRODUCT_CHANGE_KEY}\n    treatment: trade\n"
        )
        engine = _engine()
        self._import(engine, export, tmp_path, body)
        with Session(engine) as session:
            rows = session.exec(select(Transaction)).all()
        assert all(r.is_economic for r in rows)
