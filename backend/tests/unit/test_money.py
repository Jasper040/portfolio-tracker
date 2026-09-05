from dataclasses import FrozenInstanceError
from datetime import date
from decimal import Decimal

import pytest

from app.domain.money import CurrencyMismatch, FxRate, Money


def test_money_adds_within_one_currency() -> None:
    assert Money(Decimal("1.10"), "EUR") + Money(Decimal("2.20"), "EUR") == Money(
        Decimal("3.30"), "EUR"
    )


def test_money_refuses_to_add_across_currencies() -> None:
    with pytest.raises(CurrencyMismatch):
        Money(Decimal("1"), "EUR") + Money(Decimal("1"), "USD")


def test_money_subtracts_within_one_currency() -> None:
    assert Money(Decimal("3.30"), "EUR") - Money(Decimal("1.10"), "EUR") == Money(
        Decimal("2.20"), "EUR"
    )


def test_money_refuses_to_subtract_across_currencies() -> None:
    with pytest.raises(CurrencyMismatch):
        Money(Decimal("1"), "EUR") - Money(Decimal("1"), "USD")


def test_money_negates() -> None:
    assert -Money(Decimal("1.10"), "EUR") == Money(Decimal("-1.10"), "EUR")


def test_fx_converts_by_dividing_degiro_style() -> None:
    """DeGiro quotes local-per-EUR, so converting divides: -1004.25 / 1.2150.

    The broker booked -826.55 for this row; exact division gives -826.54. That
    one-cent gap is the broker's own rounding, and it is precisely why `net_base`
    is stored as broker truth rather than recomputed from its components.
    """
    rate = FxRate("USD", "EUR", Decimal("1.2150"), date(2026, 7, 13))
    result = rate.convert(Money(Decimal("-1004.25"), "USD"))
    assert result.currency == "EUR"
    assert result.amount.quantize(Decimal("0.01")) == Decimal("-826.54")
    # The broker's own figure differs by exactly one cent: documented, not asserted away.
    assert abs(result.amount - Decimal("-826.55")) < Decimal("0.02")


def test_fx_refuses_wrong_direction() -> None:
    """A EUR->USD conversion through a USD->EUR rate must raise, not silently invert."""
    rate = FxRate("USD", "EUR", Decimal("1.2150"), date(2026, 7, 13))
    with pytest.raises(CurrencyMismatch):
        rate.convert(Money(Decimal("100"), "EUR"))


def test_money_is_immutable() -> None:
    """Frozen, and specifically frozen: a bare `Exception` here would also pass if
    the assignment raised NameError, so it would assert almost nothing."""
    m = Money(Decimal("1"), "EUR")
    with pytest.raises(FrozenInstanceError):
        m.amount = Decimal("2")  # type: ignore[misc]
