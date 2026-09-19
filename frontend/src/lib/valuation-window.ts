/** Narrowing a fetched valuation series to a shorter window, in the browser.
 *
 *  The operator's observation is what this exists for: dragging the chart's own
 *  zoom slider was instant while picking a range preset was not, because the
 *  slider redraws data already here and a preset was a round trip.
 *
 *  It can be answered here because a valuation point is window-independent. The
 *  API computes each day's value from that day's holdings, prices and cash, and
 *  the requested window only decides which days are returned -- so the days a
 *  shorter window would contain are exactly the days already in hand, and a
 *  narrower request cannot produce a different figure for the same date.
 *
 *  **The performance report is not like this and must never be sliced.** Its
 *  return is chain-linked within the window and each run is rebased to 100 at
 *  its own start, so a shorter window is a different computation rather than a
 *  subset of a longer one. That screen keeps asking the API.
 *
 *  ## What the API decides and this file has to reproduce
 *
 *  Narrowing is only honest if the narrowed envelope says what the API would
 *  have said. Two fields need real work rather than copying:
 *
 *  - `coverage` is the worst verdict ACROSS THE WINDOW, so it has to be
 *    recomputed over the slice. Carrying the fetched series' value would let a
 *    window of fully-priced days inherit a `missing` from a day outside it.
 *  - `clamped` means the caller asked to start before the ledger did. Inside
 *    the range this file allows, it is provably `false` -- see below.
 */

import type { Coverage, ValuationPoint, ValuationSeries } from "../api/types";

/** Mirrors `_SEVERITY` in `backend/app/analytics/quotes.py`. The two are a pair:
 *  if they ever disagree, a narrowed window badges itself differently from the
 *  same window fetched, and nothing on screen would say which is right. */
const SEVERITY: Record<Coverage, number> = { full: 0, manual: 1, partial: 2, missing: 3 };

/** The most severe verdict among `values`; `full` when there are none.
 *
 *  Empty is `full` because that is what the backend's `worst_coverage` decides,
 *  and it documents the reasoning: this reports the worst news among a set of
 *  observations, and an empty set carries no bad news. A window past the last
 *  day holds nothing badly covered.
 */
export function worstCoverage(values: readonly Coverage[]): Coverage {
  let worst: Coverage = "full";
  for (const value of values) {
    if (SEVERITY[value] > SEVERITY[worst]) worst = value;
  }
  return worst;
}

/** `full` narrowed to start at `from`, or `null` when that cannot be proved
 *  correct and the caller should ask the API instead.
 *
 *  ## Why it can refuse, and why the guard is exactly this one
 *
 *  `value_series` uses two different floors. Asked for the whole ledger it
 *  starts at the first day a POSITION existed; asked for a window it clamps to
 *  the first day a CASH ROW existed. Those are not the same day -- a deposit
 *  normally lands before the first trade -- and the whole-ledger response never
 *  reveals the cash floor. So a window reaching back further than what we hold
 *  may legitimately contain cash-only days we were never sent, and from here
 *  that is indistinguishable from a window with nothing before it.
 *
 *  `from >= full.start` is what rules that out. Our own `start` is a real day
 *  in the series, so the API's cash floor is on or before it; a window starting
 *  at or after it therefore clamps to itself, and the days it selects are
 *  exactly our days from `from` onward. The same inequality is why `clamped` is
 *  `false` rather than copied: the caller did not ask to start before the
 *  ledger did.
 *
 *  Refusing costs one request that the response cache then remembers. Guessing
 *  would cost a figure nobody could check.
 */
export function sliceSeries(full: ValuationSeries, from: string | null): ValuationSeries | null {
  // The whole-ledger window IS the fetched series. Returned as-is rather than
  // rebuilt, so the common case allocates nothing.
  if (from === null) return full;

  // No days means no `start` to compare against, so there is nothing to prove
  // the window against either.
  if (full.start === null) return null;

  // ISO dates compare correctly as strings; that is the format's whole point.
  if (from < full.start) return null;

  const items: ValuationPoint[] = full.items.filter((item) => item.date >= from);
  const first = items[0];
  const last = items[items.length - 1];

  return {
    items,
    // The window's own bounds, not the fetched series'. An empty window has
    // neither, exactly as the API reports it.
    start: first?.date ?? null,
    end: last?.date ?? null,
    requested_from: from,
    // Proven above, not copied.
    clamped: false,
    coverage: worstCoverage(items.map((item) => item.coverage)),
    // Neither depends on the window. `method` is `null` for every valuation:
    // no lot matching ran, and M1 proved share counts are method-independent.
    base_currency: full.base_currency,
    method: full.method,
  };
}
