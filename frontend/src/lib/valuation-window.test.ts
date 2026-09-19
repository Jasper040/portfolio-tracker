/** Narrowing a fetched valuation series to a shorter window, in the browser.
 *
 *  The cases that matter are the ones where narrowing is NOT allowed. A slice
 *  that silently disagrees with what the API would have said is worse than the
 *  round trip it saved, because nothing on screen would say so.
 */

import { describe, expect, it } from "vitest";

import type { Coverage, ValuationPoint, ValuationSeries } from "../api/types";
import { sliceSeries, worstCoverage } from "./valuation-window";

function point(date: string, coverage: Coverage = "full"): ValuationPoint {
  return {
    date,
    holdings_base: "300.00",
    cash_base: "-50.00",
    value_base: "250.00",
    coverage,
    covered_pct: coverage === "missing" ? null : "1",
  };
}

function series(items: ValuationPoint[], overrides: Partial<ValuationSeries> = {}): ValuationSeries {
  return {
    items,
    start: items[0]?.date ?? null,
    end: items[items.length - 1]?.date ?? null,
    requested_from: null,
    clamped: false,
    base_currency: "EUR",
    method: null,
    coverage: worstCoverage(items.map((i) => i.coverage)),
    ...overrides,
  };
}

const FULL = series([
  point("2025-01-02"),
  point("2025-02-03", "partial"),
  point("2025-03-04"),
  point("2025-04-05", "missing"),
]);

describe("worstCoverage", () => {
  /** Mirrors the backend's `_SEVERITY` exactly. If the two ever disagree, a
   *  sliced window would badge itself better than the API would. */
  it("ranks missing above partial above manual above full", () => {
    expect(worstCoverage(["full", "manual"])).toBe("manual");
    expect(worstCoverage(["manual", "partial"])).toBe("partial");
    expect(worstCoverage(["partial", "missing"])).toBe("missing");
    expect(worstCoverage(["full", "missing", "partial", "manual"])).toBe("missing");
  });

  /** The backend's `worst_coverage` documents this as a decision rather than a
   *  fallback: an empty set carries no bad news. A window past the last day has
   *  nothing badly covered in it. */
  it("calls an empty set full, as the API does", () => {
    expect(worstCoverage([])).toBe("full");
  });
});

describe("sliceSeries", () => {
  it("returns the fetched series unchanged for the whole-ledger window", () => {
    expect(sliceSeries(FULL, null)).toBe(FULL);
  });

  it("keeps only the days on or after the requested start", () => {
    const out = sliceSeries(FULL, "2025-03-04");
    expect(out?.items.map((i) => i.date)).toEqual(["2025-03-04", "2025-04-05"]);
  });

  it("restates the window's own bounds rather than the fetched one's", () => {
    const out = sliceSeries(FULL, "2025-02-03");
    expect(out?.start).toBe("2025-02-03");
    expect(out?.end).toBe("2025-04-05");
    expect(out?.requested_from).toBe("2025-02-03");
  });

  /** The badge is computed over the SLICE, not carried from the fetched
   *  series. Carrying it would let a window of fully-priced days inherit a
   *  `missing` from a day outside it -- PT-14's mistake in the other
   *  direction. */
  it("recomputes coverage over the narrowed window", () => {
    expect(sliceSeries(FULL, "2025-01-02")?.coverage).toBe("missing");
    expect(sliceSeries(FULL, "2025-02-03")?.coverage).toBe("missing");
    // Only the fully-priced tail: the partial and the missing are both outside.
    const early = series([point("2025-01-02"), point("2025-02-03", "partial")]);
    expect(sliceSeries(early, "2025-01-02")?.coverage).toBe("partial");
  });

  /** THE case this function exists to get right. `value_series` starts a MAX
   *  request at the first day a POSITION existed, but floors a windowed request
   *  at the first day a CASH row existed -- and the response never reveals the
   *  second. So a window reaching earlier than what we hold might legitimately
   *  contain days we were never sent, and we cannot tell. Refuse, and let the
   *  caller ask the API. */
  it("refuses a window that starts before the fetched series does", () => {
    expect(sliceSeries(FULL, "2024-12-31")).toBeNull();
    expect(sliceSeries(FULL, "2025-01-01")).toBeNull();
  });

  it("allows a window starting exactly where the fetched series starts", () => {
    const out = sliceSeries(FULL, "2025-01-02");
    expect(out?.items).toHaveLength(4);
    // Proven, not copied: a window at or after our own start cannot have been
    // clamped, because the API's floor is on or before it.
    expect(out?.clamped).toBe(false);
  });

  it("refuses an empty fetched series rather than inventing an answer", () => {
    expect(sliceSeries(series([]), "2025-01-01")).toBeNull();
  });

  /** Matches the API: no points, no bounds, and `full` from the empty set. */
  it("handles a window past the last day the way the API would", () => {
    const out = sliceSeries(FULL, "2026-01-01");
    expect(out?.items).toEqual([]);
    expect(out?.start).toBeNull();
    expect(out?.end).toBeNull();
    expect(out?.coverage).toBe("full");
  });

  it("carries the envelope facts a window cannot change", () => {
    const out = sliceSeries(FULL, "2025-03-04");
    expect(out?.base_currency).toBe("EUR");
    // No lot matching ran for a valuation, on any window.
    expect(out?.method).toBeNull();
  });

  it("does not mutate the series it narrowed", () => {
    const before = FULL.items.length;
    sliceSeries(FULL, "2025-04-05");
    expect(FULL.items).toHaveLength(before);
    expect(FULL.requested_from).toBeNull();
  });
});
