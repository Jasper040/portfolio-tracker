# M2 — Prices, FX, Valuation and Coverage — Design

**Date:** 2026-09-06
**Status:** Approved
**Parent spec:** `docs/superpowers/specs/2026-09-05-portfolio-tracker-design.md`
**Milestone:** M2 (parent §10) — "Prices, FX, valuation, coverage → Portfolio value chart;
positions table; coverage strip"

A section mark (§) always refers to the parent spec. This document's own sections
are spelled out, as "section 4".

---

## 1. Purpose and scope

M1 turned the ledger into lots and closures. Everything it reports — cost basis,
realised P&L, share counts — is computable from the ledger alone, which is why
`rebuild()` can be provably deterministic.

M2 is where that stops being true. A position's *worth* needs a price, a price for a
foreign holding needs an exchange rate, and neither is in the ledger. This milestone
brings in the first data the account did not produce, and most of its design is about
keeping that data from contaminating the part that is checkable.

**In scope:** price and FX providers, symbol resolution and its verification, the daily
position and cash series, valuation as a read-time join, coverage as a first-class
result, and the three UI surfaces §10 names.

**Out of scope, and a defect if present:** TWR/MWR (M6), benchmarks (M3), the
counterfactual (M4), dividend analytics (M5), news (M8). Unrealised P&L *is* in scope —
M1 deferred it only for want of a price.

---

## 2. Decisions taken

Confirmed by the owner on 2026-09-06.

| Ref | Decision |
|---|---|
| M2-1 | The provider spike runs for real against the owner's own ISINs rather than on documentation. Findings in section 3. |
| M2-2 | Symbol resolution auto-accepts what validates against the ledger and quarantines the rest, mirroring M0's corporate-action flow. Never auto-accepts on "a series came back". |
| M2-3 | Every weekday is valued. Where a venue was shut the last close is carried forward and the day records how stale it is. No holes in the chart, no silent inference. |
| M2-4 | Portfolio value is **net**: holdings at market plus cash, so a debit balance reduces it. Cash is reported as its own component. |
| M2-5 | `rebuild()` gains `position_daily` and `cash_daily` but **not** valuation. Prices are a cache, not a derived table. Reasoning in section 4. |
| M2-6 | **Personal use only.** Yahoo's undocumented endpoint is acceptable on those terms, which closes what was open item 1. Revisit before any publication. |
| M2-7 | The cache holds a **fixed five years** — one call per instrument, no date arithmetic. The **valuation series starts at the first day a position existed**, so the chart does not open with years of flat zero. Cache depth and chart start are separate concerns. |
| M2-8 | The value chart lives on the **Positions** screen beside the table. Both go live together, so no screen mixes live and modelled figures. Dashboard stays modelled until M6. |
| M2-9 | `fetch-prices` is **incremental**, fetching only days the cache lacks, with `--full` to refetch when a provider revises its history. |
| M2-10 | The symbol-validation band is **±30%** (section 6.2). |

---

## 3. The provider spike

§8.2 deferred provider selection to "a written comparison in M2", naming ISIN
resolution as the deciding factor. This is that comparison. It was run against the
real export, not against vendor documentation, because the documentation answers a
different question than the one that matters.

### 3.1 Free tiers, measured

| Provider | Free tier | Verdict |
|---|---|---|
| EODHD | 20 calls/day, 1 year of history, personal-use licence | Rejected. The portfolio needs multiple years across dozens of instruments; backfill alone would take days and the depth is not there. |
| Twelve Data | 800 calls/day but three exchanges, US equities | Rejected. The majority of the portfolio trades on Euronext, Xetra and ASX. |
| Stooq | Free, no key, full history as CSV | Rejected. Serves a JavaScript bot-challenge to programmatic clients on both `.com` and `.pl`, regardless of user agent. Working around it would be fragile and against their terms. |
| Finnhub | 60 calls/min, native ISIN lookup | Viable as a resolver; historical candles are behind the paywall. Held in reserve. |
| **OpenFIGI** | Free, no key below 25 req/min | **Adopted** for ISIN → ticker. Resolved every ISIN in the export. |
| **Yahoo chart endpoint** | Free, no key, ~5 years of daily bars per call | **Adopted** for prices. Covers every venue the portfolio touches, and reports the series currency. |
| **ECB via Frankfurter** | Free, no key, full history in one call | **Adopted** for FX, as §8.2 anticipated. |

The stack that works needs no registration at all, which inverts the assumption behind
decision G1. The providers that require a key are the ones that cannot serve this
portfolio.

### 3.2 The finding that shaped the design

A naive read of the spike says every instrument resolved and every instrument priced.
That number is wrong, and the way it is wrong is the reason section 6 exists.

Roughly one instrument in nine resolved, via a real ISIN lookup returning a real
series, to a **leveraged or inverse ETF on the same underlying**. Not a near miss: one
large holding resolved to a 2x-short product trading at a fraction of the underlying's
price and moving in the opposite direction. Valued that way, the position would have
been wrong by a factor of tens *and* the portfolio chart would have sloped the wrong
way on exactly the days the reader most wants to trust it.

Every one of those wrong matches passed the checks an implementation would naturally
apply: the ISIN resolved, the ticker existed, the series returned years of daily bars,
and the currency matched the ledger. **"A series came back" is not evidence that it is
the right series.**

### 3.3 What the ledger can prove

The account is its own oracle. It records what was actually paid, per instrument, per
date — and a price series for the same instrument must agree with that.

One wrinkle: a provider's series is split-adjusted and an executed price is not, so a
pre-split trade compares at the split ratio rather than at parity. M1 already derives
that ratio from the corporate-action legs M0 suppressed, so the correction is available
without asking anyone to type it:

```
executed price (trade currency) ÷ split factor from derive_splits()
    compared against
the provider's split-adjusted close on the trade date
```

Measured on the real export, correct symbols produced ratios of 0.99 to 1.01 across
every trade, **including across a 10-for-1 split** once M1's own derived ratio was
applied. The impostors produced ratios ranging from under 3 to over 40, with no stable
value. The signal is not subtle, and the check independently rediscovers a split M1
derived by a completely different route — which is the kind of agreement worth having.

---

## 4. Architecture: the determinism line

§4.1 lists `position_daily` and `valuation_daily` as derived tables beside `lot` and
`lot_closure`, all rewritten by `rebuild()`. The same section calls `rebuild()` provably
deterministic. **Once valuation depends on a fetched price, those two cannot both
hold** — the same ledger rebuilt on two days would produce different rows from
identical facts, and M1's determinism tests would be asserting something false.

M2 resolves it by splitting on that line rather than papering over it.

```
config/instrument_symbols.yaml       operator answers, as corporate_actions.yaml
    ↓
providers/          network: OpenFIGI, Yahoo, ECB, manual CSV, behind Protocols
    ↓
[ price_daily ]  [ fx_daily ]        CACHE — fetched and dated, never rebuilt
                                     ↑ written only by `fetch-prices`
[ transaction ]  THE LEDGER
    ↓
domain/positions.py                  pure: rows → daily quantity and cash series
    ↓
rebuild() → [ position_daily ]  [ cash_daily ]      DERIVED — deterministic
    ↓
analytics/valuation.py               read-time join of the two halves
    ↓
api/ → chart, positions, coverage
```

**What the ledger knows is derived and deterministic. What the network knows is fetched
and dated.** `rebuild()` keeps its property because everything it writes remains a pure
function of the ledger; the price cache keeps its own provenance because every row
records who supplied it and when.

There is deliberately **no `valuation_daily` table**. Valuation is the join, so it
cannot go stale against either half — a table would have two independent triggers, a
ledger change and a price refresh, either of which would silently invalidate it.

The cost is a join per read rather than a lookup. At this portfolio's size that is tens
of thousands of rows in SQLite, which is milliseconds. M2 has no scale problem to solve
and should not pre-solve one.

---

## 5. Data model

Four new tables. Which side of the determinism line each sits on is the point.

| Table | Kind | Written by | Rebuilt? |
|---|---|---|---|
| `price_daily` | cache | `fetch-prices` | never |
| `fx_daily` | cache | `fetch-prices` | never |
| `position_daily` | derived | `rebuild()` | every time |
| `cash_daily` | derived | `rebuild()` | every time |

**`price_daily`** — `(isin, price_date, close_unadjusted, close_adjusted, currency,
source, fetched_at)`. The two closes are separate columns, taken from the provider's
split-adjusted and total-return series respectively. §7.5 requires that valuation reach
only the first and `total_return_series()` only the second; distinct columns make that
a checkable property rather than a naming convention (section 7.3 below).

**`fx_daily`** — `(from_ccy, to_ccy, rate_date, rate, source, fetched_at)`. Stored as an
explicit currency triple for the same reason §5.3 gives for `fx_rate_to_base`: a rate
without a stated direction is a runtime error waiting to be plausible.

**`position_daily`** — `(position_date, isin, quantity)`. **No `method` column.** M1
already proved share counts are method-independent, and asserts it in
`test_every_method_holds_the_same_shares`; that existing test is the justification for
this schema. Unlike `lot`, this table is written once per rebuild rather than three
times.

**`cash_daily`** — `(cash_date, balance_base)`. A running sum of `net_base`, so it is
pure ledger arithmetic and belongs on the derived side. It exists because M2-4 makes
value net of cash, and `domain/positions.py` produces it from the same pass that
produces positions. Since M6a, a `DEPOSIT` or `WITHDRAWAL` enters the balance on its
value date rather than its booking date (M6a design, M6a-11).

Money stays `Decimal` throughout, via the existing `DecimalString` column type. No
SQLite-only features; the schema stays portable per §4.3.

---

## 6. Resolution and its verification

### 6.1 Interfaces

`providers/base.py` declares three Protocols — `SymbolResolver`, `PriceProvider`,
`FxProvider` — with implementations in `openfigi.py`, `yahoo.py`, `ecb.py` and
`manual.py`. `chain.py` tries providers in order and **records which one answered**,
which is what lets `coverage: "manual"` be a fact rather than an assumption.

Protocols rather than base classes, and the network confined to this package, for the
same reason `domain/` imports no ORM: the arithmetic has to be testable without either.

### 6.2 The discriminator

`domain/symbols.py` is pure — candidate series and ledger trades in, a verdict out —
so the whole decision is unit-testable against invented series, and the real
leveraged-ETF case from section 3.2 becomes a golden fixture.

Three conditions, none sufficient alone:

1. **Currency matches** the ledger's trade currency. Necessary, and *not* sufficient:
   every impostor in section 3.2 was denominated in the same currency as its target.
2. **Every executed trade agrees** with the series, after split adjustment, within a
   band of roughly ±30%.
3. **The agreement is stable** across trades. A single lucky ratio proves nothing.

The band is deliberately generous. An executed price is an intraday fill and a close is
end of day, so a few percent of honest disagreement is expected on a volatile day —
while the closest wrong answer observed was off by a factor of nearly three. There is
around five times the margin needed, and the asymmetry justifies erring wide: a false
quarantine costs one question, a false accept puts a badly wrong number on the chart
and offers no clue that it is wrong.

### 6.3 The quarantine

Unverified resolutions land in a `symbol_review` table carrying the candidates and
their measured ratios, and are answered in `config/instrument_symbols.yaml`.
`fetch-prices` **refuses to complete** while anything is unresolved.

This is deliberately the same shape as M0's `corporate_action_review` and
`corporate_actions.yaml` — one pattern for "the machine is unsure, a human decides",
not two. As there, the answers file is ledger-adjacent and gitignored, the review table
is a projection rebuilt on each run rather than an accumulating queue, and an
instrument may be answered with a symbol or with `manual` to route it to
`config/manual_prices.csv`.

On the current export this is a handful of questions once, then silence.

---

## 7. Valuation and coverage

### 7.1 The join

For each day in range, for each instrument holding a position: take the latest
`price_daily` row dated on or before that day, the latest `fx_daily` row for its
currency, multiply by quantity, and sum. Add that day's `cash_daily` balance.

Carry-forward is not separate machinery — it is what "latest on or before" means. The
gap between the price's date and the day being valued is the staleness M2-3 requires to
be recorded.

The series starts at the first day the ledger holds any position, not at the start of
the cache (M2-7). The cache reaches further back so a single fetch needs no date
arithmetic and an older import would already be covered; valuing days on which nothing
was held would only draw a flat zero and invite the reader to wonder what broke.

### 7.2 Coverage

§8.1 makes coverage a first-class result. The four values have distinct meanings and
each must be reachable:

| Value | Meaning |
|---|---|
| `full` | Every held instrument priced within 4 calendar days — absorbs a weekend or single holiday without crying wolf |
| `partial` | Something is staler than that. `covered_pct` reports how much of the day's **value** is fresh |
| `manual` | A component came from `manual_prices.csv`, which the provider chain knows because it recorded who answered |
| `missing` | A held instrument has no price at all, not even carried. **The day's value is `null`** |

Coverage is weighted by value, not by instrument count: one large holding going dark
matters more than three small ones, and a count would say the opposite.

The `missing` rule is the one §8.1 actually turns on. A total that quietly drops an
unpriceable position looks exactly like a total that includes it, so it must not be
computed. Never €0, never a silent omission.

### 7.3 The double-count, made checkable

§7.5 requires that no call path reach both the adjusted and unadjusted close, because
total return computed from dividend-adjusted prices *plus* dividend income counts
dividends twice.

M2 makes that structural: the two closes are separate columns behind two separate
accessors, valuation calls only the unadjusted one, and `total_return_series()` calls
only the adjusted one. The architectural test §7.5 demands then has something real to
assert about the call graph.

---

## 8. API and UI

Two endpoints, both carrying `method` and `coverage` in the envelope, which
`Provenance` makes structurally impossible to omit (§9.2):

- `GET /api/valuation?from&to` → the daily series; each point carries value, its cash
  component, coverage and `covered_pct`.
- `GET /api/positions` → current holdings with quantity, cost basis, market value,
  unrealised P&L and per-row coverage.

Money crosses the wire as a string, as everywhere else.

The three surfaces §10 names, all on the **Positions** screen per M2-8: the **value
chart** (ECharts, per §9.1), the **positions table**, and a **coverage strip**. Points below full coverage render distinctly rather
than as ordinary points, so a stale stretch is visible without consulting a legend —
the same instinct as the `MODELLED` badge.

`Positions` joins `LEDGER_BACKED` in `navigation.ts`, which removes its `MODELLED`
badge automatically. Adding it there without pointing it at a real endpoint is the one
way to make the UI lie about its own provenance, so the two changes belong in the same
commit.

---

## 9. Testing

- **Pure, no network:** `domain/positions.py` (the daily series) and `domain/symbols.py`
  (the discriminator). The leveraged-ETF case from section 3.2 is a golden fixture — it is the
  exact failure the discriminator exists to catch, and a discriminator that cannot be
  shown to reject it is decoration.
- **Providers:** recorded response fixtures. CI never touches the network; a test that
  needs the internet is a test that fails for reasons unrelated to the code.
- **Integration:** the valuation join, carry-forward across a venue holiday, and each of
  the four coverage values reached deliberately rather than incidentally.
- **Architectural:** the §7.5 call-path assertion described in section 7.3.
- **Opt-in `realdata`:** derives every expectation from the gitignored export via
  `realdata_subject`, per the standing rule. `test_no_real_data_committed` keeps
  holdings out of the repo, and applies to this document too.

Coverage target 80%+, with `domain/` held higher, as §11.3 requires.

---

## 10. Open items

1. **Yahoo's endpoint is undocumented.** *Settled for now by M2-6: personal use only.*
   It carries no service guarantee and its terms are grey beyond personal use, so this
   reopens the moment the project is published. The chained-provider design keeps the
   cost of that low — replacing it is a new `PriceProvider`, not a rewrite — and
   Finnhub is held in reserve for resolution.
2. **Staleness threshold.** Four calendar days absorbs a weekend plus one holiday.
   A venue closing for longer — a national holiday week — would show as `partial`,
   which is arguably correct but has not been observed in the current data.
3. **Instruments with no public series** still require `manual_prices.csv`. The parent
   spec predicted which ones; the spike found a different set, so the list is a fact
   about the data rather than a constant, and belongs in the quarantine's output.

---

## 11. What M3 adds next

The instrument chart: markers for buys and sells, holding-period bands, in-market and
out-of-market intervals, and a benchmark rebased at entry with excess return over the
holding period. All of it needs the price series M2 introduces, which is why it comes
after rather than alongside.
