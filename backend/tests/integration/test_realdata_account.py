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
from decimal import Decimal
from pathlib import Path

import pytest

from app.ingest.degiro.account_csv import AccountRow, parse_account_csv
from app.ingest.degiro.portfolio_csv import parse_portfolio_csv
from app.ingest.degiro.transactions_csv import parse_transactions_csv
from app.ingest.reconcile import AGGREGATE_TOLERANCE, combined_eur_cash, reconcile

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


REAL_TXNS = REAL.parent / "Transactions.csv"
REAL_PORTFOLIO = REAL.parent / "Portfolio.csv"


@pytest.mark.skipif(not REAL_TXNS.exists(), reason="real DeGiro export not present")
def test_all_cross_file_invariants_are_green() -> None:
    """Sec 3.6, the whole table, against the real exports.

    This is what M0's "cross-file reconciliation report green" means. Each figure
    below is the design doc's own, derived by hand from these files.
    """

    report = reconcile(
        parse_transactions_csv(REAL_TXNS),
        _rows(),
        parse_portfolio_csv(REAL_PORTFOLIO),
    )
    assert report.ok, [(i.name, i.expected, i.actual, i.detail) for i in report.failures]
    assert len(report.invariants) == 4


@pytest.mark.skipif(not REAL_TXNS.exists(), reason="real DeGiro export not present")
def test_commission_total_is_the_published_figure() -> None:

    account_fees = sum(
        (
            r.change
            for r in _rows()
            if r.description.casefold().startswith("degiro transactiekosten")
        )
    )
    ledger_fees = sum(t.fee_base for t in parse_transactions_csv(REAL_TXNS))
    assert account_fees == ledger_fees == Decimal("-252.00")


@pytest.mark.skipif(not REAL_TXNS.exists(), reason="real DeGiro export not present")
def test_one_hundred_and_four_orders_carry_one_commission_each() -> None:

    fee_rows = [
        r for r in _rows() if r.description.casefold().startswith("degiro transactiekosten")
    ]
    order_ids = {t.order_ref for t in parse_transactions_csv(REAL_TXNS) if t.order_ref}
    assert len(order_ids) == len(fee_rows) == 104


@pytest.mark.skipif(not REAL_PORTFOLIO.exists(), reason="real DeGiro export not present")
def test_computed_cash_reproduces_the_brokers_own_balance() -> None:
    """The invariant Sec 3.6 left "pending M0", now closed.

    Portfolio.csv reports ONE combined cash line covering the DeGiro cash account
    and the flatex bank account, so the sweep between them nets out and everything
    else counts. Computed -2241.15 against a stated -2241.16: a cent, well inside
    the Sec 5.4 aggregate tolerance of EUR 0.50, and consistent with the broker's
    own arithmetic disagreeing with itself by a cent on 14 of 112 rows.
    """

    stated = parse_portfolio_csv(REAL_PORTFOLIO).cash_base
    computed = combined_eur_cash(_rows())
    assert stated == Decimal("-2241.16")
    assert abs(computed - stated) <= AGGREGATE_TOLERANCE


@pytest.mark.skipif(not REAL_PORTFOLIO.exists(), reason="real DeGiro export not present")
def test_the_flatex_rows_are_real_cash_movements_not_an_internal_pair() -> None:
    """Resolves the discrepancy recorded above, and contradicts Sec 3.2.

    Cash reconciles only when the three flatex rows are counted. Excluding them as
    "an offsetting internal pair" leaves computed cash 8000.00 too high, far outside
    any tolerance. So EUR 8000 genuinely left the combined pot, and SEPA Instant
    Terugstorting is NOT the account's only external outflow.

    This matters beyond bookkeeping: an 8000 withdrawal misclassified as internal
    would distort MWR, which weights by when money actually moved.
    """

    stated = parse_portfolio_csv(REAL_PORTFOLIO).cash_base
    rows = _rows()
    with_flatex = combined_eur_cash(rows)
    without_flatex = combined_eur_cash(
        [
            r
            for r in rows
            if not r.description.casefold().startswith(
                ("processed flatex", "flatex terugstorting")
            )
        ]
    )
    assert abs(with_flatex - stated) <= Decimal("0.50")
    assert without_flatex - with_flatex == Decimal("8000.00")


@pytest.mark.skipif(not REAL_PORTFOLIO.exists(), reason="real DeGiro export not present")
def test_portfolio_snapshot_carries_the_m1_target() -> None:
    """ORN = 32 is the number M1's corporate-action path has to reproduce."""

    snapshot = parse_portfolio_csv(REAL_PORTFOLIO)
    assert len(snapshot.positions) == 6
    assert snapshot.quantity_of("US0000000901") == Decimal("32")
