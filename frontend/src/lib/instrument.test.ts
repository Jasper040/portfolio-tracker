/** Tests for the instrument chart's pure option builder.
 *
 *  Same discipline as `valuation.test.ts`: assert on the option object ECharts
 *  would receive, never on pixels -- jsdom has no canvas, and everything worth
 *  testing here is a decision made before a pixel exists.
 */

import { describe, expect, it } from "vitest";

import type { Comparison, Interval, InstrumentChart, Marker, PricePoint } from "../api/types";
import { instrumentChartOption, markerSymbolSize, toPlotValue } from "./instrument";

function point(overrides: Partial<PricePoint> = {}): PricePoint {
  return {
    date: "2025-03-03",
    close_base: "100.00",
    coverage: "full",
    held: true,
    ...overrides,
  };
}

function interval(overrides: Partial<Interval> = {}): Interval {
  return {
    start: "2025-01-01",
    end: "2025-01-31",
    in_market: true,
    price_return: "0.05",
    ...overrides,
  };
}

function marker(overrides: Partial<Marker> = {}): Marker {
  return {
    date: "2025-01-10",
    side: "BUY",
    quantity: "10",
    price: "100.00",
    fees: "1.00",
    position_after: "10",
    ...overrides,
  };
}

function comparison(overrides: Partial<Comparison> = {}): Comparison {
  return {
    basis: "total_return",
    benchmark_key: "world",
    // Same date as `point()`'s default on purpose: overlap is the default
    // fixture shape, and a mismatched calendar is something each test that
    // needs it builds deliberately -- see "benchmark reindexing" below.
    instrument_index: [{ date: "2025-03-03", index: "100" }],
    benchmark_index: [{ date: "2025-03-03", index: "100" }],
    intervals: [],
    linked_instrument_return: "0.05",
    linked_benchmark_return: "0.04",
    linked_excess: "0.01",
    coverage: "full",
    ...overrides,
  };
}

function chart(overrides: Partial<InstrumentChart> = {}): InstrumentChart {
  return {
    isin: "XX0000000001",
    points: [point()],
    intervals: [interval()],
    markers: [marker()],
    comparison: null,
    requested_from: "2025-03-03",
    clamped: false,
    method: null,
    coverage: "full",
    ...overrides,
  };
}

describe("plotting values", () => {
  it("turns a money string into a number only for the chart", () => {
    expect(toPlotValue("100.00")).toBe(100);
  });

  it("keeps null as null so the line breaks instead of dropping to zero", () => {
    expect(toPlotValue(null)).toBeNull();
  });
});

describe("marker symbol size", () => {
  it("scales with the square root of quantity, not quantity itself", () => {
    // 4x the quantity should read as 2x the radius (area is what the eye
    // reads), never 4x the radius.
    const small = markerSymbolSize("10");
    const large = markerSymbolSize("40");
    expect(large / small).toBeCloseTo(2, 10);
  });

  it("is proportionally 3x for 9x the quantity", () => {
    const small = markerSymbolSize("5");
    const large = markerSymbolSize("45");
    expect(large / small).toBeCloseTo(3, 10);
  });
});

describe("in-market bands", () => {
  it("marks one markArea entry per in-market interval, at its own dates", () => {
    const c = chart({
      intervals: [
        interval({ start: "2025-01-01", end: "2025-01-31", in_market: true }),
        interval({ start: "2025-02-01", end: "2025-02-15", in_market: false }),
        interval({ start: "2025-02-16", end: "2025-03-01", in_market: true }),
      ],
    });
    const option = instrumentChartOption(c, { showBenchmark: false });
    const price = (option.series as Array<Record<string, unknown>>)[0]!;
    const markArea = price.markArea as { data: Array<Array<{ xAxis: string }>> };

    expect(markArea.data).toHaveLength(2);
    expect(markArea.data[0]?.[0]?.xAxis).toBe("2025-01-01");
    expect(markArea.data[0]?.[1]?.xAxis).toBe("2025-01-31");
    expect(markArea.data[1]?.[0]?.xAxis).toBe("2025-02-16");
    expect(markArea.data[1]?.[1]?.xAxis).toBe("2025-03-01");
  });

  it("draws no band at all for an out-of-market interval", () => {
    const c = chart({
      intervals: [interval({ start: "2025-01-01", end: "2025-01-31", in_market: false })],
    });
    const option = instrumentChartOption(c, { showBenchmark: false });
    const price = (option.series as Array<Record<string, unknown>>)[0]!;
    const markArea = price.markArea as { data: unknown[] };
    expect(markArea.data).toHaveLength(0);
  });
});

describe("trade markers", () => {
  it("puts one markPoint per marker", () => {
    const c = chart({
      markers: [
        marker({ date: "2025-01-05" }),
        marker({ date: "2025-01-10" }),
        marker({ date: "2025-01-20" }),
      ],
    });
    const option = instrumentChartOption(c, { showBenchmark: false });
    const price = (option.series as Array<Record<string, unknown>>)[0]!;
    const markPoint = price.markPoint as { data: unknown[] };
    expect(markPoint.data).toHaveLength(3);
  });
});

describe("benchmark toggle", () => {
  it("omits the benchmark series entirely when the toggle is off", () => {
    const c = chart({ comparison: comparison() });
    const option = instrumentChartOption(c, { showBenchmark: false });
    const series = option.series as Array<Record<string, unknown>>;
    expect(series.some((s) => s.name === "world")).toBe(false);
  });

  it("includes the benchmark series when the toggle is on and a comparison exists", () => {
    const c = chart({ comparison: comparison() });
    const option = instrumentChartOption(c, { showBenchmark: true });
    const series = option.series as Array<Record<string, unknown>>;
    expect(series.some((s) => s.name === "world")).toBe(true);
  });

  it("stays absent when the toggle is on but no benchmark was requested", () => {
    const c = chart({ comparison: null });
    const option = instrumentChartOption(c, { showBenchmark: true });
    const series = option.series as Array<Record<string, unknown>>;
    expect(series).toHaveLength(1);
  });
});

describe("benchmark reindexing", () => {
  // The benchmark's own dates come from a separate query against the
  // BENCHMARK's trading calendar; `chart.points` comes from the
  // INSTRUMENT's. Divergent market holidays are the ordinary case, not an
  // edge case, and nothing guarantees one calendar is a subset of the other.
  //
  // The x-axis is given an explicit `data` array (the instrument's own
  // dates), so ECharts never collects a new category for a benchmark date
  // that is not already in that list -- a plain `[date, value]` pairing
  // would make that point vanish silently, with no error and no visible
  // gap. Reindexing the benchmark onto the instrument's own dates first,
  // by date rather than by array position, keeps a benchmark holiday a
  // `null` gap instead of a disappearing point.

  it("is exactly as long as the instrument's own points, gapping the dates the benchmark lacks", () => {
    const c = chart({
      points: [
        point({ date: "2025-01-01" }),
        point({ date: "2025-01-02" }),
        point({ date: "2025-01-03" }),
      ],
      comparison: comparison({
        // No entry for 2025-01-02: a benchmark holiday on a day the
        // instrument itself traded.
        benchmark_index: [
          { date: "2025-01-01", index: "100" },
          { date: "2025-01-03", index: "102" },
        ],
      }),
    });
    const option = instrumentChartOption(c, { showBenchmark: true });
    const series = option.series as Array<Record<string, unknown>>;
    const benchmark = series.find((s) => s.name === "world")!;
    expect(benchmark.data).toEqual([100, null, 102]);
  });

  it("aligns by date rather than by array position, so an extra benchmark day does not shift later values", () => {
    const c = chart({
      // The instrument's own calendar skips 2025-01-02 entirely (a day it
      // was not traded/priced) -- only two points.
      points: [point({ date: "2025-01-01" }), point({ date: "2025-01-03" })],
      comparison: comparison({
        // The benchmark traded on 2025-01-02 even though the instrument's
        // own series has no row for it. A naive positional mapping (zip
        // benchmark_index directly against chart.points) would read this
        // as [2025-01-01 -> 100, 2025-01-03 -> 150] -- silently handing
        // 2025-01-03 the value that actually belongs to 2025-01-02.
        benchmark_index: [
          { date: "2025-01-01", index: "100" },
          { date: "2025-01-02", index: "150" },
          { date: "2025-01-03", index: "200" },
        ],
      }),
    });
    const option = instrumentChartOption(c, { showBenchmark: true });
    const series = option.series as Array<Record<string, unknown>>;
    const benchmark = series.find((s) => s.name === "world")!;
    // Correct-by-date: 2025-01-03 gets its OWN value, not 2025-01-02's.
    expect(benchmark.data).toEqual([100, 200]);
  });
});

describe("gaps in the price line", () => {
  it("renders an unpriceable day as null, never as zero", () => {
    const c = chart({
      points: [
        point({ date: "2025-01-01", close_base: "100.00" }),
        point({ date: "2025-01-02", close_base: null, coverage: "missing" }),
        point({ date: "2025-01-03", close_base: "102.00" }),
      ],
    });
    const option = instrumentChartOption(c, { showBenchmark: false });
    const price = (option.series as Array<Record<string, unknown>>)[0]!;
    expect(price.data).toEqual([100, null, 102]);
    expect(price.connectNulls).toBe(false);
  });
});

describe("robustness", () => {
  it("survives a chart with no points, intervals or markers", () => {
    const c = chart({ points: [], intervals: [], markers: [] });
    expect(() => instrumentChartOption(c, { showBenchmark: false })).not.toThrow();
  });
});
