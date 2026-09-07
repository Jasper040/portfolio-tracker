/** The only place in the app that imports ECharts.
 *
 *  Design doc 9.1 accepted an imperative options object in exchange for native
 *  candlesticks, declarative `markArea` bands, quantity-scaled `markPoint`s and
 *  canvas rendering with `dataZoom` -- and confined the cost to one wrapper so
 *  the rest of the app stays plain React.
 *
 *  Every decision about WHAT to draw lives in `lib/valuation.ts` as a pure
 *  function. This file only mounts, resizes and disposes, which is also why
 *  component tests can mock it away: jsdom implements no canvas, and a test that
 *  needed one would be testing the library rather than the screen.
 */

import { useEffect, useRef } from "react";
import * as echarts from "echarts";
import type { EChartsOption } from "echarts";

export interface EChartProps {
  option: EChartsOption;
  height?: number;
}

export function EChart({ option, height = 320 }: EChartProps) {
  const host = useRef<HTMLDivElement | null>(null);
  const chart = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    if (!host.current) return undefined;
    chart.current = echarts.init(host.current, undefined, { renderer: "canvas" });
    const resize = () => chart.current?.resize();
    window.addEventListener("resize", resize);
    return () => {
      window.removeEventListener("resize", resize);
      chart.current?.dispose();
      chart.current = null;
    };
  }, []);

  useEffect(() => {
    // `true` replaces the option rather than merging it. Merging would keep the
    // previous series' data alive under a new one, so switching from a five-year
    // range to a one-month one would leave the old tail on screen.
    chart.current?.setOption(option, true);
  }, [option]);

  return <div ref={host} style={{ width: "100%", height }} />;
}
