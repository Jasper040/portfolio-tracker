/** Received and withheld.
 *
 *  Dividends are reported NET throughout, because net is what arrives. Gross and
 *  withholding are shown beside it rather than instead of it: withholding is a
 *  real, recoverable-in-principle cost that a net-only view hides completely, and
 *  the country breakdown exists so the size of it is visible.
 *
 *  Forward estimates share an axis with received payments, so they are drawn
 *  hatched. See `StackedBars` for why that is not just a legend note.
 */

import { StackedBars, type BarGroup } from "../components/charts/StackedBars";
import { Panel } from "../components/ui/Panel";
import { ProportionBar } from "../components/ui/DivergingBar";
import { Table, Td } from "../components/ui/Table";
import { c, mono, paletteAt } from "../lib/theme";
import { eur, eurCompact, num, pctPlain } from "../lib/format";
import type { PortfolioData } from "../portfolio/provider";
import type { Aggregates } from "../portfolio/aggregate";

/** The quarter after the last one with real data. Everything from here on is a
 *  projection from the trailing twelve months. */
const ESTIMATE_YEAR = 2026;
const ESTIMATE_QUARTER = 4;
const TRAILING_WINDOW_START = "2025-09-01";

function quarterOf(isoDate: string): { year: number; quarter: number } {
  const year = Number(isoDate.slice(0, 4));
  const month = Number(isoDate.slice(5, 7));
  return { year, quarter: Math.ceil(month / 3) };
}

export interface DividendsProps {
  data: PortfolioData;
  agg: Aggregates;
}

export function Dividends({ data, agg }: DividendsProps) {
  const paying = data.instruments.filter((i) => i.dividends.length > 0);
  const symbols = paying.map((i) => i.symbol);
  const colors = symbols.map((_, i) => paletteAt(i));

  // Per-symbol quarterly run rate from the trailing twelve months, used to
  // project forward. Divided by four because the window is a year and the
  // projection is quarterly.
  const runRate = new Map<string, number>();
  for (const inst of data.instruments) {
    const recent = inst.dividends.filter((d) => d.date >= TRAILING_WINDOW_START);
    runRate.set(inst.symbol, recent.reduce((a, d) => a + d.net, 0) / 4);
  }

  const groups: BarGroup[] = [];
  for (let year = 2021; year <= 2027; year++) {
    for (let quarter = 1; quarter <= 4; quarter++) {
      // Only Q1 carries a label, so the axis reads as years rather than as
      // twenty-eight quarter ticks.
      const group: BarGroup = { label: quarter === 1 ? String(year) : `Q${quarter}` };
      let any = false;

      for (const inst of data.instruments) {
        const total = inst.dividends
          .filter((d) => {
            const q = quarterOf(d.date);
            return q.year === year && q.quarter === quarter;
          })
          .reduce((a, d) => a + d.net, 0);
        if (total > 0) {
          group[inst.symbol] = total;
          any = true;
        }
      }

      const isProjection =
        (year === ESTIMATE_YEAR && quarter >= ESTIMATE_QUARTER) || year > ESTIMATE_YEAR;
      if (isProjection) {
        for (const [symbol, rate] of runRate) {
          if (rate > 0) group[symbol] = rate;
        }
      }

      // Beyond the current year, only keep quarters that carry something.
      if (year <= ESTIMATE_YEAR || any || isProjection) groups.push({ ...group, __year: year, __q: quarter });
    }
  }

  const estimateFrom = groups.findIndex(
    (g) => g["__year"] === ESTIMATE_YEAR && g["__q"] === ESTIMATE_QUARTER,
  );

  const thisYear = data.instruments.reduce(
    (a, i) => a + i.dividends.filter((d) => d.date >= "2026-01-01").reduce((b, d) => b + d.net, 0),
    0,
  );
  const trailing = data.instruments.reduce(
    (a, i) =>
      a + i.dividends.filter((d) => d.date >= TRAILING_WINDOW_START).reduce((b, d) => b + d.net, 0),
    0,
  );
  const forward = [...runRate.values()].reduce((a, r) => a + r * 4, 0);

  const matches = agg.rows;

  const byCountry = new Map<string, { tax: number; rate: number }>();
  for (const inst of data.instruments) {
    const tax = inst.dividends.reduce((a, d) => a + d.tax, 0);
    if (tax <= 0) continue;
    const existing = byCountry.get(inst.country) ?? { tax: 0, rate: inst.withholding };
    existing.tax += tax;
    byCountry.set(inst.country, existing);
  }
  const countries = [...byCountry.entries()].sort((a, b) => b[1].tax - a[1].tax);
  const maxCountryTax = Math.max(...countries.map(([, v]) => v.tax), 0);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(168px, 1fr))",
          gap: 1,
          background: c.border,
          border: `1px solid ${c.border}`,
          borderRadius: 6,
          overflow: "hidden",
        }}
      >
        {[
          { label: "NET THIS YEAR", value: eurCompact(thisYear), sub: "2026 to date", color: c.positive },
          { label: "TRAILING 12M", value: eurCompact(trailing), sub: "rolling, net", color: c.positive },
          {
            label: "LIFETIME NET",
            value: eurCompact(agg.totalDividendNet),
            sub: "since first payment",
            color: c.textSecondary,
          },
          {
            label: "EFFECTIVE WH RATE",
            value:
              agg.totalDividendGross > 0
                ? pctPlain(1 - agg.totalDividendNet / agg.totalDividendGross)
                : "—",
            sub: "weighted across countries",
            color: c.negative,
          },
          {
            label: "GROSS LIFETIME",
            value: eurCompact(agg.totalDividendGross),
            sub: "before withholding",
            color: c.textMuted,
          },
          {
            label: "FORWARD 12M · EST.",
            value: eurCompact(forward),
            sub: "modelled from trailing 12 months",
            color: c.modelled,
          },
        ].map((k) => (
          <div key={k.label} style={{ background: c.panel, padding: "12px 14px" }}>
            <div style={{ fontFamily: mono, fontSize: 9.5, letterSpacing: "0.06em", color: c.textFaint }}>
              {k.label}
            </div>
            <div
              style={{
                fontFamily: mono,
                fontSize: 19,
                marginTop: 7,
                color: k.color,
                letterSpacing: "-0.02em",
              }}
            >
              {k.value}
            </div>
            <div style={{ fontSize: 10.5, color: c.textFaint, marginTop: 4 }}>{k.sub}</div>
          </div>
        ))}
      </div>

      <Panel
        title="Net dividends by quarter"
        subtitle="stacked by instrument · net of withholding"
        actions={
          <span style={{ fontSize: 10.5, color: c.modelled }}>
            Hatched bars are forward estimates from trailing 12 months.
          </span>
        }
        footer={
          <span style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
            {symbols.map((s, i) => (
              <span key={s} style={{ display: "flex", alignItems: "center", gap: 6, color: c.textMuted }}>
                <span style={{ width: 8, height: 8, borderRadius: 2, background: colors[i] }} />
                {s}
              </span>
            ))}
          </span>
        }
      >
        <StackedBars
          groups={groups}
          keys={symbols}
          colors={colors}
          estimateFrom={estimateFrom < 0 ? groups.length : estimateFrom}
        />
      </Panel>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(360px, 1fr))", gap: 12 }}>
        <Panel title="By instrument · lifetime">
          <Table>
            <tbody>
              <tr>
                {(["INSTRUMENT", "N", "GROSS", "WH TAX", "NET", "YOC CUMUL."] as const).map((label, i) => (
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
              {paying.map((inst) => {
                const gross = inst.dividends.reduce((a, d) => a + d.gross, 0);
                const tax = inst.dividends.reduce((a, d) => a + d.tax, 0);
                const cost = matches.find((r) => r.instrument.isin === inst.isin)?.match.cost ?? 0;
                return (
                  <tr key={inst.isin} style={{ borderBottom: `1px solid ${c.borderSoft}` }}>
                    <Td padding="7px 9px" numeric color={c.text}>{inst.symbol}</Td>
                    <Td padding="7px 9px" align="right" numeric color={c.textFaint}>
                      {inst.dividends.length}
                    </Td>
                    <Td padding="7px 9px" align="right" numeric color={c.textMuted}>{eur(gross, 0)}</Td>
                    <Td padding="7px 9px" align="right" numeric color={c.negative}>−{eur(tax, 0)}</Td>
                    <Td padding="7px 9px" align="right" numeric color={c.positive}>{eur(gross - tax, 0)}</Td>
                    {/* Yield on cost, cumulative: lifetime net against what the
                        open position cost. Undefined for a fully closed position,
                        where the denominator is zero rather than small. */}
                    <Td padding="7px 9px" align="right" numeric>
                      {cost > 0 ? pctPlain((gross - tax) / cost) : "—"}
                    </Td>
                  </tr>
                );
              })}
            </tbody>
          </Table>
        </Panel>

        <Panel
          title="Withholding by country"
          subtitle="Information only. Reclaim eligibility depends on your treaty position."
        >
          <div style={{ display: "flex", flexDirection: "column", gap: 9 }}>
            {countries.map(([country, v]) => (
              <div
                key={country}
                style={{
                  display: "grid",
                  gridTemplateColumns: "36px minmax(0,1fr) 78px 52px",
                  alignItems: "center",
                  gap: 10,
                  fontSize: 11.5,
                }}
              >
                <span style={{ fontFamily: mono, color: c.textSecondary }}>{country}</span>
                <ProportionBar value={v.tax} max={maxCountryTax} color={c.negative} />
                <span style={{ fontFamily: mono, textAlign: "right", color: c.negative }}>
                  −{eur(v.tax, 0)}
                </span>
                <span style={{ fontFamily: mono, textAlign: "right", color: c.textMuted }}>
                  {num(v.rate * 100, 1)}%
                </span>
              </div>
            ))}
          </div>
        </Panel>
      </div>
    </div>
  );
}
