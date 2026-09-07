"""The symbol quarantine as a projection, not a queue.

The same shape as `corporate_action_review`, and for the same reason: a queue
that accumulated would keep asking questions the operator has already answered,
which is the fastest way to train someone to ignore it. Every `fetch-prices` run
rewrites this table to describe the export as it stands.
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal

from sqlmodel import Session, select

from app.db import create_engine_and_tables
from app.domain.symbols import OUT_OF_BAND, Verdict
from app.ingest.symbols import PendingSymbol, ResolutionReport, write_symbol_review
from app.models.market import SymbolReview

D = Decimal
DETECTED = datetime(2026, 9, 6, 12, 0, 0)


def pending(isin: str) -> PendingSymbol:
    return PendingSymbol(
        isin=isin,
        product_name="Example Holdings",
        trade_currency="EUR",
        probed=("EXA2S.DE",),
        verdicts=(
            Verdict(
                symbol="EXA2S.DE",
                accepted=False,
                reason=OUT_OF_BAND,
                ratios=(D("3.33"), D("4.80")),
            ),
        ),
    )


def test_writes_one_row_per_unresolved_instrument() -> None:
    engine = create_engine_and_tables("sqlite://")
    written = write_symbol_review(
        engine,
        ResolutionReport(resolved={}, pending=(pending("NL0000000001"),)),
        detected_at=DETECTED,
    )
    assert written == 1
    with Session(engine) as session:
        row = session.exec(select(SymbolReview)).one()
    assert row.isin == "NL0000000001"
    assert row.trade_currency == "EUR"
    assert row.resolved is False


def test_keeps_the_candidates_and_their_ratios_readable() -> None:
    """The operator answering this needs the evidence, and JSON in one column is
    how a projection carries a variable-length list without a second table that
    would also have to be swept."""
    engine = create_engine_and_tables("sqlite://")
    write_symbol_review(
        engine,
        ResolutionReport(resolved={}, pending=(pending("NL0000000001"),)),
        detected_at=DETECTED,
    )
    with Session(engine) as session:
        row = session.exec(select(SymbolReview)).one()
    payload = json.loads(row.candidates)
    verdicts = payload["verdicts"]
    assert verdicts[0]["symbol"] == "EXA2S.DE"
    assert verdicts[0]["accepted"] is False
    assert verdicts[0]["ratios"] == ["3.33", "4.80"]
    assert OUT_OF_BAND in verdicts[0]["reason"]


def test_the_probed_symbols_survive_into_the_stored_rows_json() -> None:
    """`symbols` (M2 spec section 6.3) needs to tell "the resolver found
    nothing" apart from "candidates were found but none had a price series",
    the same distinction the `fetch-prices` refusal draws -- and this row's
    JSON is the only place that view reads from."""
    engine = create_engine_and_tables("sqlite://")
    write_symbol_review(
        engine,
        ResolutionReport(resolved={}, pending=(pending("NL0000000001"),)),
        detected_at=DETECTED,
    )
    with Session(engine) as session:
        row = session.exec(select(SymbolReview)).one()
    payload = json.loads(row.candidates)
    assert payload["probed"] == ["EXA2S.DE"]


def test_a_rerun_replaces_the_queue_rather_than_appending_to_it() -> None:
    engine = create_engine_and_tables("sqlite://")
    write_symbol_review(
        engine,
        ResolutionReport(resolved={}, pending=(pending("NL0000000001"),)),
        detected_at=DETECTED,
    )
    write_symbol_review(
        engine,
        ResolutionReport(resolved={}, pending=(pending("NL0000000002"),)),
        detected_at=DETECTED,
    )
    with Session(engine) as session:
        rows = session.exec(select(SymbolReview)).all()
    assert [row.isin for row in rows] == ["NL0000000002"]


def test_an_answered_export_leaves_an_empty_queue() -> None:
    engine = create_engine_and_tables("sqlite://")
    write_symbol_review(
        engine,
        ResolutionReport(resolved={}, pending=(pending("NL0000000001"),)),
        detected_at=DETECTED,
    )
    written = write_symbol_review(
        engine,
        ResolutionReport(resolved={"NL0000000001": "EXA.AS"}, pending=()),
        detected_at=DETECTED,
    )
    assert written == 0
    with Session(engine) as session:
        assert session.exec(select(SymbolReview)).all() == []
