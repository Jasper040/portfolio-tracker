/** The hairline-separated tile grids used for KPIs and stat strips.
 *
 *  The 1px gaps are the grid's own background showing through, not borders on
 *  the tiles. That is what keeps the dividers exactly 1px at every viewport
 *  width -- adjacent 1px borders would double to 2px, and `border-collapse` does
 *  not exist outside tables.
 */

import type { ReactNode } from "react";
import { c, mono } from "../../lib/theme";

export interface Tile {
  label: string;
  value: ReactNode;
  /** Optional third line, for context the value alone does not carry. */
  sub?: ReactNode;
  color?: string;
}

export interface TileGridProps {
  tiles: readonly Tile[];
  /** Minimum tile width before the grid reflows. Ignored when `columns` is set. */
  minWidth?: number;
  /** Font size of the value line. KPI rows use 19, inline stat strips 14. */
  valueSize?: number;
  background?: string;
  /** Pin the grid to a fixed column count instead of letting it auto-fit.
   *
   *  `auto-fit` derives its column count from the available width, which is fine
   *  until the tile count does not divide by it: eight KPIs in seven columns
   *  leaves one tile alone on row two beside six empty cells, and because the
   *  dividers are the container background showing through, that gap reads as one
   *  large empty tile rather than as absence.
   *
   *  A fixed 4 divides 8 exactly at every breakpoint it steps down through
   *  (4 -> 2 -> 1), so the grid is always full. The steps are media queries, which
   *  inline styles cannot express, so they live in `styles.css`. */
  columns?: 4;
}

export function TileGrid({
  tiles,
  minWidth = 158,
  valueSize = 19,
  background = c.panel,
  columns,
}: TileGridProps) {
  return (
    <div
      className={columns === 4 ? "tile-grid-4" : undefined}
      style={{
        display: "grid",
        // Omitted entirely when `columns` is set, so the stylesheet's
        // `grid-template-columns` is not fighting an inline declaration it can
        // never win against.
        ...(columns ? {} : { gridTemplateColumns: `repeat(auto-fit, minmax(${minWidth}px, 1fr))` }),
        gap: 1,
        background: c.border,
        border: `1px solid ${c.border}`,
        borderRadius: 6,
        overflow: "hidden",
      }}
    >
      {tiles.map((t) => (
        <div key={t.label} style={{ background, padding: "12px 14px 13px" }}>
          <div
            style={{
              fontFamily: mono,
              fontSize: 9.5,
              letterSpacing: "0.06em",
              color: c.textFaint,
            }}
          >
            {t.label}
          </div>
          <div
            style={{
              fontFamily: mono,
              fontSize: valueSize,
              fontWeight: 500,
              marginTop: 7,
              color: t.color ?? c.text,
              letterSpacing: "-0.02em",
            }}
          >
            {t.value}
          </div>
          {t.sub != null && (
            <div style={{ fontSize: 10.5, color: c.textFaint, marginTop: 4 }}>{t.sub}</div>
          )}
        </div>
      ))}
    </div>
  );
}

/** A compact horizontal strip of the same tiles, for inside a panel. */
export function TileStrip({ tiles }: { tiles: readonly Tile[] }) {
  return (
    <div
      style={{
        display: "flex",
        gap: 1,
        background: c.border,
        borderRadius: 3,
        overflow: "hidden",
      }}
    >
      {tiles.map((t) => (
        <div key={t.label} style={{ flex: 1, background: c.inset, padding: "9px 11px", minWidth: 0 }}>
          <div style={{ fontFamily: mono, fontSize: 9.5, color: c.textFaint, letterSpacing: "0.05em" }}>
            {t.label}
          </div>
          <div style={{ fontFamily: mono, fontSize: 14, color: t.color ?? c.textSecondary, marginTop: 4 }}>
            {t.value}
          </div>
        </div>
      ))}
    </div>
  );
}
