"""Daily bars from Yahoo's chart endpoint. Keyless, roughly five years per call.

Adopted for prices because it covers every venue the portfolio touches and
reports the series currency, which is one of the three things the symbol
discriminator checks. Undocumented, and acceptable on M2-6's terms: personal use
only. The chained-provider design keeps the cost of replacing it low.

Three parsing details decide whether the numbers are right:

* `close` is split-adjusted and dividend-UNadjusted; `adjclose` is both. They go
  into two columns because parent doc Sec 7.5 forbids any call path from reaching
  both.
* Yahoo leaves `null` in the arrays on days a venue was shut. A null is a
  missing day, not a zero -- and a zero close would sail through the symbol
  discriminator's division as an infinity or a crash.
* A timestamp is UTC, but the day a bar belongs to is the day at the EXCHANGE.
  An ASX bar stamped 23:00 UTC belongs to the following day in Sydney, and
  reading it as a UTC date would shift the whole series back by one -- enough to
  put a price on a public holiday and misalign every trade the discriminator
  checks.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from app.providers.base import ProviderError
from app.providers.yahoo import YahooPrices

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

def fixture(name: str) -> object:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))

class TestParsing:
    def test_reports_the_currency_the_series_is_quoted_in(self) -> None:
        series = YahooPrices(
            client_returning(fixture("yahoo_chart_eur.json"))
        ).full_series("EXA.AS")
        assert series is not None
        assert series.currency == "EUR"

    def test_reads_both_closes_into_separate_fields(self) -> None:
        series = YahooPrices(
            client_returning(fixture("yahoo_chart_eur.json"))
        ).full_series("EXA.AS")
        assert series is not None
        first = series.points[0]
        assert first.close_unadjusted == D("12.5")
        assert first.close_adjusted == D("12.2")

    def test_prices_are_decimals_not_floats(self) -> None:
        """A JSON number is an IEEE double. Every price in this app goes through
        `str()` before `Decimal()` so the value stored is the one Yahoo printed."""
        series = YahooPrices(
            client_returning(fixture("yahoo_chart_eur.json"))
        ).full_series("EXA.AS")
        assert series is not None
        assert all(isinstance(p.close_unadjusted, Decimal) for p in series.points)

    def test_drops_the_days_yahoo_left_null(self) -> None:
        series = YahooPrices(
            client_returning(fixture("yahoo_chart_eur.json"))
        ).full_series("EXA.AS")
        assert series is not None
        assert [p.on for p in series.points] == [date(2025, 1, 6), date(2025, 1, 8)]

    def test_dates_a_bar_by_the_exchange_day_not_the_utc_day(self) -> None:
        """The ASX fixture is stamped 2025-01-05 23:00 UTC with a +11 offset. In
        Sydney that is 2025-01-06, and Sydney is where the market was open."""
        series = YahooPrices(
            client_returning(fixture("yahoo_chart_asx.json"))
        ).full_series("EXB.AX")
        assert series is not None
        assert [p.on for p in series.points] == [date(2025, 1, 6)]

    def test_records_itself_as_the_source(self) -> None:
        series = YahooPrices(
            client_returning(fixture("yahoo_chart_eur.json"))
        ).full_series("EXA.AS")
        assert series is not None
        assert series.source == "yahoo"

    def test_returns_points_in_date_order(self) -> None:
        series = YahooPrices(
            client_returning(fixture("yahoo_chart_eur.json"))
        ).full_series("EXA.AS")
        assert series is not None
        assert list(series.points) == sorted(series.points, key=lambda p: p.on)

class TestRequests:
    def test_the_full_history_asks_for_the_fixed_five_years(self) -> None:
        """M2-7: one call per instrument, no date arithmetic."""
        client = client_returning(fixture("yahoo_chart_eur.json"))
        YahooPrices(client).full_series("EXA.AS")
        request = client.seen[0]  # type: ignore[attr-defined]
        assert request.url.params["range"] == "5y"
        assert request.url.params["interval"] == "1d"
        assert "EXA.AS" in str(request.url)

    def test_an_incremental_call_asks_only_for_what_the_cache_lacks(self) -> None:
        """M2-9. `period1` is a few days before the requested date so a provider
        that revises its most recent bars corrects them rather than leaving a
        stale close permanently cached."""
        client = client_returning(fixture("yahoo_chart_eur.json"))
        YahooPrices(client).series_since("EXA.AS", date(2025, 6, 1))
        request = client.seen[0]  # type: ignore[attr-defined]
        assert "period1" in request.url.params
        assert "period2" in request.url.params
        assert "range" not in request.url.params

    def test_sends_a_user_agent(self) -> None:
        """Without one the endpoint answers 429 to everything, which would read
        as a rate limit nobody hit."""
        client = client_returning(fixture("yahoo_chart_eur.json"))
        YahooPrices(client).full_series("EXA.AS")
        assert client.seen[0].headers.get("user-agent")  # type: ignore[attr-defined]

class TestNoSeries:
    def test_a_delisted_or_unknown_symbol_answers_nothing(self) -> None:
        """`None`, not an exception: an instrument with no series is a normal
        outcome that ends as `coverage: "missing"` and a `null` on the chart."""
        assert (
            YahooPrices(client_returning(fixture("yahoo_not_found.json"), status=404)).full_series(
                "NOPE"
            )
            is None
        )

    def test_a_server_error_raises_instead(self) -> None:
        """A non-200 status other than 404/422 is a transport-level failure, not
        a candidate verdict, and must still stop the run."""
        with pytest.raises(ProviderError):
            YahooPrices(client_returning({}, status=500)).full_series("EXA.AS")

    def test_a_result_with_no_currency_answers_nothing(self) -> None:
        """The exact shape that broke a real run: Yahoo answers a half-known
        candidate ticker with a stub result carrying no `meta.currency`. That
        means this symbol has no usable series -- the same outcome as no result
        at all -- and must not abort the whole probe (M2 spec section 6)."""
        assert (
            YahooPrices(client_returning(fixture("yahoo_chart_no_currency.json"))).full_series(
                "EXC.DE"
            )
            is None
        )

    def test_a_response_missing_the_arrays_answers_nothing(self) -> None:
        payload = {"chart": {"result": [{"meta": {"currency": "EUR", "gmtoffset": 0}}]}}
        assert YahooPrices(client_returning(payload)).full_series("EXA.AS") is None

    def test_adjclose_of_a_different_length_than_close_answers_nothing(self) -> None:
        """Falling back to the plain close here would put a dividend-unadjusted
        number in the total-return column (Sec 7.5). That makes the series
        unusable, not the response malformed -- the candidate is dropped."""
        payload = {
            "chart": {
                "result": [
                    {
                        "meta": {"currency": "EUR", "gmtoffset": 0},
                        "timestamp": [1736150400, 1736236800],
                        "indicators": {
                            "quote": [{"close": [12.5, 12.75]}],
                            "adjclose": [{"adjclose": [12.2]}],
                        },
                    }
                ]
            }
        }
        assert YahooPrices(client_returning(payload)).full_series("EXA.AS") is None

    def test_a_response_with_no_chart_key_at_all_raises(self) -> None:
        """Not a bad guess: a response shaped nothing like the documented
        endpoint is a schema change, and must still be loud."""
        with pytest.raises(ProviderError):
            YahooPrices(client_returning({"unexpected": "shape"})).full_series("EXA.AS")

    def test_a_transport_error_raises(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        with pytest.raises(ProviderError):
            YahooPrices(client).full_series("EXA.AS")
