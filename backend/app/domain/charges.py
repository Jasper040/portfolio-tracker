"""What a trade cost beyond the price of the shares.

Design doc Sec 6.4. Three components rather than one total, because they answer
different questions and the owner asked to see them apart: `commission` is what the
broker charged to execute, `autofx` is what converting the currency cost, and `tax`
is tax paid and separately reportable.

**Positive means money paid.** The ledger stores these negative, as debits. The flip
happens exactly once, in `domain.orders`, so nothing downstream has to remember
which convention it is looking at -- and a sign error shows up as a P&L that moves
the wrong way rather than as a plausible number.

Charges are never added into a cost basis. A lot bought for EUR 655.30 with EUR 4.18
of charges has a basis of 655.30; the 4.18 stays visible and is deducted from P&L.
The arithmetic of net P&L is the same either way. What differs is whether you can
still see what you paid.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from app.domain.fees import apportion

_ZERO = Decimal("0.00")


@dataclass(frozen=True, slots=True)
class Charges:
    """The three costs a trade carries, kept apart. Positive means money paid."""

    commission: Decimal = _ZERO
    autofx: Decimal = _ZERO
    tax: Decimal = _ZERO

    @property
    def total(self) -> Decimal:
        return self.commission + self.autofx + self.tax

    def __add__(self, other: "Charges") -> "Charges":
        return Charges(
            commission=self.commission + other.commission,
            autofx=self.autofx + other.autofx,
            tax=self.tax + other.tax,
        )

    @classmethod
    def zero(cls) -> "Charges":
        return cls(_ZERO, _ZERO, _ZERO)


def apportion_charges(charges: Charges, weights: Sequence[Decimal]) -> list[Charges]:
    """Split each component across `weights` so each one sums back exactly.

    Component-wise, not total-then-redivide. `apportion` is exact per call, so three
    exact splits give an exact total; splitting the total and then reconstructing a
    breakdown from it would round twice and break the Sec 11.2 invariant on the
    component the owner is actually reading.
    """
    commissions = apportion(charges.commission, weights)
    autofx = apportion(charges.autofx, weights)
    taxes = apportion(charges.tax, weights)
    return [
        Charges(commission=c, autofx=a, tax=t)
        for c, a, t in zip(commissions, autofx, taxes, strict=True)
    ]
