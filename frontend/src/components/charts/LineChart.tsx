/** The workhorse chart: multi-series lines over the shared monthly grid, with
 *  optional shaded bands, transaction markers and event dots.
 *
 *  Hand-rolled SVG rather than a charting library, for three reasons this design
 *  actually depends on:
 *
 *  1. A `null` in a series means "draw nothing here", not "treat as zero". That
 *     is what renders the Stock Detail chart as solid where a position was held
 *     and dotted where it was flat -- one instrument, two visual states, no
 *     second series needed.
 *  2. Markers scale with quantity and carry their own tooltip payload.
 *  3. Bands are declarative index ranges, so a holding period is `[from, to]`
 *     rather than a computed pixel rectangle.
 *
 *  It draws in a fixed 1000-unit viewBox and scales with CSS, so every chart in
 *  the app shares one coordinate system and nothing needs a resize observer.
 */

import { BASE_YEAR, MONTHS } from "../../lib/series";
import { c, mono } from "../../lib/theme";

export interface ChartLine {
  /** Length MONTHS. `null` breaks the line rather than dropping to zero. */
  values: readonly (number | null)[];
  color: string;
  width?: number;
  /** SVG stroke-dasharray, e.g. "5 4". */
  dash?: string;
  /** When set, the area under the line is filled with this colour. */
  fill?: string;
}

export interface ChartMarkerLine {
  k: string;
  v: string;
}

export interface ChartMarker {
  idx: number;
  value: number;
  /** 1 draws an up triangle (buy), -1 a down triangle (sell). */
  direction: 1 | -1;
  size?: number;
  title: string;
  lines: ChartMarkerLine[];
}

export interface ChartDot {
  idx: number;
  value: number;
  color?: string;
}

/** Inclusive-start, exclusive-end index range to shade. */
export type ChartBand = readonly [from: number, to: number];

/** Where the hovered marker sits, as a percentage of the chart box, plus the
 *  content to show. Percentages rather than pixels so the parent can position an
 *  HTML tooltip over a chart that scales with its container. */
export interface HoverPayload {
  leftPct: number;
  topPct: number;
  title: string;
  color: string;
  lines: ChartMarkerLine[];
}

export interface LineChartProps {
  lines: readonly ChartLine[];
  height?: number;
  /** First and last visible index. */
  from?: number;
  to?: number;
  bands?: readonly ChartBand[];
  bandFill?: string;
  markers?: readonly ChartMarker[];
  dots?: readonly ChartDot[];
  /** Formats the y-axis tick labels. */
  formatY?: (v: number) => string;
  padding?: Partial<{ l: number; r: number; t: number; b: number }>;
  onHover?: (payload: HoverPayload | null) => void;
}

const VIEW_W = 1000;

export function LineChart({
  lines,
  height = 220,
  from = 0,
  to = MONTHS - 1,
  bands = [],
  bandFill = "rgba(63,185,80,0.07)",
  markers = [],
  dots = [],
  formatY,
  padding,
  onHover,
}: LineChartProps) {
  const H = height;
  const pad = { l: 52, r: 12, t: 12, b: 22, ...padding };

  // Extent over VISIBLE points only, so zooming to 1Y rescales the axis instead
  // of leaving the series flattened against a decade-wide range.
  let lo = Infinity;
  let hi = -Infinity;
  for (const line of lines) {
    for (let i = from; i <= to; i++) {
      const v = line.values[i];
      if (v == null || !isFinite(v)) continue;
      if (v < lo) lo = v;
      if (v > hi) hi = v;
    }
  }
  if (!isFinite(lo)) {
    lo = 0;
    hi = 1;
  }
  if (hi === lo) hi = lo + 1;

  const dataLo = lo;
  const headroom = (hi - lo) * 0.1;
  lo -= headroom;
  hi += headroom;
  // Never let padding push a non-negative series below zero: a portfolio value
  // axis that starts at -3.000 invents a loss that never happened.
  if (dataLo >= 0) lo = Math.max(0, lo);

  const X = (i: number) => pad.l + ((i - from) / Math.max(1, to - from)) * (VIEW_W - pad.l - pad.r);
  const Y = (v: number) => pad.t + (1 - (v - lo) / (hi - lo)) * (H - pad.t - pad.b);

  const gridlines = [];
  for (let t = 0; t <= 4; t++) {
    const v = lo + ((hi - lo) * t) / 4;
    const y = Y(v);
    gridlines.push(
      <line key={`g${t}`} x1={pad.l} x2={VIEW_W - pad.r} y1={y} y2={y} stroke={c.grid} strokeWidth={1} />,
    );
    gridlines.push(
      <text
        key={`yl${t}`}
        x={pad.l - 8}
        y={y + 4}
        textAnchor="end"
        fill={c.textFaint}
        fontSize={11}
        fontFamily={mono}
      >
        {formatY ? formatY(v) : Math.round(v).toLocaleString("nl-NL")}
      </text>,
    );
  }

  const yearLabels = [];
  for (let i = Math.ceil(from / 12) * 12; i <= to; i += 12) {
    yearLabels.push(
      <text
        key={`x${i}`}
        x={X(i)}
        y={H - 5}
        textAnchor="middle"
        fill={c.textFaint}
        fontSize={11}
        fontFamily={mono}
      >
        {BASE_YEAR + i / 12}
      </text>,
    );
  }

  const bandRects = bands.map(([a, z], k) => {
    const start = Math.max(a, from);
    const end = Math.min(z, to);
    if (end <= start) return null;
    return (
      <rect
        key={`bd${k}`}
        x={X(start)}
        y={pad.t}
        width={X(end) - X(start)}
        height={H - pad.t - pad.b}
        fill={bandFill}
      />
    );
  });

  const paths = lines.flatMap((line, k) => {
    // Split into contiguous runs of real values. Each run becomes its own path,
    // which is what leaves a visible gap instead of a straight line jumping the
    // hole.
    const segments: [number, number][][] = [];
    let current: [number, number][] = [];
    for (let i = from; i <= to; i++) {
      const v = line.values[i];
      if (v == null || !isFinite(v)) {
        if (current.length) segments.push(current);
        current = [];
      } else {
        current.push([X(i), Y(v)]);
      }
    }
    if (current.length) segments.push(current);

    return segments.flatMap((seg, j) => {
      const first = seg[0];
      const last = seg[seg.length - 1];
      if (!first || !last) return [];
      const d = "M" + seg.map((p) => `${p[0].toFixed(1)},${p[1].toFixed(1)}`).join("L");
      const out = [];
      if (line.fill) {
        const baseline = Y(Math.max(lo, 0)).toFixed(1);
        out.push(
          <path
            key={`fl${k}-${j}`}
            d={`${d}L${last[0].toFixed(1)},${baseline}L${first[0].toFixed(1)},${baseline}Z`}
            fill={line.fill}
            stroke="none"
          />,
        );
      }
      out.push(
        <path
          key={`ln${k}-${j}`}
          d={d}
          fill="none"
          stroke={line.color}
          strokeWidth={line.width ?? 1.7}
          strokeDasharray={line.dash}
          strokeLinejoin="round"
          strokeLinecap="round"
        />,
      );
      return out;
    });
  });

  const markerNodes = markers.flatMap((m, k) => {
    if (m.idx < from || m.idx > to) return [];
    const x = X(m.idx);
    const y = Y(m.value);
    const z = m.size ?? 5;
    const d =
      m.direction > 0
        ? `M${x},${y - z * 1.15}L${x + z},${y + z * 0.75}L${x - z},${y + z * 0.75}Z`
        : `M${x},${y + z * 1.15}L${x + z},${y - z * 0.75}L${x - z},${y - z * 0.75}Z`;
    const color = m.direction > 0 ? c.positive : c.negative;
    return [
      <path key={`mk${k}`} d={d} fill={color} stroke={c.bg} strokeWidth={1.2} />,
      // A transparent 22x22 hit target: the triangle itself can be 8px across,
      // which is far too small to hover reliably.
      <rect
        key={`mh${k}`}
        x={x - 11}
        y={y - 11}
        width={22}
        height={22}
        fill="rgba(0,0,0,0)"
        style={{ cursor: "pointer" }}
        onMouseEnter={() =>
          onHover?.({
            leftPct: (x / VIEW_W) * 100,
            topPct: (y / H) * 100,
            title: m.title,
            color,
            lines: m.lines,
          })
        }
        onMouseLeave={() => onHover?.(null)}
      />,
    ];
  });

  const dotNodes = dots.map((p, k) =>
    p.idx < from || p.idx > to ? null : (
      <circle
        key={`dt${k}`}
        cx={X(p.idx)}
        cy={Y(p.value)}
        r={2.6}
        fill={c.bg}
        stroke={p.color ?? c.modelled}
        strokeWidth={1.4}
      />
    ),
  );

  return (
    <svg
      viewBox={`0 0 ${VIEW_W} ${H}`}
      style={{ width: "100%", height: "auto", display: "block" }}
      role="img"
    >
      {gridlines}
      {yearLabels}
      {bandRects}
      {paths}
      {dotNodes}
      {markerNodes}
    </svg>
  );
}
