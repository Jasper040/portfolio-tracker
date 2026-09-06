import { describe, expect, it } from "vitest";
import {
  moneyWeightedReturn,
  periodStartIndex,
  rebase,
  timeWeightedReturn,
  type PortfolioSeries,
} from "./portfolio";
import { MONTHS } from "./series";

/** A portfolio worth `start` at month 0 and `end` for every month after, with an
 *  optional deposit in month 1. Deliberately step-shaped: all the growth happens
 *  in one month, so the expected return is arithmetic rather than a simulation. */
function stepSeries(start: number, end: number, depositAtMonth1 = 0): PortfolioSeries {
  const value = new Array<number>(MONTHS).fill(end);
  value[0] = start;
  const cashflow = new Array<number>(MONTHS).fill(0);
  cashflow[1] = depositAtMonth1;
  return { value, valueNeverSold: value, cashflow, benchmarkReplay: value };
}

describe("timeWeightedReturn", () => {
  it("reports the growth when no money moved", () => {
    expect(timeWeightedReturn(stepSeries(100, 110), 0)).toBeCloseTo(0.1, 10);
  });

  it("is unmoved by a deposit — the whole point of TWR", () => {
    // 100 in, 100 more deposited, ending at 210. The portfolio still only grew
    // 10%; the rest is funding. A measure that reported 110% here would make
    // every benchmark comparison meaningless.
    const withDeposit = timeWeightedReturn(stepSeries(100, 210, 100), 0);
    const without = timeWeightedReturn(stepSeries(100, 110), 0);
    expect(withDeposit).toBeCloseTo(0.1, 10);
    expect(withDeposit).toBeCloseTo(without, 10);
  });

  it("skips months that open at zero instead of dividing by them", () => {
    // Before the first purchase there is no portfolio to have returned anything.
    const value = new Array<number>(MONTHS).fill(110);
    value[0] = 0;
    value[1] = 100;
    const series: PortfolioSeries = {
      value,
      valueNeverSold: value,
      cashflow: new Array<number>(MONTHS).fill(0),
      benchmarkReplay: value,
    };
    expect(Number.isFinite(timeWeightedReturn(series, 0))).toBe(true);
    expect(timeWeightedReturn(series, 0)).toBeCloseTo(0.1, 10);
  });
});

describe("moneyWeightedReturn", () => {
  it("annualises the IRR of the actual cashflow schedule", () => {
    // 100 out at t=0, 110 back at t=92/12 years. 1.1^(12/92) - 1.
    const expected = Math.pow(1.1, 12 / 92) - 1;
    expect(moneyWeightedReturn(stepSeries(100, 110), 0)).toBeCloseTo(expected, 5);
  });

  it("is dragged down by money deposited into flat growth", () => {
    // Where TWR is identical for these two, MWR is not: contributing a second 100
    // that then does nothing dilutes what your euros actually earned. TWR judges
    // the decisions, MWR judges the outcome.
    const lumpSum = moneyWeightedReturn(stepSeries(100, 110), 0);
    const staggered = moneyWeightedReturn(stepSeries(100, 210, 100), 0);
    expect(staggered).toBeLessThan(lumpSum);
    expect(staggered).toBeGreaterThan(0);
  });

  it("disagrees with TWR on the same series, which is why both are shown", () => {
    const series = stepSeries(100, 110);
    expect(timeWeightedReturn(series, 0)).toBeCloseTo(0.1, 10);
    // ~1.25% annualised over 7.67 years is the same 10%, spread out.
    expect(moneyWeightedReturn(series, 0)).toBeLessThan(0.02);
  });
});

describe("periodStartIndex", () => {
  it("maps every period onto the shared month grid", () => {
    const last = MONTHS - 1;
    expect(periodStartIndex("Max")).toBe(0);
    expect(periodStartIndex("1M")).toBe(last - 1);
    expect(periodStartIndex("3M")).toBe(last - 3);
    expect(periodStartIndex("1Y")).toBe(last - 12);
    expect(periodStartIndex("3Y")).toBe(last - 36);
  });

  it("puts YTD at January of the current year", () => {
    // Index 84 is 2026-01 on a grid starting 2019-01.
    expect(periodStartIndex("YTD")).toBe(84);
  });
});

describe("rebase", () => {
  it("sets the base month to 100 and scales the rest", () => {
    expect(rebase([10, 20, 40, 50], 1)).toEqual([null, 100, 200, 250]);
  });

  it("leaves everything before the base undefined rather than flat at 100", () => {
    // A flat line at 100 would claim the position existed and did nothing, which
    // is a different statement from "there was no position yet".
    expect(rebase([10, 20, 40], 2)?.slice(0, 2)).toEqual([null, null]);
  });

  it("returns all nulls rather than Infinity when the base is zero", () => {
    expect(rebase([0, 20, 40], 0)).toEqual([null, null, null]);
  });
});
