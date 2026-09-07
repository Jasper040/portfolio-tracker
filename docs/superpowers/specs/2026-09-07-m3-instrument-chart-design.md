# M3 — Instrument Chart, Benchmark and Holding Intervals — Design

**Date:** 2026-09-07
**Status:** Approved
**Parent spec:** `docs/superpowers/specs/2026-09-05-portfolio-tracker-design.md`
**Predecessor:** `docs/superpowers/specs/2026-09-06-m2-prices-fx-valuation-design.md`
**Milestone:** M3 (parent §10) — "Instrument chart → Markers, holding bands,
in/out-of-market intervals, benchmark rebased at entry, excess return over holding
period"

A section mark (§) always refers to the parent spec. "M2 section N" refers to the M2
design. This document's own sections are spelled out, as "section 4".

No instrument is named anywhere in this file, and no benchmark is named by ISIN or by
ticker. Which instruments and which proxies are involved are facts about the gitignored
export and the owner's own configuration; `test_no_real_data_committed` applies to this
document exactly as it applies to a fixture.

---

## 1. Purpose and scope

M2 answered *what is the portfolio worth*. M3 answers *was this holding worth owning* —
one instrument at a time, against what the money would have done elsewhere over the same
stretch of calendar.

The milestone is small in surface and awkward in substance. The surface is one screen and
one endpoint. The substance is that two of the project's existing invariants collide here
for the first time, and neither may be weakened to let the feature through: benchmarks
need a price series but are not holdings and cannot be validated like one (section 4), and
the chart needs both price columns at once where every prior reader needed exactly one
(section 3).

**In scope:** the `benchmark_daily` cache and its committed configuration; buy and sell
markers on a real price line; holding bands; in-market and out-of-market intervals with a
return for each; one market benchmark rebased at entry and excess return over the holding
period; a new live instrument screen; and the two carried M2 findings that live in the
file M3 has to open anyway.

**Out of scope, and a defect if present:** sector and industry classification and the
industry-proxy overlay (deferred to M6, per M3-1); the counterfactual "if held to today"
column (M4); dividend analytics (M5); TWR, MWR and attribution (M6); news markers (M8).

---

## 2. Decisions taken

Confirmed by the owner on 2026-09-07.

| Ref | Decision |
|---|---|
| M3-1 | Scope is the instrument chart and **one market benchmark**. Sector/industry classification and the industry-proxy overlay defer to M6, where industry weight over time is the feature that actually needs them. §7.6 pairs benchmarks with industry; M3 takes only the first half. |
| M3-2 | Benchmark series live in their **own `benchmark_daily` table, keyed by a configuration slug and never by ISIN**, so `config/benchmarks.yaml` is tracked. Reasoning in section 4.1. |
| M3-3 | The instrument-vs-benchmark comparison is **total return on both sides**, computed from the dividend-adjusted close, labelled as such, and never presented as TWR. |
| M3-4 | M3 ships a **new live `Instrument` screen**. The modelled `StockDetail` stays untouched under its `MODELLED` badge until M4 and M5 fill its remaining sections, then retires. |
| M3-5 | `analytics/valuation.py` **splits four ways** and `coverage` becomes the `Coverage` Literal on its dataclasses — the two findings carried out of M2, fixed in the milestone that opens the file. The cut between `quotes.py` and `prices.py` is what keeps M3-3 enforceable; see section 5.1. |
| M3-6 | Rebasing happens **at the start of each in-market interval**, not at the left edge of the visible window. Reasoning in section 5.3. |
| M3-7 | Both sides of every comparison are converted to the **base currency before rebasing**. Reasoning in section 5.3. |

---

## 3. Architecture: two series, two questions

### 3.1 Why the drawn line and the comparison cannot be the same series

The chart carries executed-price markers: a buy at a price the owner actually paid, sitting
on the line at the date it happened. That constrains the line absolutely. `close_adjusted`
restates every historical close downward by the dividends paid since, so a marker at the
real executed price would float above a dividend-adjusted line by a margin that grows the
further back you look — largest exactly where the reader is least able to check it. **The
drawn line is `close_unadjusted`.** That is not a preference; it is what makes the markers
mean anything.

The comparison is the opposite case. Asking whether a holding beat the market on price
alone systematically penalises whatever pays a dividend and flatters whatever does not, and
the size of the error is the yield — which is to say, largest for exactly the holdings a
reader is most likely to be defending. **The comparison is `close_adjusted` on both
sides** (M3-3).

So M3 is the first feature that legitimately needs both columns. Every reader before it
needed exactly one, which is why the guard §7.5 demands has held without strain until now.

### 3.2 The double-count guard, strengthened rather than relaxed

`tests/integration/test_no_double_count.py` asserts on the codebase's own syntax tree that
no read-side module reaches both closes. That test is the project's strongest structural
invariant and the cheapest possible thing to quietly weaken, so M3 does not touch its
premise. It splits the work instead:

- **`analytics/instrument_price.py`** reads `close_unadjusted` only. It produces the line,
  the holding bands, the markers and the in/out-of-market intervals.
- **`analytics/instrument_return.py`** reads `close_adjusted` only, through
  `total_return_series()` — which M2 wrote, tested, and deliberately left with no caller
  precisely so that this milestone would have something honest to call. It produces both
  rebased indices and the excess return.
- **The route composes the two** and reads neither column itself.

The test gains assertions rather than losing one. It already walks all of `app/` and
subtracts a named exempt set — its own docstring names "M3's instrument chart" as the
case that design anticipates — so the two new modules are covered the moment they exist,
with no edit. What must be added is the call-path half, which is specific by nature:
`instrument_return` may not reach `analytics.prices`, and `instrument_price` may not
reach `analytics.total_return`.

One existing assertion has to move rather than be deleted.
`test_valuation_reads_only_the_unadjusted_close` names `valuation.py`, and after the
section 5.1 split that file no longer names the column — the assertion would pass
vacuously in the "not present" direction while silently losing the "is present"
direction. It re-points at `analytics/prices.py`, which is where the column went.

A guard that has to be loosened the first time a real feature meets it was measuring the
wrong thing; this one was not.

---

## 4. Data model: the benchmark cache

### 4.1 Why a benchmark is not an instrument

Three independent reasons, any one of which is sufficient.

**The discriminator cannot validate it.** M2 section 6.2 accepts a symbol only when a
candidate series agrees with the ledger's own executed prices for that instrument. A
benchmark has no executed prices — it was never traded — so there is nothing to check it
against. Storing benchmarks in `price_daily` would mean rows in the validated table that
were never validated, which is a worse outcome than a second table: the guarantee
`price_daily` carries would become "validated, except where it isn't".

**`price_daily` is keyed by ISIN, and an ISIN is a holding.** `test_no_real_data_committed`
treats any ISIN in a tracked file as a leak, and the `.gitignore` follows the same rule
without exception — every ISIN-keyed configuration file in `config/` is ignored, with a
committed `.example`. §7.6 notes the portfolio has itself held broad-market funds. A
benchmark set keyed by ISIN and committed would therefore be a leak already or become one
the first time the owner buys a proxy, and the failure would surface as a red scanner long
after anyone remembered why.

**Coverage would be contaminated.** M2's coverage counts what fraction of *held* value
could be priced. Benchmark rows in the same table put things nobody holds into a
denominator about holdings, and every join would have to remember to exclude them.

A slug key dissolves all three. `world` is not a holding, cannot be one, and identifies the
same thing whether or not the owner ever buys it.

### 4.2 The table

```
benchmark_daily(
  id, key, price_date, close_unadjusted, close_adjusted,
  currency, source, fetched_at
)
UNIQUE (key, price_date)
```

Deliberately the same shape as `price_daily`, minus the ISIN. It sits on the **fetched**
side of M2's determinism line: `rebuild()` must never write, delete or rewrite a row here,
and `fetch-prices` remains the only writer. Both closes are stored even though M3 reads
only the adjusted one, because fetching half a response and re-fetching later for the other
half is a request no free provider has a reason to keep serving.

This adds a table and **no columns to existing tables**, so `create_all` picks it up and no
existing local database is stranded.

### 4.3 `config/benchmarks.yaml` — tracked, and the human answer

One entry per benchmark: the slug, the provider symbol, the quoted currency, a display
name, and the TER, which §7.6 requires be documented as proxy drag rather than assumed
away. The file is committed, so a fresh clone and CI both have a benchmark set.

The header states what the file is: **an answer, not a lookup.** The discriminator does not
run here and cannot (section 4.1), so the symbol in this file is taken as authoritative on
the same terms as `instrument_symbols.yaml` — a human wrote it, and nothing re-derives it.
The difference is that this file names no holding, which is why this one commits and that
one does not.

### 4.4 `fetch-prices` gains a benchmark phase

Same provider chain, same fixed five-year backfill, same incremental overlap on re-run,
same `--full`. Two behaviours differ, both because a benchmark is configured rather than
discovered:

- **A benchmark whose symbol returns no series is a hard error naming the slug**, not a
  quarantine entry. The quarantine exists to ask a human a question; here the question has
  already been asked and the file is the answer, so the only useful report is that the
  answer is wrong.
- **Benchmark failure does not block the price phase.** M2's refusal exists because a
  partial *holdings* cache silently understates the portfolio. A missing benchmark
  understates nothing — it removes an overlay, and section 5.4 makes the removal visible.
  The command reports both outcomes and exits non-zero if either failed.

The benchmark phase runs last, so a benchmark failure leaves prices and FX already
committed. That is the same shape as the M2 finding carried about a provider error during
the FX phase, and it is acceptable for the same reason: the write is idempotent, a re-run
resumes from what the cache already holds, and the alternative — holding a five-year
backfill in memory to make the whole command transactional — trades a real cost for a
failure mode that costs nothing.

---

## 5. Analytics

### 5.1 Splitting `analytics/valuation.py` first

M2 carried the finding that this file is 485 lines against a 200–400 band, holding two
independent readers over four shared private helpers, and recorded that the natural moment
to split it is "when M3 next touches it". M3 touches it: the instrument line needs the same
carry-forward and FX helpers that valuation does. Three ways:

| New file | Holds | Names a close column? |
|---|---|---|
| `analytics/quotes.py` | `Quote`, `base_currency`, the rate history, `latest_rate_on_or_before`, `in_base`, `classify`, `worst_coverage` | **Neither** |
| `analytics/prices.py` | `price_history`, `latest_on_or_before`, `quote_for` | `close_unadjusted` only |
| `analytics/valuation.py` | `value_series` and `_value_day` | Neither, after the split |
| `analytics/positions_snapshot.py` | `current_positions` | Neither |

The cut between the first two files is load-bearing rather than cosmetic, and the
existing code already permits it: `Quote` carries a field named `close`, not
`close_unadjusted`, so only `quote_for` names the column. That lets
`instrument_return.py` import the FX and coverage machinery for M3-7's currency
conversion **without acquiring a call path to the unadjusted close** — which a single
merged `quotes.py` would have handed it, quietly defeating section 3.2 through the back
door. The same property is what lets `instrument_return.py` build a `Quote` from an
adjusted close and reuse `in_base` unchanged.

The helpers shared across modules lose their leading underscore in the move; the ones
that stay private to one file keep it.

While those files are open, `coverage: str` becomes the `Coverage` Literal on all four
dataclasses — the other carried finding, and the reason it was carried was that nothing had
cause to open the file. Something now does. Neither change alters a number; both are
covered by the existing suite, which is the evidence that they didn't.

### 5.2 `analytics/instrument_price.py`

Reads `close_unadjusted` only. Produces, for one ISIN over one window:

- **The line**, in base currency, carried forward across venue holidays exactly as M2 does,
  with each point carrying its own coverage and staleness.
- **Holding bands** — contiguous stretches where `position_daily.quantity > 0`, emitted as
  date ranges. The line is split into a held series and a flat series so the two render in
  different registers; the stretch the owner was out of the market is the stretch that
  matters most and disappears most easily.
- **Markers** from the ledger: side, quantity, executed price, fees, and the resulting
  position. Marker area scales with quantity, not radius — area is what the eye reads.
- **Intervals** (§7.7): alternating in-market and out-of-market spans derived from the same
  quantity series, each with its own price return. The real export has eight instruments
  with more than one in-market interval and three with exactly three, so this is a normal
  case and not an edge.

### 5.3 `analytics/instrument_return.py`

Reads `close_adjusted` only, through `total_return_series()`. Three rules:

**Base currency before rebasing (M3-7).** A proxy quoted in one currency and an instrument
in another cannot be compared on their own quote levels: for a euro-denominated owner, a
dollar index that rose 8% while the dollar fell 8% did approximately nothing. Both sides
convert through `fx_daily` before either is rebased, so the comparison is the one the owner
actually experienced.

**Rebase at the start of each in-market interval (M3-6).** §7.6 says comparisons rebase at
entry and §7.7 says return is reported per interval; with three in-market intervals those
two only reconcile one way. The modelled screen rebases at the left edge of the visible
window instead, which makes the comparison change meaning when the reader changes the range
control — a figure that moves when you zoom is not a figure.

**Excess return over the holding period only.** Per interval, excess is the instrument's
total return minus the benchmark's over the same dates. The holding-period aggregate
geometrically links the in-market intervals and skips the flat ones, because §7.6's "actual
holding period" is precisely the union of the in-market spans; a first-to-last measurement
would silently credit the instrument with drift over stretches the owner did not own it.

That linked figure is a time-weighted construction, and the spec is explicit about it so it
is never mistaken for M6's: it is an *instrument's* linked holding-period return, not the
*portfolio's* TWR, it is unaware of cash flows by construction, and the API labels it
`basis: "total_return"` rather than returning anything called "return" unqualified (§7.4).

### 5.4 Coverage

The instrument's coverage follows M2's rules unchanged. The benchmark reports its **own,
separate** coverage, and this is deliberate: a benchmark outage must never degrade the
badge on the instrument's own line, and an excess-return figure computed against a
partially-covered proxy must say so next to itself rather than in a footnote. Where the
benchmark cannot cover an interval at all, the excess figure for that interval is `null`
with a reason — never zero, per §8.1.

---

## 6. API and UI

One composed endpoint, because the alternative — several small ones — splits the rebasing
and alignment logic between server and client and turns one coverage answer into several to
reconcile. This project's settled convention is that the server does the arithmetic in
`Decimal` and ships strings, and the client formats.

```
GET /api/instruments/{isin}/chart?range=1Y|3Y|5Y|max&benchmark=<slug>
GET /api/benchmarks
```

The chart response carries the `Provenance` envelope, which §9.2 makes structurally
impossible to omit. `method` is `null` and that is a real answer rather than an omission:
share counts are method-independent, so the chart is too — the same reasoning
`ValuationSeriesOut` already records. Alongside it: the price points, the bands, the
markers, the intervals, and a `comparison` block carrying both rebased indices, per-interval
and linked excess return, its own coverage, and the explicit `basis` label.

`GET /api/benchmarks` returns the configured set with display names and TERs, so the
screen's selector is not a hardcoded copy of a file the server already reads.

The screen is a new `frontend/src/screens/Instrument.tsx` (M3-4), and its chart is
**ECharts**, via the `<EChart option={...} />` wrapper M2 added. §9.1 chose that library
on four specifics and three of them describe this screen and nothing else in the app:
`markArea` for holding-period bands declaratively, `markPoint` with per-point
`symbolSize` for quantity-scaled buy and sell markers, and `dataZoom` plus canvas
rendering for multi-year daily series where SVG-only rendering degrades. Five years of
daily bars is roughly thirteen hundred points per line before an overlay is added, which
is the regime that argument was made about.

The hand-rolled `LineChart` is not used here and is not changed. It already accepts bands
and markers, which makes it a tempting shortcut, but it is the modelled screens'
component and it renders SVG; picking it would re-open a decision §9.1 closed, for a
screen that is the reason the decision was made.

Adding the screen to `LEDGER_BACKED` in `navigation.ts` removes its `MODELLED` badge
automatically, so that edit and the endpoint it points at belong in one commit — doing
either alone is the one way to make the UI lie about its own provenance.

Nothing on the screen passes a money string through `Number()` except plot coordinates,
per the rule `Positions.tsx` already states and the reason `api/types.ts` already records.

---

## 7. Testing

- **Pure, no network:** interval derivation from a quantity series — including an
  instrument that opens, closes and reopens, which is the three-interval case the real data
  contains; and rebasing, where the property worth asserting is that changing the requested
  range does not change any excess figure (M3-6).
- **Providers:** a recorded benchmark response fixture, as M2 does. CI never touches the
  network.
- **Integration:** the composed endpoint end to end; the base-currency conversion of a
  foreign-quoted proxy against a foreign-quoted instrument (M3-7), which is the case where
  getting it wrong still produces a plausible number; benchmark coverage short of full
  yielding a `null` excess with a reason rather than a zero.
- **Architectural:** the strengthened §7.5 assertion from section 3.2, which must be
  watched failing against a deliberately merged module before it is trusted — M1 found nine
  tests that could not fail, and a guard that has just been rewritten is the worst place
  for a tenth.
- **Frontend:** the chart option is built by a pure function in `lib/instrument.ts` and
  tested directly — bands at the right dates, one `markPoint` per ledger transaction with
  area scaled to quantity, the overlay rebased where section 5.3 says. The screen's own
  jsdom component tests mock `EChart` away, as `Positions.test.tsx` does and for the reason
  `EChart.tsx` records: jsdom implements no canvas, and a test that needed one would be
  testing the library rather than the screen.
- **Opt-in `realdata`:** §11.3 maps §9.2 to the instrument holding three in-market
  intervals. `realdata_subject.py` derives which instrument that is at run time and the test
  states no figure of its own, per the standing rule.

Coverage target 80%+, `domain/` higher, as §11.3 requires.

---

## 8. Open items

1. **The linked holding-period return is a time-weighted figure in a milestone that does
   not own TWR.** Section 5.3 argues it is forced by §7.7 and labels it so it cannot be
   read as M6's portfolio TWR. If that reading is too fine a distinction to rely on, the
   fallback is to report per-interval excess only and leave the aggregate to M6 — a smaller
   claim, at the cost of the screen having no single answer to "was this worth owning".
2. **M3's real-data acceptance will skip exactly as M2's nine do** until the outstanding
   symbol question in **PT-10** is answered. Same operator step, not a
   new one; noted so a green run with skips is not mistaken for a green run.
3. **The benchmark set is fixed and its TER drag is not modelled**, only documented, as
   §7.6 requires. A proxy underperforms its own index by roughly its TER, so a holding that
   beats the proxy by less than that has not necessarily beaten the market. Reported as a
   documented figure rather than an adjustment, because adjusting would invent a series
   nobody published.
4. **`frontend/src/portfolio/fixtures.ts` still carries real ISINs.** Pre-existing, carried
   from M2's follow-ups as **PT-19**, and untouched by M3 — but M3 is the milestone that makes the
   modelled instrument screen redundant, so the cheapest moment to replace them is when
   `StockDetail` retires after M5.

---

## 9. What M4 adds next

The counterfactual: a ranked sell-decision table in both framings, and the shadow "never
sold" portfolio with its naive-hold assumption printed beside it rather than buried. It
needs the per-closure price history M2 cached and the interval structure M3 derives, which
is why it comes after both.
