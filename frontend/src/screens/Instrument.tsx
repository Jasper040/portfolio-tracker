/** One instrument's priced line against a benchmark. The fourth screen reading
 *  the live ledger, and Task 9's whole job: Task 8 already built the fetch, the
 *  types and the pure option builder in `lib/instrument.ts`, so this screen
 *  makes no drawing decisions of its own -- it only decides what to fetch and
 *  how to render what came back.
 *
 *  The instrument picker reads from `fetchInstruments`, not `fetchPositions`.
 *  That distinction is load-bearing (fix round after the first review): a
 *  fully exited instrument has no open position, and is exactly the clearest
 *  "out of market" case this milestone exists to make visible -- narrowing
 *  the picker to open positions would make that instrument's own showcase
 *  chart unreachable from the UI even though `/api/instruments/{isin}/chart`
 *  answers for it perfectly well. `fetchInstruments` is also not the modelled
 *  dataset `StockDetail.tsx` picks from -- it is a live, already-deduplicated
 *  endpoint over every economic transaction the ledger has ever recorded.
 *
 *  Two coverages appear on this screen and they are NOT the same judgement.
 *  `chart.coverage` is the instrument's own price series, and reuses
 *  `MethodBadge` exactly as `Positions.tsx` does for a lot-matched response --
 *  `method` is always `null` here, which is a real answer, not an omission
 *  (see `api/types.ts`). `chart.comparison.span` is the BENCHMARK's own
 *  SPAN across the holding, a different measurement with the same vocabulary
 *  (`backend/app/analytics/instrument_return.py`'s `Comparison.coverage`
 *  docstring spells out why). Rendering it through `MethodBadge` would make it
 *  look like the same kind of coverage; `BenchmarkSpanBadge`, in
 *  `components/ui/BenchmarkControls.tsx`, exists so it cannot be mistaken for one.
 *
 *  Every excess figure carries `comparison.basis` next to it -- the API sends
 *  `"total_return"`, and showing a return with no basis label is exactly what
 *  design doc Sec 7.4 rules out. A `null` excess renders "—" and the `reason`
 *  the API gave, never a bare dash and never "0,00%": Task 7 spent a full fix
 *  round making sure an instrument-side span shortfall reaches no coverage
 *  badge of its own, which means `reason` is the ONLY place that fact is
 *  visible.
 *
 *  The benchmark control always renders something, even when there is nothing
 *  to pick. `config/benchmarks.yaml` ships with every entry commented out, so
 *  an empty list is the ordinary first-run state, and drawing no control at all
 *  left a reader with no way to tell the comparison feature existed. A FAILED
 *  fetch is surfaced separately from an empty set -- see `BenchmarkSelector` --
 *  because collapsing the two made a broken endpoint indistinguishable from a
 *  configuration choice.
 *
 *  Each proxy's TER travels with it: beside its name in the selector, and beside
 *  every excess figure it produced. Section 4.3 requires the TER be documented
 *  as proxy drag and section 8.3 says why it has to sit next to the number --
 *  a holding that beats the proxy by less than the TER has not necessarily
 *  beaten the market. It is a PERCENTAGE per year on the wire, so `terLabel`
 *  re-punctuates it with `decimal` and never `decimalPercent`.
 *
 *  Money and index figures stay strings end to end -- `decimalPercent` and
 *  `decimal`-family formatters re-punctuate the exact string the API sent.
 *  Nothing on this screen is passed through `Number()`; the chart's own two
 *  numeric boundaries (`toPlotValue`, `markerSymbolSize`) live in
 *  `lib/instrument.ts` with their reasons written down. See `api/types.ts`.
 */

import type { ReactNode } from "react";
import { useEffect, useMemo, useState } from "react";

import { fetchBenchmarks, fetchInstrumentChart, fetchInstruments, type Range } from "../api/client";
import type {
  Benchmark,
  Comparison,
  Interval,
  InstrumentChart,
  InstrumentSummary,
  IntervalExcess,
} from "../api/types";
import { EChart } from "../components/charts/EChart";
import { BenchmarkSelector, BenchmarkSpanBadge, terLabel } from "../components/ui/BenchmarkControls";
import { Pill, SegmentedControl } from "../components/ui/Controls";
import { MethodBadge } from "../components/ui/MethodBadge";
import { Notice } from "../components/ui/Notice";
import { Panel } from "../components/ui/Panel";
import { HeadRow, Table, TableFrame, Td, rowBackground, type ColumnDef } from "../components/ui/Table";
import { decimalPercent, decimalSignColour, shortDate } from "../lib/format";
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
 *  never be mistaken for a plain total-weighted-return number.
 *
 *  "arithmetic" is beside it for a reason `basis` cannot cover: `basis` labels
 *  how each RETURN was constructed (total return, dividends included) and says
 *  nothing about how the two were DIFFERENCED. This figure is
 *  `instrument − benchmark`, not `(1 + i) / (1 + b) - 1`, and the two answers
 *  diverge as returns grow -- a reader comparing it against a figure computed
 *  elsewhere has no other way to know which they are holding.
 *
 *  The proxy's TER follows, when one is selected, because the figure cannot be
 *  read without it: an excess smaller than the TER is not evidence the holding
 *  beat the index the proxy tracks. */
function ExcessCell({
  row,
  basis,
  ter,
}: {
  row: IntervalExcess | null;
  basis: string;
  ter: string | null;
}): ReactNode {
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
    <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 1 }}>
      <span style={{ color: decimalSignColour(row.excess) }}>{decimalPercent(row.excess)}</span>
      <span style={{ fontSize: 9.5, color: c.textFaint, textAlign: "right" }}>
        {basis}, arithmetic{ter === null ? "" : ` · ${terLabel(ter)}`}
      </span>
    </div>
  );
}

export function Instrument() {
  const [range, setRange] = useState<Range>("1Y");
  const [benchmarkKey, setBenchmarkKey] = useState<string | null>(null);
  const [isin, setIsin] = useState<string | null>(null);

  const [instruments, setInstruments] = useState<InstrumentSummary[] | null>(null);
  const [instrumentsError, setInstrumentsError] = useState<string | null>(null);

  const [benchmarks, setBenchmarks] = useState<Benchmark[]>([]);
  const [benchmarksError, setBenchmarksError] = useState<string | null>(null);

  const [chart, setChart] = useState<InstrumentChart | null>(null);
  const [chartError, setChartError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // The picker's own candidate list: every instrument the ledger has ever
  // recorded an economic trade for -- not only the ones with an open position
  // today (see this file's own docstring for why that distinction matters).
  // Fetched exactly once: unlike `Positions.tsx`'s method-scoped list, which
  // instruments were ever traded does not depend on a lot-matching method.
  useEffect(() => {
    let cancelled = false;
    fetchInstruments()
      .then((items) => {
        if (cancelled) return;
        setInstruments(items);
        setInstrumentsError(null);
        setIsin((current) => current ?? items[0]?.isin ?? null);
      })
      .catch((cause: unknown) => {
        if (cancelled) return;
        setInstrumentsError(cause instanceof Error ? cause.message : String(cause));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // The benchmark list. Independent of everything the reader picks, so it is
  // fetched exactly once.
  useEffect(() => {
    let cancelled = false;
    fetchBenchmarks()
      .then((items) => {
        if (cancelled) return;
        setBenchmarks(items);
        setBenchmarksError(null);
      })
      .catch((cause: unknown) => {
        // Non-fatal -- the chart still draws without a benchmark list -- but
        // never silent. Swallowing this made a broken endpoint look exactly
        // like "no benchmark configured", which is a legitimate and very
        // common state, so the one that needs fixing hid inside the one that
        // does not.
        if (cancelled) return;
        setBenchmarksError(cause instanceof Error ? cause.message : String(cause));
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

  if (instrumentsError) {
    return (
      <Notice tone="danger">
        Could not reach the API. {instrumentsError}. Start it with{" "}
        <code style={{ fontFamily: mono }}>
          python -m uvicorn app.main:create_app --factory
        </code>
        , and check that CORS_ORIGINS in backend/.env lists this port.
      </Notice>
    );
  }

  if (instruments === null) {
    return <div style={{ fontSize: 12, color: c.textFaint }}>Loading…</div>;
  }

  if (instruments.length === 0) {
    return (
      <Notice tone="modelled">
        No instruments traded yet. Import an export, then run{" "}
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

  const selected = instruments.find((i) => i.isin === isin);
  const comparison = chart.comparison;
  //: The proxy currently overlaid, for the TER beside every excess figure.
  //: `null` when none is selected -- and also when the list failed to load,
  //: which is why the lookup is on `benchmarks` rather than on `benchmarkKey`.
  const activeBenchmark = benchmarks.find((b) => b.key === benchmarkKey) ?? null;
  //: The left edge actually drawn. When `clamped` is true this IS the
  //: instrument's first trade, because the route clamps the window to it.
  const firstDrawnDay = chart.points[0]?.date;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {instruments.map((i) => (
            <Pill key={i.isin} monospace active={i.isin === isin} onClick={() => setIsin(i.isin)}>
              {i.product_name || i.isin}
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
          <BenchmarkSelector
            benchmarks={benchmarks}
            error={benchmarksError}
            selected={benchmarkKey}
            onSelect={setBenchmarkKey}
          />
        }
        footer={
          comparison && (
            <>
              Linked over the range: instrument {decimalPercent(comparison.linked_instrument_return)}, benchmark{" "}
              {decimalPercent(comparison.linked_benchmark_return)}, excess{" "}
              {decimalPercent(comparison.linked_excess)} — basis: {comparison.basis}, differenced
              arithmetically (instrument − benchmark), not geometrically.
              {activeBenchmark !== null && (
                <>
                  {" "}
                  {activeBenchmark.name} carries {terLabel(activeBenchmark.ter)}, reported here and
                  never subtracted: a holding that beats the proxy by less than the TER has not
                  necessarily beaten the market.
                </>
              )}
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
          {comparison && <BenchmarkSpanBadge span={comparison.span} />}
        </div>
        {/* A statement about the QUESTION, not the answer -- the same note
            `CoverageStrip` puts under the portfolio value chart, and for the
            same reason: the reader asked for a window longer than this
            instrument has existed, and the honest reply is everything there is
            plus a sentence. Without it a four-month-old position at `1Y` looks
            like a chart that simply starts late. */}
        {chart.clamped && firstDrawnDay !== undefined && (
          <div style={{ fontSize: 11, color: c.modelled, marginTop: 8 }}>
            Asked for {shortDate(chart.requested_from)}; this instrument was first traded{" "}
            {shortDate(firstDrawnDay)}. Showing everything there is rather than drawing a line for
            days it was not owned.
          </div>
        )}
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
                  <Td align="right" numeric color={decimalSignColour(interval.price_return)}>
                    {decimalPercent(interval.price_return)}
                  </Td>
                  <Td align="right" numeric color={decimalSignColour(row?.instrument_return ?? null)}>
                    {row ? decimalPercent(row.instrument_return) : "—"}
                  </Td>
                  <Td align="right" numeric color={decimalSignColour(row?.benchmark_return ?? null)}>
                    {row ? decimalPercent(row.benchmark_return) : "—"}
                  </Td>
                  <Td align="right" numeric>
                    <ExcessCell
                      row={row}
                      basis={comparison?.basis ?? ""}
                      ter={activeBenchmark?.ter ?? null}
                    />
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
