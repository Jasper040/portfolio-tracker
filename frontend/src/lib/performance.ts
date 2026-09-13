/** Everything the performance chart decides before a pixel is drawn.
 *
 *  Pure, and separate from the ECharts wrapper, for the reason `lib/valuation.ts`
 *  gives: jsdom has no canvas, but it can assert on the option object, and that
 *  object is where every decision worth testing lives.
 *
 *  Both lines are INDICES at 100 on each run's first close, exactly as the API
 *  sends them. A day between runs has no point, so it is `null` on the axis and
 *  the line breaks there instead of being drawn straight across days nobody could
 *  measure -- the chart's version of M6a-7's "no single figure spans a gap".
 *
 *  `indexOnDates` is this file's one numeric boundary, kept local rather than
 *  imported for the reason `lib/instrument.ts` gives: the place a string becomes
 *  a number should be visible without following an import.
 */

import type { EChartsOption } from "echarts";

import type { IndexPoint, PerformanceReport } from "../api/types";
import { c } from "./theme";

/** The portfolio line's legend name. */
export const PORTFOLIO_SERIES = "Portfolio (time-weighted)";

/** What the single y-axis measures. Both lines are the same kind of number, so
 *  they share it -- unlike the instrument chart, which puts a price on one axis
 *  and an index on another and has to say so. */
export const INDEX_AXIS_NAME = "index, 100 = run start";

/** The drawn axis: the window's first close, then every day with a link. */
export function performanceDates(report: PerformanceReport): string[] {
  if (report.start === null) return [];
  return [report.start, ...report.links.map((link) => link.date)];
}

/** An index onto a fixed axis, by date and never by position. A day with no
 *  point -- a gap, or a benchmark holiday -- stays `null`, so the line breaks
 *  rather than dropping to a zero that would read as a total loss. */
export function indexOnDates(
  dates: readonly string[],
  index: readonly IndexPoint[],
): Array<number | null> {
  const byDate = new Map(index.map((point) => [point.date, Number(point.index)]));
  return dates.map((date) => byDate.get(date) ?? null);
}

const DIVIDEND_TREATMENT: Readonly<Record<string, string>> = {
  held_as_cash_net_of_withholding: "dividends land in cash after withholding and stay there",
  reinvested_gross: "dividends are reinvested, before withholding",
};

/** The API's dividend label, in words (M6a-10). An unrecognised code is shown as
 *  it arrived: a raw string is more debuggable than a confident wrong sentence. */
export function dividendTreatment(code: string): string {
  return DIVIDEND_TREATMENT[code] ?? code;
}

export interface PerformanceChartConfig {
  /** The benchmark's display name, when the list has one for the key. */
  benchmarkLabel: string | null;
}

export function performanceChartOption(
  report: PerformanceReport,
  config: PerformanceChartConfig,
): EChartsOption {
  const dates = performanceDates(report);
  const comparison = report.comparison;
  const benchmarkName =
    comparison === null ? null : (config.benchmarkLabel ?? comparison.benchmark_key);

  const portfolioSeries = {
    name: PORTFOLIO_SERIES,
    type: "line" as const,
    showSymbol: false,
    connectNulls: false,
    data: indexOnDates(dates, report.portfolio_index),
    lineStyle: { color: c.accent, width: 1.6 },
  };

  return {
    animation: false,
    grid: { left: 56, right: 16, top: 34, bottom: 44 },
    legend: {
      data: benchmarkName === null ? [PORTFOLIO_SERIES] : [PORTFOLIO_SERIES, benchmarkName],
      top: 0,
      left: 0,
      itemGap: 14,
      icon: "roundRect",
      itemWidth: 14,
      itemHeight: 2,
      textStyle: { color: c.textFaint, fontSize: 10 },
    },
    xAxis: {
      type: "category",
      data: dates,
      axisLine: { lineStyle: { color: c.borderSoft } },
      axisLabel: { color: c.textFaint, fontSize: 10 },
    },
    yAxis: {
      type: "value",
      scale: true,
      name: INDEX_AXIS_NAME,
      nameLocation: "end" as const,
      nameGap: 12,
      nameTextStyle: { color: c.textFaint, fontSize: 9.5, align: "left" as const },
      axisLabel: { color: c.textFaint, fontSize: 10 },
      splitLine: { lineStyle: { color: c.borderSoft } },
    },
    dataZoom: [{ type: "inside" }, { type: "slider", height: 18, bottom: 8 }],
    tooltip: { trigger: "axis" },
    series: [
      portfolioSeries,
      // Appended only when a comparison exists -- never pushed and hidden,
      // because a hidden series still takes part in axis scaling and tooltips.
      ...(comparison !== null && benchmarkName !== null
        ? [
            {
              name: benchmarkName,
              type: "line" as const,
              showSymbol: false,
              connectNulls: false,
              data: indexOnDates(dates, comparison.benchmark_index),
              lineStyle: { color: c.neutral, width: 1.5, type: "dashed" as const },
            },
          ]
        : []),
    ],
  };
}
