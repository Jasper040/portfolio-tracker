/** The performance chart's decisions, tested as a pure function: jsdom has no
 *  canvas, but the option object is where every choice worth pinning lives. */

import { describe, expect, it } from "vitest";

import type { PerformanceReport, ReturnLink } from "../api/types";
import {
  INDEX_AXIS_NAME,
  PORTFOLIO_SERIES,
  dividendTreatment,
  indexOnDates,
  performanceChartOption,
  performanceDates,
  performanceTooltip,
} from "./performance";

function link(date: string, dailyReturn: string | null = "0.01"): ReturnLink {
  return {
    date,
    since: "",
    flow_base: "0.00",
    daily_return: dailyReturn,
    coverage: dailyReturn === null ? "missing" : "full",
    reason: dailyReturn === null ? "no valuation" : null,
  };
}

/** Two runs around a Wednesday that could not be valued. */
function gapped(overrides: Partial<PerformanceReport> = {}): PerformanceReport {
  return {
    basis: "time_weighted",
    lane: "unadjusted_close_plus_cash",
    dividends: "held_as_cash_net_of_withholding",
    base_currency: "EUR",
    start: "2025-03-03",
    end: "2025-03-07",
    requested_from: null,
    clamped: false,
    links: [
      link("2025-03-04", "0.1"),
      link("2025-03-05", null),
      link("2025-03-06", null),
      link("2025-03-07", "0.05"),
    ],
    runs: [
      { start: "2025-03-03", end: "2025-03-04", days: 1, linked_return: "0.1", coverage: "full" },
      { start: "2025-03-06", end: "2025-03-07", days: 1, linked_return: "0.05", coverage: "full" },
    ],
    portfolio_index: [
      { date: "2025-03-03", index: "100" },
      { date: "2025-03-04", index: "110" },
      { date: "2025-03-06", index: "100" },
      { date: "2025-03-07", index: "105" },
    ],
    linked_return: null,
    gaps: 1,
    reason: "1 unmeasurable stretch split this window into 2 runs; no single figure spans a gap",
    comparison: null,
    method: null,
    coverage: "missing",
    ...overrides,
  };
}

describe("the drawn axis", () => {
  it("is the window's first close followed by every linked day", () => {
    expect(performanceDates(gapped())).toEqual([
      "2025-03-03",
      "2025-03-04",
      "2025-03-05",
      "2025-03-06",
      "2025-03-07",
    ]);
  });

  it("is empty when there is nothing to measure", () => {
    expect(performanceDates(gapped({ start: null, links: [] }))).toEqual([]);
  });
});

describe("placing an index on the axis", () => {
  it("places each point by date, never by position", () => {
    expect(
      indexOnDates(
        ["2025-03-03", "2025-03-04", "2025-03-05"],
        [
          { date: "2025-03-05", index: "110" },
          { date: "2025-03-03", index: "100" },
        ],
      ),
    ).toEqual([100, null, 110]);
  });
});

describe("the chart option", () => {
  it("draws the portfolio index with the gap left open, never bridged or zeroed", () => {
    const option = performanceChartOption(gapped(), { benchmarkLabel: null });
    const series = option.series as Array<Record<string, unknown>>;
    expect(series).toHaveLength(1);
    expect(series[0]!.name).toBe(PORTFOLIO_SERIES);
    expect(series[0]!.connectNulls).toBe(false);
    expect(series[0]!.data).toEqual([100, 110, null, 100, 105]);
  });

  it("adds the benchmark on the same axis, because both lines are the same kind of number", () => {
    const option = performanceChartOption(
      gapped({
        comparison: {
          basis: "total_return",
          dividends: "reinvested_gross",
          benchmark_key: "world",
          benchmark_index: [{ date: "2025-03-03", index: "100" }],
          runs: [],
          benchmark_return: null,
          excess: null,
          span: "full",
        },
      }),
      { benchmarkLabel: "World Equities" },
    );
    const series = option.series as Array<Record<string, unknown>>;
    expect(series.map((s) => s.name)).toEqual([PORTFOLIO_SERIES, "World Equities"]);
    expect(series[1]!.yAxisIndex).toBeUndefined();
    expect((option.yAxis as Record<string, unknown>).name).toBe(INDEX_AXIS_NAME);
  });

  it("names the benchmark by its key when no label is known", () => {
    const option = performanceChartOption(
      gapped({
        comparison: {
          basis: "total_return",
          dividends: "reinvested_gross",
          benchmark_key: "world",
          benchmark_index: [],
          runs: [],
          benchmark_return: null,
          excess: null,
          span: "missing",
        },
      }),
      { benchmarkLabel: null },
    );
    const series = option.series as Array<Record<string, unknown>>;
    expect(series[1]!.name).toBe("world");
  });
});

describe("dividend treatment", () => {
  it("says in words how each side treats dividends", () => {
    expect(dividendTreatment("held_as_cash_net_of_withholding")).toMatch(/after withholding/);
    expect(dividendTreatment("reinvested_gross")).toMatch(/reinvested/);
  });

  it("shows a code it does not recognise exactly as it arrived", () => {
    expect(dividendTreatment("something_new")).toBe("something_new");
  });
});

describe("the performance chart tooltip", () => {
  /** PT-47. `indexOnDates` rebases through `Number(point.index)`, and a
   *  division rarely lands on two places; ECharts' default prints every one. */
  it("rounds a rebased index to two places", () => {
    const html = performanceTooltip([
      { axisValueLabel: "2025-09-08", seriesName: PORTFOLIO_SERIES, value: 103.45000000000002 },
    ]);
    expect(html).toContain("103,45");
    expect(html).not.toContain("103.45000000000002");
  });

  /** Both series are indices at 100, not money. A euro sign here would be a
   *  claim about what the number measures. */
  it("carries no currency mark", () => {
    const html = performanceTooltip([
      { axisValueLabel: "2025-09-08", seriesName: PORTFOLIO_SERIES, value: 100 },
    ]);
    expect(html).not.toContain("€");
  });

  it("names both sides when a benchmark is drawn", () => {
    const html = performanceTooltip([
      { axisValueLabel: "2025-09-08", seriesName: PORTFOLIO_SERIES, value: 103.4 },
      { axisValueLabel: "2025-09-08", seriesName: "World", value: 101.2 },
    ]);
    expect(html).toContain(PORTFOLIO_SERIES);
    expect(html).toContain("World");
    expect(html).toContain("103,40");
    expect(html).toContain("101,20");
  });

  /** M6a-7 on the tooltip: a gap has no index, and a zero there would read as
   *  a portfolio that lost everything. */
  it("shows a dash across a gap rather than a zero", () => {
    const html = performanceTooltip([
      { axisValueLabel: "2025-09-08", seriesName: PORTFOLIO_SERIES, value: null },
    ]);
    expect(html).toContain("—");
    expect(html).not.toContain("0,00");
  });

  it("names the day being hovered", () => {
    const html = performanceTooltip([
      { axisValueLabel: "2025-09-08", seriesName: PORTFOLIO_SERIES, value: 100 },
    ]);
    expect(html).toContain("08-09-25");
  });
});
