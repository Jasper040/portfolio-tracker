/** The chart's shape, decided before any pixel is drawn.
 *
 *  Everything here is a pure function over the API's own strings, which is what
 *  lets the chart be tested in node rather than through a canvas that jsdom does
 *  not implement.
 *
 *  The one place a money string becomes a number is `toPlotValue`, and it is
 *  deliberately the only one. A pixel position is inherently floating point, so
 *  converting at the chart boundary is honest; converting anywhere a figure is
 *  DISPLAYED would undo the exactness the backend's Decimal storage exists for,
 *  which is why every cell in the table below goes through `decimalEur`.
 */

import { describe, expect, it } from "vitest";

import {
  RANGE_PRESETS,
  buildValueOption,
  positionWeight,
  rangeStart,
  tallyCoverage,
  toPlotValue,
} from "./valuation";
import type { ValuationPoint } from "../api/types";

function point(overrides: Partial<ValuationPoint> = {}): ValuationPoint {
  return {
    date: "2025-03-03",
    holdings_base: "300.00",
    cash_base: "-50.00",
    value_base: "250.00",
    coverage: "full",
    covered_pct: "1",
    ...overrides,
  };
}

describe("range presets", () => {
  it("offers five years and a maximum", () => {
    expect(RANGE_PRESETS).toContain("5Y");
    expect(RANGE_PRESETS).toContain("MAX");
  });

  it("asks for exactly five years back", () => {
    expect(rangeStart("5Y", new Date("2026-09-06T00:00:00Z"))).toBe("2021-09-06");
  });

  it("asks for one year back", () => {
    expect(rangeStart("1Y", new Date("2026-09-06T00:00:00Z"))).toBe("2025-09-06");
  });

  it("sends no start at all for MAX", () => {
    // The server then answers from the first day a position existed, which is
    // the M2-7 default. Computing a start here would second-guess it.
    expect(rangeStart("MAX", new Date("2026-09-06T00:00:00Z"))).toBeNull();
  });
});

describe("plotting values", () => {
  it("turns a money string into a number only for the chart", () => {
    expect(toPlotValue("250.00")).toBe(250);
  });

  it("keeps null as null so the line breaks instead of dropping to zero", () => {
    // A day that could not be fully priced has no value. Plotting it as 0 would
    // draw a cliff to the axis and read as a portfolio that lost everything.
    expect(toPlotValue(null)).toBeNull();
  });
});

describe("the value chart option", () => {
  const points = [
    point({ date: "2025-03-03" }),
    point({ date: "2025-03-04", coverage: "partial", covered_pct: "0.2" }),
    point({ date: "2025-03-05", value_base: null, holdings_base: null, coverage: "missing", covered_pct: null }),
    point({ date: "2025-03-06", coverage: "manual" }),
  ];

  it("puts one x-axis category per day", () => {
    const option = buildValueOption(points, { label: "Portfolio value" });
    expect(option.xAxis).toMatchObject({
      data: ["2025-03-03", "2025-03-04", "2025-03-05", "2025-03-06"],
    });
  });

  it("leaves a gap where a day could not be valued", () => {
    const option = buildValueOption(points, { label: "Portfolio value" });
    const line = (option.series as Array<Record<string, unknown>>)[0]!;
    expect(line.data).toEqual([250, 250, null, 250]);
    // Without this ECharts joins across the gap and the missing day disappears.
    expect(line.connectNulls).toBe(false);
  });

  it("marks every point below full coverage distinctly", () => {
    // Sec 8.1's instinct, on the chart: a stale stretch has to be visible
    // without consulting a legend, the same way MODELLED is.
    const option = buildValueOption(points, { label: "Portfolio value" });
    const marks = (option.series as Array<Record<string, unknown>>)[1]!;
    expect(marks.data).toEqual([
      [1, 250],
      [3, 250],
    ]);
  });

  it("does not mark a day it could not value at all", () => {
    // There is no y for a null value. The gap in the line is what says so.
    const option = buildValueOption(points, { label: "Portfolio value" });
    const marks = (option.series as Array<Record<string, unknown>>)[1]!;
    expect((marks.data as unknown[]).length).toBe(2);
  });

  it("survives an empty series without throwing", () => {
    const option = buildValueOption([], { label: "Portfolio value" });
    expect((option.series as unknown[]).length).toBe(2);
  });
});

describe("counting coverage", () => {
  it("counts each day once, by its own verdict", () => {
    const tally = tallyCoverage([
      point(),
      point({ coverage: "partial" }),
      point({ coverage: "partial" }),
      point({ coverage: "missing" }),
      point({ coverage: "manual" }),
    ]);
    expect(tally).toEqual({ full: 1, partial: 2, manual: 1, missing: 1, total: 5 });
  });

  it("reports zeroes rather than throwing on an empty series", () => {
    expect(tallyCoverage([])).toEqual({
      full: 0,
      partial: 0,
      manual: 0,
      missing: 0,
      total: 0,
    });
  });
});

describe("position weight", () => {
  it("is the position's share of the portfolio", () => {
    expect(positionWeight("250.00", "1000.00")).toBe("0.25");
  });

  it("is null when the total was withheld", () => {
    // A share of a total that does not exist is not a smaller number.
    expect(positionWeight("250.00", null)).toBeNull();
  });

  it("is null rather than infinite on a zero total", () => {
    expect(positionWeight("250.00", "0.00")).toBeNull();
  });
});
