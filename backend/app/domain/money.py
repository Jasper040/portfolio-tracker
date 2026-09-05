"""Money and FX as value objects.

Two failure modes motivate this module:

1. Applying an FX rate in the wrong direction produces a number that is wrong but
   entirely plausible. `FxRate.convert` therefore refuses any input whose currency
   is not `from_currency`, turning a silent 35% error into an exception.
2. Floating point on money accumulates cent-level drift that is indistinguishable
   from the broker's own cent-level rounding. Everything here is `Decimal`.

DeGiro quotes its exchange rate as *units of local currency per 1 EUR*, so the
conversion divides. That is the opposite of what a field named `fx_rate_to_base`
suggests, which is exactly why the direction is encoded in the type.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


class CurrencyMismatch(Exception):
    """Raised when an operation mixes currencies that cannot be combined."""


@dataclass(frozen=True, slots=True)
class Money:
    amount: Decimal
    currency: str

    def _check(self, other: Money) -> None:
        if self.currency != other.currency:
            raise CurrencyMismatch(
                f"cannot combine {self.currency} with {other.currency}"
            )

    def __add__(self, other: Money) -> Money:
        self._check(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._check(other)
        return Money(self.amount - other.amount, self.currency)

    def __neg__(self) -> Money:
        return Money(-self.amount, self.currency)


@dataclass(frozen=True, slots=True)
class FxRate:
    """`rate` is units of `from_currency` per 1 unit of `to_currency`."""

    from_currency: str
    to_currency: str
    rate: Decimal
    as_of: date

    def convert(self, money: Money) -> Money:
        if money.currency != self.from_currency:
            raise CurrencyMismatch(
                f"rate converts {self.from_currency}->{self.to_currency}, "
                f"got {money.currency}"
            )
        return Money(money.amount / self.rate, self.to_currency)
