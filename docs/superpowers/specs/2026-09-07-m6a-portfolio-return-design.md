# M6a — Portfolio Return — Design

**Date:** 2026-09-07
**Status:** Draft
**Parent spec:** `docs/superpowers/specs/2026-09-05-portfolio-tracker-design.md`
**Predecessor:** `docs/superpowers/specs/2026-09-07-m3-instrument-chart-design.md`
**Epic:** PT-7

A section mark (§) always refers to the parent spec. "M3 section N" refers to the M3
design. This document's own sections are spelled out, as "section 4".

No instrument is named anywhere in this file, and no benchmark is named by ISIN or by
ticker. `test_no_real_data_committed` applies to this document exactly as it applies to
a fixture.

---

## 1. What this is

§10 carries M6 as two rows against one epic. It was one — "TWR / MWR / attribution —
Performance page; industry weight over time; contribution to return" — and that row
described two bodies of work with a dependency between them. This document takes the
first.

**M6a is portfolio-level time-weighted return.** A daily chain-linked series, a figure
for any window, compared against one benchmark, labelled. It reads series that M2 and M3
already produce and adds one derived quantity — the external cash flow per day.

**M6b is everything the first half cannot answer without dividends attributed per
instrument:** contribution to return, MWR/XIRR, industry weight over time.

Both sit under epic PT-7.

### 1.1 Scope

**In scope:** external cash flows per day; the chain-linked daily return series; a
window figure with its coverage verdict; the benchmark comparison on that series; a
performance screen; and the two structural findings in section 6 that M6a is the first
caller to hit.

**Out of scope, and a defect if present:**

| Deferred | Why |
|---|---|
| Per-instrument return | M3 delivers it on the adjusted lane, rebased at each in-market interval start. M6a does not rebuild it and does not wrap it. |
| Contribution to return | Needs each dividend attributed to the instrument that paid it, which is M5. M6b. |
| MWR / XIRR | Consumes M6a's flow series, but Brent over a bracketed range with a stated reason on non-convergence or multiple roots (§7.4) is its own body of work. M6b. |
| Industry weight over time | M6b, with M6's industry classification. |
| Any per-period P&L in euros | M6a reports rates. §7.1 already gives absolute P&L per lot and per closure; a second euro figure on a different basis would be two answers to one question. |

### 1.2 Decisions

| | |
|---|---|
| M6a-1 | The §10 M6 row **splits into M6a and M6b under one epic**, PT-7. This is deliberately 2:1 against the one-milestone-one-epic convention in `docs/TRACKING.md`; the alternative places attribution behind M8, which is a worse ordering than a bent convention. |
| M6a-2 | Portfolio return is computed on the **unadjusted-close-plus-cash** lane, never the adjusted one. Reasoning in section 2.1 — this is §7.5's double-count with the cash balance standing in for dividend income. |
| M6a-3 | **Cash is inside the portfolio number and contributes zero to it.** Not excluded, not netted out. Reasoning in section 2.2. |
| M6a-4 | Sub-period boundaries are **daily**. Not per transaction. Reasoning in section 3.1. |
| M6a-5 | Flows are **effective at the close** of the day they are dated. A trade needs no timing convention at all, because including cash makes `V` continuous across it. Reasoning in section 3.3. |
| M6a-6 | External flows are `DEPOSIT` and `WITHDRAWAL` **only** — §3.3's 11 deposits and 1 withdrawal. The 256 sweep rows are not flows. |
| M6a-7 | A day with no valuation invalidates the **two** periods it bounds, and **no single figure spans a gap**. Reasoning in section 4. |
| M6a-8 | `benchmark_total_return_series` **moves out of `total_return.py`** into its own module. Reasoning in section 6.1. |
| M6a-9 | The guard's endpoint list is **derived, not hardcoded**. Reasoning in section 6.2. |
| M6a-10 | The comparison is **labelled on both sides**: the portfolio's dividends sit idle in cash and are net of withholding, the benchmark's are reinvested and gross. Reasoning in section 7. |
| M6a-11 | A `DEPOSIT` or `WITHDRAWAL`, and the cash it moves, take effect on its **value date**, falling back to its booking date when the ledger recorded none; every other transaction type keeps its booking date. Reasoning in section 3.5. |

---

## 2. The two lanes

### 2.1 Why the adjusted close cannot be used here

§7.5 forbids any call path from reaching both closes, because total return computed from
dividend-adjusted prices plus dividend income counts every dividend twice.

A portfolio value that includes cash **is** dividend income. `CashDaily` is a running sum
of `net_base`, and `net_base` carries every `DIVIDEND` row. So:

```
holdings at close_adjusted  +  cash  =  every dividend, twice
```

That is §7.5 exactly, with the cash balance playing the part the prose assigns to
"dividend income". It is not a near-miss or a stylistic preference — it is the same
error, and it flatters the portfolio, which §7.5 names as the worst direction.

There are therefore two internally consistent ways to value a portfolio, and they answer
different questions:

| | Portfolio lane | Instrument lane |
|---|---|---|
| Holdings valued at | `close_unadjusted` | `close_adjusted` |
| Cash | included | absent — there is none to include |
| Dividends enter as | cash, on the pay date | reinvestment, on the ex-date |
| Withholding | deducted, via `DIVIDEND_TAX` | not modelled — the series is gross |
| Flows | `DEPOSIT`, `WITHDRAWAL` | none |
| Existing reader | `analytics/valuation.py` | `analytics/total_return.py` |
| Owner | **M6a** | M3 |
| Answers | "how did my money do" | "how did this holding do" |

The architectural test that keeps these apart is not an obstacle to M6a. It is the reason
M6a is well-defined: without it, a performance module would drift between the two bases
and produce a number that is neither.

### 2.2 Cash counts, and contributes nothing

Excluding cash would measure the invested sleeve alone. That is a real metric, but it
cannot see the decision to hold cash — and choosing to sit out is a decision whose cost
or benefit belongs in a performance figure.

Including cash without comment hides it the other way: a portfolio held half in cash
trails an equity benchmark, and the number would not say why.

The decomposition says both at once. For any period, with weights taken at the period's
start:

```
r_portfolio  =  Σ_j  w_j · r_j   +   w_cash · 0
```

Cash carries weight and returns zero. Cash drag stops being an unexplained gap between
the portfolio and the benchmark and becomes a line item with a number against it.

**M6a computes only the left-hand side.** The per-instrument decomposition on the right
is contribution to return, and it needs dividends attributed per instrument — M6b. M6a
is written so that the decomposition drops in without the aggregate changing, which is
the invariant M6b asserts against.

---

## 3. The method

### 3.1 Why daily, and not per transaction

The natural instinct is to cut a new period at every transaction, since holdings are
constant between transactions and the periods therefore sum exactly. The instinct is
sound and the grid is wrong, for four reasons.

**The prices do not exist.** `price_daily` holds one close per instrument per day.
Revaluing the portfolio at the instant of a trade needs an intraday price for every
*other* holding at that instant. `Transaction.trade_time` is a raw `HH:MM` string that M0
deliberately refused to promote to a datetime, because DeGiro states no timezone and
inventing one would fabricate a fact.

**The execution price is on the wrong scale for the other lane.** `value_base / quantity`
is an actual traded price, which sits correctly beside an unadjusted close but not beside
an adjusted one — an adjusted series is rescaled by every subsequent dividend, so placing
an execution price into it is a units mismatch that manufactures return. Correcting it
needs the ratio between the two closes, which no read-side module is allowed to compute.
This does not bind M6a, which never touches the adjusted close; it is recorded because it
forecloses the obvious next question, which is whether the instrument lane could adopt the
same grid. It cannot.

**The periods are not comparable.** A transaction grid produces periods of three minutes
and periods of eight months. The sum over them is still exact, but every per-period
statistic — an average, a worst period, a volatility — would be computed over units that
are not the same size.

**The grid would be endogenous.** Its boundaries are chosen by the owner's own trading,
so they correlate with the returns being measured. §3.3 already recorded the same hazard
from the other end: classifying the sweep rows as external flows would make "TWR's
sub-period boundaries fragment into noise".

A daily grid is a strict superset. Transaction-period figures remain recoverable by
summing daily links, and a chart, a drawdown and a benchmark axis come with it.

### 3.2 The formula

For each day `d` with a predecessor:

```
             V(d) − V(d−1) − F(d)
    r(d)  =  ────────────────────
                   V(d−1)

       R  =  Π (1 + r(d))  −  1
```

`V` is `ValuationPoint.value_base` — holdings at the unadjusted close plus cash, already
converted to base currency and already carrying a coverage verdict. `F(d)` is the net
external flow value-dated to day `d` (M6a-11).

Nothing else is needed. There is no separate dividend term (dividends are in `V` through
cash), no separate fee term (fees are in `V` through cash, which is what §6 means by
portfolio-level fees reducing TWR), and no flow term inside a period, because with a
daily grid every flow lands on a boundary.

### 3.3 A trade needs no convention; a flow does

`PositionDaily` is "shares held at the **end** of one day" and `CashDaily` is the balance
at the **end** of one day, so `V(d)` is a single coherent end-of-day snapshot and `r(d)`
compares two of them.

**A trade requires no timing rule, and this is the payoff of M6a-3.** A buy moves value
from cash into holdings; both sides are inside `V`, so the transfer nets to zero and `V`
is continuous across it. What survives the netting is exactly what should:

* the **fee**, which leaves the account and is a real reduction in `V`;
* the difference between the **execution price and that day's close** on the traded
  quantity, which is a real gain or loss the owner took that day.

No lag join, no holdings-during-the-day question, no intraday price. Excluding cash would
have made every trade an external flow into the securities sleeve and forced a timing
convention onto all of them; including it dissolves the problem. The transaction-grid
instinct in section 3.1 was reaching for a fix to a problem that M6a-3 removes.

**A flow does require a rule**, because it crosses the account boundary rather than moving
within it. A deposit on day `d` is inside `V(d)` and outside `V(d−1)`, so subtracting
`F(d)` from the numerator removes it exactly, and the flow earns nothing on the day it
arrives.

The alternative — flows effective at the start of the day, denominator `V(d−1) + F(d)` —
is the Modified Dietz convention and is not more correct here. The two differ by one day's
return on one flow, and §3.3 fixes the number of genuine flows in the entire ledger at
twelve. The bound on the disagreement is one day of market movement on twelve amounts,
once each.

### 3.4 Where the series starts

`r(d)` needs a predecessor and a non-zero denominator. The series therefore begins on the
first day after the first day where `V > 0`. Before the first deposit there is no capital
and no return to measure, which is a different statement from a return of zero.

### 3.5 Which date a flow carries

M6a-11: a `DEPOSIT` or `WITHDRAWAL`, and the cash it moves, take effect on its value date,
falling back to its booking date when the ledger recorded none; every other transaction
type keeps its booking date.

A real export books an iDEAL deposit a calendar day after the broker already made it
spendable, and records the earlier day as the row's own value date. A buy placed in
between spends money the broker had already made available. Dated and cashed on the
later, booking day instead, that deposit left the buy it funded against a cash balance
close to zero: the previous close's value shrank to a residual, and an ordinary market
move on the day was divided by that residual rather than by the portfolio it actually
belonged to.

M6a-5 already requires a flow effective at the close of the day it is dated, and section
3.3's claim that `V` is continuous across a trade depends on the money already being
there on the day it is. Both need the **value date**; the booking date is only the
fallback for a row where the ledger recorded no value date at all.

**A known limit.** The implementation plan's P-2 gives a link no return only when its
denominator is zero or negative. A denominator that is small and still positive — for
instance, a flow with no recorded value date, booked after the trade it funded — passes
that guard untouched. The only backstop against it is the realdata acceptance bound that
no single day may double or erase the portfolio; a smaller residual than this export
happens to produce would not necessarily be caught by it.

---

## 4. Coverage on a differenced series

`value_series` yields `value_base = None` on any day where a held instrument could not be
priced — M2's rule that a total quietly dropping a position looks exactly like a total
that includes it.

A return is a difference, so **one unusable day invalidates two links**, not one. This is
the first place in the codebase where a coverage gap propagates rather than staying put,
and it needs a rule rather than an accident.

**The rule: no single figure spans a gap.**

Skipping the broken links and multiplying the rest is the tempting failure. It silently
asserts that the gap days were flat, and it returns a number indistinguishable from an
honest one. Chain-linking across a gap gives the return the portfolio *would* have had if
nothing happened while the lights were off — a different claim from the return it had.

So:

* `r(d)` is `None` when either `V(d)` or `V(d−1)` is `None`.
* The series is reported in **contiguous runs**, each with its own linked figure.
* A window figure spanning more than one run is `None`, with the gap count and the worst
  coverage stated — the same shape as `ValuationPoint`, which returns `None` for
  `holdings_base` and `covered_pct` together rather than a total that omits a position.
* A window with exactly one run carries `worst_coverage` over its days, so a figure built
  from stale or manual prices says so.

---

## 5. Modules

```
analytics/flows.py             NEW    external flows per day, from the ledger
analytics/portfolio_return.py  NEW    differences and chain-links a ValuationSeries
analytics/benchmark_return.py  MOVED  out of total_return.py — section 6.1
api/routes_performance.py      NEW    the endpoint
```

`flows.py` selects `Transaction` rows where `txn_type` is `DEPOSIT` or `WITHDRAWAL` and
sums `net_base` per value date (M6a-11). It names no close column and touches no price
table. Small enough to be pure and to carry hand-computed fixtures the way
`domain/lots.py` does.

`portfolio_return.py` consumes a `ValuationSeries` and a flow mapping and returns runs of
linked returns. **It names no close column at all** — it inherits that from
`valuation.py`, which `test_valuation_no_longer_names_a_close_column_directly` already
pins. It must never import `total_return.py`.

`routes_performance.py` composes the portfolio series with a benchmark series. It is the
first endpoint to legitimately need both lanes in one response, which is what section 6
is about.

---

## 6. Two structural findings

### 6.1 The benchmark reader and the instrument reader must not share a module

`total_return.py` holds `total_return_series` (an ISIN's adjusted closes) and
`benchmark_total_return_series` (a slug's adjusted closes). They read the same column from
two tables, and next to a valuation path they are not equally dangerous:

* Reaching an **instrument's** adjusted closes from a path that also values that
  instrument at the unadjusted close plus cash is §7.5's double count.
* Reaching a **benchmark's** adjusted closes from that same path is not. A benchmark is
  not held, pays the owner nothing, and appears in no cash balance. There is nothing to
  count twice.

While both live in one module, a performance endpoint cannot have the second without the
first. Splitting `benchmark_total_return_series` into `analytics/benchmark_return.py` lets
M6a compare against a benchmark while remaining provably unable to reach its own holdings'
adjusted closes.

This is the same cut, for the same reason, that M3 made between `quotes.py` and
`prices.py`: separate the machinery that is safe for both lanes from the reader that binds
a module to one.

### 6.2 The call-path guard does not cover new endpoints

`test_no_double_count.py` has two halves. The module half walks all of `app/` and subtracts
a named, existence-checked exempt set — its docstring explains that this is precisely so a
new read-side package is covered the moment it exists.

The call-path half does not follow its own advice:

```python
for endpoint in ("app.api.routes_valuation", "app.api.routes_positions"):
    assert "app.analytics.total_return" not in _reachable_from(endpoint)
```

A hardcoded pair. `routes_performance` would be unguarded on arrival, and it is the
endpoint with the strongest reason to reach across — it needs a benchmark, and before
6.1's split the benchmark is in the forbidden module.

The list should be **derived**: every module under `api/` matching `routes_*`, minus a
named exempt set, mirroring the module half. A guard whose coverage depends on someone
remembering to extend it is the failure mode the file's own docstring warns about.

---

## 7. What the comparison actually compares

§7.4: TWR is the only figure compared against a benchmark, and no endpoint returns an
unlabelled return. M6a's figure needs the label to carry two systematic differences, both
pushing the same way.

**Dividends idle versus reinvested.** `BenchmarkDaily.close_adjusted` assumes every
distribution is reinvested in the proxy. The portfolio's dividends land in cash and stay
there until the owner does something with them. Over a long window at a meaningful yield,
the portfolio trails for a reason that is not selection.

**Net versus gross of withholding.** The portfolio's dividends are net — `DIVIDEND_TAX`
reduces cash. The adjusted close is gross.

Neither is a defect to engineer away in a personal tracker; both are facts the figure must
state. Presenting an unlabelled "you underperformed by X" when part of X is withholding
tax and reinvestment convention would be exactly the unlabelled return §7.4 forbids.

---

## 8. Testing

Hand-computed fixtures, per §11, over a synthetic ledger with invented amounts.

**The load-bearing test: a deposit into a flat market returns zero.** Prices unchanged, a
deposit of any size, `r = 0` for that day and every day after. This single assertion
catches every way flow handling goes wrong — sign errors, the flow left in the numerator,
the flow added to the denominator, a flow dated to the wrong side of the close. It should
be the first test written and it should fail first.

Alongside it:

* **No flows collapses to the ratio.** Over a window with no deposits or withdrawals, the
  chained figure equals `V(end) / V(start) − 1` exactly. Chain-linking is only worth its
  complexity where flows exist, and this pins that it costs nothing where they do not.
* **Scale invariance.** Doubling every deposit amount on a fixed price path leaves every
  `r(d)` unchanged. This is the property that distinguishes TWR from MWR and is the reason
  §7.4 compares only TWR against a benchmark.
* **A trade at the close, with no fee, does not move the return.** Buying on day `d` at
  that day's closing price with zero charges leaves `r(d)` exactly as it would have been
  without the trade, at any size. This is M6a-5's claim that `V` is continuous across an
  internal transfer, and it is the assertion that fails the moment cash is dropped from
  the denominator.
* **A trade away from the close moves it by the right amount.** The same trade executed
  below the close raises `r(d)` by the traded quantity times the gap, less the fee, and
  by nothing else. Stated as its own test because the previous one passes trivially for
  an implementation that ignores trades altogether.
* **A gap yields no figure.** One unpriceable day inside a window produces two `None`
  links, two runs, and a `None` window figure with a stated gap count. Asserted as an
  absence, not as a zero.
* **Sweeps are not flows.** A ledger containing sweep rows produces the same return series
  as one without them. §3.3's finding, made checkable.
* **The guard covers the new endpoint.** `routes_performance` cannot reach `total_return`,
  and the derived endpoint list is non-empty and contains it — the vacuity check M1
  learned to write.

---

## 9. What M6a hands to M6b

The flow series and the linked return series are the two inputs M6b needs and does not
otherwise have. MWR/XIRR brackets a root over the flows M6a extracts; contribution to
return decomposes the aggregate M6a computes, and asserts against it.

The invariant that joins them, once M5 attributes dividends per instrument:

```
r_portfolio(d)  ==  Σ_j w_j(d−1) · r_j(d)  +  w_cash(d−1) · 0
```

This is exact on a day with no trade, where the start-of-day weights hold all day. On a
trade day the weights shift intraday and the identity carries a residual — the same
execution-price-versus-close term section 3.3 isolates. M6b owns that term; M6a's
obligation is only that the aggregate it publishes is the one the decomposition must sum
to, so the residual surfaces as a named quantity rather than as a rounding disagreement
nobody can source.
