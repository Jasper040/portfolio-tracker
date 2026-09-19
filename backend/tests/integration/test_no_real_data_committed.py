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

The scan is opt-in, because it needs the export to know what to look for. Absent
it, there is nothing to check and the scan skips. The tests of its matching rules
are not: they run on invented input, in every suite, because a rule exercised only
against the real repo is a rule whose blind spots stay invisible (PT-30).

Three kinds of thing are searched for, each for its own reason:

* **ISINs** identify a holding outright. Any occurrence is a leak.
* **Instrument names** identify it just as well. Matched as whole phrases and as
  distinctive tokens, because a two-word name leaks through either word alone.
* **Amounts, at five significant digits or more.** A cost basis carried to the
  cent is the owner's; a fixture using a round thousand is not, and the
  threshold is what separates them without a hand-maintained allowlist that
  would rot. Small round values are deliberately not searched: a two-euro
  commission is attributable to nobody once the ISIN beside it is synthetic.

PYTEST_DONT_REWRITE -- pytest's assertion rewriting prints both operands of a
failing assert, and the operands here are derived from the gitignored export:
identifiers, balances, dates, and model reprs that carry all three. That output
reaches a terminal, and from there agent transcripts, pasted reports and issue
comments. The marker turns the rewriting off, so a failure reports only what
its own message says.

This module keeps its explicit failure message, which names the leaked token and
the file holding it, and that is deliberate: this test only fails when the value
is ALREADY in a tracked file, so printing it reveals nothing the repository does
not already contain -- and without it the operator cannot find what to remove.
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

_needs_export = pytest.mark.skipif(
    not (EXPORT / "Transactions.csv").exists(),
    reason="real DeGiro export not present; nothing to check against",
)

#: Where the instrument name lives in each file. Only this column is scanned for
#: names: `Account.csv`'s description column is transaction vocabulary -- `Koop`,
#: `Dividend`, `Aansluitingskosten` -- which the parser is *supposed* to contain.
_PRODUCT_COLUMN = {"Transactions.csv": 2, "Account.csv": 3, "Portfolio.csv": 0}

#: Legal suffixes, share classes, fund boilerplate and ordinary English. None of
#: these identifies a holding: every listed company is a "CORP" and the word
#: "states" appears in ordinary prose. What survives the filter is the part of a
#: name that actually picks out one instrument.
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
    # Three characters, searched for since PT-30 lowered the floor. Each is a
    # word the code uses for its own reasons -- a quantifier, an HTML element, a
    # round number -- and appears in dozens of tracked files without picking out
    # a holding. A ticker never belongs here: a ticker in a tracked file is a leak.
    "all", "div", "500",
}


def _tracked_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout.split("\n")
    return [REPO / name for name in out if name.strip()]


def _significant_digits(value: Decimal) -> int:
    """Digits that carry information: leading and trailing zeros carry none.

    A round thousand is one significant digit and belongs to nobody; a price
    carried to the cent is five or more and belongs to the owner.
    """
    return len(f"{value:f}".replace("-", "").replace(".", "").strip("0"))


#: The shortest name token searched for. A ticker is often three characters, and a
#: floor of four let a ticker-shaped holding through unsearched (PT-30). A length
#: floor drops every short word, holdings included; `_NOT_A_HOLDING` drops only
#: the ones that identify nothing, which is the job it already does for long words.
#: Two stays below the floor: at that length nearly every token is a legal suffix
#: or a preposition, and the whole name is still matched as a phrase.
_MIN_TOKEN = 3


def _name_tokens(name: str) -> set[str]:
    """What one product name contributes to the search.

    The whole name, so a holding cannot hide behind a stoplisted word, plus each
    distinctive token, so it cannot hide behind an abbreviation either.
    """
    if len(name) < _MIN_TOKEN:
        return set()
    return {name} | {
        token
        for token in re.split(r"[\s,./\"()&+-]+", name)
        if len(token) >= _MIN_TOKEN and token.lower() not in _NOT_A_HOLDING
    }


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
                    elif index == product_column and line_no > 0:
                        tokens.update(_name_tokens(text))

    return isins, tokens, amounts


def _corporate_action_dates() -> set[str]:
    """Dates of the suppressed corporate-action pairs, in both spellings.

    A "10-for-1 split on <date>" names the instrument to anyone familiar with
    listed equities, even with every name and ISIN replaced -- so the date is
    identifying on its own. Only corporate-action dates are checked, not every trade date: a
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


def _leaks_in(
    rel: str,
    body: str,
    isins: set[str],
    tokens: set[str],
    amounts: set[str],
    stamps: set[str],
) -> list[str]:
    """Everything identifying in one tracked file's text.

    Extracted from the scan below so the matching rules can be asserted
    directly, on a synthetic body. A predicate that only ever runs over the
    real repo is a predicate whose blind spots stay invisible until one of them
    ships -- which is exactly what happened to the ISIN rule below.
    """
    found: list[str] = []
    # Case-INsensitively. `config/benchmarks.yaml` is the one tracked file in
    # `config/`, and it is tracked only because `ingest/benchmarks.py` refuses
    # an ISIN-shaped key. That loader's slug rule forces LOWERCASE, so the one
    # shape it could ever let through is a lowercase ISIN -- which a
    # case-sensitive match here would then wave past as well. Two defences with
    # the same gap are one defence.
    lowered = body.lower()
    for isin in sorted(isins):
        if isin.lower() in lowered:
            found.append(f"{rel}: ISIN {isin}")
    for token in sorted(tokens):
        if re.search(rf"\b{re.escape(token)}\b", body, re.IGNORECASE):
            found.append(f"{rel}: instrument name {token!r}")
    for amount in sorted(amounts):
        if amount in body:
            found.append(f"{rel}: amount {amount}")
    for stamp in sorted(stamps):
        if stamp in body:
            found.append(f"{rel}: corporate-action date {stamp}")
    return found


def test_an_isin_is_a_leak_whatever_case_it_is_written_in() -> None:
    """The scanner's own blind spot, asserted on a synthetic body.

    The ISIN below is invented and belongs to nobody; the key around it is the
    exact shape `ingest/benchmarks.py` used to accept.
    """
    leaks = _leaks_in(
        "config/benchmarks.yaml",
        "benchmark-nl0000000001:\n  symbol: AAA.XX\n",
        {"NL0000000001"},
        set(),
        set(),
        set(),
    )
    assert leaks == ["config/benchmarks.yaml: ISIN NL0000000001"]


def test_a_three_character_holding_token_is_searched_for() -> None:
    """Tickers are three characters, and a floor of four never searched for one.

    The floor sat exactly above a ticker-shaped token, so a tracked file carried
    one while this scan stayed green (PT-30). The name is invented.
    """
    assert "QZX" in _name_tokens("QZX Velmora")
    assert _name_tokens("QZX") == {"QZX"}


@pytest.mark.realdata
@_needs_export
def test_no_tracked_file_contains_a_real_holding() -> None:
    isins, tokens, amounts = _real_values()
    assert isins, "read no ISINs from the export; the scan would pass vacuously"
    stamps = _corporate_action_dates()

    leaks: list[str] = []
    for path in _tracked_files():
        try:
            body = path.read_text(encoding="utf-8", errors="ignore")
        except (OSError, ValueError):
            continue
        leaks.extend(
            _leaks_in(path.relative_to(REPO).as_posix(), body, isins, tokens, amounts, stamps)
        )

    assert not leaks, "real holdings found in tracked files:\n  " + "\n  ".join(leaks)
