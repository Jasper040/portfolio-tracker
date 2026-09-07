"""ISIN -> ticker, via OpenFIGI. Keyless below 25 requests a minute.

It resolved every ISIN in the export, which is why it was adopted -- and it
resolved roughly one in nine to the wrong instrument, which is why what it
returns is called a CANDIDATE everywhere in this codebase. This module's job is
to turn an ISIN into a list of things worth checking; `domain/symbols.py` decides
which of them the ledger agrees with.

The exchange-code mapping is where the candidates come from. OpenFIGI answers in
Bloomberg exchange codes and Yahoo wants its own suffixes, so one ISIN produces
one candidate per listing plus a bare-ticker fallback for anything unmapped --
better one extra candidate the discriminator rejects in a millisecond than a
silent miss on a venue nobody thought of.

Batching (M2 defect fix): keyless OpenFIGI allows only 25 requests/minute, and
one request per ISIN blew through that on a real portfolio's first backfill.
`/v3/mapping` accepts up to 10 jobs per request, so `candidates_for` chunks the
ISINs and sends them as one job per ISIN per request, matching each response
entry back to its job BY POSITION -- the response carries no ISIN of its own.
The batching tests below exist to keep that positional match honest.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.providers.base import ProviderError
from app.providers.openfigi import (
    _MAX_RATE_LIMIT_RETRIES,
    _RATE_LIMIT_RETRY_SECONDS,
    EXCHANGE_SUFFIX,
    OpenFigiResolver,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "providers"

def client_returning(payload: object, status: int = 200) -> httpx.Client:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=payload)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    client.seen = seen  # type: ignore[attr-defined]
    return client

def client_with_responses(responses: list[tuple[int, object]]) -> httpx.Client:
    """A client that answers a different response on each call, then repeats
    its last response for any call beyond the list -- for testing a request
    that fails once and then clears, or one that never does."""
    seen: list[httpx.Request] = []
    remaining = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        status, payload = remaining.pop(0) if remaining else responses[-1]
        return httpx.Response(status, json=payload)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    client.seen = seen  # type: ignore[attr-defined]
    return client

def client_echoing_tickers(tickers: dict[str, str]) -> httpx.Client:
    """A client that answers each job with a ticker looked up by the ISIN it
    carries -- standing in for OpenFIGI's own positional response, since the
    real endpoint never echoes the ISIN back. Any ISIN not in `tickers`
    answers with a warning, OpenFIGI's "no such identifier" shape."""
    seen: list[list[dict[str, str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        jobs = json.loads(request.content)
        seen.append(jobs)
        entries = [
            {"data": [{"ticker": tickers[job["idValue"]], "name": "Example", "exchCode": "US"}]}
            if job["idValue"] in tickers
            else {"warning": "No identifier found."}
            for job in jobs
        ]
        return httpx.Response(200, json=entries)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    client.seen = seen  # type: ignore[attr-defined]
    return client

def fixture(name: str) -> object:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))

class TestCandidates:
    def test_maps_each_listing_to_a_yahoo_symbol(self) -> None:
        resolver = OpenFigiResolver(client_returning(fixture("openfigi_mapping.json")))
        symbols = [c.symbol for c in resolver.candidates("NL0000000001")]
        assert "EXA.AS" in symbols   # Bloomberg NA -> Euronext Amsterdam
        assert "EXA.DE" in symbols   # Bloomberg GY -> Xetra

    def test_always_offers_the_bare_ticker_as_well(self) -> None:
        """US listings carry no suffix, and an unmapped exchange code would
        otherwise produce no candidate at all. One extra candidate costs a
        rejection; a missing one costs a quarantine."""
        resolver = OpenFigiResolver(client_returning(fixture("openfigi_mapping.json")))
        assert "EXA" in [c.symbol for c in resolver.candidates("NL0000000001")]

    def test_offers_each_symbol_once(self) -> None:
        resolver = OpenFigiResolver(client_returning(fixture("openfigi_mapping.json")))
        symbols = [c.symbol for c in resolver.candidates("NL0000000001")]
        assert len(symbols) == len(set(symbols))

    def test_carries_the_name_and_exchange_for_the_quarantine(self) -> None:
        """An operator answering a quarantine is choosing between tickers. A
        list of bare symbols with no names is a list they cannot answer."""
        resolver = OpenFigiResolver(client_returning(fixture("openfigi_mapping.json")))
        first = resolver.candidates("NL0000000001")[0]
        assert first.name == "EXAMPLE HOLDINGS NV"
        assert first.exchange_code in EXCHANGE_SUFFIX
        assert first.source == "openfigi"

    def test_an_unknown_isin_yields_nothing(self) -> None:
        """A warning, not an error: an instrument with no public identifier is a
        fact about the data, and it belongs in the quarantine rather than
        stopping the run."""
        resolver = OpenFigiResolver(client_returning(fixture("openfigi_not_found.json")))
        assert resolver.candidates("NL0000000009") == ()

    def test_asks_for_the_isin_by_the_identifier_type_openfigi_expects(self) -> None:
        client = client_returning(fixture("openfigi_mapping.json"))
        OpenFigiResolver(client).candidates("NL0000000001")
        body = json.loads(client.seen[0].content)  # type: ignore[attr-defined]
        assert body == [{"idType": "ID_ISIN", "idValue": "NL0000000001"}]

class TestBatching:
    """`candidates_for` is the one parsing path -- `candidates` delegates to it
    with a single-ISIN list, so these tests exercise both at once."""

    def test_splits_more_than_ten_isins_across_multiple_requests(self) -> None:
        isins = [f"NL00000000{i:02d}" for i in range(1, 13)]  # 12 -> two requests
        tickers = {isin: f"T{isin[-2:]}" for isin in isins}
        client = client_echoing_tickers(tickers)

        result = OpenFigiResolver(client).candidates_for(isins)

        bodies = client.seen  # type: ignore[attr-defined]
        assert len(bodies) == 2
        assert all(len(body) <= 10 for body in bodies)
        assert sum(len(body) for body in bodies) == 12
        assert set(result) == set(isins)
        assert result["NL0000000001"][0].symbol == "T01"
        assert result["NL0000000012"][0].symbol == "T12"

    def test_every_input_isin_appears_including_ones_answered_with_a_warning(self) -> None:
        client = client_echoing_tickers({"NL0000000001": "EXA"})
        result = OpenFigiResolver(client).candidates_for(
            ["NL0000000001", "NL0000000009"]
        )
        assert set(result) == {"NL0000000001", "NL0000000009"}
        assert result["NL0000000001"] != ()
        assert result["NL0000000009"] == ()

    def test_matches_results_to_the_right_isin_by_position(self) -> None:
        """The response array carries no ISIN of its own -- only its position
        ties an entry back to the job that asked for it. Two ISINs, two
        different tickers: if the match were ever off by one, one instrument
        would silently get the other's ticker."""
        client = client_echoing_tickers(
            {"NL0000000001": "AAAA", "US0000000404": "BBBB"}
        )
        result = OpenFigiResolver(client).candidates_for(
            ["NL0000000001", "US0000000404"]
        )
        assert result["NL0000000001"][0].symbol == "AAAA"
        assert result["US0000000404"][0].symbol == "BBBB"

    def test_a_short_response_array_raises_rather_than_guessing_an_alignment(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            jobs = json.loads(request.content)
            return httpx.Response(200, json=[{"data": []}] * (len(jobs) - 1))

        client = httpx.Client(transport=httpx.MockTransport(handler))
        with pytest.raises(ProviderError, match="2 job"):
            OpenFigiResolver(client).candidates_for(
                ["NL0000000001", "US0000000404"]
            )

class TestFailures:
    def test_raises_rather_than_reporting_no_candidates_on_a_server_error(self) -> None:
        """"Answered with nothing" and "could not be reached" are different
        facts. Collapsing them would quarantine an instrument for a network
        blip and tell the operator to go and find a ticker that already works."""
        resolver = OpenFigiResolver(client_returning({}, status=500))
        with pytest.raises(ProviderError):
            resolver.candidates("NL0000000001")

    def test_raises_when_the_response_is_not_the_shape_it_documents(self) -> None:
        resolver = OpenFigiResolver(client_returning({"unexpected": True}))
        with pytest.raises(ProviderError):
            resolver.candidates("NL0000000001")

class TestRateLimit:
    """Batching should keep a portfolio this size well under 25 req/min, so
    this is a safety net for a bigger one -- it must degrade with a wait
    rather than abort the whole backfill. `sleep` is injected in every test
    here so none of them actually wait."""

    def test_a_429_that_clears_on_retry_succeeds(self) -> None:
        client = client_with_responses(
            [(429, {}), (200, fixture("openfigi_mapping.json"))]
        )
        waits: list[float] = []
        resolver = OpenFigiResolver(client, sleep=waits.append)

        result = resolver.candidates("NL0000000001")

        assert result != ()
        assert waits == [_RATE_LIMIT_RETRY_SECONDS]

    def test_a_429_that_persists_raises_provider_error(self) -> None:
        client = client_returning({}, status=429)
        waits: list[float] = []
        resolver = OpenFigiResolver(client, sleep=waits.append)

        with pytest.raises(ProviderError, match="429"):
            resolver.candidates("NL0000000001")

        assert waits == [_RATE_LIMIT_RETRY_SECONDS] * _MAX_RATE_LIMIT_RETRIES
