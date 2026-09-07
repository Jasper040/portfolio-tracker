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

        **Why an empty series is treated differently depending on `since`.**
        There are two distinct things a provider can mean by "no points here",
        and they must not be conflated:

        * On a full fetch (`since is None`), an empty series means this symbol
          has NO series at all -- nothing has ever been published for it, or
          the provider does not recognise the ticker. That is exactly the case
          the manual file exists to answer, so it is not treated as an answer:
          the loop keeps trying other providers, and falls through to
          `self._manual` if none of them have anything either.
        * On an incremental fetch (`since` is a date), an empty series from a
          provider that otherwise returned (i.e. did not answer `None`) means
          this symbol has NOTHING NEW since `since` -- the cache is already
          current. That is a real, complete answer, not an absence, and it must
          be returned as-is rather than falling through to the manual file.
          `ManualPrices.series` ignores `since` entirely and returns the whole
          CSV; treating an empty incremental result as "no answer" would upsert
          that whole history over the cached provider rows on every run with
          nothing new, rewriting `source` to `"manual"` and flipping already-
          fresh days from `full` coverage to `manual` for no reason at all.

        Only a bare `None` from the provider -- meaning it does not know the
        symbol, full stop -- justifies falling through, in either case.
        """
        if symbol is not None:
            for provider in self._providers:
                if since is None:
                    found = provider.full_series(symbol)
                    # An empty full series is not an answer. Accepting it would
                    # leave the instrument unpriced with no fallback attempted.
                    if found is not None and found.points:
                        return found
                else:
                    found = provider.series_since(symbol, since)
                    if found is not None:
                        # Empty here means "nothing new since `since`", not
                        # "this symbol has no series" -- see the docstring
                        # above. Return it as the real answer it is; do not
                        # fall through to the manual file.
                        return found
        return self._manual.series(isin)
