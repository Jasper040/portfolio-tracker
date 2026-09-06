"""ECB reference rates via Frankfurter. Keyless, full history in one call.

The direction is the whole test suite. `FxDaily.rate` is units of `from_ccy` per
1 unit of `to_ccy` -- the same direction as `domain.money.FxRate` and as DeGiro's
own `Exchange rate` column -- so you DIVIDE a foreign amount by it to reach EUR.

Frankfurter is therefore queried with `base=EUR&symbols=USD`, which returns "1
EUR = 1.04 USD". That number IS the stored rate, verbatim, with no reciprocal:
taking one would cost exactness on every row and put two directions in one
codebase, which is precisely the failure `FxRate` exists to make impossible.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from app.domain.money import FxRate, Money
from app.providers.base import ProviderError
from app.providers.ecb import EcbRates

D = Decimal
FIXTURES = Path(__file__).parents[1] / "fixtures" / "providers"

def client_returning(payload: object, status: int = 200) -> httpx.Client:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=payload)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    client.seen = seen  # type: ignore[attr-defined]
    return client

def fixture() -> object:
    return json.loads((FIXTURES / "frankfurter_series.json").read_text(encoding="utf-8"))

class TestDirection:
    def test_queries_with_eur_as_the_base(self) -> None:
        client = client_returning(fixture())
        EcbRates(client).series("USD", "EUR", start=date(2025, 1, 6), end=date(2025, 1, 8))
        params = client.seen[0].url.params  # type: ignore[attr-defined]
        assert params["base"] == "EUR"
        assert params["symbols"] == "USD"

    def test_stores_frankfurters_number_verbatim(self) -> None:
        series = EcbRates(client_returning(fixture())).series(
            "USD", "EUR", start=date(2025, 1, 6), end=date(2025, 1, 8)
        )
        assert series is not None
        assert series.points[0].rate == D("1.04")

    def test_the_stored_rate_converts_the_way_FxRate_does(self) -> None:
        """104 USD at 1.04 USD per EUR is 100 EUR. The reciprocal would say
        108.16 -- wrong by 8% and entirely believable."""
        series = EcbRates(client_returning(fixture())).series(
            "USD", "EUR", start=date(2025, 1, 6), end=date(2025, 1, 8)
        )
        assert series is not None
        rate = FxRate("USD", "EUR", series.points[0].rate, date(2025, 1, 6))
        assert rate.convert(Money(D("104.00"), "USD")).amount == D("100")

class TestParsing:
    def test_returns_one_point_per_published_day_in_order(self) -> None:
        series = EcbRates(client_returning(fixture())).series(
            "USD", "EUR", start=date(2025, 1, 6), end=date(2025, 1, 8)
        )
        assert series is not None
        assert [p.on for p in series.points] == [
            date(2025, 1, 6),
            date(2025, 1, 7),
            date(2025, 1, 8),
        ]

    def test_rates_are_decimals_not_floats(self) -> None:
        series = EcbRates(client_returning(fixture())).series(
            "USD", "EUR", start=date(2025, 1, 6), end=date(2025, 1, 8)
        )
        assert series is not None
        assert all(isinstance(p.rate, Decimal) for p in series.points)

    def test_records_itself_as_the_source(self) -> None:
        series = EcbRates(client_returning(fixture())).series(
            "USD", "EUR", start=date(2025, 1, 6), end=date(2025, 1, 8)
        )
        assert series is not None
        assert series.source == "ecb"

    def test_the_identity_pair_needs_no_request(self) -> None:
        """EUR to EUR is 1 by definition. Fetching it would record a network
        fact where an arithmetic one belongs, and Frankfurter does not serve it."""
        client = client_returning(fixture())
        series = EcbRates(client).series("EUR", "EUR", start=date(2025, 1, 6), end=date(2025, 1, 8))
        assert series is None
        assert client.seen == []  # type: ignore[attr-defined]

    def test_an_unsupported_currency_answers_nothing(self) -> None:
        assert (
            EcbRates(client_returning({"rates": {}})).series(
                "XYZ", "EUR", start=date(2025, 1, 6), end=date(2025, 1, 8)
            )
            is None
        )

class TestFailures:
    def test_a_server_error_raises(self) -> None:
        with pytest.raises(ProviderError):
            EcbRates(client_returning({}, status=500)).series(
                "USD", "EUR", start=date(2025, 1, 6), end=date(2025, 1, 8)
            )

    def test_refuses_a_pair_that_does_not_reach_the_base_currency(self) -> None:
        """Frankfurter is queried with EUR as the base, so it can only answer
        pairs ending in EUR. Asking for USD->AUD would silently return
        EUR-denominated numbers under the wrong label."""
        with pytest.raises(ValueError, match="EUR"):
            EcbRates(client_returning(fixture())).series(
                "USD", "AUD", start=date(2025, 1, 6), end=date(2025, 1, 8)
            )
