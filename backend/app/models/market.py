"""The fetched side of the determinism line (M2 spec section 4).

Everything in this file holds what the network said, dated by when it said it.
None of it is rebuilt: `rebuild()` must never write, delete or rewrite a row
here. `fetch-prices` is the only writer.

That separation is the whole architecture of M2. What the ledger knows is derived
and deterministic -- the same rows rebuilt on two days give the same answer. What
the network knows is fetched and dated, and the same request on two days may
legitimately give two answers, because a provider revises its history. Mixing the
two into one table would mean `rebuild()` was no longer a function of the ledger,
and M1's determinism tests would be asserting something false.

Prices carry `source` and `fetched_at` for the same reason. `coverage: "manual"`
is a claim about where a number came from; without a column recording who
answered, it would be an assumption.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Column, UniqueConstraint
from sqlmodel import Field, SQLModel

from app.models.types import DecimalString


class PriceDaily(SQLModel, table=True):
    """One instrument's close on one day, as one provider reported it.

    Two closes, in two columns, because parent doc Sec 7.5 forbids any call path
    from reaching both: total return computed from dividend-adjusted prices PLUS
    dividend income counts dividends twice. Separate columns behind separate
    accessors make that a checkable property of the call graph rather than a
    naming convention that decays.

    Both series are split-adjusted. "Unadjusted" means dividend-unadjusted:

    * `close_unadjusted` is the provider's plain close (Yahoo's `close`), and is
      what valuation and the symbol discriminator read;
    * `close_adjusted` is the total-return series (Yahoo's `adjclose`), read
      only by `analytics/total_return.py`.
    """

    __tablename__ = "price_daily"
    __table_args__ = (
        UniqueConstraint("isin", "price_date", name="uq_price_daily_isin_date"),
    )

    id: UUID = Field(primary_key=True)
    isin: str = Field(index=True)
    price_date: date = Field(index=True)
    close_unadjusted: Decimal = Field(sa_column=Column(DecimalString(), nullable=False))
    close_adjusted: Decimal = Field(sa_column=Column(DecimalString(), nullable=False))
    #: The currency the series is quoted in, as the provider reported it. Not
    #: assumed from the ledger: a match between the two is one of the three
    #: conditions the symbol discriminator checks (M2 spec section 6.2).
    currency: str
    #: Which provider answered. "manual" is what makes `coverage: "manual"` a fact.
    source: str
    fetched_at: datetime


class FxDaily(SQLModel, table=True):
    """One exchange rate on one day.

    `rate` is **units of `from_ccy` per 1 unit of `to_ccy`** -- so a USD->EUR row
    holding 1.0854 means 1.0854 USD buys 1 EUR, and you DIVIDE a USD amount by it
    to reach EUR. That is deliberately the same direction as
    `domain.money.FxRate` and as DeGiro's own `Exchange rate` column (parent doc
    Sec 3.5), so a row can be handed straight to `FxRate` without a reciprocal.

    A reciprocal would cost exactness -- one division on every stored rate -- and,
    worse, would put two directions in one codebase. The provider is queried in
    whichever direction returns this number verbatim (see `providers/ecb.py`).

    An explicit currency triple rather than an implied base, for the reason parent
    doc Sec 5.3 gives: a rate without a stated direction is a runtime error
    waiting to be plausible.
    """

    __tablename__ = "fx_daily"
    __table_args__ = (
        UniqueConstraint("from_ccy", "to_ccy", "rate_date", name="uq_fx_daily_pair_date"),
    )

    id: UUID = Field(primary_key=True)
    from_ccy: str = Field(index=True)
    to_ccy: str = Field(index=True)
    rate_date: date = Field(index=True)
    rate: Decimal = Field(sa_column=Column(DecimalString(), nullable=False))
    source: str
    fetched_at: datetime


class BenchmarkDaily(SQLModel, table=True):
    """One benchmark proxy's close on one day.

    Deliberately the same shape as `PriceDaily` minus the ISIN, and on the same
    (fetched) side of the determinism line: `rebuild()` must never write here.

    The key is a configuration slug -- `world`, not an ISIN -- for the three
    reasons M3 section 4.1 gives, of which the operative one is that
    `config/benchmarks.yaml` is tracked and an ISIN in a tracked file is a
    holding. `ingest/benchmarks.py` refuses an ISIN-shaped key so that stays
    true.

    Both closes are stored though M3 reads only the adjusted one: the provider
    returns both in one response, and fetching half of it now to re-fetch the
    other half later is a request no free provider has reason to keep serving.
    """

    __tablename__ = "benchmark_daily"
    __table_args__ = (
        UniqueConstraint("key", "price_date", name="uq_benchmark_daily_key_date"),
    )

    id: UUID = Field(primary_key=True)
    key: str = Field(index=True)
    price_date: date = Field(index=True)
    close_unadjusted: Decimal = Field(sa_column=Column(DecimalString(), nullable=False))
    close_adjusted: Decimal = Field(sa_column=Column(DecimalString(), nullable=False))
    currency: str
    source: str
    fetched_at: datetime


class SymbolReview(SQLModel, table=True):
    """The symbol quarantine (M2 spec section 6.3). Deliberately NOT the ledger.

    The same shape as `CorporateActionReview`, and for the same reasons: a
    projection of `(the ledger, config/instrument_symbols.yaml)` rebuilt on every
    `fetch-prices` run rather than a queue that accumulates, with the answers
    living in a hand-edited file beside the ledger.

    One row per unresolved instrument. `candidates` is a JSON list of what the
    resolver found and what the discriminator measured against each -- the
    ratios, not just a verdict, because the operator answering this needs to see
    why a candidate was rejected. The real failure this guards against is a
    leveraged or inverse ETF on the same underlying, which passes every naive
    check: the ISIN resolves, the ticker exists, years of daily bars come back,
    and the currency matches (M2 spec section 3.2).
    """

    __tablename__ = "symbol_review"
    __table_args__ = (UniqueConstraint("isin", name="uq_symbol_review_isin"),)

    id: UUID = Field(primary_key=True)
    isin: str = Field(index=True)
    product_name: str
    #: The currency the ledger's own trades in this instrument were priced in.
    trade_currency: str
    #: JSON: [{"symbol", "currency", "ratios", "worst_ratio", "accepted", "reason"}]
    candidates: str
    detected_at: datetime
    resolved: bool = False
