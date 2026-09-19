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
import { eur, shortDate } from "./format";
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

/** What the marker series is called, in the coverage strip's own words.
 *
 *  It read "Below full coverage" until PT-48, which is accurate and meant
 *  nothing to the operator who hovered it. A `partial` day is one whose price
 *  was carried forward from an earlier day; a `manual` day is one whose price
 *  was typed in. "Stale or hand-supplied" is exactly how `CoverageStrip` names
 *  those two, and having the chart and the sentence under it use one vocabulary
 *  is the whole fix.
 *
 *  Exported so the tooltip and the tests name it from one place.
 */
export const BELOW_FULL_SERIES = "Stale or hand-supplied price";

/** The subset of an ECharts tooltip callback param this app reads.
 *
 *  Declared locally rather than imported: ECharts types `value` as a broad
 *  union that would need narrowing anyway, and the three fields below are all
 *  a category-axis tooltip ever needs.
 */
export interface TooltipParam {
  axisValueLabel?: string;
  seriesName?: string;
  /** A number for the line series, `[index, value]` for the scatter. */
  value?: unknown;
}

/** A tooltip param's y, whichever shape the series stores.
 *
 *  The line holds `number | null` directly; the marker scatter holds
 *  `[index, value]` because it is plotted against a category index. */
function plottedY(value: unknown): number | null {
  if (typeof value === "number") return value;
  if (Array.isArray(value) && typeof value[1] === "number") return value[1];
  return null;
}

/** The value chart's tooltip (PT-47, PT-48).
 *
 *  Two jobs. It rounds to the cent -- ECharts' default prints the raw float,
 *  and a point is `quantity x price x fx_rate`, which reaches a dozen fraction
 *  digits long before it reaches the screen. And it says what the coverage
 *  marker means rather than repeating the line's own number underneath it,
 *  which is what the marker was silently doing.
 *
 *  Formatting a float here does not undo what `decimal` protects. The chart's
 *  data is already past `toPlotValue`, the one deliberate `Number()` boundary
 *  in this file; this is a pixel-space value being labelled, not a ledger
 *  figure being displayed. Every figure in the TABLE still goes through
 *  `decimalEur` on the original string.
 */
export function valueTooltip(params: TooltipParam | readonly TooltipParam[]): string {
  const rows = Array.isArray(params) ? params : [params as TooltipParam];
  const first = rows[0];
  if (first === undefined) return "";

  const day = first.axisValueLabel ?? "";
  const lines = rows.map((row) => {
    // The marker sits on the same y as the line. Printing it again would read
    // as a second measurement of the same day.
    if (row.seriesName === BELOW_FULL_SERIES) return BELOW_FULL_SERIES;
    const y = plottedY(row.value);
    // An em dash, never "€ 0,00": design doc 8.1 forbids a zero that would read
    // as a portfolio which lost everything.
    return `${row.seriesName ?? ""} ${y === null ? "—" : eur(y)}`;
  });

  return [day ? shortDate(day) : "", ...lines].filter(Boolean).join("<br/>");
}

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
    tooltip: { trigger: "axis", formatter: valueTooltip },
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
        name: BELOW_FULL_SERIES,
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
