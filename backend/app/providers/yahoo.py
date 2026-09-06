"""Daily bars from Yahoo's chart endpoint. Free, no key, ~5 years per call.

Adopted because it covers every venue the portfolio touches and reports the
series currency, which is one of the three conditions the symbol discriminator
checks (M2 spec section 3.1). Undocumented, and acceptable on M2-6's terms:
personal use only. It carries no service guarantee and its terms are grey beyond
that, which reopens the moment this project is published -- the chained-provider
design keeps the cost of replacing it to writing one new `PriceProvider`.

Three parsing details decide whether the numbers are right, and each has a test:

* `quote[0].close` is split-adjusted and dividend-UNadjusted; `adjclose[0]` is
  both. They land in two columns because parent doc Sec 7.5 forbids any call path
  from reaching both -- total return from dividend-adjusted prices PLUS dividend
  income counts dividends twice.
* `null` appears in both arrays on days a venue was shut. A null is a missing
  day, not a zero. A zero close would divide by zero in the discriminator and
  value a position at nothing on the chart.
* A timestamp is UTC seconds, but a bar belongs to the day at the EXCHANGE. An
  ASX bar stamped 23:00 UTC is the next day in Sydney; read as a UTC date the
  whole series shifts back by one, putting prices on public holidays and
  misaligning every trade the discriminator checks. `meta.gmtoffset` is the fix.

A `None` return and a `ProviderError` mean different things, and most of what
calls this module is candidate probing, not a known-good lookup: symbol
resolution builds a batch of speculative tickers from an exchange-suffix map and
fetches every one, expecting most to be wrong (M2 spec section 6). When a
`result` comes back with a missing or unusable component -- no `meta.currency`,
no `timestamp`/`close` arrays, or an `adjclose` that is missing, not a list, or a
different length from `close` -- that is Yahoo answering a half-known ticker
with a stub, and it means exactly what an absent result means: this symbol has
no usable series. It returns `None` and the candidate is dropped, same as a 404.
Raising there would let one wrong guess out of a hundred abort the whole
backfill, which defeats the purpose of probing at all.
`ProviderError` stays reserved for what probing cannot explain away: a transport
failure, a non-404/422 HTTP error, and a response with no `chart` key at all --
that last one is not a bad guess, it is Yahoo answering in a shape this module
does not recognise. A wholesale schema change of that kind cannot hide behind
`None` either: every symbol would come back with no usable series, every
instrument would fail to resolve, and `fetch-prices` would refuse to complete
with every instrument sitting in the symbol quarantine, asking the owner to
answer them by hand. The failure still surfaces -- as a question instead of a
crash, and loudly the moment it is a real one instead of a wrong candidate.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from app.providers.base import PricePoint, PriceSeries, ProviderError

_ENDPOINT = "https://query1.finance.yahoo.com/v8/finance/chart/"
_SOURCE = "yahoo"

#: Without one the endpoint answers 429 to everything, which reads as a rate
#: limit nobody hit.
_HEADERS = {"User-Agent": "portfolio-tracker/0.1 (personal use)"}

#: How far before the requested date an incremental call reaches back. A
#: provider revises its most recent bars; without the overlap a stale close
#: would stay cached for ever because the incremental window never covers it
#: again.
_OVERLAP = timedelta(days=5)

class YahooPrices:
    """`PriceProvider` over the chart endpoint."""

    name = _SOURCE

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def full_series(self, symbol: str) -> PriceSeries | None:
        """The fixed five years (M2-7). `range=5y`: one call, no date arithmetic."""
        return self._fetch(symbol, {"range": "5y", "interval": "1d"})

    def series_since(self, symbol: str, since: date) -> PriceSeries | None:
        """Only what the cache lacks (M2-9), with a few days of overlap."""
        start = datetime.combine(since - _OVERLAP, datetime.min.time(), tzinfo=UTC)
        end = datetime.now(tz=UTC) + timedelta(days=1)
        return self._fetch(
            symbol,
            {
                "period1": str(int(start.timestamp())),
                "period2": str(int(end.timestamp())),
                "interval": "1d",
            },
        )

    def _fetch(self, symbol: str, params: dict[str, str]) -> PriceSeries | None:
        try:
            response = self._client.get(
                f"{_ENDPOINT}{symbol}", params=params, headers=_HEADERS, timeout=30.0
            )
        except httpx.HTTPError as unreachable:
            raise ProviderError(f"yahoo: {symbol}: {unreachable}") from unreachable

        if response.status_code in (404, 422):
            # A delisted or unknown symbol. A normal outcome that ends as
            # `coverage: "missing"` and a `null` on the chart.
            return None
        if response.status_code != 200:
            raise ProviderError(f"yahoo: {symbol}: HTTP {response.status_code}")

        return _parse(symbol, response.json())

def _parse(symbol: str, payload: Any) -> PriceSeries | None:
    chart = payload.get("chart") if isinstance(payload, dict) else None
    if not isinstance(chart, dict):
        raise ProviderError(f"yahoo: {symbol}: response carries no chart")

    results = chart.get("result")
    if not results:
        return None

    result = results[0]
    meta = result.get("meta") or {}
    currency = str(meta.get("currency") or "").upper()
    if not currency:
        # A stub result for a half-known candidate: no currency means no usable
        # series, the same outcome as no result at all. Most candidates are
        # wrong by construction (see module docstring); this must not abort
        # the whole probe.
        return None

    # Seconds to add to a UTC timestamp to reach the exchange's local clock.
    offset = int(meta.get("gmtoffset") or 0)

    stamps = result.get("timestamp")
    indicators = result.get("indicators") or {}
    quotes = (indicators.get("quote") or [{}])[0]
    adjusted = (indicators.get("adjclose") or [{}])[0]
    closes = quotes.get("close")
    adj_closes = adjusted.get("adjclose")

    if not isinstance(stamps, list) or not isinstance(closes, list):
        # Same reasoning: a candidate stub with no close series has no usable
        # series, not a schema violation.
        return None
    if not isinstance(adj_closes, list) or len(adj_closes) != len(closes):
        # Falling back to the plain close here would put a dividend-unadjusted
        # number in the total-return column, and Sec 7.5's whole point is that
        # the two must never be confused. That makes the series unusable, not
        # the response malformed -- the candidate is dropped, not fatal.
        return None

    points: list[PricePoint] = []
    for stamp, close, adj_close in zip(stamps, closes, adj_closes, strict=True):
        if close is None or adj_close is None:
            continue  # a day the venue was shut; a null is missing, not zero
        try:
            unadjusted_value = Decimal(str(close))
            adjusted_value = Decimal(str(adj_close))
        except InvalidOperation as bad:
            raise ProviderError(f"yahoo: {symbol}: unreadable close {close!r}") from bad
        if unadjusted_value <= 0 or adjusted_value <= 0:
            continue
        points.append(
            PricePoint(
                on=datetime.fromtimestamp(int(stamp) + offset, tz=UTC).date(),
                close_unadjusted=unadjusted_value,
                close_adjusted=adjusted_value,
            )
        )

    return PriceSeries(
        symbol=symbol,
        currency=currency,
        source=_SOURCE,
        points=tuple(sorted(points, key=lambda point: point.on)),
    )
