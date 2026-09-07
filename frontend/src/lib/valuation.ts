/** Everything the value chart decides before a pixel is drawn.
 *
 *  Kept pure and separate from the ECharts wrapper for one practical reason:
 *  jsdom has no canvas, so a component test cannot render a chart -- but it can
 *  assert on the option object, and that object is where every decision worth
 *  testing lives.
 *
 *  `toPlotValue` is the only place in the app where a money string becomes a
 *  JavaScript number, and it is deliberately narrow. A pixel position is
 *  inherently floating point, so converting at the chart boundary is honest.
 *  Converting anywhere a figure is DISPLAYED would undo the exactness the
 *  backend's Decimal storage exists for -- which is why every cell of the
 *  positions table goes through `decimalEur` on the original string instead.
 */

import type { EChartsOption } from "echarts";

import type { Coverage, ValuationPoint } from "../api/types";
import { c } from "./theme";

export const RANGE_PRESETS = ["1M", "3M", "6M", "1Y", "2Y", "5Y", "MAX"] as const;
export type RangePreset = (typeof RANGE_PRESETS)[number];

const MONTHS_BACK: Record<Exclude<RangePreset, "MAX">, number> = {
  "1M": 1,
  "3M": 3,
  "6M": 6,
  "1Y": 12,
  "2Y": 24,
  "5Y": 60,
};

/** The `from` to send, or `null` to send none.
 *
 *  `MAX` sends none on purpose. The server then answers from the first day a
 *  position existed (M2-7); computing a start here would be this client
 *  second-guessing a decision the ledger already knows the answer to.
 *
 *  Every other preset sends an explicit date, which the server honours back to
 *  the ledger's own first day and clamps beyond it. That is what makes "show me
 *  five years" answerable on a two-year-old account: everything there is, plus a
 *  flag saying the window was shortened.
 */
export function rangeStart(preset: RangePreset, today: Date): string | null {
  if (preset === "MAX") return null;
  const start = new Date(
    Date.UTC(today.getUTCFullYear(), today.getUTCMonth() - MONTHS_BACK[preset], today.getUTCDate()),
  );
  return start.toISOString().slice(0, 10);
}

/** A money string to a chart coordinate. `null` stays `null` so the line breaks
 *  rather than dropping to the axis -- a zero there reads as a portfolio that
 *  lost everything, which is exactly the claim design doc 8.1 forbids. */
export function toPlotValue(value: string | null): number | null {
  return value === null ? null : Number(value);
}

export interface CoverageTally {
  full: number;
  partial: number;
  manual: number;
  missing: number;
  total: number;
}

export function tallyCoverage(points: readonly ValuationPoint[]): CoverageTally {
  const tally: CoverageTally = { full: 0, partial: 0, manual: 0, missing: 0, total: 0 };
  for (const point of points) {
    tally[point.coverage] += 1;
    tally.total += 1;
  }
  return tally;
}

const BELOW_FULL: ReadonlySet<Coverage> = new Set<Coverage>(["partial", "manual"]);

export interface ValueOptionConfig {
  label: string;
}

/** The ECharts option for the portfolio value chart.
 *
 *  Two series, not one. The line carries every day including the nulls, with
 *  `connectNulls: false` so an unpriceable day is a visible gap rather than a
 *  straight segment drawn over it. The scatter carries only the days below full
 *  coverage that still have a value, so a stale stretch is visible without
 *  consulting a legend -- the same instinct as the MODELLED badge.
 *
 *  A `missing` day appears in neither: it has no y-coordinate, and the gap in
 *  the line is the honest way to say so.
 */
export function buildValueOption(
  points: readonly ValuationPoint[],
  config: ValueOptionConfig,
): EChartsOption {
  const marks: Array<[number, number]> = [];
  points.forEach((point, index) => {
    const value = toPlotValue(point.value_base);
    if (value !== null && BELOW_FULL.has(point.coverage)) marks.push([index, value]);
  });

  return {
    animation: false,
    grid: { left: 64, right: 16, top: 16, bottom: 44 },
    xAxis: {
      type: "category",
      data: points.map((point) => point.date),
      axisLine: { lineStyle: { color: c.borderSoft } },
      axisLabel: { color: c.textFaint, fontSize: 10 },
    },
    yAxis: {
      type: "value",
      scale: true,
      axisLabel: { color: c.textFaint, fontSize: 10 },
      splitLine: { lineStyle: { color: c.borderSoft } },
    },
    // Canvas plus dataZoom is why design doc 9.1 chose this library: a
    // five-year daily series is well past where SVG rendering degrades.
    dataZoom: [{ type: "inside" }, { type: "slider", height: 18, bottom: 8 }],
    tooltip: { trigger: "axis" },
    series: [
      {
        name: config.label,
        type: "line",
        showSymbol: false,
        connectNulls: false,
        data: points.map((point) => toPlotValue(point.value_base)),
        lineStyle: { color: c.accent, width: 1.5 },
      },
      {
        name: "Below full coverage",
        type: "scatter",
        symbolSize: 5,
        data: marks,
        itemStyle: { color: c.modelled },
      },
    ],
  };
}

/** One position's share of the portfolio, as a ratio string for `decimalPercent`.
 *
 *  The second and last place a money string becomes a number. A weight is a
 *  display ratio rounded to one decimal place, so float precision cannot reach
 *  the rendered figure -- unlike a cost basis, where it would.
 *
 *  `null` when the total is `null`: a share of a total that does not exist is
 *  not a smaller number, it is not a number. That is design doc 8.1 one level
 *  below the withheld total itself.
 */
export function positionWeight(value: string | null, total: string | null): string | null {
  if (value === null || total === null) return null;
  const denominator = Number(total);
  if (!denominator) return null;
  return String(Number(value) / denominator);
}
