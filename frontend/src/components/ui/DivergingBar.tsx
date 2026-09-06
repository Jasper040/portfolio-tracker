/** A bar that grows left or right from a centre line.
 *
 *  Used for contribution-to-return and the What-If delta column. The centre tick
 *  is drawn slightly taller than the track so zero stays findable even when a row
 *  has no bar at all -- otherwise a contribution of exactly nothing renders as an
 *  empty cell that reads as missing data rather than as zero.
 *
 *  `value` and `max` are passed rather than a pre-computed width so the caller
 *  cannot accidentally scale two adjacent bars against different maxima.
 */

import { c } from "../../lib/theme";

export interface DivergingBarProps {
  value: number;
  /** Largest absolute value in the set. Half the track is given to it. */
  max: number;
  color: string;
  height?: number;
}

export function DivergingBar({ value, max, color, height = 8 }: DivergingBarProps) {
  const width = max > 0 ? (Math.abs(value) / max) * 50 : 0;
  const left = value < 0 ? 50 - width : 50;

  return (
    <span
      style={{
        height,
        background: c.inset,
        borderRadius: 2,
        position: "relative",
        display: "block",
      }}
    >
      <span
        style={{
          position: "absolute",
          top: 0,
          bottom: 0,
          left: `${left}%`,
          width: `${width}%`,
          background: color,
          borderRadius: 2,
        }}
      />
      <span
        style={{
          position: "absolute",
          top: -2,
          bottom: -2,
          left: "50%",
          width: 1,
          background: c.chipBorder,
        }}
      />
    </span>
  );
}

/** A plain left-anchored proportion bar, for withholding by country. */
export function ProportionBar({
  value,
  max,
  color,
  height = 8,
}: {
  value: number;
  max: number;
  color: string;
  height?: number;
}) {
  const width = max > 0 ? (value / max) * 100 : 0;
  return (
    <span
      style={{ height, background: c.inset, borderRadius: 2, position: "relative", display: "block" }}
    >
      <span
        style={{
          position: "absolute",
          top: 0,
          bottom: 0,
          left: 0,
          width: `${width}%`,
          background: color,
          borderRadius: 2,
        }}
      />
    </span>
  );
}
