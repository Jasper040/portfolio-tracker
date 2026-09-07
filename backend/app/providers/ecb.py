"""ECB reference rates, via Frankfurter. Free, no key, full history in one call.

Parent doc Sec 8.2 anticipated ECB rates and the spike confirmed them: one
request covers five years of a currency pair, which is the whole FX backfill.

**The direction is the entire design of this module.** `FxDaily.rate` holds units
of `from_ccy` per 1 unit of `to_ccy`, matching `domain.money.FxRate` and DeGiro's
own `Exchange rate` column -- so a foreign amount is DIVIDED by it to reach EUR.

Frankfurter is therefore queried with the BASE currency as its base:
`base=EUR&symbols=USD` returns "1 EUR = 1.04 USD", and 1.04 is the stored rate
verbatim. Querying the other way and taking a reciprocal would cost exactness on
every row and put two FX directions in one codebase, which is exactly the
plausible-wrong-number failure `FxRate` exists to prevent.

A consequence worth stating: this provider can only answer pairs whose `to_ccy`
is the base currency. Asking for USD->AUD raises rather than returning
EUR-denominated numbers under the wrong label.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from app.providers.base import FxPoint, FxSeries, ProviderError

_ENDPOINT = "https://api.frankfurter.app/"
_SOURCE = "ecb"
_BASE = "EUR"

class EcbRates:
    """`FxProvider` over Frankfurter's ECB series."""

    name = _SOURCE

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def series(
        self, from_ccy: str, to_ccy: str, *, start: date, end: date
    ) -> FxSeries | None:
        if to_ccy != _BASE:
            # `ProviderError`, not a bare `ValueError`: `fetch_prices` passes
            # whatever base currency the `Account` row carries, so a non-EUR
            # account reaches this branch in normal operation, not only in a
            # test. The CLI's `fetch_prices_command` already catches
            # `ProviderError` and exits 2 with the message; a `ValueError`
            # here would escape as an uncaught traceback instead.
            raise ProviderError(
                f"frankfurter is queried with {_BASE} as its base, so it cannot answer "
                f"{from_ccy}->{to_ccy}; a rate to anything but {_BASE} would be a "
                "different number under this label"
            )
        if from_ccy == to_ccy:
            # 1 by definition. Fetching it would record a network fact where an
            # arithmetic one belongs, and Frankfurter does not serve it.
            return None

        url = f"{_ENDPOINT}{start.isoformat()}..{end.isoformat()}"
        try:
            response = self._client.get(
                url, params={"base": _BASE, "symbols": from_ccy}, timeout=30.0
            )
        except httpx.HTTPError as unreachable:
            raise ProviderError(f"ecb: {from_ccy}->{to_ccy}: {unreachable}") from unreachable

        if response.status_code != 200:
            raise ProviderError(f"ecb: {from_ccy}->{to_ccy}: HTTP {response.status_code}")

        points = _parse(from_ccy, response.json())
        if not points:
            return None
        return FxSeries(from_ccy=from_ccy, to_ccy=to_ccy, source=_SOURCE, points=points)

def _parse(from_ccy: str, payload: Any) -> tuple[FxPoint, ...]:
    rates = payload.get("rates") if isinstance(payload, dict) else None
    if not isinstance(rates, dict):
        raise ProviderError(f"ecb: {from_ccy}: response carries no rates")

    points: list[FxPoint] = []
    for day, quoted in rates.items():
        if not isinstance(quoted, dict) or from_ccy not in quoted:
            continue
        try:
            # `str()` first: a JSON number is an IEEE double, and every rate in
            # this app is the exact figure the ECB published.
            rate = Decimal(str(quoted[from_ccy]))
            on = datetime.strptime(day, "%Y-%m-%d").date()
        except (InvalidOperation, ValueError) as bad:
            raise ProviderError(f"ecb: {from_ccy}: unreadable rate on {day!r}") from bad
        if rate > 0:
            points.append(FxPoint(on=on, rate=rate))

    return tuple(sorted(points, key=lambda point: point.on))
