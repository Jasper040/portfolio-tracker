"""Hand-maintained prices, for instruments no free provider covers.

Parent doc Sec 8.2 puts a hand-editable CSV at the end of the provider chain, and
the spike confirmed it is needed: some holdings have no public series at any
price. The set is a fact about the data rather than a constant, so it is
discovered by the quarantine rather than hardcoded here.

The loader is strict in the same way `ingest/corporate_actions.load_resolutions`
is, and for a sharper reason. Every other number in this app is cross-checked
against something: a trade against the broker's own total, a lot against
`Portfolio.csv`, a symbol against the executed prices. A manual price is checked
against nothing at all, so a misplaced decimal point reaches the chart
unchallenged. Guessing at a malformed row would be guessing at the one number
with no second opinion.
"""

from __future__ import annotations

import csv
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from app.providers.base import MANUAL, PricePoint, PriceSeries

_COLUMNS = ("isin", "date", "close", "currency")

class MalformedManualPrices(ValueError):
    """The file exists but cannot be trusted. Names the file, line and field."""

@dataclass(frozen=True, slots=True)
class ManualPrices:
    """Every hand-typed price, indexed by instrument."""

    _by_isin: Mapping[str, PriceSeries]

    @property
    def isins(self) -> frozenset[str]:
        return frozenset(self._by_isin)

    def series(self, isin: str) -> PriceSeries | None:
        return self._by_isin.get(isin)

    @classmethod
    def load(cls, path: Path) -> "ManualPrices":
        """Read the file. A missing one answers nothing, which is not an error."""
        if not path.exists():
            return cls(_by_isin={})

        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.reader(handle)
            header = next(reader, None)
            if header is None or tuple(cell.strip() for cell in header) != _COLUMNS:
                raise MalformedManualPrices(
                    f"{path}: header must be exactly {','.join(_COLUMNS)}"
                )
            # `#`-prefixed rows are comments, skipped wherever they fall -- not just
            # a leading block. This file is hand-edited, unlike the YAML beside it
            # (`corporate_actions.yaml`), which gets comments for free from its
            # format; a CSV needs the loader to grant the same courtesy explicitly.
            # Every other rejection below stays exactly as strict as it was: a
            # manual price is the one number in this app with no cross-check, so
            # nothing past the comment convention gets a pass for being merely
            # plausible.
            rows = [
                row
                for row in reader
                if any(cell.strip() for cell in row) and not row[0].strip().startswith("#")
            ]

        points: dict[str, dict[date, PricePoint]] = {}
        currencies: dict[str, str] = {}
        for line_no, row in enumerate(rows, start=2):
            where = f"{path}: line {line_no}"
            if len(row) != len(_COLUMNS):
                raise MalformedManualPrices(f"{where} has {len(row)} fields, expected 4")

            isin, on_text, close_text, currency = (cell.strip() for cell in row)
            if not isin:
                raise MalformedManualPrices(f"{where}: isin is empty")
            if not currency:
                raise MalformedManualPrices(f"{where}: currency is empty")

            try:
                on = date.fromisoformat(on_text)
            except ValueError as bad:
                raise MalformedManualPrices(
                    f"{where}: date {on_text!r} is not YYYY-MM-DD"
                ) from bad

            try:
                close = Decimal(close_text)
            except InvalidOperation as bad:
                raise MalformedManualPrices(
                    f"{where}: close {close_text!r} is not a number"
                ) from bad
            if close <= 0:
                raise MalformedManualPrices(f"{where}: close {close} is not positive")

            held = currencies.setdefault(isin, currency)
            if held != currency:
                raise MalformedManualPrices(
                    f"{where}: {isin}'s currency is {held} elsewhere in this file, "
                    f"not {currency}"
                )

            days = points.setdefault(isin, {})
            if on in days:
                raise MalformedManualPrices(f"{where}: {isin} is priced twice on {on}")
            # A number somebody typed has no dividend adjustment to apply, so it
            # is its own total return. Leaving the adjusted close empty would
            # make `total_return_series()` skip the instrument in silence.
            days[on] = PricePoint(on=on, close_unadjusted=close, close_adjusted=close)

        return cls(
            _by_isin={
                isin: PriceSeries(
                    symbol=isin,
                    currency=currencies[isin],
                    source=MANUAL,
                    points=tuple(days[on] for on in sorted(days)),
                )
                for isin, days in points.items()
            }
        )
