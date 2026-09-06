"""Try providers in order, and record which one answered.

Parent doc Sec 8.2's chained `PriceProvider`s with a hand-editable fallback. The
recording half is what M2 depends on: `coverage: "manual"` is a claim about where
a number came from, and the only thing that makes it a fact is that the series
carries the name of whoever supplied it all the way into `price_daily.source`.

The chain is keyed by ISIN and symbol together, because the two ends of it are
keyed differently. A network provider knows tickers; the manual file knows
instruments, since an instrument routed to it usually has no ticker to know.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from app.providers.base import PriceProvider, PriceSeries
from app.providers.manual import ManualPrices


class PriceChain:
    """The provider order, with the manual file last."""

    def __init__(self, providers: Sequence[PriceProvider], manual: ManualPrices) -> None:
        self._providers = tuple(providers)
        self._manual = manual

    def series(
        self, isin: str, symbol: str | None, *, since: date | None
    ) -> PriceSeries | None:
        """Prices for one instrument, from the first source that has any.

        `symbol is None` means the operator answered `manual` in
        `instrument_symbols.yaml`: go straight to the file without asking a
        provider, because they have already said none can help.

        `since is None` asks for the fixed five-year history (M2-7); a date asks
        only for what the cache lacks (M2-9).

        Returns `None` when nothing can answer. Not an exception and not an empty
        series: `None` is what the caller turns into `coverage: "missing"`, and a
        day it touches is valued `null` rather than short.
        """
        if symbol is not None:
            for provider in self._providers:
                found = (
                    provider.full_series(symbol)
                    if since is None
                    else provider.series_since(symbol, since)
                )
                # An empty series is not an answer. Accepting it would leave the
                # instrument unpriced with no fallback attempted.
                if found is not None and found.points:
                    return found
        return self._manual.series(isin)
