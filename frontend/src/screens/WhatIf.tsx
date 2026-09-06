/** The counterfactual, in both directions.
 *
 *  This screen is the reason `lib/counterfactual.ts` names its policy in the type
 *  system. Every number here is modelled under NAIVE_HOLD -- the sold shares are
 *  valued at today's price and the proceeds are ignored -- which overstates the
 *  case whenever a sale funded a later purchase. That caveat is repeated on the
 *  screen rather than left to documentation, because these are the numbers most
 *  likely to be quoted out of context.
 *
 *  Showing both directions is the point. A tool that only surfaced sales that
 *  went on to rise would be an engine for regret, not for judgement.
 */

import { LineChart } from "../components/charts/LineChart";
import { Panel } from "../components/ui/Panel";
import { SeriesChip } from "../components/ui/Controls";
import { DivergingBar } from "../components/ui/DivergingBar";
import { HeadRow, Table, Td, type ColumnDef } from "../components/ui/Table";
import { c, mono } from "../lib/theme";
import { eur, eurCompact, eurSigned, num, shortDate, signColor, costColor } from "../lib/format";
import { monthIndex } from "../lib/series";
import type { PortfolioData } from "../portfolio/provider";
import type { Aggregates } from "../portfolio/aggregate";
import type { LotMethod } from "../lib/lots";

const COLUMNS: readonly ColumnDef[] = [
  { label: "INSTRUMENT" },
  { label: "SOLD" },
  { label: "QTY", align: "right" },
  { label: "SOLD AT", align: "right" },
  { label: "REALISED", align: "right" },
  { label: "IF HELD", align: "right" },
  { label: "DELTA", align: "right" },
  { label: "", align: "right" },
];

export interface WhatIfProps {
  data: PortfolioData;
  agg: Aggregates;
  method: LotMethod;
}

export function WhatIf({ data, agg, method }: WhatIfProps) {
  const sorted = [...agg.closures].sort((a, b) => b.delta - a.delta);
  const maxDelta = Math.max(...sorted.map((x) => Math.abs(x.delta)), 0);

  // One card per instrument, not per closure: three tranches of the same sale
  // tell one story, and three near-identical cards would bury it.
  const byInstrument = new Map<
    string,
    { symbol: string; isin: string; delta: number; realised: number; qty: number; price: number; date: string }
  >();
  for (const entry of sorted) {
    const key = entry.instrument.isin;
    const existing = byInstrument.get(key);
    const next = existing ?? {
      symbol: entry.instrument.symbol,
      isin: key,
      delta: 0,
      realised: 0,
      qty: 0,
      price: entry.closure.closePrice,
      date: entry.closure.closeDate,
    };
    next.delta += entry.delta;
    next.realised += entry.closure.pnl;
    next.qty += entry.closure.qty;
    next.price = entry.closure.closePrice;
    if (entry.closure.closeDate > next.date) next.date = entry.closure.closeDate;
    byInstrument.set(key, next);
  }

  const cards = [...byInstrument.values()].sort((a, b) => b.delta - a.delta);

  const totalIfHeld = sorted.reduce((a, x) => a + x.closure.qty * x.instrument.current, 0);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <Panel
        title="Shadow portfolio"
        subtitle="Actual value against a portfolio where nothing was ever sold, and against buying the benchmark with the same deposit schedule."
        footer={
          <span style={{ display: "flex", gap: 14, flexWrap: "wrap", alignItems: "center" }}>
            <SeriesChip label="Actual portfolio" color={c.accent} lineStyle="solid" active />
            <SeriesChip label="Never sold (naive)" color={c.modelled} lineStyle="dashed" active />
            <SeriesChip label="Benchmark replay of my deposits" color={c.neutral} lineStyle="dotted" active />
            <span style={{ marginLeft: "auto", color: c.modelled }}>
              Naive hold: ignores that later purchases were funded by sale proceeds.
            </span>
          </span>
        }
      >
        <LineChart
          lines={[
            { values: data.series.value, color: c.accent, width: 2, fill: "rgba(76,141,246,0.10)" },
            { values: data.series.valueNeverSold, color: c.modelled, width: 1.6, dash: "5 4" },
            {
              values: data.series.benchmarkReplay.map((v, i) => (i < 1 ? null : v)),
              color: c.neutral,
              width: 1.4,
              dash: "2 3",
            },
          ]}
          height={250}
          formatY={(v) => eurCompact(v).replace("€ ", "")}
        />
      </Panel>

      <Panel
        title="Closed positions — the gap you stop watching"
        subtitle="Each panel picks up where your ledger stops. Solid to the sale, dotted after."
      >
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(292px, 1fr))", gap: 12 }}>
          {cards.map((card) => {
            const inst = data.instruments.find((i) => i.isin === card.isin);
            if (!inst) return null;
            const soldIdx = Math.round(monthIndex(card.date));
            const firstIdx = inst.transactions[0]?.idx ?? 0;

            // Three overlapping segments of one price series: owned up to the
            // sale, the gap after it, and any re-opened position. Splitting on
            // `null` is what lets them share an axis without three series.
            const beforeSale = inst.prices.map((v, i) =>
              i <= soldIdx && (inst.qtyNeverSold[i] ?? 0) > 0 ? v : null,
            );
            const gap = inst.prices.map((v, i) => {
              if (i < soldIdx) return null;
              const flatNow = (inst.qty[i] ?? 0) <= 0;
              const wasFlat = i > soldIdx && (inst.qty[i - 1] ?? 0) <= 0;
              return flatNow || wasFlat ? v : null;
            });
            const afterRebuy = inst.prices.map((v, i) => (i >= soldIdx && (inst.qty[i] ?? 0) > 0 ? v : null));
            const reBought = gap.some((v) => v != null) && afterRebuy.some((v) => v != null);

            return (
              <div
                key={card.isin}
                style={{
                  border: `1px solid ${c.border}`,
                  borderRadius: 5,
                  background: c.sunken,
                  padding: "12px 13px",
                }}
              >
                <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
                  <span style={{ fontFamily: mono, fontSize: 12, color: c.text }}>{card.symbol}</span>
                  <span style={{ fontSize: 10.5, color: c.textFaint }}>sold {shortDate(card.date)}</span>
                  <span
                    style={{
                      marginLeft: "auto",
                      fontFamily: mono,
                      fontSize: 13,
                      color: costColor(card.delta),
                    }}
                  >
                    {eurSigned(card.delta)}
                  </span>
                </div>

                <div style={{ margin: "9px -4px 6px" }}>
                  <LineChart
                    lines={[
                      { values: beforeSale, color: c.accent, width: 2 },
                      { values: gap, color: c.modelled, width: 1.6, dash: "4 4" },
                      { values: afterRebuy, color: c.accent, width: 2 },
                    ]}
                    height={120}
                    from={Math.max(0, firstIdx - 1)}
                    padding={{ l: 40, r: 8, t: 10, b: 20 }}
                    formatY={(v) => num(v, 0)}
                    dots={[{ idx: soldIdx, value: card.price, color: c.negative }]}
                  />
                </div>

                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: "1fr 1fr",
                    gap: "2px 10px",
                    fontFamily: mono,
                    fontSize: 10.5,
                  }}
                >
                  <span style={{ color: c.textFaint }}>sold at</span>
                  <span style={{ textAlign: "right", color: c.textSecondary }}>{eur(card.price)}</span>
                  <span style={{ color: c.textFaint }}>today</span>
                  <span style={{ textAlign: "right", color: c.textSecondary }}>{eur(inst.current)}</span>
                  <span style={{ color: c.textFaint }}>realised</span>
                  <span style={{ textAlign: "right", color: signColor(card.realised) }}>
                    {eurSigned(card.realised)}
                  </span>
                  <span style={{ color: c.modelled }}>if held</span>
                  <span style={{ textAlign: "right", color: c.modelled }}>
                    {eur(card.qty * inst.current, 0)}
                  </span>
                </div>

                <div style={{ fontSize: 10.5, color: c.textMuted, marginTop: 9, lineHeight: 1.55 }}>
                  {card.delta > 0
                    ? `Holding those ${num(card.qty, 0)} shares would be worth ${eurCompact(card.delta)} more than the proceeds.`
                    : `Exiting avoided ${eurCompact(-card.delta)} of further decline on ${num(card.qty, 0)} shares.`}
                  {reBought && " Position re-opened later — the solid tail is the current holding."}
                </div>
              </div>
            );
          })}
        </div>
      </Panel>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))", gap: 12 }}>
        {[
          {
            label: "REALISED ON CLOSURES",
            value: eurSigned(agg.totalRealised),
            color: signColor(agg.totalRealised),
            sub: `Actual, booked, ${method} matched.`,
          },
          {
            label: "SAME SHARES AT TODAY'S PRICE",
            value: eurCompact(totalIfHeld),
            color: c.modelled,
            sub: "Modelled. Naive hold, no redeployment of proceeds.",
          },
          {
            label: "NET DELTA · WHAT SELLING COST ME",
            value: eurSigned(agg.scorecard.net),
            color: costColor(agg.scorecard.net),
            sub:
              agg.scorecard.net > 0
                ? "Positive: holding everything to today would have been worth more."
                : "Negative: selling ended up ahead of holding everything to today.",
          },
        ].map((tile) => (
          <Panel key={tile.label}>
            <div style={{ fontFamily: mono, fontSize: 9.5, letterSpacing: "0.05em", color: c.textFaint }}>
              {tile.label}
            </div>
            <div
              style={{
                fontFamily: mono,
                fontSize: 22,
                marginTop: 8,
                color: tile.color,
                letterSpacing: "-0.02em",
              }}
            >
              {tile.value}
            </div>
            <div style={{ fontSize: 11, color: c.textMuted, marginTop: 6, lineHeight: 1.6 }}>{tile.sub}</div>
          </Panel>
        ))}
      </div>

      <Panel title="Every closure, both directions" subtitle={`Sorted by delta. ${method} matching.`}>
        <div style={{ overflowX: "auto" }}>
          <Table minWidth={860}>
            <HeadRow columns={COLUMNS} />
            <tbody>
              {sorted.map((entry, i) => (
                <tr key={`${entry.instrument.isin}-${i}`} style={{ borderBottom: `1px solid ${c.borderSoft}` }}>
                  <Td padding="8px 10px" numeric color={c.text}>{entry.instrument.symbol}</Td>
                  <Td padding="8px 10px" numeric>{shortDate(entry.closure.closeDate)}</Td>
                  <Td padding="8px 10px" align="right" numeric>{num(entry.closure.qty, 0)}</Td>
                  <Td padding="8px 10px" align="right" numeric color={c.textMuted}>
                    {eur(entry.closure.closePrice)}
                  </Td>
                  <Td padding="8px 10px" align="right" numeric color={signColor(entry.closure.pnl)}>
                    {eurSigned(entry.closure.pnl)}
                  </Td>
                  <Td padding="8px 10px" align="right" numeric color={c.modelled}>
                    {eur(entry.closure.qty * entry.instrument.current, 0)}
                  </Td>
                  <Td padding="8px 10px" align="right" numeric color={costColor(entry.delta)}>
                    {eurSigned(entry.delta)}
                  </Td>
                  <Td padding="8px 10px" align="right">
                    <span style={{ display: "inline-block", width: "100%", maxWidth: 120, verticalAlign: "middle" }}>
                      <DivergingBar
                        value={entry.delta}
                        max={maxDelta}
                        color={costColor(entry.delta)}
                        height={7}
                      />
                    </span>
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        </div>
      </Panel>
    </div>
  );
}
