# Personal Portfolio Tracker — Design

**Date:** 2026-09-05
**Status:** Awaiting review
**Source spec:** handoff document, sections 1–10

---

## 1. Purpose and scope

A single-user investment portfolio tracker that answers four questions the broker will not:

1. How did each individual batch of shares perform?
2. What did selling cost me, or save me?
3. What did the stock do while I was not holding it?
4. Am I beating the market, or riding it?

Everything else is supporting infrastructure. Single user, not multi-tenant, possibly
open-sourced later — so secrets stay out of the repo and broker-specific code stays
behind interfaces.

**Explicit non-goal:** this is not a tax tool. The lot model exists for insight. Box 3
taxes a deemed return on year-end value, and the Wet werkelijk rendement proposal
(Tweede Kamer, Feb 2026, targeted 2028, uncertain) is value-change based, not lot-based.
Withholding tax actually paid is reported as a *fact*; nothing is presented as a tax
figure, a Box 3 calculation, or advice.

---

## 2. Decisions taken

Confirmed by the owner on 2026-09-05:

| Ref | Decision |
|---|---|
| B1 | ORION 18-02-2025 is the 10:1 split. Suppress both synthetic rows; apply as a corporate action. |
| B2 | Meridian Mining 14-08-2026 is a non-economic pair. Owner recalls a cancellation; DeGiro labels it `PRODUCTWIJZIGING` (product change), same ISIN on both sides. Ledger effect is identical either way: suppress both rows, no P&L, no position change. Recorded as `PRODUCT_CHANGE` with the owner's note attached. |
| C1 | The Jan-2026 round trip was a genuine decision (panic sell, later rebuy). It counts fully in the counterfactual. |
| C2 | `closure_reason` is adopted anyway, defaulting to `DECISION`. It exists so mechanical closures (transfers, product changes, delistings) can be excluded later without a schema change. |
| G1 | No API key held; DeGiro offers no API. Free-tier providers only. Provider selection is a written spike in M2 (prices/FX) and M7 (transaction sync). |
| K1 | Real exports are gitignored. CI runs against synthetic golden files that reproduce every structural quirk with invented amounts. A local, opt-in suite runs against the real files. |
| D–F, H–J | Approved as proposed (see §6.4, §5.3, §5.4, §7.6, §1, §5.2). |
| — | Charting: Apache ECharts. Justification in §9.1. |

---

## 3. The DeGiro export format

Three files, UTF-8 with BOM, Dutch locale (`DD-MM-YYYY`, decimal comma, thousands dot),
quoted fields containing commas.

### 3.1 The header is misaligned. Map columns by position, never by name.

In all three files a currency column *precedes* its amount, but the header's blank
placeholder sits on the wrong side. Verified layouts:

```
Transactions.csv  0 date  1 time  2 product  3 isin  4 ref_exchange  5 venue
                  6 qty   7 price 8 price_ccy 9 local_value 10 local_ccy
                  11 value_eur 12 fx_rate 13 autofx_fee 14 txn_fee 15 total_eur 16 order_id

Account.csv       0 date  1 time  2 value_date 3 product 4 isin 5 description
                  6 fx_rate 7 change_ccy 8 change 9 balance_ccy 10 balance 11 order_id
                  # header reads "...,FX,Change,,Balance,,Order Id" — off by one against data

Portfolio.csv     0 product 1 isin 2 amount 3 closing 4 local_ccy 5 local_value 6 value_eur
```

`ingest/degiro/dialect.py` owns these layouts as explicit constants, with a startup
assertion that the header string matches the one they were verified against. If DeGiro
changes the export, import fails loudly rather than silently shifting every column.

### 3.2 `Account.csv` row taxonomy

252 distinct `Description` strings, roughly 20 kinds. The field is free text and
**embeds trade details** (`Koop 2 @ 502,15 USD`), so classification is prefix/regex-based.
The file **mixes Dutch and English** in the same export — `Dividend`, `Dividendbelasting`,
`Valuta Creditering`, `Koop`/`Verkoop` alongside `iDEAL Deposit`,
`Flatex Interest Income`, `Processed Flatex Withdrawal`, `Degiro Cash Sweep Transfer`.
Neither language may be assumed.

| Pattern | Rows | Maps to | Note |
|---|---|---|---|
| `Koop N @ …` / `Verkoop N @ …` | 108 | *(dropped)* | Duplicate of Transactions.csv, which is authoritative for trades |
| `Degiro Cash Sweep Transfer` | 116 | **dropped** | Internal sweep; carries the signed amount |
| `Overboeking naar/van uw geldrekening bij flatexDEGIRO Bank` | 116 | **dropped** | Internal sweep; amount only in the description text |
| `Reservation iDEAL` | 22 | **dropped** | Reserve/release pairs, net exactly €0.00 |
| `iDEAL Deposit` | 11 | `DEPOSIT` | **The only genuine inflows.** +€24,500.00 |
| `Processed Flatex Withdrawal` / `flatex terugstorting` | 2 | **dropped** | Offsetting internal pair (+8000 / −8000, 04-03-2026) |
| `SEPA Instant Terugstorting` | 1 | `WITHDRAWAL` | **The only genuine outflow.** −€7,564.26 |
| `Dividend` | 52 | `DIVIDEND` | Local currency: USD 80.02, EUR 30.56, HKD 2.45 |
| `Dividendbelasting` | 38 | `DIVIDEND_TAX` | USD −3.77, EUR −4.29. **Not 1:1 with dividends** |
| `Valuta Creditering` / `Valuta Debitering` | 172 | `FX_CONVERT` | 86 pairs; the FX rate lives on these rows |
| `DEGIRO Transactiekosten en/of kosten van derden` | 104 | `FEE` (trade-linked) | −€252.00 total; carries `order_id` |
| `Transactiebelasting Frankrijk` | 2 | `TAX` (trade-linked) | −€9.15, French FTT; carries `order_id` |
| `DEGIRO Aansluitingskosten <year> (<exchange>)` | 16 | `FEE` (portfolio-level) | −€40.00, annual per-exchange connectivity |
| `Rente` / `Flatex Interest Income` | 13 | `INTEREST` | Net −€59.94 — interest **paid** on a negative balance |
| `Inkomsten uit Securities Lending - <month>` | 7 | `SECURITIES_LENDING` | +€11.21. **New type, not in the source spec** |
| `SPLIT AANPASSING: …` | 2 | `CORPORATE_ACTION` | Explicitly labels the ORN split |
| `PRODUCTWIJZIGING : …` | 2 | `CORPORATE_ACTION` | Explicitly labels the Meridian Mining pair |

### 3.3 The cash-sweep trap

232 of 785 `Account.csv` rows are internal transfers between the DeGiro investment cash
account and the flatex bank account, plus 22 iDEAL reservation rows and 2 offsetting
flatex withdrawal rows. **256 rows that look exactly like deposits and withdrawals and
are not.**

Classify them as external cash flows and MWR/XIRR becomes meaningless while TWR's
sub-period boundaries fragment into noise — source spec §9.8 cannot pass. The genuine
external flows are **11 deposits and 1 withdrawal**.

This is recorded here because it is invisible from the numbers alone: the sweep rows
carry real signed euro amounts and plausible running balances. Only the description
distinguishes them.

### 3.4 Corporate actions are labelled in `Account.csv`, not `Transactions.csv`

`Transactions.csv` renders the ORN split as an ordinary offsetting buy/sell pair with a
blank Order ID. `Account.csv` names it `SPLIT AANPASSING`. Detection therefore joins the
two files on `(date, isin, abs(amount))` and keys on the description prefix — far
stronger than the value-shape heuristic originally proposed.

The heuristic is retained only as a *secondary* check that raises a quarantine warning
when a value-shaped candidate has no matching `Account.csv` label. That catches the case
where DeGiro changes its wording.

### 3.5 Other verified quirks

- **`Order ID` is not unique.** 105 distinct across 112 rows; blank on synthetic rows.
  Partial fills share one ID (AMD: `−5 @ 145.3000` and `−5 @ 145.3050`, same minute,
  same venue).
- **Commission is per *order*, booked on one arbitrary fill row.** CASTOR 07-10-2026:
  `+25` (fee blank) and `+75` (fee −2.00) under one Order ID. AutoFX fee is per *row*.
- **`Exchange rate` is local-per-EUR — divide, do not multiply.** Blank for EUR rows.
- **Broker arithmetic disagrees with itself by €0.01 on 14 of 112 rows.**
- **Four currencies:** EUR, USD, AUD, HKD (the last only via an Pacific Assurance dividend).
- **Cash balance runs negative** (−€2,987.28 at export) and debit interest is paid. This
  matters for the cash-constrained shadow portfolio (§7.3).
- **`Reference exchange` codes are DeGiro's own** (EAM, NDQ, NSY, TDG, XET, ASE, ASX,
  EPA), not MICs. `Venue` codes are execution venues including systematic internalisers.
  A mapping table is needed for `instrument.exchange_mic`.

### 3.6 Cross-file invariants (free reconciliation tests)

| Invariant | Expected | Verified |
|---|---|---|
| `Σ Transactiekosten` in Account.csv == `Σ txn_fee` in Transactions.csv | −€252.00 | ✅ |
| Distinct non-blank `order_id` in Transactions.csv == count of `Transactiekosten` rows | 104 == 104 | ✅ |
| `order_id` sets are equal in both directions between the two files | 0 orphans either way | ✅ |
| Computed open positions == Portfolio.csv | 6 positions; **ORN = 32** | ✅ |
| Computed EUR cash == Portfolio.csv `CASH & CASH FUND` | −€2,987.28 | pending M0 |

Two of these are load-bearing for the design rather than merely nice to have.

**Exactly one commission per order.** 104 distinct non-blank order ids, 104
`Transactiekosten` rows, and the two id sets are equal with zero orphans in either
direction. This is the direct evidence for order-level fee attribution (§6.4): the
commission is an attribute of the *order*, and the fill row it happens to land on is
arbitrary.

**ORN = 32 is the sharpest test in the project.** 32 shares is only reachable if the
split is applied as a corporate action (1 share bought 2025-01-30 at cost basis €655.30,
becoming 10, plus later buys of 2, 10 and 10). Book the split as a trade and the position
is wrong — so this single number validates the corporate-action path end to end against
the broker's own statement.

---

## 4. Architecture

### 4.1 Layering

```
config/          hand-editable YAML: benchmarks, sector map, instrument overrides
    ↓
ingest/          broker files → NormalisedRow → transaction rows (append-only)
    ↓
[ transaction ]  THE LEDGER. Append-only, immutable. The only source of truth.
    ↓
rebuild/         wipes and recomputes ALL derived tables, deterministically
    ↓
[ lot, lot_closure, position_daily, valuation_daily ]   derived, disposable
    ↓
analytics/       pandas frames over derived tables — never over the ledger
    ↓
api/             every metric carries its method and coverage flag
    ↓
frontend/        MethodBadge renders those flags next to the number
```

`domain/` sits beside all of this and is **pure**: no ORM imports, no network, no
`datetime.now()`. It takes value objects and returns value objects. The lot matcher, fee
attribution, corporate actions, returns, FX decomposition and counterfactuals all live
there. This is what makes §11's hand-computed fixtures possible and `rebuild()` provably
deterministic.

### 4.2 Repository layout

As presented and approved in the Step 0 plan:
`backend/app/{domain,models,ingest,providers,rebuild,analytics,api}`,
`frontend/src/{api,components,pages,lib}`, plus `config/`, `docs/`, and
`backend/tests/{fixtures,golden,unit,integration}`.

### 4.3 Portability

SQLite locally, portable to Postgres/D1. No SQLite-only features and no implicit
coercion: explicit types everywhere, `DECIMAL`/`NUMERIC` for money (never `float`),
native dates, UUID primary keys, all foreign keys declared. No business logic in a
Cloudflare-specific runtime.

---

## 5. Data model

As specified in the source document, with these deltas.

### 5.1 New and changed transaction types

Added: `SECURITIES_LENDING` (§3.2), `PRODUCT_CHANGE`, `CORPORATE_ACTION_ADJUSTMENT`.
`TAX` is separated from `FEE` so the French FTT can be attributed to a lot while
remaining reportable as tax paid.

### 5.2 `transaction` additions

| Field | Purpose |
|---|---|
| `order_ref` | Broker order id, nullable, **not unique** |
| `order_group_id` | Derived: groups fill rows into one economic order (§6.4) |
| `is_economic` | `false` for suppressed synthetic rows; they stay in the ledger with `raw_json` intact but are skipped by lot matching |
| `closure_reason` | `DECISION` (default), `TRANSFER`, `PRODUCT_CHANGE`, `DELISTING` |
| `source_file_hash` | Ties a row to the exact file that produced it |

Nothing is ever deleted. Suppression is a flag, so `rebuild()` can reinterpret it later.

One account, base currency EUR. Flatex cash interest belongs to the same `account` row —
the flatex bank account is not modelled separately, since only its sweep boundary is
visible in the export.

### 5.3 Money and FX are value objects, not floats

`Money(amount: Decimal, currency: str)` and `FxRate(from_ccy, to_ccy, rate, as_of)`.
`FxRate.convert()` raises on a currency mismatch. This makes the direction error from
§3.5 a runtime exception rather than a believable wrong number.

`fx_rate_to_base` is stored as an explicit `(from_ccy, to_ccy, rate)` triple. DeGiro's
raw value is preserved verbatim in `raw_json`.

### 5.4 Broker truth over arithmetic truth

`net_base` is DeGiro's `Total EUR` verbatim — it is what actually hit the cash account.
It is never recomputed. Reconciliation tolerances: **±€0.02 per row, ±€0.50 per portfolio
aggregate**.

---

## 6. Ingest

### 6.1 Idempotency

`source_ref = sha256(order_ref, trade_datetime, isin, qty, price, ordinal)` where
`ordinal` is the index within the group of otherwise-identical rows, assigned **after a
deterministic total sort of the whole file** — never file position. Re-importing the same
file, or a re-export with rows in a different order, changes nothing.

`import_batch` records the file hash, row count and parser version. Any batch can be
undone; undoing one triggers a `rebuild()`.

### 6.2 Authority between files

`Transactions.csv` is authoritative for trades. `Account.csv`'s `Koop`/`Verkoop` rows are
dropped as duplicates, but their cash amounts are used to reconcile. `Account.csv` is
authoritative for everything else. `Portfolio.csv` is never imported — it is only a
reconciliation target.

### 6.3 Corporate-action quarantine

Candidates are written to a `corporate_action_review` table and **import refuses to
complete until each is resolved**. Resolutions are stored in
`config/corporate_actions.yaml` as ledger-adjacent facts, so `rebuild()` stays
reproducible without re-answering.

Seeded from the real data: ORN 10:1 split (2025-02-18), Meridian Mining product change
(2026-08-14).

### 6.4 Fee attribution

Rows are grouped into economic orders on `(order_ref, trade_datetime, isin)`, falling
back to a synthetic key when `order_ref` is blank. Fees and trade-linked taxes are
attributed **at order level**, then pro-rata by value across the closures the order
produces. Buy fees capitalise into the lot at open; sell fees reduce proceeds.

Portfolio-level fees (`Aansluitingskosten`) are never attributed to a lot. They appear in
portfolio cost totals and reduce TWR/MWR, not per-lot P&L.

Stated in the docstring of `domain/fees.py`, per source spec §10.

---

## 7. Analytics

### 7.1 Lot matching

FIFO (default), LIFO and HIFO — a setting, surfaced in the UI wherever a realised number
appears. Each lot and closure carries holding days, absolute P&L in base currency,
% return, annualised return, and attributed fees and taxes.

Splits adjust `open_qty`, `remaining_qty` and `open_price` on every open lot, leaving cost
basis unchanged.

### 7.2 The three counterfactual numbers

Computed and labelled distinctly; never merged:

- **(a) Realised P&L per closure** — what actually happened.
- **(b) Per-closure counterfactual** — `qty × (price_today − price_sold) × fx_today +
  dividends_since_sale × qty × fx − fees_avoided`. Positive means selling cost money;
  negative means it prevented a loss. **Both framings are shown.**
- **(c) Shadow "never sold" portfolio** — full ledger replay with `SELL` suppressed.

### 7.3 Shadow-portfolio assumption

v1 is naive hold: every buy is kept, ignoring that some were funded by sale proceeds.
**The assumption is printed next to the chart, not buried in docs.** v2
(cash-constrained) is deferred.

Note for v2: this account ran a *negative* cash balance and paid debit interest, so
"could this buy have been funded" is not a simple non-negative-cash test. v2 must model
the actual credit facility or it will be wrong in a new way.

### 7.4 Returns

TWR and MWR/XIRR are always shown together and always labelled. No endpoint returns an
unlabelled "return". TWR is the only figure compared against a benchmark.

XIRR uses Brent's method over a bracketed range and returns `null` with a reason on
non-convergence or multiple roots — never a wrong root.

### 7.5 Valuation and the double-count

`close_unadjusted` is used for valuation. `close_adjusted` is reachable **only** through
`total_return_series()`, which the valuation path cannot call. An architectural test
asserts that no call path reaches both.

### 7.6 Benchmarks and industry

Benchmarks are liquid ETF proxies, configured in `config/benchmarks.yaml` and documented
as proxies including TER drag. The set stays fixed even though the portfolio has itself
held Northwind US 500, Corefund Global Equity and All-Country.

Industry classification comes from the price provider's sector strings, mapped in
`config/sector_map.yaml` — hand-editable, because it will be wrong for some holdings.
GICS is licensed and not used.

All comparisons rebase to 100 at **entry date for that position**. Excess return is
computed over the actual holding period only.

### 7.7 In-market and out-of-market intervals

Derived per instrument from the position series: intervals where quantity > 0 and where
it is 0. Instrument return is reported for each. The real data has eight instruments with
multiple in-market intervals; Meridian Mining, Lyra Resources and Halvard Group each have three.

---

## 8. External data

### 8.1 Coverage is a first-class result, not an error

Of 37 instruments, several are hard or impossible on free tiers: Aster Launch
(`US0000000904`, private, no public series), Apex Minerals (ASX microcap), Pacific Assurance
quoted in EUR on Tradegate, and Pallas International.

Any metric depending on a missing series returns `null` with
`coverage: "missing" | "partial" | "manual" | "full"`. The UI renders "no data" — never
€0, never a silent omission from an aggregate. Aggregates state how much of the portfolio
they cover.

### 8.2 Provider strategy (spike in M2)

Chained `PriceProvider`s with a hand-editable `config/manual_prices.csv` fallback.
Selection is deferred to a written comparison in M2; ISIN resolution is the deciding
factor, since free tiers rarely offer ISIN lookup. OpenFIGI is the leading candidate for
the ISIN → ticker layer. ECB reference rates for FX.

### 8.3 Transaction sync (spike in M7)

DeGiro has no API. SnapTrade Personal (free tier) is the intended bridge, treated as a
*second source of the same truth*: deduped against CSV rows via `source_ref`, with a
reconciliation report that flags disagreements rather than silently preferring one.
SnapTrade's current DeGiro coverage is unverified and is part of the spike.

Tokens, if stored, live in a separate store from the ledger database, documented in
`docs/methodology.md`.

### 8.4 News (M8)

A `NewsProvider` interface with one implementation. **Fetch on request only, never in a
batch job** — free tiers will not survive backfilling every holding. Every fetched item
is cached in `news_event` keyed by instrument plus date range, so a repeat request costs
nothing.

---

## 9. Frontend

### 9.1 ECharts

Chosen over Recharts on four specifics: native `candlestick` (Recharts has none);
`markArea` for holding-period bands declaratively; `markPoint` with per-point
`symbolSize` for quantity-scaled buy/sell triangles; and `dataZoom` plus canvas rendering
for multi-year daily series where SVG-only rendering degrades.

Cost: an imperative options object rather than composable components. Confined to a
single `<EChart option={...} />` wrapper so the rest of the app stays plain React.

### 9.2 Method visibility is structural

API response schemas **cannot be constructed** without `method` and `coverage` fields.
`MethodBadge` renders them. A convention would decay; a required field will not.

---

## 10. Milestones

| # | Slice | Visible outcome |
|---|---|---|
| M0 | Ledger + both DeGiro parsers + quarantine | Browser table of all transactions, `raw_json` inspectable, batch undo, cross-file reconciliation report green |
| M1 | Lots, closures, FIFO/LIFO/HIFO, splits, `rebuild()` | Lot table per source spec §5, method switcher, **ORN = 32 shares matching Portfolio.csv** |
| M2 | Prices, FX, valuation, coverage | Portfolio value chart; positions table; coverage strip |
| M3 | Instrument chart | Markers, holding bands, in/out-of-market intervals, benchmark rebased at entry, excess return over holding period |
| M4 | Counterfactuals | Ranked sell-decision table (both framings) plus shadow portfolio with its assumption printed |
| M5 | Dividends | Gross/tax/net per instrument per year; yield-on-cost vs yield-on-market labelled; calendar; forward estimate marked as an estimate |
| M6 | TWR / MWR / attribution | Performance page; industry weight over time; contribution to return |
| M7 | SnapTrade + reconciliation | Disagreement report |
| M8 | News on demand | Chart markers plus drill-down, cached, fetch-on-click only |

**Change from the Step 0 plan:** `Account.csv` parsing moves from M5 into M0. Deposits,
withdrawals, dividends and fees are needed for the cross-file reconciliation invariants
(§3.6) that make M0's import trustworthy. The *dividend analytics* stay in M5.

---

## 11. Testing

### 11.1 Test data policy

Real exports are gitignored. CI runs against `backend/tests/golden/` — synthetic files
with invented amounts reproducing every structural quirk: partial fills sharing an order
id, byte-identical fill rows, the split pair, the product-change pair, blank order ids,
the €0.01 arithmetic mismatch, four currencies, cash sweeps, iDEAL reservation pairs,
mixed-language descriptions, and the misaligned header.

A separate opt-in suite (`pytest -m realdata`) runs against the real files locally.

### 11.2 The five hardest problems and their tests

1. **Corporate actions vs real trades** — golden test: ORN holds one lot from
   2025-01-30, cost basis €655.30, quantity 10 after the split, realised P&L from
   2025-02-18 exactly €0.00. Synthetic 2:1, 3:2 and 1:10 reverse fixtures. **Plus a
   decoy:** a genuine same-day round trip with a real order id and fees must NOT be
   flagged.
2. **Dedupe vs partial fills** — `rows_in_file == transactions_in_db`, unchanged on
   re-import. Two byte-identical fill rows produce 2 transactions, still 2 after
   re-import. Row-shuffle test producing identical `source_ref` sets.
3. **Double-counting and FX direction** — `total_return(adjusted) ≈
   price_return(unadjusted) + dividend_income`; architectural test that no path reaches
   both; source spec §9.4 FX decomposition with hand-computed values; `FxRate` mismatch
   raises.
4. **Fee attribution across fills** — source spec §9.7 multi-lot sell; CASTOR 07-10-2026
   splitting €2.00 into €0.50/€1.50; and the standing invariant **Σ attributed fees ==
   Σ ledger fees**, asserted after every rebuild, not only in tests.
5. **Rebuild determinism** — double-rebuild produces identical dumps; shuffled insertion
   order produces identical derived tables; a parser-rule mutation propagates to
   `lot_closure` with zero manual steps.

### 11.3 Source-spec scenarios

All eight source spec §9 scenarios, with hand-computed expected values in YAML, **not
generated from this codebase**. Real-data counterparts where available: §9.2 maps to US
Meridian (three in-market intervals), §9.3 to the ORN split, §9.4 to any USD position
across an FX move, §9.6 to re-importing the real file, §9.8 to the 11 genuine deposits
once the sweep rows are correctly excluded.

Coverage target 80%+, with `domain/` held higher — it is pure and has no excuse.

---

## 12. Open items

1. **G — provider strategy.** "A free third party integration in between to easily sync
   data" is read as two separate needs: free-tier **price/FX** providers (M2 spike) and
   **SnapTrade** as the transaction-sync bridge (M7 spike). If the intent was that
   automatic transaction sync matters more than the milestone order implies, M7 should
   move ahead of M4 — flagged, not decided.
2. **B2 wording.** Recorded as `PRODUCT_CHANGE` per DeGiro's own label, with the owner's
   "cancelled transaction" note attached. No ledger impact either way.
3. **HKD** was not in the original currency picture. It appears via one Pacific Assurance
   dividend. FX coverage must include it.
4. **Securities lending income** (+€11.21) is a new transaction type. Confirm it belongs
   in portfolio return — proposed: yes, as income, alongside dividends but reported
   separately.
5. **Exchange code mapping.** DeGiro's `Reference exchange` codes need a mapping to real
   MICs for `instrument.exchange_mic`. Proposed: a small hand-maintained table in
   `config/`, defaulting to storing DeGiro's code verbatim when unmapped.

---

## 13. Ground rules

- No secrets in the repo. `.env` plus `.env.example`. Required secrets validated at startup.
- SnapTrade tokens, if stored, live outside the ledger DB and are documented.
- Type hints throughout the Python; strict TypeScript on the frontend.
- Every analytics function documents its assumptions, especially the counterfactual ones.
- Where a number depends on a methodological choice, that choice is visible next to it.
- Immutable data structures; no in-place mutation of domain objects.
- Files 200–400 lines typical, 800 maximum.
