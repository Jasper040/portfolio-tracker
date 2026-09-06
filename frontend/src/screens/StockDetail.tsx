/** One instrument, in six sections.
 *
 *  Section B (the chart) is placed above section A (the lot table) even though it
 *  is lettered second: the chart is the story and the table is the evidence. The
 *  section letters are kept because they are how the design doc refers to them.
 *
 *  The chart's central idea is that a single price series is drawn in two visual
 *  registers -- solid where a position was held, dotted where it was flat. The
 *  stretch you were out of the market is exactly the stretch you stop watching,
 *  and this screen refuses to let it disappear.
 */

import { useMemo, useState } from "react";
import { LineChart, type ChartBand, type ChartMarker, type HoverPayload } from "../components/charts/LineChart";
import { ChartTooltip } from "../components/charts/ChartTooltip";
import { Badge } from "../components/ui/Badge";
import { Panel } from "../components/ui/Panel";
import { ActionButton, Pill, SegmentedControl, SeriesChip } from "../components/ui/Controls";
import { HeadRow, Table, Td, type ColumnDef } from "../components/ui/Table";
import { TileGrid, TileStrip } from "../components/ui/Tiles";
import { c, mono } from "../lib/theme";
import { daysBetween, eur, eurCompact, eurSigned, num, pct, pctPlain, shortDate, signColor } from "../lib/format";
import { MONTHS, TODAY, monthLabel } from "../lib/series";
import { rebase } from "../lib/portfolio";
import { closureDelta } from "../lib/counterfactual";
import type { LotMethod } from "../lib/lots";
import type { PortfolioData } from "../portfolio/provider";
import type { Aggregates } from "../portfolio/aggregate";

type Range = "1Y" | "3Y" | "5Y" | "Max";
const RANGES: readonly Range[] = ["1Y", "3Y", "5Y", "Max"];

const LOT_COLUMNS: readonly ColumnDef[] = [
  { label: "LOT" },
  { label: "OPENED" },
  { label: "QTY", align: "right" },
  { label: "COST/SH", align: "right" },
  { label: "CLOSED" },
  { label: "QTY CLOSED", align: "right" },
  { label: "PRICE/SH", align: "right" },
  { label: "STATUS" },
  { label: "P&L €", align: "right" },
  { label: "P&L %", align: "right" },
  { label: "ANNUALISED", align: "right" },
  { label: "HELD (D)", align: "right" },
  { label: "FEES", align: "right" },
  // Amber header: this column is modelled, and the tint says so before the
  // reader gets to the footnote.
  { label: "IF HELD TO TODAY", align: "right", color: c.modelled },
];

export interface StockDetailProps {
  data: PortfolioData;
  agg: Aggregates;
  method: LotMethod;
  isin: string;
  onSelect: (isin: string) => void;
}

export function StockDetail({ data, agg, method, isin, onSelect }: StockDetailProps) {
  const [range, setRange] = useState<Range>("Max");
  const [showBench, setShowBench] = useState(true);
  const [showIndustry, setShowIndustry] = useState(false);
  const [showDivs, setShowDivs] = useState(true);
  const [hover, setHover] = useState<HoverPayload | null>(null);

  const row = agg.rows.find((r) => r.instrument.isin === isin) ?? agg.rows[0];
  const instrument = row?.instrument ?? data.instruments[0];
  const match = row?.match;

  const last = MONTHS - 1;
  const firstTxIdx = instrument?.transactions[0]?.idx ?? 0;

  const from = useMemo(() => {
    if (!instrument) return 0;
    if (range === "1Y") return last - 12;
    if (range === "3Y") return last - 36;
    if (range === "5Y") return last - 60;
    return Math.max(0, firstTxIdx - 2);
  }, [range, last, firstTxIdx, instrument]);

  if (!instrument || !match || !row) {
    return <div style={{ color: c.textMuted, fontSize: 12 }}>No instrument selected.</div>;
  }

  // Contiguous stretches where a position was open. Emitted as index ranges, so
  // the chart shades them without knowing anything about dates.
  const bands: ChartBand[] = [];
  {
    let start: number | null = null;
    for (let i = 0; i < MONTHS; i++) {
      const qty = instrument.qty[i] ?? 0;
      if (qty > 0 && start === null) start = i;
      if (qty <= 0 && start !== null) {
        bands.push([start, i]);
        start = null;
      }
    }
    if (start !== null) bands.push([start, last]);
  }

  const heldLine = instrument.prices.map((v, i) => ((instrument.qty[i] ?? 0) > 0 ? v : null));
  const flatLine = instrument.prices.map((v, i) => ((instrument.qty[i] ?? 0) > 0 ? null : v));

  const overlays = [];
  if (showBench) {
    const b = data.benchmarkPrices["IWDA"] ?? [];
    // Scaled to meet the instrument's price at the left edge of the visible
    // window, so the comparison is of shape rather than of absolute level.
    const k = (instrument.prices[from] ?? 1) / (b[from] || 1);
    overlays.push({ values: b.map((v) => v * k), color: c.neutral, width: 1.3, dash: "2 3" });
  }
  if (showIndustry) {
    const b = data.benchmarkPrices["MEUD"] ?? [];
    const k = (instrument.prices[from] ?? 1) / (b[from] || 1);
    overlays.push({ values: b.map((v) => v * k), color: c.violet, width: 1.3, dash: "6 3" });
  }

  const maxQty = Math.max(...instrument.transactions.map((t) => t.qty), 1);
  const markers: ChartMarker[] = instrument.transactions.map((t) => {
    let positionAfter = 0;
    for (const z of instrument.transactions) {
      if (z.date <= t.date) positionAfter += (z.type === "BUY" ? 1 : -1) * z.qty;
    }
    return {
      idx: t.idx,
      value: t.price,
      direction: t.type === "BUY" ? 1 : -1,
      // Square-root scaling, not linear: area is what the eye reads, so a 4x
      // quantity should be a 2x radius.
      size: 4 + 6 * Math.sqrt(t.qty / maxQty),
      title: `${t.type} · ${t.id}`,
      lines: [
        { k: "date", v: shortDate(t.date) },
        { k: "qty", v: num(t.qty, 0) },
        { k: "price", v: eur(t.price) },
        { k: "fees", v: eur(t.fees) },
        { k: "position after", v: `${num(positionAfter, 0)} sh` },
      ],
    };
  });

  const dots = showDivs
    ? instrument.dividends.map((d) => ({ idx: d.idx, value: instrument.prices[d.idx] ?? 0, color: c.modelled }))
    : [];

  // ── Section A rows: closures first, then still-open lots.
  const closureRows = match.closures.map((cl) => {
    const ret = cl.qty * cl.openPrice > 0 ? cl.pnl / (cl.qty * cl.openPrice) : 0;
    const days = daysBetween(cl.openDate, cl.closeDate);
    const { delta } = closureDelta(cl, instrument.current);
    return {
      key: `${cl.lotId}-${cl.closeDate}`,
      id: cl.lotId,
      opened: shortDate(cl.openDate),
      qty: num(cl.qty, 0),
      costPer: eur(cl.openPrice),
      closed: shortDate(cl.closeDate),
      qtyClosed: num(cl.qty, 0),
      pricePer: eur(cl.closePrice),
      status: "REALISED",
      statusColor: c.positive,
      statusBg: c.positiveBg,
      background: c.panel,
      pnl: eurSigned(cl.pnl),
      pnlPct: pct(ret),
      pnlColor: signColor(cl.pnl),
      // Annualising a two-week holding period produces a number in the millions
      // of percent. Below a month it is suppressed rather than shown.
      annualised: days > 30 ? pct(Math.pow(1 + ret, 365 / days) - 1) : "—",
      held: num(days, 0),
      fees: eur(cl.fees),
      counterfactual: `${eurSigned(delta)} delta`,
      counterfactualColor: signColor(delta),
    };
  });

  const openRows = match.open.map((lot) => {
    const ret = lot.price > 0 ? (instrument.current - lot.price) / lot.price : 0;
    const days = daysBetween(lot.date, TODAY);
    const pnl = lot.qty * (instrument.current - lot.price) - lot.fees;
    return {
      key: lot.id,
      id: lot.id,
      opened: shortDate(lot.date),
      qty: num(lot.qty, 0),
      costPer: eur(lot.price),
      closed: "—",
      qtyClosed: "—",
      pricePer: eur(instrument.current),
      status: "OPEN",
      statusColor: c.accent,
      statusBg: c.accentBg,
      background: c.panelAlt,
      pnl: eurSigned(pnl),
      pnlPct: pct(ret),
      pnlColor: signColor(pnl),
      annualised: days > 30 ? pct(Math.pow(1 + ret, 365 / days) - 1) : "—",
      held: num(days, 0),
      fees: eur(lot.fees),
      counterfactual: "held",
      counterfactualColor: c.textFaint,
    };
  });

  // ── Section C: alternating held / flat stretches since the first purchase.
  const intervals: { inMarket: boolean; from: number; to: number; ret: number }[] = [];
  {
    let start = firstTxIdx;
    let inMarket = (instrument.qty[firstTxIdx] ?? 0) > 0;
    for (let i = firstTxIdx + 1; i <= last; i++) {
      const now = (instrument.qty[i] ?? 0) > 0;
      if (now !== inMarket || i === last) {
        const a = Math.max(start, firstTxIdx);
        if (i > a) {
          const pa = instrument.prices[a] ?? 0;
          const pb = instrument.prices[i] ?? 0;
          intervals.push({ inMarket, from: a, to: i, ret: pa > 0 ? pb / pa - 1 : 0 });
        }
        start = i;
        inMarket = now;
      }
    }
  }

  const compareFrom = Math.max(0, firstTxIdx);
  const iwda = data.benchmarkPrices["IWDA"] ?? [];
  const meud = data.benchmarkPrices["MEUD"] ?? [];
  const retInstrument = (instrument.prices[compareFrom] ?? 0) > 0
    ? instrument.current / (instrument.prices[compareFrom] ?? 1) - 1 : 0;
  const retWorld = (iwda[compareFrom] ?? 0) > 0 ? (iwda[last] ?? 0) / (iwda[compareFrom] ?? 1) - 1 : 0;
  const retEurope = (meud[compareFrom] ?? 0) > 0 ? (meud[last] ?? 0) / (meud[compareFrom] ?? 1) - 1 : 0;

  const divNet = instrument.dividends.reduce((a, d) => a + d.net, 0);
  const recentDivs = instrument.dividends.slice(-6).reverse();

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        {data.instruments.map((i) => (
          <Pill key={i.isin} monospace active={i.isin === instrument.isin} onClick={() => onSelect(i.isin)}>
            {i.symbol}
          </Pill>
        ))}
      </div>

      <Panel style={{ padding: "16px 18px" }}>
        <div style={{ display: "flex", alignItems: "flex-start", gap: 20, flexWrap: "wrap" }}>
          <div style={{ minWidth: 0 }}>
            <div style={{ fontSize: 19, fontWeight: 600, letterSpacing: "-0.02em" }}>{instrument.name}</div>
            <div
              style={{
                fontFamily: mono,
                fontSize: 10.5,
                color: c.textFaint,
                marginTop: 5,
                letterSpacing: "0.02em",
              }}
            >
              {instrument.isin} · {instrument.symbol} · {instrument.currency}
            </div>
            <div style={{ fontSize: 11, color: c.textMuted, marginTop: 6 }}>
              {instrument.sector} · {instrument.industry}
            </div>
          </div>
          <div style={{ marginLeft: "auto", minWidth: "min(100%, 460px)" }}>
            <TileGrid
              minWidth={112}
              valueSize={14}
              background={c.inset}
              tiles={[
                { label: "PRICE", value: eur(instrument.current) },
                { label: "QTY HELD", value: num(match.qty, 0), color: c.textSecondary },
                { label: "POSITION", value: eurCompact(match.qty * instrument.current), color: c.textSecondary },
                {
                  label: "BLENDED COST",
                  value: match.qty ? eur(match.cost / match.qty) : "—",
                  color: c.textMuted,
                },
                { label: "REALISED", value: eurSigned(match.realised), color: signColor(match.realised) },
              ]}
            />
          </div>
        </div>
      </Panel>

      <Panel
        title="B · Price and my activity"
        subtitle="Solid where I held. Dotted where I was flat — the stretch you stop watching."
        footer={
          <span style={{ display: "flex", gap: 18, flexWrap: "wrap" }}>
            <span>▲ buy&nbsp;&nbsp;▼ sell — marker size scales with quantity; hover for the lot</span>
            <span style={{ marginLeft: "auto" }}>Shaded bands = position &gt; 0</span>
          </span>
        }
      >
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", margin: "0 0 10px" }}>
          <SeriesChip
            label="World proxy"
            color={c.neutral}
            lineStyle="dotted"
            active={showBench}
            onToggle={() => setShowBench((v) => !v)}
          />
          <SeriesChip
            label="Industry proxy"
            color={c.violet}
            lineStyle="dashed"
            active={showIndustry}
            onToggle={() => setShowIndustry((v) => !v)}
          />
          <SeriesChip
            label="Dividends"
            color={c.modelled}
            lineStyle="solid"
            active={showDivs}
            onToggle={() => setShowDivs((v) => !v)}
          />
          <div style={{ marginLeft: "auto" }}>
            <SegmentedControl options={RANGES} value={range} onChange={setRange} size="sm" />
          </div>
        </div>

        <div style={{ position: "relative" }}>
          <LineChart
            lines={[
              { values: flatLine, color: c.textFaint, width: 1.5, dash: "3 4" },
              { values: heldLine, color: c.accent, width: 2.1 },
              ...overlays,
            ]}
            height={280}
            from={from}
            bands={bands}
            markers={markers}
            dots={dots}
            formatY={(v) => num(v, 0)}
            onHover={setHover}
          />
          <ChartTooltip hover={hover} />
        </div>
      </Panel>

      <Panel
        title="A · Lots and closures"
        subtitle={
          <span style={{ color: c.modelled }}>
            Matched under {method} — changing the method recomputes every realised figure below.
          </span>
        }
      >
        <div style={{ overflowX: "auto" }}>
          <Table minWidth={1180}>
            <HeadRow columns={LOT_COLUMNS} />
            <tbody>
              {[...closureRows, ...openRows].map((r) => (
                <tr key={r.key} style={{ borderBottom: `1px solid ${c.borderSoft}`, background: r.background }}>
                  <Td padding="9px 10px" numeric color={c.textMuted}>{r.id}</Td>
                  <Td padding="9px 10px" numeric>{r.opened}</Td>
                  <Td padding="9px 10px" align="right" numeric>{r.qty}</Td>
                  <Td padding="9px 10px" align="right" numeric color={c.textMuted}>{r.costPer}</Td>
                  <Td padding="9px 10px" numeric>{r.closed}</Td>
                  <Td padding="9px 10px" align="right" numeric>{r.qtyClosed}</Td>
                  <Td padding="9px 10px" align="right" numeric color={c.textMuted}>{r.pricePer}</Td>
                  <Td padding="9px 10px">
                    <Badge color={r.statusColor} background={r.statusBg}>{r.status}</Badge>
                  </Td>
                  <Td padding="9px 10px" align="right" numeric color={r.pnlColor}>{r.pnl}</Td>
                  <Td padding="9px 10px" align="right" numeric color={r.pnlColor}>{r.pnlPct}</Td>
                  <Td padding="9px 10px" align="right" numeric color={c.textMuted}>{r.annualised}</Td>
                  <Td padding="9px 10px" align="right" numeric color={c.textFaint}>{r.held}</Td>
                  <Td padding="9px 10px" align="right" numeric color={c.textFaint}>{r.fees}</Td>
                  <Td
                    padding="9px 10px"
                    align="right"
                    numeric
                    color={r.counterfactualColor}
                    style={{ borderLeft: "1px dashed #33291270", background: "#17140A80" }}
                  >
                    {r.counterfactual}
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        </div>
        <div style={{ fontSize: 10.5, color: c.textFaint, marginTop: 10, lineHeight: 1.6 }}>
          <span style={{ color: c.positive }}>■</span> realised&nbsp;
          <span style={{ color: c.accent }}>■</span> unrealised&nbsp;·&nbsp;
          <span style={{ color: c.modelled }}>If held to today</span> assumes the closed quantity was
          never sold and no proceeds were redeployed. Modelled, not actual.
        </div>
      </Panel>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(390px, 1fr))", gap: 12 }}>
        <Panel title="C · In-market vs out-of-market" subtitle="What it did while I wasn't holding.">
          <Table>
            <tbody>
              <tr>
                {(["STATE", "SPAN", "DAYS", "ITS RETURN", "EFFECT"] as const).map((label, i) => (
                  <th
                    key={label}
                    style={{
                      textAlign: i >= 2 ? "right" : "left",
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
              {intervals
                .filter((v) => v.to - v.from >= 1)
                .map((v, i) => (
                  <tr key={i} style={{ borderBottom: `1px solid ${c.borderSoft}` }}>
                    <Td padding="8px 9px">
                      <Badge
                        color={v.inMarket ? c.accent : c.textMuted}
                        background={v.inMarket ? c.accentBg : c.neutralBg}
                      >
                        {v.inMarket ? "HELD" : "FLAT"}
                      </Badge>
                    </Td>
                    <Td padding="8px 9px" numeric nowrap>
                      {monthLabel(v.from)} → {v.to >= last ? "now" : monthLabel(v.to)}
                    </Td>
                    <Td padding="8px 9px" align="right" numeric color={c.textFaint}>
                      {num((v.to - v.from) * 30.4, 0)}
                    </Td>
                    <Td padding="8px 9px" align="right" numeric color={signColor(v.ret)}>
                      {pct(v.ret)}
                    </Td>
                    {/* While flat, a rise is a miss and a fall is an escape, so
                        the colour inverts relative to the return beside it. */}
                    <Td
                      padding="8px 9px"
                      align="right"
                      color={v.inMarket ? c.textFaint : v.ret > 0 ? c.negative : c.positive}
                      style={{ fontSize: 11 }}
                    >
                      {v.inMarket ? "captured" : v.ret > 0 ? `missed ${pct(v.ret)}` : `avoided ${pct(-v.ret)}`}
                    </Td>
                  </tr>
                ))}
            </tbody>
          </Table>
        </Panel>

        <Panel
          title="E · Dividends from this instrument"
          subtitle={
            instrument.dividends.length
              ? `Last ${Math.min(6, instrument.dividends.length)} of ${instrument.dividends.length} payments · ${pctPlain(instrument.withholding)} withholding (${instrument.country})`
              : "No distributions — this instrument is accumulating or pays nothing."
          }
        >
          <Table>
            <tbody>
              <tr>
                {(["DATE", "GROSS", "WH TAX", "NET"] as const).map((label, i) => (
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
              {recentDivs.map((d) => (
                <tr key={d.date} style={{ borderBottom: `1px solid ${c.borderSoft}` }}>
                  <Td padding="7px 9px" numeric>{shortDate(d.date)}</Td>
                  <Td padding="7px 9px" align="right" numeric color={c.textMuted}>{eur(d.gross)}</Td>
                  <Td padding="7px 9px" align="right" numeric color={c.negative}>−{eur(d.tax)}</Td>
                  <Td padding="7px 9px" align="right" numeric color={c.positive}>{eur(d.net)}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
          <div style={{ marginTop: 12 }}>
            <TileStrip
              tiles={[
                { label: "LIFETIME NET", value: eur(divNet, 0) },
                {
                  label: "YIELD ON COST · CUMUL.",
                  value: match.cost > 0 ? pctPlain(divNet / match.cost) : "—",
                },
                {
                  label: "YIELD ON MARKET · ANN.",
                  value:
                    match.qty > 0 && instrument.dividends.length
                      ? pctPlain(
                          ((divNet / instrument.dividends.length) * (instrument.dividendFrequency || 1)) /
                            (match.qty * instrument.current),
                          2,
                        )
                      : "—",
                },
              ]}
            />
          </div>
        </Panel>
      </div>

      <Panel
        title="D · Instrument vs market vs industry"
        subtitle="Rebased to 100 at my first purchase."
        actions={
          <>
            <SeriesChip label={instrument.symbol} color={c.accent} lineStyle="solid" active />
            <SeriesChip label="IWDA.AS" color={c.neutral} lineStyle="dotted" active />
            <SeriesChip label="MEUD.PA" color={c.violet} lineStyle="dashed" active />
          </>
        }
      >
        <LineChart
          lines={[
            { values: rebase(instrument.prices, compareFrom), color: c.accent, width: 2.1 },
            { values: rebase(iwda, compareFrom), color: c.neutral, width: 1.4, dash: "2 3" },
            { values: rebase(meud, compareFrom), color: c.violet, width: 1.4, dash: "6 3" },
          ]}
          height={210}
          from={compareFrom}
          formatY={(v) => num(v, 0)}
        />
        <div style={{ margin: "8px 0 4px" }}>
          <TileGrid
            minWidth={180}
            valueSize={14}
            background={c.inset}
            tiles={[
              { label: "MY HOLDING PERIOD", value: pct(retInstrument), color: signColor(retInstrument) },
              {
                label: "EXCESS VS WORLD",
                value: pct(retInstrument - retWorld),
                color: signColor(retInstrument - retWorld),
              },
              {
                label: "EXCESS VS EUROPE",
                value: pct(retInstrument - retEurope),
                color: signColor(retInstrument - retEurope),
              },
            ]}
          />
        </div>
      </Panel>

      <Panel style={{ background: c.sunken, display: "flex", alignItems: "center", gap: 16, flexWrap: "wrap" }}>
        <div>
          <div style={{ fontSize: 12.5, fontWeight: 600, color: c.textMuted }}>F · News &amp; events</div>
          <div style={{ fontSize: 11, color: c.textFaint, marginTop: 3 }}>
            Never auto-loaded. Fetches count against the provider's free tier; cached results render
            instantly on revisit.
          </div>
        </div>
        <div style={{ marginLeft: "auto" }}>
          <ActionButton>Fetch events for visible range</ActionButton>
        </div>
      </Panel>
    </div>
  );
}
