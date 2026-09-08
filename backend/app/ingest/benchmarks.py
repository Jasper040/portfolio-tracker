"""The benchmark set: which proxies to fetch, and what they cost to hold.

M3 section 4.3. This file is the human's answer, on the same terms as
`instrument_symbols.yaml` -- the symbol discriminator does not run here and
cannot, because a benchmark has no executed prices in the ledger to be checked
against (M3 section 4.1). Nothing re-derives what is written here.

The difference, and the reason this one is TRACKED while every other config
file in `config/` is gitignored, is that a key here names no holding. That is
enforced rather than asked for: `_check_key` refuses a key with anything
ISIN-shaped ANYWHERE in it, not merely a key that is entirely an ISIN --
`benchmark-nl0000000001` is a valid slug and used to load. Committing a
benchmark set is only safe while that holds, so it is a test rather than a
comment.

`test_no_real_data_committed.py` is the backstop, and it now matches ISINs
case-insensitively: the slug rule below forces lowercase, so a case-sensitive
backstop shared this check's exact blind spot and was no backstop at all.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

import yaml

#: Lowercase slug. Deliberately narrow: it has to be incapable of expressing an
#: ISIN, and "as narrow as the job allows" is the cheapest way to be sure.
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*$")

#: Two letters, nine alphanumerics, one check digit -- ANYWHERE in the key,
#: not anchored to its ends. `benchmark-nl0000000001` is a perfectly valid
#: slug, so an anchored match saw "not an ISIN" and let a holding into the one
#: tracked file in `config/`. The hyphen is what makes the unanchored form
#: safe for ordinary keys: it breaks the run of alphanumerics an ISIN needs, so
#: `broad-world-acc-2` cannot contain one however long it grows.
_ISIN = re.compile(r"[A-Za-z]{2}[A-Za-z0-9]{9}[0-9]")

_REQUIRED = ("symbol", "currency", "name", "ter")


class BenchmarkConfigError(ValueError):
    """The benchmark file says something that cannot be acted on."""


@dataclass(frozen=True, slots=True)
class Benchmark:
    key: str
    symbol: str
    currency: str
    name: str
    #: Total expense ratio, as a percentage per year. Documented rather than
    #: applied: parent doc Sec 7.6 requires proxy drag be visible, and adjusting
    #: for it would invent a series nobody published.
    ter: Decimal


def _check_key(key: str) -> None:
    if _ISIN.search(key):
        raise BenchmarkConfigError(
            f"benchmark key {key!r} looks like an ISIN, or contains something that "
            "does. Keys are slugs precisely so this file can be committed; an ISIN in "
            "a tracked file is a holding, which test_no_real_data_committed treats as "
            "a leak. If the key is innocent, break the run of letters and digits with "
            "a hyphen."
        )
    if not _SLUG.match(key):
        raise BenchmarkConfigError(
            f"benchmark key {key!r} is not a slug: lowercase letters, digits and hyphens."
        )


def load_benchmarks(path: Path) -> tuple[Benchmark, ...]:
    """Read the configured benchmark set. A missing file is an empty set."""
    if not path.exists():
        return ()

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise BenchmarkConfigError(f"{path} must be a mapping of key to benchmark.")

    out: list[Benchmark] = []
    for key, body in raw.items():
        key = str(key)
        _check_key(key)
        if not isinstance(body, dict):
            raise BenchmarkConfigError(f"benchmark {key!r} must be a mapping.")
        for field in _REQUIRED:
            if body.get(field) in (None, ""):
                raise BenchmarkConfigError(f"benchmark {key!r} is missing {field!r}.")
        try:
            # str() first: PyYAML parses an unquoted 0.07 as a float, and a float
            # is exactly what this codebase never lets near a number it reports.
            ter = Decimal(str(body["ter"]))
        except InvalidOperation as exc:
            raise BenchmarkConfigError(
                f"benchmark {key!r} has a ter that is not a number: {body['ter']!r}"
            ) from exc
        out.append(
            Benchmark(
                key=key,
                symbol=str(body["symbol"]),
                currency=str(body["currency"]).upper(),
                name=str(body["name"]),
                ter=ter,
            )
        )
    return tuple(out)
