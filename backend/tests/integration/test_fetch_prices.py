"""Filling the cache: what gets written, what gets refused, and how deep it goes.

Three properties this suite exists to hold:

* **It refuses while a symbol is unresolved, having written nothing.** A partial
  cache is worse than an empty one -- the chart would draw, with some
  instruments silently absent, and `coverage` would say `partial` as though the
  provider were at fault.
* **It backfills a fixed five years.** M2-7 makes the cache depth a constant and
  the chart start a separate question, which is what lets a reader ask to look
  back five years and actually be answered.
* **It is incremental afterwards.** A five-year refetch on every run is a
  request no free provider has a reason to keep serving.

Everything runs against stub providers. CI never touches the network.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlmodel import Session, select

from app.db import create_engine_and_tables
from app.ingest.prices import (
    _FX_OVERLAP,
    FetchResult,
    Providers,
    UnresolvedSymbols,
    fetch_prices,
)
from app.ingest.symbols import SymbolAnswer
from app.models.ledger import Account, ImportBatch, Transaction
from app.models.market import FxDaily, PriceDaily
from app.providers.base import FxPoint, FxSeries, PricePoint, PriceSeries, SymbolCandidate
from app.providers.chain import PriceChain
from app.providers.manual import ManualPrices

D = Decimal
ZERO = D("0.00")
NOW = datetime(2026, 9, 6, 12, 0, 0, tzinfo=UTC)
TODAY = NOW.date()

# --------------------------------------------------------------------------
# Stubs
# --------------------------------------------------------------------------
class StubResolver:
    name = "stub"

    def __init__(self, by_isin: dict[str, tuple[str, ...]]) -> None:
        self._by_isin = by_isin

    def candidates(self, isin: str) -> tuple[SymbolCandidate, ...]:
        return tuple(
            SymbolCandidate(symbol=s, name="Example", exchange_code="NA", source="stub")
            for s in self._by_isin.get(isin, ())
        )

class StubPrices:
    name = "stub"

    def __init__(self, by_symbol: dict[str, PriceSeries]) -> None:
        self._by_symbol = by_symbol
        self.full_calls: list[str] = []
        self.since_calls: list[tuple[str, date]] = []

    def full_series(self, symbol: str) -> PriceSeries | None:
        self.full_calls.append(symbol)
        return self._by_symbol.get(symbol)

    def series_since(self, symbol: str, since: date) -> PriceSeries | None:
        self.since_calls.append((symbol, since))
        found = self._by_symbol.get(symbol)
        if found is None:
            return None
        return PriceSeries(
            symbol=found.symbol,
            currency=found.currency,
            source=found.source,
            points=tuple(p for p in found.points if p.on >= since),
        )

class StubFx:
    name = "stub-fx"

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, date, date]] = []

    def series(self, from_ccy: str, to_ccy: str, *, start: date, end: date) -> FxSeries | None:
        self.calls.append((from_ccy, to_ccy, start, end))
        if from_ccy == to_ccy:
            return None
        day = start
        points: list[FxPoint] = []
        while day <= end:
            if day.weekday() < 5:
                points.append(FxPoint(on=day, rate=D("1.04")))
            day += timedelta(days=1)
        return FxSeries(from_ccy=from_ccy, to_ccy=to_ccy, source="stub-fx", points=tuple(points))

def five_years_of(symbol: str, currency: str, close: str) -> PriceSeries:
    """A daily series reaching the full backfill depth."""
    points: list[PricePoint] = []
    day = TODAY - timedelta(days=365 * 5)
    while day <= TODAY:
        if day.weekday() < 5:
            points.append(
                PricePoint(on=day, close_unadjusted=D(close), close_adjusted=D(close))
            )
        day += timedelta(days=1)
    return PriceSeries(symbol=symbol, currency=currency, source="stub", points=tuple(points))

def providers(
    *,
    resolver: StubResolver,
    prices: StubPrices,
    fx: StubFx,
    manual: ManualPrices | None = None,
) -> Providers:
    return Providers(
        resolver=resolver,
        prices=prices,
        chain=PriceChain([prices], manual or ManualPrices(_by_isin={})),
        fx=fx,
    )

# --------------------------------------------------------------------------
# A tiny ledger
# --------------------------------------------------------------------------
def ledger(engine, rows: list[tuple[str, date, str, str, str]]) -> None:
    """`rows` are (isin, trade_date, quantity, price_local, currency)."""
    account_id, batch_id = uuid4(), uuid4()
    with Session(engine) as session:
        session.add(Account(id=account_id, broker="degiro", name="test", base_currency="EUR"))
        session.add(
            ImportBatch(
                id=batch_id,
                source="degiro",
                filename="Transactions.csv",
                file_sha256="0" * 64,
                parser_version="1",
                imported_at=NOW.replace(tzinfo=None),
                row_count=len(rows),
                inserted_count=len(rows),
            )
        )
        for index, (isin, on, quantity, price, currency) in enumerate(rows):
            value = -(D(quantity) * D(price))
            session.add(
                Transaction(
                    id=uuid4(),
                    account_id=account_id,
                    import_batch_id=batch_id,
                    source="degiro",
                    source_ref=f"ref-{index}",
                    txn_type="BUY" if D(quantity) > 0 else "SELL",
                    trade_date=on,
                    trade_time="10:00",
                    isin=isin,
                    product_name="Example Holdings",
                    quantity=D(quantity),
                    price_local=D(price),
                    currency_local=currency,
                    fee_base=ZERO,
                    tax_base=ZERO,
                    autofx_fee_base=ZERO,
                    value_base=value,
                    net_base=value,
                    order_ref=f"ord-{index}",
                    raw_json="{}",
                )
            )
        session.commit()

@pytest.fixture(name="engine")
def _engine():
    return create_engine_and_tables("sqlite://")

BOUGHT_ON = TODAY - timedelta(days=400)

def _last_business_day_on_or_before(day: date) -> date:
    """`five_years_of` only prices weekdays; a fixture date can land on a
    weekend depending on which real day `NOW` happens to be."""
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day

# --------------------------------------------------------------------------
class TestRefusal:
    def test_refuses_while_a_symbol_is_unresolved(self, engine) -> None:
        ledger(engine, [("NL0000000001", BOUGHT_ON, "10", "20.00", "EUR")])
        # The only candidate trades at a third of the ledger's executed price:
        # the leveraged-product shape from the provider spike.
        stub = StubPrices({"EXA2S.DE": five_years_of("EXA2S.DE", "EUR", "6.00")})

        with pytest.raises(UnresolvedSymbols) as refused:
            fetch_prices(
                engine,
                providers(
                    resolver=StubResolver({"NL0000000001": ("EXA2S.DE",)}),
                    prices=stub,
                    fx=StubFx(),
                ),
                answers={},
                now=NOW,
                full=False,
            )

        assert [p.isin for p in refused.value.report.pending] == ["NL0000000001"]

    def test_writes_no_prices_when_it_refuses(self, engine) -> None:
        """A partial cache is worse than an empty one: the chart draws, some
        instruments are silently absent, and `coverage` blames the provider."""
        ledger(
            engine,
            [
                ("NL0000000001", BOUGHT_ON, "10", "20.00", "EUR"),
                ("NL0000000002", BOUGHT_ON, "5", "40.00", "EUR"),
            ],
        )
        stub = StubPrices(
            {
                "EXA.AS": five_years_of("EXA.AS", "EUR", "20.00"),
                "EXB2S.DE": five_years_of("EXB2S.DE", "EUR", "6.00"),
            }
        )
        with pytest.raises(UnresolvedSymbols):
            fetch_prices(
                engine,
                providers(
                    resolver=StubResolver(
                        {"NL0000000001": ("EXA.AS",), "NL0000000002": ("EXB2S.DE",)}
                    ),
                    prices=stub,
                    fx=StubFx(),
                ),
                answers={},
                now=NOW,
                full=False,
            )
        with Session(engine) as session:
            assert session.exec(select(PriceDaily)).all() == []

class TestBackfill:
    def test_stores_a_five_year_history_on_a_first_run(self, engine) -> None:
        """M2-7's fixed depth, asserted rather than assumed. This is what lets a
        reader ask to look back five years and be answered."""
        ledger(engine, [("NL0000000001", BOUGHT_ON, "10", "20.00", "EUR")])
        result = fetch_prices(
            engine,
            providers(
                resolver=StubResolver({"NL0000000001": ("EXA.AS",)}),
                prices=StubPrices({"EXA.AS": five_years_of("EXA.AS", "EUR", "20.00")}),
                fx=StubFx(),
            ),
            answers={},
            now=NOW,
            full=False,
        )
        assert isinstance(result, FetchResult)
        assert result.earliest is not None
        assert result.earliest <= TODAY - timedelta(days=365 * 4 + 180)

    def test_fetches_fx_over_the_same_five_years(self, engine) -> None:
        """A price five years deep with FX two years deep would value the older
        half of the chart at `missing`, which reads as a provider outage."""
        ledger(engine, [("US0000000404", BOUGHT_ON, "10", "20.00", "USD")])
        fx = StubFx()
        fetch_prices(
            engine,
            providers(
                resolver=StubResolver({"US0000000404": ("EXU",)}),
                prices=StubPrices({"EXU": five_years_of("EXU", "USD", "20.00")}),
                fx=fx,
            ),
            answers={},
            now=NOW,
            full=False,
        )
        with Session(engine) as session:
            earliest = min(row.rate_date for row in session.exec(select(FxDaily)).all())
        assert earliest <= TODAY - timedelta(days=365 * 4 + 180)

    def test_asks_for_fx_only_for_currencies_that_are_not_the_base(self, engine) -> None:
        ledger(engine, [("NL0000000001", BOUGHT_ON, "10", "20.00", "EUR")])
        fx = StubFx()
        fetch_prices(
            engine,
            providers(
                resolver=StubResolver({"NL0000000001": ("EXA.AS",)}),
                prices=StubPrices({"EXA.AS": five_years_of("EXA.AS", "EUR", "20.00")}),
                fx=fx,
            ),
            answers={},
            now=NOW,
            full=False,
        )
        assert fx.calls == []

class TestIncremental:
    def test_a_second_run_asks_only_for_what_the_cache_lacks(self, engine) -> None:
        """The overlap here is Yahoo's to apply (`YahooPrices._OVERLAP`), not
        this module's: a caller that subtracted one too would double it, so the
        exact newest cached date must reach the provider unmodified."""
        # Answered rather than probed: resolve_symbols re-probes an unanswered
        # instrument on every call (it has no memory of a prior run), which
        # would call `full_series` on this same stub during resolution and
        # swamp the very call log this test inspects. An answered instrument
        # is never probed (see TestAnsweredInstruments below), which isolates
        # the assertion to the price-fetch path this test is about.
        ledger(engine, [("NL0000000001", BOUGHT_ON, "10", "20.00", "EUR")])
        stub = StubPrices({"EXA.AS": five_years_of("EXA.AS", "EUR", "20.00")})
        answers = {"NL0000000001": SymbolAnswer("NL0000000001", "EXA.AS", "")}
        kit = providers(resolver=StubResolver({}), prices=stub, fx=StubFx())

        fetch_prices(engine, kit, answers=answers, now=NOW, full=False)
        with Session(engine) as session:
            newest_cached = max(
                row.price_date
                for row in session.exec(
                    select(PriceDaily).where(PriceDaily.isin == "NL0000000001")
                ).all()
            )
        stub.full_calls.clear()
        stub.since_calls.clear()
        fetch_prices(engine, kit, answers=answers, now=NOW, full=False)

        assert stub.since_calls, "the second run must be incremental"
        assert stub.full_calls == []
        # Exactly the newest cached date, not that date minus some overlap:
        # doubling Yahoo's own overlap would silently widen the window on
        # every incremental run.
        assert stub.since_calls == [("EXA.AS", newest_cached)]

    def test_the_incremental_fx_window_reaches_back_past_the_newest_cached_rate(
        self, engine
    ) -> None:
        """Unlike Yahoo, `EcbRates.series` carries no overlap of its own: it
        answers exactly the `[start, end]` window it is given. An ECB reference
        rate is rarely but not never corrected, and a window that begins
        exactly at the newest cached date could never see such a correction."""
        ledger(engine, [("US0000000404", BOUGHT_ON, "10", "20.00", "USD")])
        fx = StubFx()
        answers = {"US0000000404": SymbolAnswer("US0000000404", "EXU", "")}
        kit = providers(
            resolver=StubResolver({}),
            prices=StubPrices({"EXU": five_years_of("EXU", "USD", "20.00")}),
            fx=fx,
        )

        fetch_prices(engine, kit, answers=answers, now=NOW, full=False)
        with Session(engine) as session:
            newest_cached_rate = max(
                row.rate_date for row in session.exec(select(FxDaily)).all()
            )
        fx.calls.clear()
        fetch_prices(engine, kit, answers=answers, now=NOW, full=False)

        assert len(fx.calls) == 1
        _, _, start, _ = fx.calls[0]
        assert start < newest_cached_rate
        assert start == newest_cached_rate - _FX_OVERLAP

    def test_full_refetches_the_whole_history(self, engine) -> None:
        """M2-9's escape hatch, for when a provider revises its history."""
        # Answered, for the same reason as the test above: an unanswered
        # instrument is re-probed via this same stub on every call, which
        # would double-count against the `full_calls` this test inspects.
        ledger(engine, [("NL0000000001", BOUGHT_ON, "10", "20.00", "EUR")])
        stub = StubPrices({"EXA.AS": five_years_of("EXA.AS", "EUR", "20.00")})
        answers = {"NL0000000001": SymbolAnswer("NL0000000001", "EXA.AS", "")}
        kit = providers(resolver=StubResolver({}), prices=stub, fx=StubFx())

        fetch_prices(engine, kit, answers=answers, now=NOW, full=False)
        stub.full_calls.clear()
        fetch_prices(engine, kit, answers=answers, now=NOW, full=True)

        assert stub.full_calls == ["EXA.AS"]

    def test_a_revised_close_replaces_the_cached_one(self, engine) -> None:
        """Refetching must update, not duplicate: the unique constraint would
        otherwise abort the run, and swallowing it would keep a stale price for
        ever."""
        ledger(engine, [("NL0000000001", BOUGHT_ON, "10", "20.00", "EUR")])
        kit = providers(
            resolver=StubResolver({"NL0000000001": ("EXA.AS",)}),
            prices=StubPrices({"EXA.AS": five_years_of("EXA.AS", "EUR", "20.00")}),
            fx=StubFx(),
        )
        fetch_prices(engine, kit, answers={}, now=NOW, full=False)

        revised = providers(
            resolver=StubResolver({"NL0000000001": ("EXA.AS",)}),
            prices=StubPrices({"EXA.AS": five_years_of("EXA.AS", "EUR", "21.00")}),
            fx=StubFx(),
        )
        fetch_prices(engine, revised, answers={}, now=NOW, full=True)

        # Not BOUGHT_ON itself: it can land on a weekend depending on which
        # real day NOW is, and `five_years_of` only ever prices weekdays, so
        # an exact match on BOUGHT_ON would never be findable in that case
        # regardless of the implementation under test.
        priced_day = _last_business_day_on_or_before(BOUGHT_ON)
        with Session(engine) as session:
            rows = session.exec(
                select(PriceDaily).where(PriceDaily.price_date == priced_day)
            ).all()
        assert len(rows) == 1
        assert rows[0].close_unadjusted == D("21.00")

class TestAnsweredInstruments:
    def test_an_answered_symbol_is_fetched_without_being_probed(self, engine) -> None:
        ledger(engine, [("NL0000000001", BOUGHT_ON, "10", "20.00", "EUR")])
        stub = StubPrices({"EXA.AS": five_years_of("EXA.AS", "EUR", "20.00")})
        fetch_prices(
            engine,
            providers(resolver=StubResolver({}), prices=stub, fx=StubFx()),
            answers={"NL0000000001": SymbolAnswer("NL0000000001", "EXA.AS", "")},
            now=NOW,
            full=False,
        )
        with Session(engine) as session:
            assert session.exec(select(PriceDaily)).first() is not None

    def test_an_instrument_answered_manual_is_priced_from_the_file(
        self, engine, tmp_path
    ) -> None:
        """And the stored row says `manual`, which is the whole mechanism behind
        `coverage: "manual"`."""
        ledger(engine, [("US0000000404", BOUGHT_ON, "10", "20.00", "USD")])
        csv_path = tmp_path / "manual_prices.csv"
        csv_path.write_text(
            f"isin,date,close,currency\nUS0000000404,{BOUGHT_ON.isoformat()},20.00,USD\n",
            encoding="utf-8",
        )
        result = fetch_prices(
            engine,
            providers(
                resolver=StubResolver({}),
                prices=StubPrices({}),
                fx=StubFx(),
                manual=ManualPrices.load(csv_path),
            ),
            answers={"US0000000404": SymbolAnswer("US0000000404", None, "")},
            now=NOW,
            full=False,
        )
        assert result.sources.get("manual") == 1
        with Session(engine) as session:
            assert session.exec(select(PriceDaily)).one().source == "manual"

    def test_an_instrument_nothing_can_price_leaves_no_row_and_no_refusal(
        self, engine
    ) -> None:
        """Answered, and still unpriceable. That is `coverage: "missing"`, which
        is a result the API reports -- not an error that stops the run."""
        ledger(engine, [("US0000000404", BOUGHT_ON, "10", "20.00", "USD")])
        result = fetch_prices(
            engine,
            providers(resolver=StubResolver({}), prices=StubPrices({}), fx=StubFx()),
            answers={"US0000000404": SymbolAnswer("US0000000404", None, "")},
            now=NOW,
            full=False,
        )
        assert result.price_rows == 0
        with Session(engine) as session:
            assert session.exec(select(PriceDaily)).all() == []
