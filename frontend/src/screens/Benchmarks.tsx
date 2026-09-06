/** Where performance came from.
 *
 *  Everything on this screen is measured against INVESTABLE proxies rather than
 *  indices. An index return is not a thing anyone could have earned: it has no
 *  TER, no tracking error and no spread. Comparing against the ETF instead means
 *  excess return answers "would I have done better buying the obvious thing",
 *  which is the only version of the question with a decision attached.
 */

import { LineChart, type ChartLine } from "../components/charts/LineChart";
import { Scatter } from "../components/charts/Scatter";
import { Panel } from "../components/ui/Panel";
import { SeriesChip } from "../components/ui/Controls";
import { DivergingBar } from "../components/ui/DivergingBar";
import { Table, Td } from "../components/ui/Table";
import { c, mono, paletteAt } from "../lib/theme";
import { num, pct, signColor } from "../lib/format";
import { MONTHS } from "../lib/series";
import type { PortfolioData } from "../portfolio/provider";
import type { Aggregates } from "../portfolio/aggregate";

/** Window for the industry comparison, in months. */
const THREE_YEARS = 36;

export interface BenchmarksProps {
  data: PortfolioData;
  agg: Aggregates;
}

export function Benchmarks({ data, agg }: BenchmarksProps) {
  const last = MONTHS - 1;

  // The portfolio as a TWR index: the same chain-linking as `timeWeightedReturn`,
  // retained month by month so it can be plotted rather than reduced to a scalar.
  const portfolioIndex: number[] = [100];
  {
    let factor = 100;
    for (let i = 1; i < MONTHS; i++) {
      const begin = data.series.value[i - 1] ?? 0;
      if (begin > 0) {
        factor *= ((data.series.value[i] ?? 0) - (data.series.cashflow[i] ?? 0)) / begin;
      }
      portfolioIndex.push(factor);
    }
  }

  const benchmarkLines: ChartLine[] = data.benchmarks.map((b, i) => {
    const prices = data.benchmarkPrices[b.key] ?? [];
    const base = prices[0] || 1;
    return {
      values: prices.map((v) => (v / base) * 100),
      color: i === 0 ? c.neutral : c.violet,
      width: 1.4,
      dash: i === 0 ? "2 3" : "6 3",
    };
  });

  const portfolioReturn = (portfolioIndex[last] ?? 100) / 100 - 1;

  // ── Contribution by industry over the trailing three years.
  const industryTotals = new Map<string, { weight: number; contribution: number }>();
  for (const row of agg.held) {
    const key = row.instrument.industry;
    const base = row.instrument.prices[last - THREE_YEARS] ?? row.instrument.prices[0] ?? 1;
    const entry = industryTotals.get(key) ?? { weight: 0, contribution: 0 };
    entry.weight += row.value;
    entry.contribution +=
      (row.value / (agg.totalValue || 1)) * (base > 0 ? row.instrument.current / base - 1 : 0);
    industryTotals.set(key, entry);
  }
  const industries = [...industryTotals.entries()].sort((a, b) => b[1].contribution - a[1].contribution);
  const maxContribution = Math.max(...industries.map(([, v]) => Math.abs(v.contribution)), 0);

  // ── Industry weight over time, as cumulative shares of portfolio value.
  //
  // Drawn as filled cumulative lines from the TOP of the stack downwards, so each
  // fill covers the ones beneath it. Painting them bottom-up would leave every
  // band hidden behind the next.
  const industryKeys = [...industryTotals.keys()];
  const cumulative = industryKeys.map(() => new Array<number | null>(MONTHS).fill(null));
  for (let i = 0; i < MONTHS; i++) {
    const values = industryKeys.map((key) => {
      let v = 0;
      for (const row of agg.held) {
        if (row.instrument.industry === key) v += (row.instrument.qty[i] ?? 0) * (row.instrument.prices[i] ?? 0);
      }
      return v;
    });
    const total = values.reduce((a, v) => a + v, 0);
    let acc = 0;
    values.forEach((v, ki) => {
      acc += total > 0 ? (v / total) * 100 : 0;
      const series = cumulative[ki];
      if (series) series[i] = total > 0 ? acc : null;
    });
  }
  const areaLines: ChartLine[] = [];
  for (let ki = industryKeys.length - 1; ki >= 0; ki--) {
    const values = cumulative[ki];
    if (!values) continue;
    areaLines.push({ values, color: paletteAt(ki), width: 1, fill: `${paletteAt(ki)}55` });
  }

  // ── Holding vs its industry proxy, three-year return.
  const meud = data.benchmarkPrices["MEUD"] ?? [];
  const proxyReturn =
    (meud[last - THREE_YEARS] ?? 0) > 0 ? (meud[last] ?? 0) / (meud[last - THREE_YEARS] ?? 1) - 1 : 0;
  const scatterPoints = agg.held.map((row, i) => {
    const base = row.instrument.prices[last - THREE_YEARS] ?? row.instrument.prices[0] ?? 1;
    // A small deterministic offset per instrument so holdings sharing one proxy
    // do not stack into a single unreadable dot.
    const jitter = ((i % 5) - 2) * 0.06;
    return {
      label: row.instrument.symbol,
      x: proxyReturn + jitter,
      y: base > 0 ? row.instrument.current / base - 1 : 0,
    };
  });

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <Panel
        title="Portfolio TWR vs benchmark proxies"
        subtitle="rebased to 100 at first transaction · TWR, time-weighted, cashflow-neutral"
        footer={
          <span style={{ display: "flex", gap: 14, flexWrap: "wrap" }}>
            <SeriesChip label="My portfolio (TWR index)" color={c.accent} lineStyle="solid" active />
            {data.benchmarks.map((b, i) => (
              <SeriesChip
                key={b.key}
                label={b.ticker}
                color={i === 0 ? c.neutral : c.violet}
                lineStyle={i === 0 ? "dotted" : "dashed"}
                active
              />
            ))}
          </span>
        }
      >
        <LineChart
          lines={[{ values: portfolioIndex, color: c.accent, width: 2.1 }, ...benchmarkLines]}
          height={250}
          formatY={(v) => num(v, 0)}
        />
      </Panel>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(340px, 1fr))", gap: 12 }}>
        <Panel title="Excess return vs proxy">
          <Table>
            <tbody>
              <tr>
                {(["PROXY", "MINE", "PROXY RET", "EXCESS", "PERIOD"] as const).map((label, i) => (
                  <th
                    key={label}
                    style={{
                      textAlign: i === 0 ? "left" : "right",
                      padding: "7px 9px",
                      fontFamily: mono,
                      fontSize: 9,
                      letterSpacing: "0.05em",
                      color: c.textFaint,
                      fontWeight: 500,
                      borderBottom: `1px solid ${c.borderStrong}`,
                    }}
                  >
                    {label}
                  </th>
                ))}
              </tr>
              {data.benchmarks.map((b) => {
                const prices = data.benchmarkPrices[b.key] ?? [];
                const theirs = (prices[0] ?? 0) > 0 ? (prices[last] ?? 0) / (prices[0] ?? 1) - 1 : 0;
                const excess = portfolioReturn - theirs;
                return (
                  <tr key={b.key} style={{ borderBottom: `1px solid ${c.borderSoft}` }}>
                    <Td padding="8px 9px">
                      <span style={{ display: "block", color: c.text }}>{b.name}</span>
                      <span
                        style={{
                          display: "block",
                          fontFamily: mono,
                          fontSize: 9.5,
                          color: c.textFaint,
                          marginTop: 2,
                        }}
                      >
                        {b.ticker} · TER {b.ter}
                      </span>
                    </Td>
                    <Td padding="8px 9px" align="right" numeric>{pct(portfolioReturn)}</Td>
                    <Td padding="8px 9px" align="right" numeric color={c.textMuted}>{pct(theirs)}</Td>
                    <Td padding="8px 9px" align="right" numeric color={signColor(excess)}>{pct(excess)}</Td>
                    <Td padding="8px 9px" align="right" numeric color={c.textFaint}>since 2019</Td>
                  </tr>
                );
              })}
            </tbody>
          </Table>
          <div style={{ fontSize: 10.5, color: c.modelled, marginTop: 11, lineHeight: 1.6 }}>
            Benchmarks and industries are ETF proxies, not indices. Their TER is embedded in the proxy
            return, so excess return is measured against an investable alternative.
          </div>
        </Panel>

        <Panel
          title="Contribution by industry"
          subtitle="weight × return, summing to total portfolio return"
        >
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {industries.map(([label, v]) => (
              <div
                key={label}
                style={{
                  display: "grid",
                  gridTemplateColumns: "108px minmax(0,1fr) 58px",
                  alignItems: "center",
                  gap: 10,
                  fontSize: 11.5,
                }}
              >
                <span
                  style={{
                    color: c.textSecondary,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {label}
                </span>
                <DivergingBar
                  value={v.contribution}
                  max={maxContribution}
                  color={signColor(v.contribution)}
                />
                <span style={{ fontFamily: mono, textAlign: "right", color: signColor(v.contribution) }}>
                  {pct(v.contribution)}
                </span>
              </div>
            ))}
          </div>
        </Panel>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(340px, 1fr))", gap: 12 }}>
        <Panel title="Industry weight over time" subtitle="stacked share of portfolio value">
          <LineChart lines={areaLines} height={220} from={24} formatY={(v) => `${num(v, 0)}%`} />
        </Panel>

        <Panel
          title="Holding vs its industry"
          subtitle="3-year return · above the diagonal beat its sector"
        >
          <Scatter points={scatterPoints} />
        </Panel>
      </div>
    </div>
  );
}
