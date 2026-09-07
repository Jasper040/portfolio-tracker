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

import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import httpx

from app.providers.base import ProviderError, SymbolCandidate

_ENDPOINT = "https://api.openfigi.com/v3/mapping"
_SOURCE = "openfigi"

#: OpenFIGI's `/v3/mapping` accepts at most 10 mapping jobs per request without
#: an API key -- the same keyless limit that makes 25 requests/minute the
#: overall ceiling. Batching to this size turns a few dozen ISINs into a
#: handful of requests instead of one per instrument.
_MAX_JOBS_PER_REQUEST = 10

#: How many times to retry a request that comes back 429, before giving up.
#: Batching should keep this from ever firing on a portfolio the size M2 is
#: built for; it exists so a larger one degrades with a wait instead of
#: aborting the whole backfill.
_MAX_RATE_LIMIT_RETRIES = 3

#: How long to wait between retries. OpenFIGI's keyless allowance resets on a
#: rolling one-minute window, so a minute is enough to clear it.
_RATE_LIMIT_RETRY_SECONDS = 60.0

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
    """`SymbolResolver` over OpenFIGI's mapping endpoint.

    `candidates_for` is the one parsing path; `candidates` is the readable
    single-ISIN case and delegates to it, so there is exactly one place that
    turns a response into `SymbolCandidate`s and exactly one place that
    matches a response entry back to the ISIN that asked for it.
    """

    name = _SOURCE

    def __init__(
        self, client: httpx.Client, *, sleep: Callable[[float], None] = time.sleep
    ) -> None:
        self._client = client
        self._sleep = sleep

    def candidates(self, isin: str) -> tuple[SymbolCandidate, ...]:
        return self.candidates_for([isin])[isin]

    def candidates_for(self, isins: Sequence[str]) -> dict[str, tuple[SymbolCandidate, ...]]:
        # A list, walked in the order given -- never a set. The response comes
        # back as a bare array with no ISIN attached to each entry, so the only
        # thing that ties an answer back to its question is this order.
        ordered = list(isins)
        result: dict[str, tuple[SymbolCandidate, ...]] = {}
        for start in range(0, len(ordered), _MAX_JOBS_PER_REQUEST):
            chunk = ordered[start : start + _MAX_JOBS_PER_REQUEST]
            entries = self._post_chunk(chunk)
            if len(entries) != len(chunk):
                # Guessing an alignment here would silently hand one
                # instrument's candidates to another. Naming the mismatch and
                # stopping is the only safe response.
                raise ProviderError(
                    f"openfigi: sent {len(chunk)} job(s) but received "
                    f"{len(entries)} result(s)"
                )
            for isin, entry in zip(chunk, entries, strict=True):
                result[isin] = _parse_entry(isin, entry)
        return result

    def _post_chunk(self, isins: Sequence[str]) -> list[Any]:
        jobs = [{"idType": "ID_ISIN", "idValue": isin} for isin in isins]
        label = ", ".join(isins)
        attempt = 0
        while True:
            try:
                response = self._client.post(
                    _ENDPOINT,
                    json=jobs,
                    headers={"Content-Type": "application/json"},
                    timeout=20.0,
                )
            except httpx.HTTPError as unreachable:
                raise ProviderError(f"openfigi: {label}: {unreachable}") from unreachable

            if response.status_code == 429:
                attempt += 1
                if attempt > _MAX_RATE_LIMIT_RETRIES:
                    raise ProviderError(
                        f"openfigi: {label}: HTTP 429 after "
                        f"{_MAX_RATE_LIMIT_RETRIES} retries"
                    )
                self._sleep(_RATE_LIMIT_RETRY_SECONDS)
                continue

            if response.status_code != 200:
                # Reached, not answered. Collapsing this into "no candidates"
                # would quarantine an instrument for a network blip and send
                # the operator looking for a ticker that already works.
                raise ProviderError(f"openfigi: {label}: HTTP {response.status_code}")

            payload = response.json()
            if not isinstance(payload, list):
                raise ProviderError(f"openfigi: {label}: unexpected response shape")
            return payload

def _parse_entry(isin: str, entry: Any) -> tuple[SymbolCandidate, ...]:
    """One job's worth of the response, already matched to its ISIN by position."""
    if not isinstance(entry, dict):
        raise ProviderError(f"openfigi: {isin}: unexpected response shape")

    if "warning" in entry:
        # A genuine "no such identifier". A fact about the data, and it
        # belongs in the quarantine rather than stopping the run.
        return ()

    data = entry.get("data")
    if not isinstance(data, list):
        raise ProviderError(f"openfigi: {isin}: response carries no data list")

    return tuple(_candidates_from(data))

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
