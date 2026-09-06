/** Table primitives.
 *
 *  Seven of the nine screens are mostly tables, and they share one treatment:
 *  monospace uppercase headers on a slightly lighter ground, hairline row rules,
 *  right-aligned numerics. These wrappers exist so that treatment is described
 *  once. `Td` defaults to monospace-off and left-aligned, because prose columns
 *  are the ones where getting it wrong is most visible.
 *
 *  Numbers are ALWAYS monospace and right-aligned. That is not decoration: it is
 *  what lets a column of euro amounts be scanned for magnitude without reading
 *  any of them.
 */

import type { CSSProperties, ReactNode } from "react";
import { c, mono } from "../../lib/theme";

export type Align = "left" | "right";

export interface ColumnDef {
  label: string;
  align?: Align;
  /** Overrides the header colour. Used to mark the sorted column, and to tint
   *  the counterfactual column amber so it reads as modelled at a glance. */
  color?: string;
  onClick?: () => void;
}

/** Horizontally scrollable frame. Wide tables scroll inside their own box rather
 *  than widening the page, which is what keeps the sidebar and header still. */
export function TableFrame({
  children,
  style,
}: {
  children: ReactNode;
  style?: CSSProperties;
}) {
  return (
    <div
      style={{
        border: `1px solid ${c.border}`,
        borderRadius: 6,
        background: c.panel,
        overflowX: "auto",
        ...style,
      }}
    >
      {children}
    </div>
  );
}

export function Table({
  minWidth,
  children,
}: {
  minWidth?: number;
  children: ReactNode;
}) {
  return (
    <table
      style={{
        width: "100%",
        borderCollapse: "collapse",
        fontSize: 11.5,
        minWidth,
      }}
    >
      {children}
    </table>
  );
}

export function HeadRow({ columns }: { columns: readonly ColumnDef[] }) {
  return (
    <thead>
      <tr>
        {columns.map((col, i) => (
          <th
            key={`${col.label}-${i}`}
            onClick={col.onClick}
            aria-sort={col.color && col.onClick ? "descending" : undefined}
            style={{
              textAlign: col.align ?? "left",
              padding: "9px 11px",
              fontFamily: mono,
              fontSize: 9.5,
              letterSpacing: "0.05em",
              color: col.color ?? c.textFaint,
              fontWeight: 500,
              borderBottom: `1px solid ${c.borderStrong}`,
              whiteSpace: "nowrap",
              background: c.panelAlt,
              cursor: col.onClick ? "pointer" : undefined,
            }}
          >
            {col.label}
          </th>
        ))}
      </tr>
    </thead>
  );
}

/** A lighter header for the nested tables inside expanded rows. */
export function SubHeadRow({ columns }: { columns: readonly ColumnDef[] }) {
  return (
    <tr>
      {columns.map((col, i) => (
        <th
          key={`${col.label}-${i}`}
          style={{
            textAlign: col.align ?? "left",
            padding: "5px 9px",
            fontFamily: mono,
            fontSize: 9,
            color: c.textDim,
            fontWeight: 500,
            borderBottom: `1px solid ${c.border}`,
          }}
        >
          {col.label}
        </th>
      ))}
    </tr>
  );
}

export interface TdProps {
  align?: Align;
  color?: string;
  /** Monospace. Set for every numeric and identifier cell. */
  numeric?: boolean;
  nowrap?: boolean;
  colSpan?: number;
  padding?: string;
  children?: ReactNode;
  style?: CSSProperties;
}

export function Td({
  align = "left",
  color,
  numeric,
  nowrap,
  colSpan,
  padding = "9px 11px",
  children,
  style,
}: TdProps) {
  return (
    <td
      colSpan={colSpan}
      style={{
        padding,
        textAlign: align,
        fontFamily: numeric ? mono : undefined,
        color: color ?? c.textSecondary,
        whiteSpace: nowrap ? "nowrap" : undefined,
        ...style,
      }}
    >
      {children}
    </td>
  );
}

/** Zebra background for row `i`. Alternating rows rather than row borders alone
 *  because these tables run to eleven columns, and at that width a hairline is
 *  not enough to keep the eye on one row. */
export function rowBackground(i: number): string {
  return i % 2 ? c.panel : c.panelAlt;
}
