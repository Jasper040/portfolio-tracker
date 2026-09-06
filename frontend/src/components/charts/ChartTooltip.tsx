/** The hover card for a chart marker.
 *
 *  Rendered as HTML positioned over the SVG rather than as SVG text, for two
 *  reasons: it must not be clipped by the chart's viewBox, and it needs real text
 *  layout for the key/value rows. `pointer-events: none` keeps it from stealing
 *  the hover that is keeping it open -- without that, moving onto the tooltip
 *  fires the marker's `mouseleave` and it flickers.
 *
 *  The parent must be `position: relative`.
 */

import { c, mono } from "../../lib/theme";
import type { HoverPayload } from "./LineChart";

export function ChartTooltip({ hover }: { hover: HoverPayload | null }) {
  if (!hover) return null;

  return (
    <div
      style={{
        position: "absolute",
        left: `${hover.leftPct}%`,
        top: `${hover.topPct}%`,
        transform: "translate(-50%, -115%)",
        pointerEvents: "none",
        background: "#191C21",
        border: `1px solid ${c.chipBorder}`,
        borderRadius: 4,
        padding: "8px 10px",
        boxShadow: "0 6px 20px #00000080",
        zIndex: 5,
        whiteSpace: "nowrap",
      }}
      role="tooltip"
    >
      <div
        style={{
          fontFamily: mono,
          fontSize: 10,
          color: hover.color,
          letterSpacing: "0.04em",
          marginBottom: 5,
        }}
      >
        {hover.title}
      </div>
      {hover.lines.map((l) => (
        <div
          key={l.k}
          style={{ display: "flex", gap: 12, fontFamily: mono, fontSize: 10.5, lineHeight: 1.7 }}
        >
          <span style={{ color: c.textFaint }}>{l.k}</span>
          <span style={{ marginLeft: "auto", color: c.text }}>{l.v}</span>
        </div>
      ))}
    </div>
  );
}
