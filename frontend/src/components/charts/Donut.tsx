/** Allocation donut.
 *
 *  Drawn as a single circle per slice using `stroke-dasharray` rather than arc
 *  paths: one dash of the slice's arc length, one gap of the remainder, rotated
 *  into place by an accumulating `stroke-dashoffset`. That avoids arc-flag
 *  trigonometry entirely, and a slice at 99.9% still renders correctly, which is
 *  exactly where hand-written arc paths tend to flip inside out.
 */

export interface DonutSlice {
  label: string;
  value: number;
  color: string;
}

export interface DonutProps {
  slices: readonly DonutSlice[];
  /** Rendered in the middle. Usually the total. */
  centre?: string;
  size?: number;
}

const R = 52;
const CX = 70;
const CY = 70;

export function Donut({ slices, centre, size = 132 }: DonutProps) {
  const circumference = 2 * Math.PI * R;
  // Guard the divisor, not the caller: an empty portfolio should render an empty
  // ring, not NaN-length dashes that make the whole SVG vanish.
  const total = slices.reduce((a, s) => a + s.value, 0) || 1;

  let offset = 0;
  const arcs = slices.map((s, k) => {
    const length = (s.value / total) * circumference;
    const node = (
      <circle
        key={k}
        cx={CX}
        cy={CY}
        r={R}
        fill="none"
        stroke={s.color}
        strokeWidth={15}
        strokeDasharray={`${length.toFixed(2)} ${(circumference - length).toFixed(2)}`}
        strokeDashoffset={-offset}
        transform={`rotate(-90 ${CX} ${CY})`}
      />
    );
    offset += length;
    return node;
  });

  return (
    <svg
      viewBox="0 0 140 140"
      style={{ width: size, height: size, display: "block" }}
      role="img"
    >
      {arcs}
      {centre && (
        <text
          x={CX}
          y={CY + 5}
          textAnchor="middle"
          fill="#B8BFC7"
          fontSize={14}
          fontFamily="'IBM Plex Mono', monospace"
        >
          {centre}
        </text>
      )}
    </svg>
  );
}
