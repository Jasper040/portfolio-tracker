/** The ten-second answer.
 *
 *  Reading order is deliberate and top-to-bottom: the period you are asking
 *  about, then the headline numbers, then the shape of the money over time, then
 *  what it is made of, then which holdings moved it, and finally the one
 *  judgement the app is opinionated about -- what selling cost you.
 */

import { useMemo, useState } from "react";
import { LineChart } from "../components/charts/LineChart";
import { Donut } from "../components/charts/Donut";
import { DivergingBar } from "../components/ui/DivergingBar";
import { Panel } from "../components/ui/Panel";
import { SegmentedControl, SeriesChip } from "../components/ui/Controls";
import { TileGrid, type Tile } from "../components/ui/Tiles";
import { c, mono, paletteAt } from "../lib/theme";
import { eurCompact, eurSigned, pct, num, signColor, costColor } from "../lib/format";
import { MONTHS, monthLabel } from "../lib/series";
import {
  PERIODS,
  moneyWeightedReturn,
  periodStartIndex,
  timeWeightedReturn,
  type Period,
} from "../lib/portfolio";
import { groupByValue, type Aggregates } from "../portfolio/aggregate";
import type { PortfolioData } from "../portfolio/provider";
import type { LotMethod } from "../lib/lots";

export interface DashboardProps {
  data: PortfolioData;
  agg: Aggregates;
  method: LotMethod;
  onOpenWhatIf: () => void;
}

export function Dashboard({ data, agg, method, onOpenWhatIf }: DashboardProps) {
  const [period, setPeriod] = useState<Period>("Max");
  const [showNeverSold, setShowNeverSold] = useState(true);
  const [showBenchmark, setShowBenchmark] = useState(true);

  const start = periodStartIndex(period);
  const twr = useMemo(() => timeWeightedReturn(data.series, start), [data.series, start]);
  const mwr = useMemo(() => moneyWeightedReturn(data.series, start), [data.series, start]);

  const kpis: Tile[] = [
    {
      label: "TOTAL VALUE",
      value: eurCompact(agg.totalValue),
      sub: `${agg.held.length} positions, 2 accounts`,
    },
    { label: "COST BASIS", value: eurCompact(agg.totalCost), sub: `net of fees, ${method}`, color: c.textSecondary },
    {
      label: "UNREALISED P&L",
      value: eurCompact(agg.totalUnrealised),
      sub: `${pct(agg.totalCost > 0 ? agg.totalUnrealised / agg.totalCost : 0)} on cost`,
      color: signColor(agg.totalUnrealised),
    },
    {
      label: "REALISED P&L",
      value: eurCompact(agg.totalRealised),
      sub: `lifetime · ${method} matching`,
      color: signColor(agg.totalRealised),
    },
    {
      label: "NET DIVIDENDS",
      value: eurCompact(agg.totalDividendNet),
      sub: "lifetime, after withholding",
      color: c.positive,
    },
    {
      label: `TWR · ${period}`,
      value: pct(twr),
      sub: "time-weighted, cashflow-neutral",
      color: signColor(twr),
    },
    {
      label: `MWR · ${period}`,
      value: pct(mwr),
      sub: "money-weighted, annualised IRR",
      color: signColor(mwr),
    },
    {
      label: "FEES PAID",
      value: eurCompact(agg.totalFees),
      sub: `${agg.transactionCount} transactions, lifetime`,
      color: c.textMuted,
    },
  ];

  const lines = [
    { values: data.series.value, color: c.accent, width: 2, fill: "rgba(76,141,246,0.10)" },
    ...(showNeverSold
      ? [{ values: data.series.valueNeverSold, color: c.modelled, width: 1.5, dash: "5 4" }]
      : []),
    ...(showBenchmark
      ? [
          {
            // Index 0 is dropped: before the first deposit the replay portfolio
            // holds nothing, and drawing it at zero implies a real position worth
            // nothing rather than the absence of one.
            values: data.series.benchmarkReplay.map((v, i) => (i < 1 ? null : v)),
            color: c.neutral,
            width: 1.4,
            dash: "2 3",
          },
        ]
      : []),
  ];

  const donuts = [
    { title: "By instrument", key: (r: { instrument: { symbol: string } }) => r.instrument.symbol },
    { title: "By industry", key: (r: { instrument: { industry: string } }) => r.instrument.industry },
    { title: "By currency", key: (r: { instrument: { currency: string } }) => r.instrument.currency },
    { title: "By account", key: (r: { instrument: { account: string } }) => `DeGiro ${r.instrument.account}` },
  ].map(({ title, key }) => {
    const slices = groupByValue(agg.held, key).map((s, i) => ({ ...s, color: paletteAt(i) }));
    const total = slices.reduce((a, s) => a + s.value, 0);
    return { title, slices, total };
  });

  // Contribution = weight x return over the period, which is additive to the
  // portfolio return. Raw P&L is not: a 200% gain on a 1% position would top a
  // P&L-sorted list while having moved almost nothing.
  const contributions = agg.held
    .map((r) => {
      const base = r.instrument.prices[Math.max(start, 0)] ?? r.instrument.prices[0] ?? 0;
      const ret = base > 0 ? r.instrument.current / base - 1 : 0;
      return { symbol: r.instrument.symbol, value: (r.value / agg.totalValue) * ret };
    })
    .sort((a, b) => b.value - a.value);

  // Top five and bottom five, deduplicated: with fewer than ten holdings the two
  // slices overlap, and showing a name twice would imply two positions.
  const top = contributions.slice(0, 5);
  const bottom = contributions.slice(-5).filter((x) => !top.includes(x));
  const contributionRows = [...top, ...bottom];
  const contributionMax = Math.max(...contributionRows.map((x) => Math.abs(x.value)), 0);

  const score = agg.scorecard;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <SegmentedControl label="PERIOD" options={PERIODS} value={period} onChange={setPeriod} />
        <span style={{ fontSize: 11, color: c.textFaint, marginLeft: 4 }}>
          {monthLabel(start)} → {monthLabel(MONTHS - 1)} · {MONTHS - start} monthly marks
        </span>
      </div>

      <TileGrid tiles={kpis} />

      <Panel
        title="Portfolio value"
        subtitle="base currency, month-end marks"
        actions={
          <>
            <SeriesChip label="Actual" color={c.accent} lineStyle="solid" active />
            <SeriesChip
              label="Never sold"
              color={c.modelled}
              lineStyle="dashed"
              active={showNeverSold}
              onToggle={() => setShowNeverSold((v) => !v)}
            />
            <SeriesChip
              label="Global Equity proxy"
              color={c.neutral}
              lineStyle="dotted"
              active={showBenchmark}
              onToggle={() => setShowBenchmark((v) => !v)}
            />
          </>
        }
        footer="Benchmark line replays my actual deposit schedule into IWDA.AS. Never-sold line is modelled: naive hold, ignoring that proceeds funded later purchases."
      >
        <LineChart
          lines={lines}
          height={250}
          from={start}
          formatY={(v) => eurCompact(v).replace("€ ", "")}
        />
      </Panel>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(268px, 1fr))",
          gap: 12,
        }}
      >
        {donuts.map((d) => (
          <Panel key={d.title} title={d.title}>
            <div style={{ display: "flex", gap: 14, alignItems: "center" }}>
              <div style={{ flex: "0 0 auto" }}>
                <Donut slices={d.slices} centre={eurCompact(d.total)} />
              </div>
              <div style={{ flex: "1 1 0", minWidth: 0, display: "flex", flexDirection: "column", gap: 4 }}>
                {d.slices.slice(0, 6).map((s) => (
                  <div
                    key={s.label}
                    style={{ display: "flex", alignItems: "center", gap: 7, fontSize: 11, minWidth: 0 }}
                  >
                    <span
                      style={{
                        width: 8,
                        height: 8,
                        borderRadius: 2,
                        background: s.color,
                        flex: "0 0 auto",
                      }}
                    />
                    <span
                      style={{
                        color: c.textSecondary,
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {s.label}
                    </span>
                    <span style={{ marginLeft: "auto", fontFamily: mono, color: c.textMuted, flex: "0 0 auto" }}>
                      {num(d.total > 0 ? (s.value / d.total) * 100 : 0, 1)}%
                    </span>
                  </div>
                ))}
              </div>
            </div>
          </Panel>
        ))}
      </div>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))",
          gap: 12,
        }}
      >
        <Panel title="Contribution to return" subtitle="weight × return over period. Not raw P&L.">
          <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
            {contributionRows.map((row) => (
              <div
                key={row.symbol}
                style={{
                  display: "grid",
                  gridTemplateColumns: "74px minmax(0,1fr) 62px",
                  alignItems: "center",
                  gap: 10,
                  fontSize: 11.5,
                }}
              >
                <span style={{ fontFamily: mono, color: c.textSecondary }}>{row.symbol}</span>
                <DivergingBar value={row.value} max={contributionMax} color={signColor(row.value)} />
                <span style={{ fontFamily: mono, textAlign: "right", color: signColor(row.value) }}>
                  {pct(row.value, 2)}
                </span>
              </div>
            ))}
          </div>
        </Panel>

        <Panel
          title="Selling scorecard"
          subtitle="Net effect of every closure to date, versus holding each to today."
          style={{ display: "flex", flexDirection: "column" }}
        >
          <div style={{ display: "flex", alignItems: "baseline", gap: 12, margin: "16px 0 4px" }}>
            <span
              style={{
                fontFamily: mono,
                fontSize: 30,
                fontWeight: 500,
                color: costColor(score.net),
                letterSpacing: "-0.03em",
              }}
            >
              {eurSigned(score.net)}
            </span>
            <span style={{ fontSize: 11, color: c.textMuted }}>
              what selling cost me across {score.closureCount} closures · positive = worse off
            </span>
          </div>

          <div
            style={{
              display: "flex",
              gap: 1,
              background: c.border,
              borderRadius: 3,
              overflow: "hidden",
              marginTop: 12,
            }}
          >
            <div style={{ flex: 1, background: c.inset, padding: "9px 11px" }}>
              <div style={{ fontFamily: mono, fontSize: 9.5, color: c.textFaint, letterSpacing: "0.05em" }}>
                COST ME
              </div>
              <div style={{ fontFamily: mono, fontSize: 14, color: c.negative, marginTop: 4 }}>
                {eurCompact(score.costMe)}
              </div>
            </div>
            <div style={{ flex: 1, background: c.inset, padding: "9px 11px" }}>
              <div style={{ fontFamily: mono, fontSize: 9.5, color: c.textFaint, letterSpacing: "0.05em" }}>
                SAVED ME
              </div>
              <div style={{ fontFamily: mono, fontSize: 14, color: c.positive, marginTop: 4 }}>
                {eurCompact(score.savedMe)}
              </div>
            </div>
          </div>

          <div style={{ marginTop: "auto", paddingTop: 14, fontSize: 11, color: c.textMuted, lineHeight: 1.6 }}>
            {score.net > 0
              ? `Holding every closed position to today would be worth ${eurCompact(score.net)} more than what selling produced.`
              : `Selling ended up ${eurCompact(-score.net)} ahead of holding everything to today.`}{" "}
            <a
              href="#whatif"
              onClick={(e) => {
                e.preventDefault();
                onOpenWhatIf();
              }}
            >
              Open What-If →
            </a>
          </div>
        </Panel>
      </div>
    </div>
  );
}
