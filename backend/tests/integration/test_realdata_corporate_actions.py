"""Opt-in suite: corporate-action detection against the owner's real exports.

The golden fixture proves the mechanism; this proves the two events the design doc
actually names (Sec 6.3): the ORION 10-for-1 split of 2025-02-18 and the United
Meridian Mining product change of 2026-08-14. Both are labelled in `Account.csv`,
so a green run here means the join found every event the broker declared -- and,
just as importantly, invented none.

Never runs in CI: the exports are gitignored, so these tests skip themselves when
the directory is absent.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.ingest.corporate_actions import (
    PRODUCT_CHANGE,
    SPLIT,
    CorporateAction,
    detect,
)
from app.ingest.degiro.account_csv import parse_account_csv
from app.ingest.degiro.transactions_csv import parse_transactions_csv
from tests.integration import realdata_subject as subject

EXPORT = Path(__file__).parents[3] / "degiro-export"
REAL_TRANSACTIONS = EXPORT / "Transactions.csv"
REAL_ACCOUNT = EXPORT / "Account.csv"

pytestmark = [
    pytest.mark.realdata,
    pytest.mark.skipif(
        not (REAL_TRANSACTIONS.exists() and REAL_ACCOUNT.exists()),
        reason="real DeGiro export not present",
    ),
]

# Read from the export rather than written down -- see `realdata_subject`.
ORION_KEY = subject.split_key() if subject.available() else ""
MERIDIAN_KEY = subject.product_change_key() if subject.available() else ""


def _found() -> tuple[CorporateAction, ...]:
    return detect(parse_transactions_csv(REAL_TRANSACTIONS), parse_account_csv(REAL_ACCOUNT))


def test_finds_exactly_the_two_declared_events() -> None:
    """Two, not three. A false positive quarantines a real trade and blocks import."""
    assert [candidate.key for candidate in _found()] == [ORION_KEY, MERIDIAN_KEY]


def test_account_csv_names_every_candidate() -> None:
    """Nothing falls through to the Sec 3.4 secondary heuristic in this export.

    An UNLABELLED result here would mean DeGiro has changed its wording, which is
    the failure the heuristic exists to catch rather than to absorb.
    """
    assert [candidate.kind for candidate in _found()] == [SPLIT, PRODUCT_CHANGE]


def test_the_split_joins_on_the_local_amount() -> None:
    """The LOCAL amount, not the euro figure the same rows also carry.

    Sec 3.4 joins the two files on the trade-currency amount; keying on euros
    would match nothing on a foreign-currency action."""
    orion = next(c for c in _found() if c.key == ORION_KEY)
    assert orion.local_amount == subject.split().local_amount
    assert orion.label.startswith("SPLIT AANPASSING")


def test_the_product_change_joins_on_the_local_amount() -> None:
    """The local amount again, where `Value EUR` reads something else entirely
    -- the currency trap, in the
    only place it can be observed against real broker data."""
    meridian = next(c for c in _found() if c.key == MERIDIAN_KEY)
    assert meridian.local_amount == subject.product_change()[2]
    assert meridian.label.startswith("PRODUCTWIJZIGING")


def test_covers_exactly_the_four_blank_order_id_rows() -> None:
    """The whole value shape, asserted against the file: 112 rows, 4 blank ids, and
    the four the detector claims must be precisely those four."""
    transactions = parse_transactions_csv(REAL_TRANSACTIONS)
    blank = {row.source_ref for row in transactions if not row.order_ref}
    covered = {ref for candidate in _found() for ref in candidate.source_refs}
    assert len(blank) == 4
    assert covered == blank
