/** Everything the instrument chart decides before a pixel is drawn.
 *
 *  Kept pure and separate from the ECharts wrapper for the same reason as
 *  `lib/valuation.ts`: jsdom has no canvas, so a component test cannot render
 *  a chart -- but it can assert on the option object, and that object is
 *  where every decision worth testing lives.
 *
 *  `toPlotValue` and `markerSymbolSize` are the only two places in this file
 *  a string becomes a number, and both are deliberately narrow. A pixel
 *  position, and a pixel radius, are inherently floating point, so converting
 *  at the chart boundary is honest. Converting anywhere a figure is DISPLAYED
 *  would undo the exactness the backend's Decimal storage exists for -- every
 *  other reader of `PricePoint`, `Marker` and `Comparison` keeps the string.
 */

import type { EChartsOption } from "echarts";

import type { Comparison, InstrumentChart, Marker } from "../api/types";
import { c } from "./theme";

/** A money or index string to a chart coordinate. `null` stays `null` so the
 *  line breaks rather than dropping to the axis -- a zero there reads as "this
 *  instrument was worthless that day", which Sec 8.1 forbids. Mirrors
 *  `toPlotValue` in `lib/valuation.ts`, kept local rather than imported so
 *  this file's one numeric boundary is visible without following an import.
 *
 *  Overloaded rather than widened to `string | null` at every call site: a
 *  `Marker`'s price and a `Comparison`'s index values are never `null` on the
 *  wire (see `MarkerOut`/`IndexPointOut`), so those call sites get a plain
 *  `number` back instead of a `number | null` they would otherwise have to
 *  re-narrow with a non-null assertion. */
export function toPlotValue(value: string): number;
export function toPlotValue(value: string | null): number | null;
export function toPlotValue(value: string | null): number | null {
  return value === null ? null : Number(value);
}

//: Pixel radius at one unit of quantity. Arbitrary but irrelevant to what is
//: tested: only the RATIO between two markers' sizes is a real claim.
const MARKER_BASE_SIZE = 6;

/** How large a trade's marker draws. Scaled by the square root of quantity,
 *  never quantity itself: a marker's size is read by its AREA, and area grows
 *  with the square of radius, so a 4x trade must draw at 2x the radius to
 *  look 4x as large rather than 16x. This is the file's other numeric
 *  boundary -- `quantity` is a share count, not money, but the same rule
 *  applies: converted only here, for a rendering attribute, never displayed
 *  or stored as a number. */
export function markerSymbolSize(quantity: string): number {
  return MARKER_BASE_SIZE * Math.sqrt(Number(quantity));
}

const MARKER_COLOR: Record<string, string> = {
  BUY: c.positive,
  SELL: c.negative,
};

function markerPoint(marker: Marker) {
  return {
    name: marker.side,
    coord: [marker.date, toPlotValue(marker.price)],
    symbolSize: markerSymbolSize(marker.quantity),
    itemStyle: { color: MARKER_COLOR[marker.side] ?? c.neutral },
  };
}

function inMarketBand(
  interval: { start: string; end: string },
): [{ xAxis: string }, { xAxis: string }] {
  return [{ xAxis: interval.start }, { xAxis: interval.end }];
}

function benchmarkSeries(comparison: Comparison) {
  return {
    name: comparison.benchmark_key,
    type: "line" as const,
    yAxisIndex: 1,
    showSymbol: false,
    connectNulls: false,
    data: comparison.benchmark_index.map((p) => [p.date, toPlotValue(p.index)]),
    lineStyle: { color: c.neutral, width: 1.5, type: "dashed" as const },
  };
}

export interface InstrumentChartConfig {
  showBenchmark: boolean;
}

/** The ECharts option for one instrument's price line.
 *
 *  One series carries the price, including every `null` day, with
 *  `connectNulls: false` so an unpriceable day is a visible gap rather than a
 *  straight segment drawn over it -- exactly `buildValueOption`'s reasoning
 *  for the portfolio value line.
 *
 *  `markArea` draws one band per IN-MARKET interval only: an out-of-market
 *  stretch gets no band at all, which is the "solid where held, blank where
 *  flat" idea the design doc describes for this screen. `markPoint` draws one
 *  point per executed trade, sized by `markerSymbolSize`.
 *
 *  The benchmark series is appended only when both a comparison exists AND
 *  the toggle is on -- never pushed and hidden, because a hidden ECharts
 *  series still participates in axis scaling and tooltips.
 */
export function instrumentChartOption(
  chart: InstrumentChart,
  opts: InstrumentChartConfig,
): EChartsOption {
  const priceSeries = {
    name: chart.isin,
    type: "line" as const,
    showSymbol: false,
    connectNulls: false,
    data: chart.points.map((p) => toPlotValue(p.close_base)),
    lineStyle: { color: c.accent, width: 1.5 },
    markArea: {
      itemStyle: { color: c.accentBg },
      data: chart.intervals.filter((i) => i.in_market).map(inMarketBand),
    },
    markPoint: {
      symbol: "circle",
      data: chart.markers.map(markerPoint),
    },
  };

  const comparison = chart.comparison;

  return {
    animation: false,
    grid: { left: 64, right: 64, top: 16, bottom: 44 },
    xAxis: {
      type: "category",
      data: chart.points.map((p) => p.date),
      axisLine: { lineStyle: { color: c.borderSoft } },
      axisLabel: { color: c.textFaint, fontSize: 10 },
    },
    yAxis: [
      {
        type: "value",
        scale: true,
        axisLabel: { color: c.textFaint, fontSize: 10 },
        splitLine: { lineStyle: { color: c.borderSoft } },
      },
      {
        type: "value",
        scale: true,
        axisLabel: { color: c.textFaint, fontSize: 10 },
        splitLine: { show: false },
      },
    ],
    // Canvas plus dataZoom for the same reason as the value chart: a
    // multi-year daily series is well past where SVG rendering degrades.
    dataZoom: [{ type: "inside" }, { type: "slider", height: 18, bottom: 8 }],
    tooltip: { trigger: "axis" },
    series: [
      priceSeries,
      // Appended only when both a comparison exists AND the toggle is on --
      // never pushed and left hidden, because a hidden ECharts series still
      // participates in axis scaling and tooltips.
      ...(opts.showBenchmark && comparison !== null ? [benchmarkSeries(comparison)] : []),
    ],
  };
}
