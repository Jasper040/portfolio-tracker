/** The portfolio's time-weighted return, against one benchmark (M6a). The fifth
 *  screen reading the live ledger.
 *
 *  It fetches one endpoint and draws what came back: every figure is decided on
 *  the backend, and every money and return string stays a string here (see
 *  `api/types.ts`). The chart's one numeric boundary is `indexOnDates` in
 *  `lib/performance.ts`.
 *
 *  Four rules from the M6a design decide what is on screen:
 *
 *  - **No single figure spans a gap** (M6a-7). `linked_return` is `null` across
 *    one, so the headline tile reads "—" with the API's reason, the line breaks,
 *    and the runs table still carries one figure per run.
 *  - **Every return carries its basis** (parent doc Sec 7.4): time-weighted on
 *    the unadjusted close plus cash for the portfolio, total return for the
 *    benchmark.
 *  - **The comparison says how each side treats dividends** (M6a-10). Both
 *    differences favour the benchmark, and a reader shown only the excess
 *    cannot tell how much of it is withholding and reinvestment.
 *  - **`null` is "—", never "0,00%"** (parent doc Sec 8.1).
 */

import { useEffect, useMemo, useState } from "react";

import { fetchBenchmarks, fetchPerformance } from "../api/client";
import type {
  Benchmark,
  PerformanceReport,
  PortfolioComparison,
  ReturnRun,
  RunExcess,
} from "../api/types";
import { EChart } from "../components/charts/EChart";
import {
  BenchmarkSelector,
  BenchmarkSpanBadge,
  terLabel,
} from "../components/ui/BenchmarkControls";
import { SegmentedControl } from "../components/ui/Controls";
import { MethodBadge } from "../components/ui/MethodBadge";
import { Notice } from "../components/ui/Notice";
import { Panel } from "../components/ui/Panel";
import {
  HeadRow,
  Table,
  TableFrame,
  Td,
  rowBackground,
  type ColumnDef,
} from "../components/ui/Table";
import { TileGrid, type Tile } from "../components/ui/Tiles";
import { decimalPercent, decimalSignColour, shortDate } from "../lib/format";
import { dividendTreatment, performanceChartOption } from "../lib/performance";
import { c, mono } from "../lib/theme";
import { RANGE_PRESETS, rangeStart, type RangePreset } from "../lib/valuation";

const RUN_COLUMNS: readonly ColumnDef[] = [
  { label: "FROM CLOSE" },
  { label: "TO CLOSE" },
  { label: "DAYS", align: "right" },
  { label: "TWR", align: "right" },
  { label: "COVERAGE", align: "right" },
  { label: "BENCH (TR)", align: "right" },
  { label: "EXCESS", align: "right" },
];

/** A run's benchmark row, matched on its span and never on array position. */
function excessFor(comparison: PortfolioComparison | null, run: ReturnRun): RunExcess | null {
  if (comparison === null) return null;
  return comparison.runs.find((row) => row.start === run.start && row.end === run.end) ?? null;
}

function excessNote(report: PerformanceReport): string | null {
  const comparison = report.comparison;
  if (comparison === null) return "Pick a benchmark to compare";
  if (comparison.excess !== null) return "portfolio − benchmark, arithmetic";
  return report.reason ?? comparison.runs[0]?.reason ?? null;
}

function headlineTiles(report: PerformanceReport, benchmark: Benchmark | null): Tile[] {
  const comparison = report.comparison;
  const window =
    report.start !== null && report.end !== null
      ? `${shortDate(report.start)} – ${shortDate(report.end)}`
      : "";
  return [
    {
      label: "TIME-WEIGHTED RETURN",
      value: decimalPercent(report.linked_return),
      color: decimalSignColour(report.linked_return),
      sub: report.linked_return === null ? report.reason : `${window} · ${report.basis}`,
    },
    {
      label: "BENCHMARK (TOTAL RETURN)",
      value: comparison ? decimalPercent(comparison.benchmark_return) : "—",
      sub: benchmark ? `${benchmark.name} · ${terLabel(benchmark.ter)}` : "No benchmark selected",
    },
    {
      label: "EXCESS",
      value: comparison ? decimalPercent(comparison.excess) : "—",
      color: decimalSignColour(comparison?.excess ?? null),
      sub: excessNote(report),
    },
    {
      label: "GAPS",
      value: String(report.gaps),
      sub: `${report.runs.length} ${report.runs.length === 1 ? "run" : "runs"} measured`,
    },
  ];
}

/** Both sides' labels, in words. The comparison is not readable without them. */
function LabelsFooter({ report, benchmark }: { report: PerformanceReport; benchmark: Benchmark | null }) {
  const comparison = report.comparison;
  return (
    <>
      Portfolio: {report.basis} on {report.lane} — {dividendTreatment(report.dividends)}.
      {comparison !== null && (
        <>
          {" "}
          Benchmark{benchmark ? ` (${benchmark.name})` : ""}: {comparison.basis} —{" "}
          {dividendTreatment(comparison.dividends)}. Both differences favour the benchmark,
          and neither is adjusted away.
          {benchmark !== null && (
            <>
              {" "}
              {benchmark.name} carries {terLabel(benchmark.ter)}, reported and never subtracted.
            </>
          )}
        </>
      )}
    </>
  );
}

function ExcessCell({ row }: { row: RunExcess | null }) {
  if (row === null) return <span style={{ color: c.textFaint }}>—</span>;
  if (row.excess === null) {
    return (
      <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 2 }}>
        <span style={{ color: c.textFaint }}>—</span>
        <span style={{ fontSize: 9.5, color: c.textFaint, textAlign: "right" }}>{row.reason}</span>
      </div>
    );
  }
  return <span style={{ color: decimalSignColour(row.excess) }}>{decimalPercent(row.excess)}</span>;
}

export interface PerformanceProps {
  /** Bumped by the header's refresh control once it has cleared the response
   *  cache, so both effects below re-run against an empty one. Optional and
   *  defaulted, so a caller that never refreshes needs no change. */
  refreshToken?: number;
}

export function Performance({ refreshToken = 0 }: PerformanceProps) {
  const [range, setRange] = useState<RangePreset>("MAX");
  const [benchmarkKey, setBenchmarkKey] = useState<string | null>(null);
  const [benchmarks, setBenchmarks] = useState<Benchmark[]>([]);
  const [benchmarksError, setBenchmarksError] = useState<string | null>(null);
  const [report, setReport] = useState<PerformanceReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // The benchmark list does not depend on anything the reader picks -- only on
  // the operator saying the configuration may have changed underneath us.
  useEffect(() => {
    let cancelled = false;
    fetchBenchmarks()
      .then((items) => {
        if (cancelled) return;
        setBenchmarks(items);
        setBenchmarksError(null);
      })
      .catch((cause: unknown) => {
        // Non-fatal, never silent: `BenchmarkSelector` renders a failed list
        // differently from an empty configured one.
        if (cancelled) return;
        setBenchmarksError(cause instanceof Error ? cause.message : String(cause));
      });
    return () => {
      cancelled = true;
    };
  }, [refreshToken]);

  // The cancellation guard: a slow MAX response landing after a fast 1Y one
  // would otherwise draw the whole ledger under a one-year control.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchPerformance({ from: rangeStart(range, new Date()), benchmark: benchmarkKey })
      .then((data) => {
        if (cancelled) return;
        setReport(data);
        setLoading(false);
      })
      .catch((cause: unknown) => {
        if (cancelled) return;
        setError(cause instanceof Error ? cause.message : String(cause));
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [range, benchmarkKey, refreshToken]);

  const activeBenchmark = benchmarks.find((b) => b.key === benchmarkKey) ?? null;
  const option = useMemo(
    () =>
      report ? performanceChartOption(report, { benchmarkLabel: activeBenchmark?.name ?? null }) : null,
    [report, activeBenchmark],
  );

  if (error) {
    return (
      <Notice tone="danger">
        Could not reach the API. {error}. Start it with{" "}
        <code style={{ fontFamily: mono }}>python -m uvicorn app.main:create_app --factory</code>,
        and check that CORS_ORIGINS in backend/.env lists this port.
      </Notice>
    );
  }

  if (report === null || option === null) {
    return <div style={{ fontSize: 12, color: c.textFaint }}>Loading…</div>;
  }

  if (report.links.length === 0) {
    return (
      <Notice tone="modelled">
        Nothing to measure yet. Import an export, then run{" "}
        <code style={{ fontFamily: mono }}>python -m app.cli fetch-prices</code> and{" "}
        <code style={{ fontFamily: mono }}>python -m app.cli rebuild</code>.
      </Notice>
    );
  }

  const comparison = report.comparison;
  const shownBenchmark = comparison !== null ? activeBenchmark : null;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
        {/* The window's price coverage -- staleness, as on Positions. `method`
            is always null: a time-weighted return ran no lot matching. */}
        <MethodBadge method={report.method} coverage={report.coverage} />
        {comparison && <BenchmarkSpanBadge span={comparison.span} />}
        <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 10 }}>
          {loading && (
            <span style={{ fontSize: 10, color: c.textFaint, fontFamily: mono }}>UPDATING…</span>
          )}
          <SegmentedControl options={RANGE_PRESETS} value={range} onChange={setRange} label="RANGE" size="sm" />
        </div>
      </div>

      <TileGrid tiles={headlineTiles(report, shownBenchmark)} />

      <Panel
        title="Time-weighted return"
        subtitle="Rebased to 100 at the start of each run — a break in the line is a gap no figure spans"
        actions={
          <BenchmarkSelector
            benchmarks={benchmarks}
            error={benchmarksError}
            selected={benchmarkKey}
            onSelect={setBenchmarkKey}
          />
        }
        footer={<LabelsFooter report={report} benchmark={shownBenchmark} />}
      >
        <EChart option={option} height={300} />
        {report.clamped && report.requested_from !== null && report.start !== null && (
          <div style={{ fontSize: 11, color: c.modelled, marginTop: 8 }}>
            Asked for {shortDate(report.requested_from)}; measurement begins{" "}
            {shortDate(report.start)}. Showing everything there is rather than padding the difference.
          </div>
        )}
      </Panel>

      <TableFrame>
        <Table minWidth={760}>
          <HeadRow columns={RUN_COLUMNS} />
          <tbody>
            {report.runs.map((run, index) => {
              const row = excessFor(comparison, run);
              return (
                <tr
                  key={`${run.start}-${run.end}`}
                  style={{ background: rowBackground(index), borderBottom: `1px solid ${c.borderSoft}` }}
                >
                  <Td numeric>{shortDate(run.start)}</Td>
                  <Td numeric>{shortDate(run.end)}</Td>
                  <Td align="right" numeric>{run.days}</Td>
                  <Td align="right" numeric color={decimalSignColour(run.linked_return)}>
                    {decimalPercent(run.linked_return)}
                  </Td>
                  <Td align="right" numeric color={run.coverage === "full" ? c.textFaint : c.modelled}>
                    {run.coverage}
                  </Td>
                  <Td align="right" numeric color={decimalSignColour(row?.benchmark_return ?? null)}>
                    {row ? decimalPercent(row.benchmark_return) : "—"}
                  </Td>
                  <Td align="right" numeric>
                    <ExcessCell row={row} />
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
