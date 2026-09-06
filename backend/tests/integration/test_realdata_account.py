"""Opt-in suite: the Account.csv classifier against the owner's real export.

Design doc Sec 3.2 publishes an exact row count for every pattern, derived by hand
from this file. That makes it the strongest available check on the classifier --
stronger than any synthetic fixture, because a rule that is merely plausible will
still land on the wrong count here.

Never runs in CI: the export is gitignored, so these tests skip themselves when the
directory is absent.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from app.ingest.degiro.account_csv import AccountRow, parse_account_csv

REAL = Path(__file__).parents[3] / "degiro-export" / "Account.csv"

pytestmark = [
    pytest.mark.realdata,
    pytest.mark.skipif(not REAL.exists(), reason="real DeGiro export not present"),
]


def _rows() -> list[AccountRow]:
    return parse_account_csv(REAL)


def test_reads_every_row() -> None:
    assert len(_rows()) == 785


def test_every_description_is_recognised() -> None:
    """Zero unknowns across 252 distinct free-text descriptions.

    This is the test that catches DeGiro changing its wording: Sec 3.4 notes they
    have done it before, and an unrecognised row is neither kept nor dropped, so a
    silent behaviour change is impossible.
    """
    unrecognised = sorted({r.description for r in _rows() if not r.action.recognised})
    assert unrecognised == []


def test_kept_row_counts_match_the_published_taxonomy() -> None:
    """Every count in the Sec 3.2 table, asserted at once."""
    kept = Counter(r.action.txn_type for r in _rows() if r.action.keep)
    assert kept == Counter(
        {
            "FX_CONVERT": 172,
            "DIVIDEND": 52,
            "DIVIDEND_TAX": 38,
            "FEE": 16,
            "INTEREST": 13,
            "DEPOSIT": 11,
            "SECURITIES_LENDING": 7,
            "CORPORATE_ACTION": 4,
            "TAX": 2,
            "WITHDRAWAL": 1,
        }
    )


def test_only_eleven_deposits_and_one_withdrawal_survive_the_sweep_trap() -> None:
    """Sec 3.3, the highest-risk classification in the project.

    256 rows carry real signed euro amounts and plausible running balances while
    being internal transfers. If any leaked through, this count would be wrong and
    MWR would be meaningless -- and nothing in the numbers would show it.
    """
    rows = _rows()
    assert sum(1 for r in rows if r.action.txn_type == "DEPOSIT") == 11
    assert sum(1 for r in rows if r.action.txn_type == "WITHDRAWAL") == 1


def test_dropped_row_counts_match_the_published_taxonomy() -> None:
    def bucket(description: str) -> str:
        text = description.casefold()
        for prefix in (
            "koop ",
            "verkoop ",
            "degiro transactiekosten",
            "degiro cash sweep",
            "overboeking",
            "reservation ideal",
            "processed flatex",
            "flatex terugstorting",
        ):
            if text.startswith(prefix):
                return prefix
        return "?"

    dropped = Counter(bucket(r.description) for r in _rows() if not r.action.keep)
    assert dropped["koop "] + dropped["verkoop "] == 108
    assert dropped["degiro cash sweep"] == 116
    assert dropped["overboeking"] == 116
    assert dropped["degiro transactiekosten"] == 104
    assert dropped["reservation ideal"] == 22
    assert dropped["?"] == 0


def test_the_flatex_withdrawal_pattern_has_three_rows_not_two() -> None:
    """DISCREPANCY WITH THE DESIGN DOC, recorded here rather than papered over.

    Sec 3.2 lists `Processed Flatex Withdrawal` / `flatex terugstorting` as 2 rows,
    an "offsetting internal pair (+8000 / -8000, 04-03-2026)". The file holds three:

        2026-03-03  Processed Flatex Withdrawal  -8000.00
        2026-03-04  Processed Flatex Withdrawal  +8000.00
        2026-03-04  flatex terugstorting         -8000.00

    The 04-03 pair does offset. The 03-03 row is extra and unpaired, so the three
    together net to -8000.00 rather than zero. Dropping all three -- which the
    taxonomy says to do -- therefore removes EUR 8000 of cash movement.

    Whether that is correct depends on whether the money left the flatex bank
    account or merely moved within it, and the Sec 3.6 cash invariant
    (computed EUR cash == Portfolio.csv, -2241.16) is the test that settles it.
    That invariant is not implemented yet, so this test pins the observed shape so
    the question cannot be silently lost.
    """
    rows = [
        r
        for r in _rows()
        if r.description.casefold().startswith(("processed flatex", "flatex terugstorting"))
    ]
    assert len(rows) == 3
    assert sum(r.change for r in rows if r.change is not None) == -8000
