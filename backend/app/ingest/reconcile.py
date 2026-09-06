"""Cross-file reconciliation (design doc Sec 3.6).

The two DeGiro exports overlap, and the overlap is free evidence: every trade's
commission appears once in each file, every order id appears in both, and the cash
book must reproduce the broker's own closing balance. Checking those agreements
turns "the parser ran without raising" into "the parser read the same account the
broker did".

M0's stated outcome is a green report here, which is why this returns a report of
named invariants rather than raising on the first failure -- knowing that three of
four passed and exactly which one did not is the difference between a diagnosis and
a bisect.

Pure: parsed rows in, findings out. No I/O, no ORM.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.ingest.base import NormalisedRow
from app.ingest.degiro.account_csv import AccountRow
from app.ingest.degiro.portfolio_csv import PortfolioSnapshot

#: Sec 5.4: broker arithmetic disagrees with itself by a cent on 14 of 112 rows, so
#: an aggregate is reconciled to EUR 0.50 rather than to the cent. Anything larger
#: than this is a real disagreement, not rounding.
AGGREGATE_TOLERANCE = Decimal("0.50")

_TRANSACTION_FEE_PREFIX = "degiro transactiekosten"
_CASH_SWEEP_PREFIX = "degiro cash sweep"


@dataclass(frozen=True, slots=True)
class Invariant:
    name: str
    ok: bool
    expected: str
    actual: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    invariants: tuple[Invariant, ...]

    @property
    def ok(self) -> bool:
        return all(invariant.ok for invariant in self.invariants)

    @property
    def failures(self) -> tuple[Invariant, ...]:
        return tuple(invariant for invariant in self.invariants if not invariant.ok)


def _is(row: AccountRow, prefix: str) -> bool:
    return row.description.casefold().startswith(prefix)


def combined_eur_cash(account: list[AccountRow]) -> Decimal:
    """Reproduce Portfolio.csv's `CASH & CASH FUND & FTX CASH (EUR)` line.

    That line is COMBINED: it covers the DeGiro investment cash account and the
    flatex bank account as one pot. So every EUR movement counts except the sweep
    between them, which merely moves money from one pocket to the other.

    Only the `Degiro Cash Sweep Transfer` leg needs excluding. Its mirror,
    `Overboeking naar/van uw geldrekening`, carries no amount in the Change column
    at all -- DeGiro puts it in the description text (Sec 3.2) -- so it contributes
    nothing and needs no special case.

    Note this deliberately uses Account.csv's own `Koop`/`Verkoop` cash amounts
    rather than `net_base` from Transactions.csv. For a foreign-currency trade the
    EUR movement happens on the `Valuta Creditering` row, not on the trade; adding
    `net_base` as well would count the same purchase twice, once in USD and once in
    EUR.
    """
    return sum(
        (
            row.change
            for row in account
            if row.change is not None
            and row.change_currency == "EUR"
            and not _is(row, _CASH_SWEEP_PREFIX)
        ),
        Decimal(0),
    )


def reconcile(
    transactions: list[NormalisedRow],
    account: list[AccountRow],
    portfolio: PortfolioSnapshot | None = None,
) -> ReconciliationReport:
    """Check every cross-file invariant that can be checked from parsed files.

    `portfolio` is optional because the first three invariants compare the two
    exports against each other and need nothing else. Passing it adds the cash
    check, which is the one that catches a misclassified transfer.
    """
    fee_rows = [row for row in account if _is(row, _TRANSACTION_FEE_PREFIX)]

    account_fees = sum((row.change for row in fee_rows if row.change is not None), Decimal(0))
    ledger_fees = sum((row.fee_base for row in transactions), Decimal(0))

    transaction_ids = {row.order_ref for row in transactions if row.order_ref}
    fee_ids = {row.order_ref for row in fee_rows if row.order_ref}
    orphaned_in_transactions = transaction_ids - fee_ids
    orphaned_in_account = fee_ids - transaction_ids

    invariants = [
        Invariant(
            name="commission totals agree between the two files",
            ok=abs(account_fees - ledger_fees) <= AGGREGATE_TOLERANCE,
            expected=f"Account.csv Transactiekosten == Transactions.csv fees ({account_fees})",
            actual=str(ledger_fees),
        ),
        Invariant(
            name="exactly one commission row per order",
            # The direct evidence for order-level fee attribution (Sec 6.4): the
            # commission is an attribute of the ORDER, and the fill row it happens
            # to land on in Transactions.csv is arbitrary.
            ok=len(transaction_ids) == len(fee_rows) == len(fee_ids),
            expected=f"{len(transaction_ids)} distinct order ids == one fee row each",
            actual=f"{len(fee_rows)} fee rows, {len(fee_ids)} distinct ids",
        ),
        Invariant(
            name="order ids match in both directions",
            ok=not orphaned_in_transactions and not orphaned_in_account,
            expected="0 orphans either way",
            actual=(
                f"{len(orphaned_in_transactions)} in Transactions.csv only, "
                f"{len(orphaned_in_account)} in Account.csv only"
            ),
            detail=", ".join(sorted(orphaned_in_transactions | orphaned_in_account)[:5]),
        ),
    ]

    if portfolio is not None:
        computed = combined_eur_cash(account)
        difference = computed - portfolio.cash_base
        invariants.append(
            Invariant(
                name="computed EUR cash matches Portfolio.csv",
                ok=abs(difference) <= AGGREGATE_TOLERANCE,
                expected=str(portfolio.cash_base),
                actual=str(computed),
                detail=f"difference {difference}",
            )
        )

    return ReconciliationReport(invariants=tuple(invariants))
