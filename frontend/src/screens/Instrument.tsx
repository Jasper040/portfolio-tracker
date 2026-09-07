/** One instrument's priced line against a benchmark. The fourth screen reading
 *  the live ledger, and Task 9's whole job: Task 8 already built the fetch, the
 *  types and the pure option builder in `lib/instrument.ts`, so this screen
 *  makes no drawing decisions of its own -- it only decides what to fetch and
 *  how to render what came back.
 *
 *  The instrument picker reads from `fetchPositions`, the same live endpoint
 *  `Positions.tsx` uses, rather than the modelled dataset `StockDetail.tsx`
 *  picks from. That is a real narrowing -- only instruments with an open
 *  position can be charted here, not every instrument the ledger ever saw --
 *  chosen because every other candidate list on this screen (transactions,
 *  lots) would have needed its own dedup pass for one picker, and this repo
 *  already has a live, deduplicated list of ISINs sitting behind one call.
 *
 *  Two coverages appear on this screen and they are NOT the same judgement.
 *  `chart.coverage` is the instrument's own price series, and reuses
 *  `MethodBadge` exactly as `Positions.tsx` does for a lot-matched response --
 *  `method` is always `null` here, which is a real answer, not an omission
 *  (see `api/types.ts`). `chart.comparison.coverage` is the BENCHMARK's own
 *  SPAN across the holding, a different measurement with the same vocabulary
 *  (`backend/app/analytics/instrument_return.py`'s `Comparison.coverage`
 *  docstring spells out why). Rendering it through `MethodBadge` would make it
 *  look like the same kind of coverage; `BenchmarkSpanBadge` below exists so it
 *  cannot be mistaken for one.
 *
 *  Every excess figure carries `comparison.basis` next to it -- the API sends
 *  `"total_return"`, and showing a return with no basis label is exactly what
 *  design doc Sec 7.4 rules out. A `null` excess renders "—" and the `reason`
 *  the API gave, never a bare dash and never "0,00%": Task 7 spent a full fix
 *  round making sure an instrument-side span shortfall reaches no coverage
 *  badge of its own, which means `reason` is the ONLY place that fact is
 *  visible.
 *
 *  Money and index figures stay strings end to end -- `decimalPercent` and
 *  `decimal`-family formatters re-punctuate the exact string the API sent.
 *  Nothing on this screen is passed through `Number()`; the chart's own two
 *  numeric boundaries (`toPlotValue`, `markerSymbolSize`) live in
 *  `lib/instrument.ts` with their reasons written down. See `api/types.ts`.
 */

import type { ReactNode } from "react";
import { useEffect, useMemo, useState } from "react";

import { fetchBenchmarks, fetchInstrumentChart, fetchPositions, type Range } from "../api/client";
import type {
  Benchmark,
  Comparison,
  Coverage,
  Interval,
  InstrumentChart,
  IntervalExcess,
  LotMethodTag,
  PositionsPage,
} from "../api/types";
import { EChart } from "../components/charts/EChart";
import { Pill, SegmentedControl } from "../components/ui/Controls";
import { MethodBadge } from "../components/ui/MethodBadge";
import { Notice } from "../components/ui/Notice";
import { Panel } from "../components/ui/Panel";
import { HeadRow, Table, TableFrame, Td, rowBackground, type ColumnDef } from "../components/ui/Table";
import { decimalIsNegative, decimalPercent, shortDate } from "../lib/format";
import { instrumentChartOption } from "../lib/instrument";
import { c, mono } from "../lib/theme";

const RANGE_OPTIONS: readonly Range[] = ["1Y", "3Y", "5Y", "max"];

const INTERVAL_COLUMNS: readonly ColumnDef[] = [
  { label: "START" },
  { label: "END" },
  { label: "STATUS" },
  { label: "PRICE RETURN", align: "right" },
  { label: "INSTR RETURN (TR)", align: "right" },
  { label: "BENCH RETURN (TR)", align: "right" },
  { label: "EXCESS", align: "right" },
];

/** Same tone table as `MethodBadge`'s, kept private and separate on purpose:
 *  the two badges must never share a component, or a future edit to one would
 *  silently restyle the other into looking like the same judgement. */
const SPAN_TONE: Record<Coverage, { color: string; label: string }> = {
  full: { color: c.textMuted, label: "FULL" },
  partial: { color: c.modelled, label: "PARTIAL" },
  manual: { color: c.modelled, label: "MANUAL" },
  missing: { color: c.negative, label: "MISSING" },
};

/** The benchmark's own span coverage. Deliberately not `MethodBadge`: that
 *  component's "COVERAGE" label is the staleness judgement everywhere else in
 *  this app, and this is a span judgement instead -- see this file's own
 *  docstring and `Comparison.coverage` on the backend. */
function BenchmarkSpanBadge({ coverage }: { coverage: Coverage }) {
  const tone = SPAN_TONE[coverage];
  return (
    <span
      title="How much of the holding the benchmark series itself spans -- not how stale it is. A different question from the coverage badge above."
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        border: `1px solid ${c.borderStrong}`,
        borderRadius: 4,
        padding: "4px 9px",
        fontSize: 11,
        whiteSpace: "nowrap",
      }}
    >
      <span style={{ fontFamily: mono, fontSize: 9.5, color: c.textFaint }}>BENCHMARK SPAN</span>
      <span style={{ fontFamily: mono, color: tone.color }}>{tone.label}</span>
    </span>
  );
}

function signColour(value: string | null): string {
  if (value == null) return c.textMuted;
  return decimalIsNegative(value) ? c.negative : c.positive;
}

/** Matches a holding interval to its excess row by date, never by array
 *  position -- `comparison.intervals` carries in-market intervals only (an
 *  out-of-market stretch is excluded rather than filled with nulls, per
 *  `Comparison.intervals`'s own docstring), so the two arrays are not
 *  guaranteed to line up index for index. */
function excessFor(comparison: Comparison | null, interval: Interval): IntervalExcess | null {
  if (comparison === null) return null;
  return comparison.intervals.find((row) => row.start === interval.start && row.end === interval.end) ?? null;
}

/** The excess cell. `null` renders "—" plus `reason` -- the one place an
 *  instrument-side span shortfall is visible at all (see this file's
 *  docstring) -- and a real figure always carries `basis` beside it so it can
 *  never be mistaken for a plain total-weighted-return number. */
function ExcessCell({ row, basis }: { row: IntervalExcess | null; basis: string }): ReactNode {
  if (row === null) return <span style={{ color: c.textFaint }}>—</span>;

  if (row.excess === null) {
    return (
      <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 2 }}>
        <span style={{ color: c.textFaint }}>—</span>
        <span style={{ fontSize: 9.5, color: c.textFaint, textAlign: "right" }}>{row.reason}</span>
      </div>
    );
  }

  return (
    <span>
      <span style={{ color: signColour(row.excess) }}>{decimalPercent(row.excess)}</span>{" "}
      <span style={{ fontSize: 9.5, color: c.textFaint }}>({basis})</span>
    </span>
  );
}

export interface InstrumentProps {
  method: LotMethodTag;
}

export function Instrument({ method }: InstrumentProps) {
  const [range, setRange] = useState<Range>("1Y");
  const [benchmarkKey, setBenchmarkKey] = useState<string | null>(null);
  const [isin, setIsin] = useState<string | null>(null);

  const [positions, setPositions] = useState<PositionsPage | null>(null);
  const [positionsError, setPositionsError] = useState<string | null>(null);

  const [benchmarks, setBenchmarks] = useState<Benchmark[]>([]);

  const [chart, setChart] = useState<InstrumentChart | null>(null);
  const [chartError, setChartError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // The picker's own candidate list: currently open live positions. Runs again
  // on a method change for the same reason `Positions.tsx` refetches -- the
  // set of open positions can differ by lot method -- but never overwrites an
  // ISIN the reader already picked.
  useEffect(() => {
    let cancelled = false;
    fetchPositions(method)
      .then((page) => {
        if (cancelled) return;
        setPositions(page);
        setPositionsError(null);
        setIsin((current) => current ?? page.items[0]?.isin ?? null);
      })
      .catch((cause: unknown) => {
        if (cancelled) return;
        setPositionsError(cause instanceof Error ? cause.message : String(cause));
      });
    return () => {
      cancelled = true;
    };
  }, [method]);

  // The benchmark list. Independent of everything the reader picks, so it is
  // fetched exactly once.
  useEffect(() => {
    let cancelled = false;
    fetchBenchmarks()
      .then((items) => {
        if (!cancelled) setBenchmarks(items);
      })
      .catch(() => {
        // Non-fatal: the chart still draws without a benchmark list, it just
        // has nothing to offer in the selector.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // The chart itself. The cancellation guard matters here even more than on
  // Positions: switching instruments AND changing the range both restart this
  // effect, and a slow response for the instrument just left would otherwise
  // land under the instrument just opened.
  useEffect(() => {
    if (isin === null) return undefined;
    let cancelled = false;
    setLoading(true);
    setChartError(null);

    fetchInstrumentChart(isin, { range, benchmark: benchmarkKey })
      .then((data) => {
        if (cancelled) return;
        setChart(data);
        setLoading(false);
      })
      .catch((cause: unknown) => {
        if (cancelled) return;
        setChartError(cause instanceof Error ? cause.message : String(cause));
        setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [isin, range, benchmarkKey]);

  const option = useMemo(
    () => (chart ? instrumentChartOption(chart, { showBenchmark: benchmarkKey !== null }) : null),
    [chart, benchmarkKey],
  );

  if (positionsError) {
    return (
      <Notice tone="danger">
        Could not reach the API. {positionsError}. Start it with{" "}
        <code style={{ fontFamily: mono }}>
          python -m uvicorn app.main:create_app --factory
        </code>
        , and check that CORS_ORIGINS in backend/.env lists this port.
      </Notice>
    );
  }

  if (positions === null) {
    return <div style={{ fontSize: 12, color: c.textFaint }}>Loading…</div>;
  }

  if (positions.items.length === 0) {
    return (
      <Notice tone="modelled">
        No open live positions to chart yet. Import an export, then run{" "}
        <code style={{ fontFamily: mono }}>python -m app.cli fetch-prices</code> and{" "}
        <code style={{ fontFamily: mono }}>python -m app.cli rebuild</code>.
      </Notice>
    );
  }

  if (chartError) {
    return <Notice tone="danger">Could not load the chart. {chartError}.</Notice>;
  }

  if (loading || chart === null || option === null) {
    return <div style={{ fontSize: 12, color: c.textFaint }}>Loading…</div>;
  }

  const selected = positions.items.find((p) => p.isin === isin);
  const comparison = chart.comparison;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {positions.items.map((p) => (
            <Pill key={p.isin} monospace active={p.isin === isin} onClick={() => setIsin(p.isin)}>
              {p.product_name || p.isin}
            </Pill>
          ))}
        </div>
        <div style={{ marginLeft: "auto" }}>
          <SegmentedControl options={RANGE_OPTIONS} value={range} onChange={setRange} label="RANGE" size="sm" />
        </div>
      </div>

      <Panel
        title={selected?.product_name || chart.isin}
        subtitle={`${chart.isin} — solid where held, blank where flat`}
        actions={
          benchmarks.length > 0 ? (
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
              <span style={{ fontFamily: mono, fontSize: 10, color: c.textFaint }}>BENCHMARK</span>
              <Pill monospace active={benchmarkKey === null} onClick={() => setBenchmarkKey(null)}>
                None
              </Pill>
              {benchmarks.map((b) => (
                <Pill
                  key={b.key}
                  monospace
                  active={benchmarkKey === b.key}
                  onClick={() => setBenchmarkKey(b.key)}
                >
                  {b.name}
                </Pill>
              ))}
            </div>
          ) : undefined
        }
        footer={
          comparison && (
            <>
              Linked over the range: instrument {decimalPercent(comparison.linked_instrument_return)}, benchmark{" "}
              {decimalPercent(comparison.linked_benchmark_return)}, excess{" "}
              {decimalPercent(comparison.linked_excess)} — basis: {comparison.basis}.
            </>
          )
        }
      >
        <EChart option={option} height={320} />
        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", marginTop: 10 }}>
          {/* The instrument's own price coverage -- a staleness judgement,
              exactly as it is on Positions and Lots. `method` is always `null`
              here, which `MethodBadge` renders as NOT LOT-MATCHED: a real
              answer, since a price series has no lot method. */}
          <MethodBadge method={chart.method} coverage={chart.coverage} />
          {/* The benchmark's own span coverage -- a different judgement, kept
              in its own component so it cannot read as the same badge above. */}
          {comparison && <BenchmarkSpanBadge coverage={comparison.coverage} />}
        </div>
      </Panel>

      <TableFrame>
        <Table minWidth={860}>
          <HeadRow columns={INTERVAL_COLUMNS} />
          <tbody>
            {chart.intervals.map((interval, index) => {
              const row = excessFor(comparison, interval);
              return (
                <tr
                  key={`${interval.start}-${interval.end}`}
                  style={{ background: rowBackground(index), borderBottom: `1px solid ${c.borderSoft}` }}
                >
                  <Td numeric>{shortDate(interval.start)}</Td>
                  <Td numeric>{shortDate(interval.end)}</Td>
                  <Td color={interval.in_market ? c.text : c.textFaint}>
                    {interval.in_market ? "In market" : "Out of market"}
                  </Td>
                  <Td align="right" numeric color={signColour(interval.price_return)}>
                    {decimalPercent(interval.price_return)}
                  </Td>
                  <Td align="right" numeric color={signColour(row?.instrument_return ?? null)}>
                    {row ? decimalPercent(row.instrument_return) : "—"}
                  </Td>
                  <Td align="right" numeric color={signColour(row?.benchmark_return ?? null)}>
                    {row ? decimalPercent(row.benchmark_return) : "—"}
                  </Td>
                  <Td align="right" numeric>
                    <ExcessCell row={row} basis={comparison?.basis ?? ""} />
                  </Td>
                </tr>
              );
            })}
          </tbody>
        </Table>
      </TableFrame>
    </div>
  );
}
