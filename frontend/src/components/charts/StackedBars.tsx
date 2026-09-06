/** Stacked bars for dividends by quarter.
 *
 *  The one non-obvious feature is `estimateFrom`: bars at or beyond that index
 *  are forward estimates rather than received payments, and are drawn hatched
 *  and semi-transparent. Estimates and facts sit on the same axis because that
 *  is the useful comparison, so they must be impossible to confuse at a glance --
 *  a legend note alone would not survive a screenshot.
 */

import { c, mono } from "../../lib/theme";

/** A bar group: `label` plus one numeric field per stack key. */
export type BarGroup = { label?: string } & Record<string, number | string | undefined>;

export interface StackedBarsProps {
  groups: readonly BarGroup[];
  /** Stack order, bottom to top. */
  keys: readonly string[];
  /** One colour per key, index-aligned. */
  colors: readonly string[];
  /** Groups from this index onward render as hatched estimates. Pass
   *  `groups.length` to mark none. */
  estimateFrom: number;
  height?: number;
}

const VIEW_W = 1000;

export function StackedBars({
  groups,
  keys,
  colors,
  estimateFrom,
  height = 200,
}: StackedBarsProps) {
  const H = height;
  const pad = { l: 52, r: 12, t: 12, b: 26 };

  const totals = groups.map((g) =>
    keys.reduce((a, k) => a + (typeof g[k] === "number" ? (g[k] as number) : 0), 0),
  );
  const max = Math.max(...totals, 0) || 1;
  const bandWidth = (VIEW_W - pad.l - pad.r) / Math.max(1, groups.length);
  const plotH = H - pad.t - pad.b;

  const grid = [];
  for (let t = 0; t <= 4; t++) {
    const v = (max * t) / 4;
    const y = pad.t + (1 - t / 4) * plotH;
    grid.push(<line key={`g${t}`} x1={pad.l} x2={VIEW_W - pad.r} y1={y} y2={y} stroke={c.grid} />);
    grid.push(
      <text key={`y${t}`} x={pad.l - 8} y={y + 4} textAnchor="end" fill={c.textFaint} fontSize={11} fontFamily={mono}>
        {Math.round(v).toLocaleString("nl-NL")}
      </text>,
    );
  }

  const bars = groups.flatMap((g, gi) => {
    const isEstimate = gi >= estimateFrom;
    let acc = 0;
    const nodes = keys.flatMap((k, ki) => {
      const raw = g[k];
      const v = typeof raw === "number" ? raw : 0;
      if (v <= 0) return [];
      const h = (v / max) * plotH;
      const y = pad.t + plotH - ((acc + v) / max) * plotH;
      acc += v;
      return [
        <rect
          key={`b${gi}-${ki}`}
          x={pad.l + gi * bandWidth + bandWidth * 0.16}
          y={y}
          width={bandWidth * 0.68}
          height={Math.max(1, h)}
          fill={isEstimate ? "url(#dividend-hatch)" : (colors[ki] ?? c.textMuted)}
          opacity={isEstimate ? 0.55 : 1}
        />,
      ];
    });

    if (g.label) {
      nodes.push(
        <text
          key={`x${gi}`}
          x={pad.l + gi * bandWidth + bandWidth / 2}
          y={H - 7}
          textAnchor="middle"
          fill={c.textFaint}
          fontSize={10.5}
          fontFamily={mono}
        >
          {g.label}
        </text>,
      );
    }
    return nodes;
  });

  return (
    <svg viewBox={`0 0 ${VIEW_W} ${H}`} style={{ width: "100%", height: "auto", display: "block" }} role="img">
      <defs>
        <pattern id="dividend-hatch" patternUnits="userSpaceOnUse" width={5} height={5} patternTransform="rotate(45)">
          <line x1={0} y1={0} x2={0} y2={5} stroke={c.positive} strokeWidth={2} />
        </pattern>
      </defs>
      {grid}
      {bars}
    </svg>
  );
}
