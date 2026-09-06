"""Parser for DeGiro's Account.csv (the cash book).

`Transactions.csv` is authoritative for trades; this file is authoritative for
everything else (design doc Sec 6.2). It is also the only place corporate actions
are named (Sec 3.4) and the only place the genuine deposits and withdrawals can be
told apart from internal transfers (Sec 3.3).

That last point is why this is the highest-risk parser in the project. 256 of 785
rows look exactly like external cash flows and are not: DeGiro's own sweep between
the investment account and the flatex bank account, iDEAL reservation pairs, and an
offsetting flatex withdrawal pair. They carry real signed euro amounts and plausible
running balances, so nothing in the numbers distinguishes them -- only the
description does. Book them as deposits and MWR becomes meaningless while TWR's
sub-period boundaries fragment into noise.

Classification is therefore prefix-based on free text, which brings its own trap:
`Dividendbelasting` starts with `Dividend`. Rules are ordered most-specific-first
and an unrecognised description is neither kept nor silently dropped -- it is
flagged, because Sec 3.4 notes DeGiro has already changed its wording once.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from app.ingest.degiro.dialect import (
    ACCOUNT_HEADER,
    ACCOUNT_RAW_FIELDS,
    AcctCol,
    assert_header,
    parse_dutch_date,
    parse_optional_decimal,
)

PARSER_VERSION = "degiro-account-1"
SOURCE = "degiro"


class MalformedRow(Exception):
    """A row could not be parsed. Carries the file and line so it can be found."""


@dataclass(frozen=True, slots=True)
class AccountAction:
    """What to do with a row, and why.

    `txn_type` is `None` whenever `keep` is False. Carrying a type on a dropped row
    would invite a later change to import it by accident -- which is precisely the
    cash-sweep failure this parser exists to prevent.
    """

    keep: bool
    txn_type: str | None
    #: False when no rule matched. Distinguishes "deliberately dropped" from
    #: "this parser has never seen this wording", which must not look the same.
    recognised: bool
    #: True for fees that belong to the portfolio rather than to any lot
    #: (Sec 6.4: `Aansluitingskosten` is never attributed to a position).
    portfolio_level: bool = False
    note: str = ""


_DROP = "dropped"

#: Ordered rules, matched by case-insensitive prefix. ORDER IS LOAD-BEARING:
#: `Dividendbelasting` must be tested before `Dividend`, or 38 withholding rows book
#: as income and every dividend figure in the app is inflated by the tax withheld.
#: Matching is case-insensitive because the export mixes Dutch and English and is
#: inconsistent about capitalisation (`flatex terugstorting` is lowercase in a file
#: that otherwise title-cases); an unrecognised row is still reported, so robustness
#: here does not cost visibility.
_RULES: tuple[tuple[str, AccountAction], ...] = (
    # --- Withholding before dividend. See above.
    ("dividendbelasting", AccountAction(True, "DIVIDEND_TAX", True)),
    ("dividend", AccountAction(True, "DIVIDEND", True)),
    # --- Trade duplicates. Transactions.csv is authoritative (Sec 6.2), and it
    #     already carries the per-order commission on one of its fill rows. Importing
    #     these would double-count both the trade and its fee.
    ("koop ", AccountAction(False, None, True, note=_DROP)),
    ("verkoop ", AccountAction(False, None, True, note=_DROP)),
    ("degiro transactiekosten", AccountAction(False, None, True, note=_DROP)),
    # --- Fees and taxes that exist only here.
    ("degiro aansluitingskosten", AccountAction(True, "FEE", True, portfolio_level=True)),
    ("transactiebelasting frankrijk", AccountAction(True, "TAX", True)),
    # --- Internal transfers. The Sec 3.3 trap.
    ("degiro cash sweep transfer", AccountAction(False, None, True, note=_DROP)),
    ("overboeking naar uw geldrekening", AccountAction(False, None, True, note=_DROP)),
    ("overboeking van uw geldrekening", AccountAction(False, None, True, note=_DROP)),
    ("reservation ideal", AccountAction(False, None, True, note=_DROP)),
    ("processed flatex withdrawal", AccountAction(False, None, True, note=_DROP)),
    ("flatex terugstorting", AccountAction(False, None, True, note=_DROP)),
    # --- The only genuine external flows in the whole export.
    ("ideal deposit", AccountAction(True, "DEPOSIT", True)),
    ("sepa instant terugstorting", AccountAction(True, "WITHDRAWAL", True)),
    # --- Income and FX.
    ("valuta creditering", AccountAction(True, "FX_CONVERT", True)),
    ("valuta debitering", AccountAction(True, "FX_CONVERT", True)),
    ("flatex interest income", AccountAction(True, "INTEREST", True)),
    ("rente", AccountAction(True, "INTEREST", True)),
    ("inkomsten uit securities lending", AccountAction(True, "SECURITIES_LENDING", True)),
    # --- Corporate actions. The only place they are named (Sec 3.4).
    ("split aanpassing", AccountAction(True, "CORPORATE_ACTION", True)),
    ("productwijziging", AccountAction(True, "CORPORATE_ACTION", True)),
)

_UNRECOGNISED = AccountAction(keep=False, txn_type=None, recognised=False, note="unrecognised")


def classify(description: str) -> AccountAction:
    """Decide what a description means. Never guesses."""
    text = description.strip().casefold()
    for prefix, action in _RULES:
        if text.startswith(prefix):
            return action
    return _UNRECOGNISED


@dataclass(frozen=True, slots=True)
class AccountRow:
    """One Account.csv line, parsed and classified but not yet a ledger row."""

    line_no: int
    trade_date: date
    trade_time: str | None
    value_date: date | None
    product: str | None
    isin: str | None
    description: str
    fx_rate: Decimal | None
    change: Decimal | None
    change_currency: str | None
    balance: Decimal | None
    balance_currency: str | None
    order_ref: str | None
    action: AccountAction
    raw: dict[str, str]


def _blank_to_none(value: str) -> str | None:
    stripped = value.strip()
    return stripped or None


def parse_account_csv(path: Path) -> list[AccountRow]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        assert_header(header, ACCOUNT_HEADER, path.name)
        # Physical line numbers are kept for the same reason as in the transactions
        # parser: a parse error must name a line you can go and read.
        numbered = [
            (line_no, row)
            for line_no, row in enumerate(reader, start=2)
            if any(cell.strip() for cell in row)
        ]

    rows: list[AccountRow] = []
    for line_no, row in numbered:
        if len(row) != len(ACCOUNT_RAW_FIELDS):
            raise MalformedRow(
                f"{path.name} line {line_no}: expected {len(ACCOUNT_RAW_FIELDS)} "
                f"columns, got {len(row)}"
            )
        try:
            description = row[AcctCol.DESCRIPTION].strip()
            rows.append(
                AccountRow(
                    line_no=line_no,
                    trade_date=parse_dutch_date(row[AcctCol.DATE]),
                    trade_time=_blank_to_none(row[AcctCol.TIME]),
                    value_date=(
                        parse_dutch_date(row[AcctCol.VALUE_DATE])
                        if row[AcctCol.VALUE_DATE].strip()
                        else None
                    ),
                    product=_blank_to_none(row[AcctCol.PRODUCT]),
                    isin=_blank_to_none(row[AcctCol.ISIN]),
                    description=description,
                    fx_rate=parse_optional_decimal(row[AcctCol.FX]),
                    change=parse_optional_decimal(row[AcctCol.CHANGE]),
                    change_currency=_blank_to_none(row[AcctCol.CHANGE_CCY]),
                    balance=parse_optional_decimal(row[AcctCol.BALANCE]),
                    balance_currency=_blank_to_none(row[AcctCol.BALANCE_CCY]),
                    order_ref=_blank_to_none(row[AcctCol.ORDER_ID]),
                    action=classify(description),
                    raw=dict(zip(ACCOUNT_RAW_FIELDS, row, strict=True)),
                )
            )
        except MalformedRow:
            raise
        except Exception as exc:
            raise MalformedRow(f"{path.name} line {line_no}: {exc}") from exc

    return rows
