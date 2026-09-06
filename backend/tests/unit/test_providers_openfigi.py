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
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.providers.base import ProviderError
from app.providers.openfigi import EXCHANGE_SUFFIX, OpenFigiResolver

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

class TestFailures:
    def test_raises_rather_than_reporting_no_candidates_on_a_server_error(self) -> None:
        """"Answered with nothing" and "could not be reached" are different
        facts. Collapsing them would quarantine an instrument for a network
        blip and tell the operator to go and find a ticker that already works."""
        resolver = OpenFigiResolver(client_returning({}, status=500))
        with pytest.raises(ProviderError):
            resolver.candidates("NL0000000001")

    def test_raises_on_a_rate_limit(self) -> None:
        resolver = OpenFigiResolver(client_returning({}, status=429))
        with pytest.raises(ProviderError, match="429"):
            resolver.candidates("NL0000000001")

    def test_raises_when_the_response_is_not_the_shape_it_documents(self) -> None:
        resolver = OpenFigiResolver(client_returning({"unexpected": True}))
        with pytest.raises(ProviderError):
            resolver.candidates("NL0000000001")
