"""No holding of the owner's may appear in a tracked file.

Design doc Sec 13 keeps secrets out of the repo and Sec 1 leaves the door open to
open-sourcing this later. The real exports are gitignored, but that only stops the
*files* leaking -- it does nothing about a figure copied out of one into a test, a
docstring or a design note, which is how every leak this test was written to catch
actually happened.

So the check runs the other way round: read the gitignored export, work out what
"real" means from it, and assert none of it appears in anything git tracks. That
makes the rule enforceable rather than merely stated, and it cannot go stale --
re-import a different export and the test re-derives what to look for.

Opt-in, because it needs the export to know what to look for. Absent it, there is
nothing to check and the suite skips.

Three kinds of thing are searched for, each for its own reason:

* **ISINs** identify a holding outright. Any occurrence is a leak.
* **Instrument names** identify it just as well. Tokenised, because "ORION
  CORPORATION" leaks through "ORION" alone.
* **Amounts, at five significant digits or more.** A cost basis of 655.30 is the
  owner's. A test fixture using 1000.00 is not -- and the threshold is what tells
  them apart without a hand-maintained allowlist that would rot. Three-digit
  values like 2.00 are deliberately not searched: a commission that size is
  attributable to nobody once the ISIN beside it is synthetic.
"""

from __future__ import annotations

import csv
import re
import subprocess
from decimal import Decimal
from pathlib import Path

import pytest

REPO = Path(__file__).parents[3]
EXPORT = REPO / "degiro-export"

pytestmark = [
    pytest.mark.realdata,
    pytest.mark.skipif(
        not (EXPORT / "Transactions.csv").exists(),
        reason="real DeGiro export not present; nothing to check against",
    ),
]

#: Where the instrument name lives in each file. Only this column is scanned for
#: names: `Account.csv`'s description column is transaction vocabulary -- `Koop`,
#: `Dividend`, `Aansluitingskosten` -- which the parser is *supposed* to contain.
_PRODUCT_COLUMN = {"Transactions.csv": 2, "Account.csv": 3, "Portfolio.csv": 0}

#: Legal suffixes, share classes, fund boilerplate and ordinary English. None of
#: these identifies a holding: every listed company is a "CORP" and the word
#: "states" appears in prose. What is left after removing them -- MERIDIAN,
#: ORION, APEX -- is the part that names something the owner actually owns.
_NOT_A_HOLDING = {
    "acc", "adr", "and", "bankaccount", "cash", "class", "com", "company", "corp",
    "corporation", "dist", "etf", "eur", "euro", "flatex", "ftx", "fund", "group",
    "holding", "holdings", "inc", "incorporated", "limited", "ltd", "manufacturing",
    "on", "plc", "sa", "the", "ucits", "usd", "aud", "hkd", "nv", "n.v", "n.v.",
    "states", "united", "international", "technologies", "industries", "core",
    "value", "factor", "advanced", "global", "future", "defence", "tech", "titans",
    "jones", "world", "aviation", "platforms", "semiconductor", "trust", "northern",
    "space", "exploration", "infrastructure", "systems", "minerals", "bank",
    # Ordinary English that also happens to appear in a fund or company name.
    # Dropping them as single tokens costs nothing, because the full name is
    # matched as a phrase below and the ISIN is matched exactly -- an instrument
    # cannot hide behind a word its own name shares with a CSS property.
    "meta", "product", "accumulating",
}


def _tracked_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout.split("\n")
    return [REPO / name for name in out if name.strip()]


def _significant_digits(value: Decimal) -> int:
    """Digits that carry information: leading and trailing zeros carry none.

    1000.00 is one significant digit and belongs to nobody; 655.30 is five and
    belongs to the owner.
    """
    return len(f"{value:f}".replace("-", "").replace(".", "").strip("0"))


def _real_values() -> tuple[set[str], set[str], set[str]]:
    """ISINs, name tokens and amount spellings, read from the gitignored export."""
    isins: set[str] = set()
    tokens: set[str] = set()
    amounts: set[str] = set()

    for name, product_column in _PRODUCT_COLUMN.items():
        path = EXPORT / name
        if not path.exists():
            continue
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for line_no, row in enumerate(csv.reader(handle)):
                for index, cell in enumerate(row):
                    text = cell.strip()
                    if re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]", text):
                        isins.add(text)
                    elif re.fullmatch(r"-?\d{1,3}(\.\d{3})*(,\d+)?", text) or re.fullmatch(
                        r"-?\d+,\d+", text
                    ):
                        number = Decimal(text.replace(".", "").replace(",", "."))
                        if _significant_digits(number) >= 5:
                            # Both spellings: the export is Dutch-locale, the code
                            # that copied a figure out of it usually is not.
                            plain = f"{abs(number):f}".rstrip("0").rstrip(".")
                            amounts.add(plain)
                            amounts.add(plain.replace(".", ","))
                    elif index == product_column and line_no > 0 and len(text) >= 4:
                        # The whole name, so a holding cannot hide behind a
                        # stoplisted word, plus each distinctive token, so it
                        # cannot hide behind an abbreviation either.
                        tokens.add(text)
                        for token in re.split(r"[\s,./\"()&+-]+", text):
                            if len(token) >= 4 and token.lower() not in _NOT_A_HOLDING:
                                tokens.add(token)

    return isins, tokens, amounts


def _corporate_action_dates() -> set[str]:
    """Dates of the suppressed corporate-action pairs, in both spellings.

    A "10-for-1 split on <date>" names the instrument to anyone who follows
    markets, even with every name and ISIN replaced -- so the date is identifying
    on its own. Only corporate-action dates are checked, not every trade date: a
    date the owner happened to buy on identifies nothing, and searching for all of
    them would flag the plan filenames and every changelog entry.
    """
    dates: set[str] = set()
    with (EXPORT / "Transactions.csv").open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        next(reader)
        blank_order_id = [
            row for row in reader if len(row) > 16 and not row[16].strip() and row[0].strip()
        ]
    for row in blank_order_id:
        day, month, year = row[0].strip().split("-")
        dates.add(f"{year}-{month}-{day}")
        dates.add(f"{day}-{month}-{year}")
    return dates


def test_no_tracked_file_contains_a_real_holding() -> None:
    isins, tokens, amounts = _real_values()
    assert isins, "read no ISINs from the export; the scan would pass vacuously"

    leaks: list[str] = []
    for path in _tracked_files():
        try:
            body = path.read_text(encoding="utf-8", errors="ignore")
        except (OSError, ValueError):
            continue
        rel = path.relative_to(REPO).as_posix()

        for isin in sorted(isins):
            if isin in body:
                leaks.append(f"{rel}: ISIN {isin}")
        for token in sorted(tokens):
            if re.search(rf"\b{re.escape(token)}\b", body, re.IGNORECASE):
                leaks.append(f"{rel}: instrument name {token!r}")
        for amount in sorted(amounts):
            if amount in body:
                leaks.append(f"{rel}: amount {amount}")
        for stamp in sorted(_corporate_action_dates()):
            if stamp in body:
                leaks.append(f"{rel}: corporate-action date {stamp}")

    assert not leaks, "real holdings found in tracked files:\n  " + "\n  ".join(leaks)
