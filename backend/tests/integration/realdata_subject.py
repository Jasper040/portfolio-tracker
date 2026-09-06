"""What the opt-in real-data suite asserts against, read from the export itself.

Not a test module -- a source of expectations.

The `realdata` tests used to carry the owner's figures as literals: an ISIN, a
share count, a cost basis. That made the repo hold a slice of a real portfolio,
which design doc Sec 13 forbids and Sec 1's "possibly open-sourced later" makes
worse. Deleting the literals and inventing replacements would have been the
obvious fix and the wrong one -- the tests would then assert against numbers
nobody had checked.

So the expectations are derived from the gitignored export at run time. Nothing
identifying reaches a tracked file, and the assertions get *stronger*: they now
say "the pipeline reproduces what the broker's own statement says", which is the
claim actually worth making. A hand-typed 32 only ever said "the pipeline
reproduces what somebody typed".

Everything here reads the CSVs directly rather than going through `app.ingest`.
That is deliberate: an oracle that shares its parser with the code under test
agrees with it by construction, and would keep agreeing after both broke together.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).parents[3]
EXPORT = REPO / "degiro-export"
ANSWERS = REPO / "config" / "corporate_actions.yaml"

_ZERO = Decimal("0")

# Column positions, per design doc Sec 3.1: the header is misaligned, so every
# reader maps by position and never by name.
_DATE, _TIME, _PRODUCT, _ISIN, _QTY = 0, 1, 2, 3, 6
_LOCAL_VALUE, _VALUE_EUR, _AUTOFX, _FEE, _TOTAL_EUR, _ORDER_ID = 9, 11, 13, 14, 15, 16


def available() -> bool:
    """Whether there is anything to assert against on this machine."""
    return (EXPORT / "Transactions.csv").exists() and ANSWERS.exists()


def _decimal(text: str) -> Decimal | None:
    text = text.strip()
    if not text:
        return None
    return Decimal(text.replace(".", "").replace(",", "."))


def _dutch_date(text: str) -> date:
    day, month, year = text.strip().split("-")
    return date(int(year), int(month), int(day))


@dataclass(frozen=True, slots=True)
class TradeRow:
    """One row of `Transactions.csv`, typed but otherwise verbatim."""

    trade_date: date
    isin: str
    quantity: Decimal
    #: `Local value` -- the trade currency, which is what a
    #: corporate-action key is built from (Sec 6.3).
    gross_local: Decimal
    value_base: Decimal
    fee: Decimal
    autofx: Decimal
    order_ref: str | None


def trades() -> list[TradeRow]:
    rows: list[TradeRow] = []
    with (EXPORT / "Transactions.csv").open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        next(reader)
        for row in reader:
            if not any(cell.strip() for cell in row):
                continue
            rows.append(
                TradeRow(
                    trade_date=_dutch_date(row[_DATE]),
                    isin=row[_ISIN].strip(),
                    quantity=_decimal(row[_QTY]) or _ZERO,
                    gross_local=_decimal(row[_LOCAL_VALUE]) or _ZERO,
                    value_base=_decimal(row[_VALUE_EUR]) or _ZERO,
                    fee=_decimal(row[_FEE]) or _ZERO,
                    autofx=_decimal(row[_AUTOFX]) or _ZERO,
                    order_ref=row[_ORDER_ID].strip() or None,
                )
            )
    return rows


@dataclass(frozen=True, slots=True)
class SplitSubject:
    """The one corporate action that actually changed a share count.

    Found by shape, not by name: a pair of blank-order-id rows on one day and one
    instrument whose share counts differ. That is what a split *is* in this export
    (Sec 3.4), and reading it this way means the test knows which instrument to
    interrogate without the repo ever naming it.
    """

    isin: str
    effective_on: date
    ratio: Decimal
    #: The amount the quarantine key is built from: local currency, not euros.
    local_amount: Decimal
    #: The buy that opened the position being split, and its own charges.
    opened_on: date
    pre_split_quantity: Decimal
    value_base: Decimal
    commission: Decimal
    autofx: Decimal

    @property
    def post_split_quantity(self) -> Decimal:
        return self.pre_split_quantity * self.ratio

    @property
    def post_split_price(self) -> Decimal:
        return abs(self.value_base) / self.post_split_quantity

    @property
    def cost_basis(self) -> Decimal:
        return abs(self.value_base)


def split() -> SplitSubject:
    """The split, plus the lot it restated. Raises if the export has none."""
    groups: dict[tuple[str, date], list[TradeRow]] = {}
    for row in trades():
        if row.order_ref is None and row.quantity != 0:
            groups.setdefault((row.isin, row.trade_date), []).append(row)

    for (isin, effective_on), legs in sorted(groups.items(), key=lambda kv: kv[0][1]):
        out = sum((leg.quantity for leg in legs if leg.quantity < 0), _ZERO)
        into = sum((leg.quantity for leg in legs if leg.quantity > 0), _ZERO)
        if out == 0 or into == 0 or into / abs(out) == 1:
            continue  # a product change swaps the instrument, not the share count

        opening = min(
            (
                r
                for r in trades()
                if r.isin == isin
                and r.order_ref is not None
                and r.quantity > 0
                and r.trade_date < effective_on
            ),
            key=lambda r: r.trade_date,
        )
        return SplitSubject(
            isin=isin,
            effective_on=effective_on,
            ratio=into / abs(out),
            local_amount=abs(legs[0].gross_local),
            opened_on=opening.trade_date,
            pre_split_quantity=opening.quantity,
            value_base=opening.value_base,
            commission=-opening.fee,
            autofx=-opening.autofx,
        )

    raise AssertionError("the export contains no share split to assert against")


def product_change() -> tuple[str, date, Decimal]:
    """The suppressed pair that did NOT change the share count (Sec 3.4).

    Returns the ISIN, the date, and the LOCAL amount the quarantine key is built
    from -- euros would key it to a different number entirely."""
    groups: dict[tuple[str, date], list[TradeRow]] = {}
    for row in trades():
        if row.order_ref is None and row.quantity != 0:
            groups.setdefault((row.isin, row.trade_date), []).append(row)

    for (isin, on), legs in groups.items():
        out = sum((leg.quantity for leg in legs if leg.quantity < 0), _ZERO)
        into = sum((leg.quantity for leg in legs if leg.quantity > 0), _ZERO)
        if out != 0 and into / abs(out) == 1:
            return isin, on, abs(legs[0].gross_local)

    raise AssertionError("the export contains no product change to assert against")


def ledger_charges() -> Decimal:
    """What the broker took on the rows lot matching can see, as money paid.

    Computed straight off the CSV so it is an independent oracle for the standing
    invariant rather than a restatement of the code that enforces it.
    """
    suppressed = {(split().isin, split().effective_on)}
    isin, on, _ = product_change()
    suppressed.add((isin, on))
    return sum(
        (-row.fee - row.autofx for row in trades() if (row.isin, row.trade_date) not in suppressed),
        _ZERO,
    )


def quarantine_key(isin: str, on: date, local_amount: Decimal) -> str:
    """The `isin:date:amount` key `config/corporate_actions.yaml` is written against.

    Built here rather than imported from `app.ingest.corporate_actions` so the test
    asserts the key the *operator* would write, not the one the code happens to
    generate -- the two agreeing is the thing worth checking.
    """
    return f"{isin}:{on.isoformat()}:{local_amount:.2f}"


def split_key() -> str:
    subject = split()
    return quarantine_key(subject.isin, subject.effective_on, subject.local_amount)


def product_change_key() -> str:
    return quarantine_key(*product_change())


def suppressed_isins() -> set[str]:
    """Both corporate actions' instruments -- what M0's quarantine flags."""
    return {split().isin, product_change()[0]}


def broker_cash_balance() -> Decimal:
    """The combined DeGiro + flatex cash line from `Portfolio.csv`."""
    with (EXPORT / "Portfolio.csv").open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.reader(handle):
            if row and row[0].strip().startswith("CASH & CASH FUND"):
                return _decimal(row[6]) or _ZERO
    raise AssertionError("Portfolio.csv states no cash line")


def transaction_fee_total() -> Decimal:
    """Every `Transactiekosten` row in `Account.csv`, summed. Sec 3.6's invariant
    says this equals the trade file's fee column, which is why it is worth an
    independent oracle."""
    total = _ZERO
    with (EXPORT / "Account.csv").open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        next(reader)
        for row in reader:
            if len(row) > 8 and row[5].strip().casefold().startswith(
                "degiro transactiekosten"
            ):
                total += _decimal(row[8]) or _ZERO
    return total
