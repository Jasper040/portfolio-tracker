/** Holding return against its industry proxy's return.
 *
 *  Both axes share one scale and a 45-degree reference line is drawn across it,
 *  so "did this holding beat its sector" is answered by which side of the
 *  diagonal a point falls on -- no reading of two numbers and subtracting. Points
 *  are coloured by the same test, so the answer survives a black-and-white print
 *  of the screen only as position, which is why the diagonal is drawn at all.
 */

import { c, mono } from "../../lib/theme";

export interface ScatterPoint {
  label: string;
  /** Industry proxy return over the window. */
  x: number;
  /** This holding's return over the same window. */
  y: number;
}

export interface ScatterProps {
  points: readonly ScatterPoint[];
}

const W = 520;
const H = 300;

export function Scatter({ points }: ScatterProps) {
  const pad = { l: 48, r: 14, t: 14, b: 34 };

  const all = points.flatMap((p) => [p.x, p.y]);
  let lo = all.length ? Math.min(...all) : 0;
  let hi = all.length ? Math.max(...all) : 1;
  if (hi === lo) hi = lo + 1;
  const headroom = (hi - lo) * 0.12;
  lo -= headroom;
  hi += headroom;

  const X = (v: number) => pad.l + ((v - lo) / (hi - lo)) * (W - pad.l - pad.r);
  const Y = (v: number) => pad.t + (1 - (v - lo) / (hi - lo)) * (H - pad.t - pad.b);

  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", height: "auto", display: "block" }} role="img">
      <line x1={X(lo)} y1={Y(lo)} x2={X(hi)} y2={Y(hi)} stroke={c.chipBorder} strokeDasharray="4 4" />
      <line x1={pad.l} x2={W - pad.r} y1={Y(0)} y2={Y(0)} stroke={c.grid} />
      <line x1={X(0)} x2={X(0)} y1={pad.t} y2={H - pad.b} stroke={c.grid} />
      <text x={W / 2} y={H - 6} textAnchor="middle" fill={c.textFaint} fontSize={11}>
        industry proxy return
      </text>
      <text
        x={12}
        y={H / 2}
        textAnchor="middle"
        fill={c.textFaint}
        fontSize={11}
        transform={`rotate(-90 12 ${H / 2})`}
      >
        my holding return
      </text>
      {points.map((p, k) => {
        const beat = p.y > p.x;
        return (
          <g key={k}>
            <circle
              cx={X(p.x)}
              cy={Y(p.y)}
              r={5}
              fill={beat ? "#3FB95055" : "#E5534B55"}
              stroke={beat ? c.positive : c.negative}
              strokeWidth={1.4}
            />
            <text x={X(p.x) + 9} y={Y(p.y) + 4} fill={c.textMuted} fontSize={10.5} fontFamily={mono}>
              {p.label}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
