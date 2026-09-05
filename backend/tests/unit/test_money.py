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


def test_fx_converts_by_dividing_degiro_style() -> None:
    """DeGiro quotes local-per-EUR, so converting divides: -1004.25 / 1.2150."""
    rate = FxRate("USD", "EUR", Decimal("1.2150"), date(2026, 7, 13))
    result = rate.convert(Money(Decimal("-1004.25"), "USD"))
    assert result.currency == "EUR"
    assert result.amount.quantize(Decimal("0.01")) == Decimal("-1170.88")


def test_fx_refuses_wrong_direction() -> None:
    """A EUR->USD conversion through a USD->EUR rate must raise, not silently invert."""
    rate = FxRate("USD", "EUR", Decimal("1.2150"), date(2026, 7, 13))
    with pytest.raises(CurrencyMismatch):
        rate.convert(Money(Decimal("100"), "EUR"))


def test_money_is_immutable() -> None:
    m = Money(Decimal("1"), "EUR")
    with pytest.raises(Exception):
        m.amount = Decimal("2")  # type: ignore[misc]
