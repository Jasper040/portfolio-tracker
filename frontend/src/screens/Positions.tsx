/** What I hold right now, and the lots underneath it.
 *
 *  A position is a summary of its lots, and the lots are where the cost basis
 *  actually lives, so the row expands into them rather than sending you to
 *  another screen. Clicking the row expands; clicking the instrument name
 *  navigates -- two targets in one row, which is why the hint sits above the
 *  table rather than being left to discovery.
 */

import { Fragment, useState } from "react";
import {
  HeadRow,
  SubHeadRow,
  Table,
  TableFrame,
  Td,
  rowBackground,
  type ColumnDef,
} from "../components/ui/Table";
import { Pill } from "../components/ui/Controls";
import { c, mono, paletteAt } from "../lib/theme";
import { daysBetween, eur, eurSigned, num, pct, pctPlain, signColor } from "../lib/format";
import { TODAY } from "../lib/series";
import type { Aggregates, PositionRow } from "../portfolio/aggregate";
import type { LotMethod } from "../lib/lots";

type SortKey =
  | "name" | "qty" | "avg" | "px" | "day" | "value" | "pnl" | "pnlPct" | "weight" | "sector" | "ccy";

const COLUMNS: readonly { key: SortKey; label: string; align: "left" | "right" }[] = [
  { key: "name", label: "INSTRUMENT", align: "left" },
  { key: "qty", label: "QTY", align: "right" },
  { key: "avg", label: "AVG COST", align: "right" },
  { key: "px", label: "PRICE", align: "right" },
  { key: "day", label: "DAY", align: "right" },
  { key: "value", label: "MARKET VALUE", align: "right" },
  { key: "pnl", label: "UNREALISED", align: "right" },
  { key: "pnlPct", label: "%", align: "right" },
  { key: "weight", label: "WEIGHT", align: "right" },
  { key: "sector", label: "SECTOR", align: "left" },
  { key: "ccy", label: "CCY", align: "right" },
];

const LOT_COLUMNS: readonly ColumnDef[] = [
  { label: "LOT" },
  { label: "OPENED" },
  { label: "QTY", align: "right" },
  { label: "COST/SH", align: "right" },
  { label: "VALUE", align: "right" },
  { label: "UNREAL.", align: "right" },
  { label: "HELD", align: "right" },
];

/** Sort accessors. Strings and numbers are both possible, so the comparator
 *  below branches on the returned type rather than assuming numeric. */
const ACCESSORS: Record<SortKey, (r: PositionRow) => string | number> = {
  name: (r) => r.instrument.name,
  qty: (r) => r.match.qty,
  avg: (r) => (r.match.qty ? r.match.cost / r.match.qty : 0),
  px: (r) => r.instrument.current,
  day: (r) => r.dayChange,
  value: (r) => r.value,
  pnl: (r) => r.pnl,
  pnlPct: (r) => r.pnlPct,
  weight: (r) => r.value,
  sector: (r) => r.instrument.sector,
  ccy: (r) => r.instrument.currency,
};

export interface PositionsProps {
  agg: Aggregates;
  method: LotMethod;
  instrumentIndex: Map<string, number>;
  onOpenInstrument: (isin: string) => void;
}

export function Positions({ agg, method, instrumentIndex, onOpenInstrument }: PositionsProps) {
  const [sortKey, setSortKey] = useState<SortKey>("value");
  const [sortDir, setSortDir] = useState<1 | -1>(-1);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [aggregated, setAggregated] = useState(true);

  const sorted = [...agg.held].sort((a, b) => {
    const x = ACCESSORS[sortKey](a);
    const y = ACCESSORS[sortKey](b);
    const cmp = typeof x === "string" ? x.localeCompare(String(y)) : x - Number(y);
    return cmp * sortDir;
  });

  const columns: ColumnDef[] = COLUMNS.map((col) => ({
    label: col.label,
    align: col.align,
    color: sortKey === col.key ? c.modelled : undefined,
    onClick: () => {
      // Re-clicking the active column flips direction; a new column starts
      // descending, because for every column here the interesting end is the top.
      setSortDir((d) => (sortKey === col.key ? ((d * -1) as 1 | -1) : -1));
      setSortKey(col.key);
    },
  }));

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
        <Pill active={aggregated} onClick={() => setAggregated((v) => !v)}>
          {aggregated ? "Aggregated across accounts" : "Split by account"}
        </Pill>
        <span style={{ fontSize: 11, color: c.textFaint }}>
          {agg.held.length} open positions · {agg.rows.length} instruments ever held
        </span>
        <span style={{ marginLeft: "auto", fontSize: 10.5, color: c.textFaint }}>
          Click a row to expand its open lots · click the name for Stock Detail
        </span>
      </div>

      <TableFrame>
        <Table minWidth={1060}>
          <HeadRow columns={columns} />
          <tbody>
            {sorted.map((row, i) => {
              const key = row.instrument.isin;
              const isOpen = expanded[key] ?? false;
              const avgCost = row.match.qty ? row.match.cost / row.match.qty : 0;
              return (
                <Fragment key={key}>
                  {/* A Fragment, not a wrapper element: this renders two sibling
                      <tr>s and a <tbody> may only contain <tr> directly -- the
                      same constraint documented in the original TransactionTable. */}
                  <tr
                    onClick={() => setExpanded((s) => ({ ...s, [key]: !s[key] }))}
                    style={{
                      cursor: "pointer",
                      background: rowBackground(i),
                      borderBottom: `1px solid ${c.borderSoft}`,
                    }}
                  >
                    <Td>
                      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                        <span
                          style={{
                            width: 3,
                            height: 20,
                            borderRadius: 2,
                            background: paletteAt(instrumentIndex.get(key) ?? 0),
                            flex: "0 0 auto",
                          }}
                        />
                        <span>
                          <button
                            type="button"
                            onClick={(e) => {
                              // Without this the row's own handler also fires and
                              // the position expands on its way to another screen.
                              e.stopPropagation();
                              onOpenInstrument(key);
                            }}
                            style={{
                              all: "unset",
                              cursor: "pointer",
                              display: "block",
                              color: c.text,
                              fontWeight: 500,
                            }}
                          >
                            {row.instrument.name}
                          </button>
                          <span
                            style={{
                              display: "block",
                              fontFamily: mono,
                              fontSize: 9.5,
                              color: c.textFaint,
                              marginTop: 2,
                            }}
                          >
                            {row.instrument.symbol} · {key} · DeGiro {row.instrument.account}
                          </span>
                        </span>
                      </div>
                    </Td>
                    <Td align="right" numeric>{num(row.match.qty, 0)}</Td>
                    <Td align="right" numeric color={c.textMuted}>{eur(avgCost)}</Td>
                    <Td align="right" numeric>{eur(row.instrument.current)}</Td>
                    <Td align="right" numeric color={signColor(row.dayChange)}>{pct(row.dayChange)}</Td>
                    <Td align="right" numeric color={c.text}>{eur(row.value, 0)}</Td>
                    <Td align="right" numeric color={signColor(row.pnl)}>{eurSigned(row.pnl)}</Td>
                    <Td align="right" numeric color={signColor(row.pnl)}>{pct(row.pnlPct)}</Td>
                    <Td align="right" numeric color={c.textMuted}>
                      {pctPlain(agg.totalValue > 0 ? row.value / agg.totalValue : 0)}
                    </Td>
                    <Td color={c.textMuted} nowrap>
                      {row.instrument.sector} / {row.instrument.industry}
                    </Td>
                    <Td align="right" numeric color={c.textFaint}>{row.instrument.currency}</Td>
                  </tr>

                  {isOpen && (
                    <tr style={{ background: c.sunken }}>
                      <td colSpan={COLUMNS.length} style={{ padding: "0 11px 14px 26px" }}>
                        <div
                          style={{
                            fontFamily: mono,
                            fontSize: 9.5,
                            letterSpacing: "0.05em",
                            color: c.textFaint,
                            padding: "10px 0 8px",
                          }}
                        >
                          OPEN LOTS — {row.instrument.symbol} · {method}
                        </div>
                        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
                          <tbody>
                            <SubHeadRow columns={LOT_COLUMNS} />
                            {row.match.open.map((lot) => {
                              const lotPnl =
                                lot.qty * (row.instrument.current - lot.price) - lot.fees;
                              return (
                                <tr key={lot.id}>
                                  <Td padding="6px 9px" numeric color={c.textMuted}>{lot.id}</Td>
                                  <Td padding="6px 9px" numeric>{lot.date}</Td>
                                  <Td padding="6px 9px" align="right" numeric>{num(lot.qty, 0)}</Td>
                                  <Td padding="6px 9px" align="right" numeric color={c.textMuted}>
                                    {eur(lot.price)}
                                  </Td>
                                  <Td padding="6px 9px" align="right" numeric>
                                    {eur(lot.qty * row.instrument.current, 0)}
                                  </Td>
                                  <Td
                                    padding="6px 9px"
                                    align="right"
                                    numeric
                                    color={signColor(row.instrument.current - lot.price)}
                                  >
                                    {eurSigned(lotPnl)}
                                  </Td>
                                  <Td padding="6px 9px" align="right" numeric color={c.textFaint}>
                                    {num(daysBetween(lot.date, TODAY), 0)}d
                                  </Td>
                                </tr>
                              );
                            })}
                          </tbody>
                        </table>
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
          <tfoot>
            <tr style={{ background: c.panelAlt, borderTop: `1px solid ${c.borderStrong}` }}>
              <Td padding="10px 11px" style={{ fontSize: 11, fontWeight: 600, color: c.text }}>
                Total · reconciles to Dashboard
              </Td>
              <td />
              <td />
              <td />
              <Td padding="10px 11px" align="right" numeric color={signColor(agg.dayChange)}>
                {pct(agg.dayChange)}
              </Td>
              <Td padding="10px 11px" align="right" numeric color={c.text}>
                {eur(agg.totalValue, 0)}
              </Td>
              <Td padding="10px 11px" align="right" numeric color={signColor(agg.totalUnrealised)}>
                {eurSigned(agg.totalUnrealised)}
              </Td>
              <Td padding="10px 11px" align="right" numeric color={signColor(agg.totalUnrealised)}>
                {pct(agg.totalCost > 0 ? agg.totalUnrealised / agg.totalCost : 0)}
              </Td>
              <Td padding="10px 11px" align="right" numeric color={c.textMuted}>
                100,0%
              </Td>
              <td />
              <td />
            </tr>
          </tfoot>
        </Table>
      </TableFrame>
    </div>
  );
}
