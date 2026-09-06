"""ISIN -> ticker candidates, via OpenFIGI. Free, no key below 25 req/min.

Adopted because it resolved every ISIN in the export where the alternatives could
not (M2 spec section 3.1). It is a CANDIDATE generator and nothing more: roughly
one ISIN in nine resolved -- correctly, by OpenFIGI's own lights -- to a
leveraged or inverse product on the same underlying. OpenFIGI is not wrong about
that; a 2x-short ETF genuinely carries its own identifiers. The question "which of
these did the account actually trade" is one only the ledger can answer, and
`domain/symbols.py` answers it.

The exchange mapping is where candidates come from. OpenFIGI speaks Bloomberg
exchange codes and Yahoo wants its own suffixes, so each listing becomes one
candidate. Anything unmapped falls back to the bare ticker, which is also always
offered: US listings carry no suffix at all, and an extra candidate the
discriminator rejects in a millisecond is cheaper than a venue nobody mapped.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx

from app.providers.base import ProviderError, SymbolCandidate

_ENDPOINT = "https://api.openfigi.com/v3/mapping"
_SOURCE = "openfigi"

#: Bloomberg exchange code -> Yahoo suffix. Covers the venues the portfolio
#: touches (Euronext, Xetra, ASX, LSE, US) plus the neighbours it is one trade
#: away from. Unmapped codes are not an error: the bare ticker is always offered
#: as well, so a missing entry costs a candidate, not a resolution.
EXCHANGE_SUFFIX: Mapping[str, str] = {
    "NA": ".AS",   # Euronext Amsterdam
    "FP": ".PA",   # Euronext Paris
    "BB": ".BR",   # Euronext Brussels
    "GY": ".DE",   # Xetra
    "GR": ".DE",   # Germany, composite
    "GF": ".F",    # Frankfurt floor
    "TQ": ".DE",   # Tradegate
    "LN": ".L",    # London
    "SM": ".MC",   # Madrid
    "IM": ".MI",   # Milan
    "SW": ".SW",   # SIX Swiss
    "AT": ".AX",   # ASX
    "HK": ".HK",   # Hong Kong
    "US": "",      # US composite
    "UN": "",      # NYSE
    "UQ": "",      # Nasdaq
    "UW": "",      # Nasdaq
    "UA": "",      # NYSE American
    "UR": "",      # US OTC
}

class OpenFigiResolver:
    """`SymbolResolver` over OpenFIGI's mapping endpoint."""

    name = _SOURCE

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def candidates(self, isin: str) -> tuple[SymbolCandidate, ...]:
        try:
            response = self._client.post(
                _ENDPOINT,
                json=[{"idType": "ID_ISIN", "idValue": isin}],
                headers={"Content-Type": "application/json"},
                timeout=20.0,
            )
        except httpx.HTTPError as unreachable:
            raise ProviderError(f"openfigi: {isin}: {unreachable}") from unreachable

        if response.status_code != 200:
            # Reached, not answered. Collapsing this into "no candidates" would
            # quarantine an instrument for a network blip and send the operator
            # looking for a ticker that already works.
            raise ProviderError(f"openfigi: {isin}: HTTP {response.status_code}")

        payload = response.json()
        if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
            raise ProviderError(f"openfigi: {isin}: unexpected response shape")

        first = payload[0]
        if "warning" in first:
            # A genuine "no such identifier". A fact about the data, and it
            # belongs in the quarantine rather than stopping the run.
            return ()

        entries = first.get("data")
        if not isinstance(entries, list):
            raise ProviderError(f"openfigi: {isin}: response carries no data list")

        return tuple(_candidates_from(entries))

def _candidates_from(entries: list[Any]) -> list[SymbolCandidate]:
    found: list[SymbolCandidate] = []
    seen: set[str] = set()

    def offer(symbol: str, name: str, exchange: str) -> None:
        if symbol and symbol not in seen:
            seen.add(symbol)
            found.append(
                SymbolCandidate(
                    symbol=symbol, name=name, exchange_code=exchange, source=_SOURCE
                )
            )

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        ticker = str(entry.get("ticker") or "").strip()
        if not ticker:
            continue
        name = str(entry.get("name") or "").strip()
        exchange = str(entry.get("exchCode") or "").strip()
        suffix = EXCHANGE_SUFFIX.get(exchange)
        if suffix is not None:
            offer(f"{ticker}{suffix}", name, exchange)

    # The bare ticker, always, and last: it is the fallback for an unmapped
    # exchange and the only right answer for a US listing.
    for entry in entries:
        if isinstance(entry, dict):
            ticker = str(entry.get("ticker") or "").strip()
            if ticker:
                offer(
                    ticker,
                    str(entry.get("name") or "").strip(),
                    str(entry.get("exchCode") or "").strip(),
                )

    return found
