/** What I hold right now, priced. The third screen reading the live ledger.
 *
 *  Three surfaces on one screen, per M2-8: the net value chart, the coverage
 *  strip under it, and the positions table. They go live together so no screen
 *  mixes live and modelled figures.
 *
 *  Every figure here is formatted from the exact decimal string the API sent.
 *  Nothing on this screen is passed through `Number()` -- the two places a
 *  string becomes a number, `toPlotValue` for a pixel coordinate and
 *  `positionWeight` for a display ratio, both live in `lib/valuation.ts` with
 *  their reasons written down. See `api/types.ts` for why that matters.
 *
 *  `null` renders "—", never "€ 0,00" and never an empty cell. A zero is a
 *  claim and a blank is indistinguishable from a screen that forgot; design doc
 *  8.1 rules out both. The same applies to the portfolio total, which is
 *  withheld entirely rather than partially summed when any position is
 *  unpriceable.
 */

import { useEffect, useMemo, useState } from "react";

import { fetchPositions, fetchValuation } from "../api/client";
import type { LotMethodTag, PositionsPage, ValuationSeries } from "../api/types";
import { EChart } from "../components/charts/EChart";
import { SegmentedControl } from "../components/ui/Controls";
import { CoverageStrip } from "../components/ui/CoverageStrip";
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
import {
  decimal,
  decimalEur,
  decimalPercent,
  decimalSignColour,
  shortDate,
} from "../lib/format";
import { c, mono } from "../lib/theme";
import {
  RANGE_PRESETS,
  buildValueOption,
  positionWeight,
  rangeStart,
  type RangePreset,
} from "../lib/valuation";
import { sliceSeries } from "../lib/valuation-window";

const COLUMNS: readonly ColumnDef[] = [
  { label: "INSTRUMENT" },
  { label: "QTY", align: "right" },
  { label: "COST BASIS", align: "right" },
  { label: "PRICE", align: "right" },
  { label: "AS OF", align: "right" },
  { label: "MARKET VALUE", align: "right" },
  // Gross, charges and net as three columns, not one. Sec 6.4: a position owes
  // three answers -- what the stock did, what the broker took, what is left.
  { label: "GROSS", align: "right" },
  { label: "CHARGES", align: "right" },
  { label: "UNREALISED", align: "right" },
  { label: "%", align: "right" },
  { label: "WEIGHT", align: "right" },
  { label: "CCY", align: "right" },
  { label: "SOURCE", align: "right" },
];

export interface PositionsProps {
  method: LotMethodTag;
  onOpenInstrument: (isin: string) => void;
  /** Bumped by the header's refresh control once it has cleared the response
   *  cache, so both effects below re-run against an empty one. Optional and
   *  defaulted, so a caller that never refreshes needs no change. */
  refreshToken?: number;
}

/** Both responses, or neither -- at RENDER time.
 *
 *  They used to be fetched together too, by one effect keyed on both controls.
 *  That made "the chart arrived but the table did not" unrepresentable, but it
 *  also meant changing the range refetched the positions table and changing the
 *  method refetched a multi-year daily series, each to answer a question it does
 *  not ask (PT-46). The two fetches are now independent and keyed on what they
 *  actually depend on; the guarantee that mattered is kept by the render gate
 *  below, which still refuses to draw a half-arrived screen.
 */
interface LivePages {
  series: ValuationSeries;
  positions: PositionsPage;
}

function failure(cause: unknown): string {
  return cause instanceof Error ? cause.message : String(cause);
}

export function Positions({ method, onOpenInstrument, refreshToken = 0 }: PositionsProps) {
  const [range, setRange] = useState<RangePreset>("MAX");
  // The whole-ledger series, fetched once. Every shorter window is narrowed
  // out of it without touching the network -- see `lib/valuation-window.ts`
  // for why that is sound and when it is refused.
  const [ledger, setLedger] = useState<ValuationSeries | null>(null);
  // The one case narrowing cannot answer: a window reaching back further than
  // the series we hold. Kept with the window it belongs to, so a stale answer
  // cannot be drawn under a control that has since moved on.
  const [fetched, setFetched] = useState<{ from: string; series: ValuationSeries } | null>(null);
  const [holdings, setHoldings] = useState<PositionsPage | null>(null);
  // One error slot per fetch, never one shared between them. They settle
  // independently now (PT-46), so a single slot meant whichever finished LAST
  // won: a positions success would clear a valuation failure, leaving `ledger`
  // null, `pages` null, and the screen on "Loading…" for ever with nothing on
  // screen saying anything had gone wrong. Found in review before merge.
  const [ledgerError, setLedgerError] = useState<string | null>(null);
  const [holdingsError, setHoldingsError] = useState<string | null>(null);
  // A count, not a boolean: two independent fetches can be in flight at once
  // and a boolean would let the first to finish clear the second's indicator.
  const [inFlight, setInFlight] = useState(0);

  // `new Date()` is read once per range change rather than on every render, so
  // a re-render cannot silently shift the window by a day.
  const from = useMemo(() => rangeStart(range, new Date()), [range]);

  // The whole-ledger series does not depend on the range -- that is the entire
  // point. `refreshToken` is not a parameter of the question either; it is the
  // operator saying the answer may have changed under us.
  useEffect(() => {
    let cancelled = false;
    setInFlight((n) => n + 1);
    fetchValuation({})
      .then((data) => {
        if (cancelled) return;
        setLedger(data);
        setLedgerError(null);
      })
      .catch((cause: unknown) => {
        if (cancelled) return;
        setLedgerError(failure(cause));
      })
      // Unconditional: the increment happened whether or not this run is still
      // the current one, so the decrement has to as well.
      .finally(() => setInFlight((n) => n - 1));

    return () => {
      cancelled = true;
    };
  }, [refreshToken]);

  // Synchronous, and that is the fix the operator asked for: picking a preset
  // is now a `filter` over data already here, exactly like dragging the chart's
  // own zoom slider, instead of a round trip.
  const narrowed = useMemo(
    () => (ledger === null ? null : sliceSeries(ledger, from)),
    [ledger, from],
  );

  // Only reached when narrowing refused -- a window starting before the series
  // we hold, where the API knows something we cannot derive (that it clamped).
  useEffect(() => {
    if (ledger === null || narrowed !== null || from === null) return undefined;

    let cancelled = false;
    setInFlight((n) => n + 1);
    fetchValuation({ from })
      .then((data) => {
        if (cancelled) return;
        setFetched({ from, series: data });
        setLedgerError(null);
      })
      .catch((cause: unknown) => {
        if (cancelled) return;
        setLedgerError(failure(cause));
      })
      .finally(() => setInFlight((n) => n - 1));

    return () => {
      cancelled = true;
    };
  }, [ledger, narrowed, from, refreshToken]);

  const valuation = narrowed ?? (fetched !== null && fetched.from === from ? fetched.series : null);

  // The positions table depends on the lot method and nothing else.
  useEffect(() => {
    let cancelled = false;
    setInFlight((n) => n + 1);
    fetchPositions(method)
      .then((data) => {
        if (cancelled) return;
        setHoldings(data);
        setHoldingsError(null);
      })
      .catch((cause: unknown) => {
        if (cancelled) return;
        setHoldingsError(failure(cause));
      })
      .finally(() => setInFlight((n) => n - 1));

    return () => {
      cancelled = true;
    };
  }, [method, refreshToken]);

  const pages: LivePages | null =
    valuation !== null && holdings !== null
      ? { series: valuation, positions: holdings }
      : null;

  const option = useMemo(
    () => buildValueOption(valuation?.items ?? [], { label: "Portfolio value" }),
    [valuation],
  );

  // Either failure is worth the notice; the first one found is reported, and
  // neither can be cleared by the other's success.
  const error = ledgerError ?? holdingsError;
  if (error !== null) {
    return (
      <Notice tone="danger">
        Could not reach the API. {error}. Start it with{" "}
        <code style={{ fontFamily: mono }}>
          python -m uvicorn app.main:create_app --factory
        </code>
        , and check that CORS_ORIGINS in backend/.env lists this port.
      </Notice>
    );
  }

  if (pages === null) {
    return <div style={{ fontSize: 12, color: c.textFaint }}>Loading…</div>;
  }

  const { series, positions } = pages;
  const total = positions.total_market_value_base;

  // An unfetched cache and an empty portfolio look identical on screen, and only
  // one of them is a fact about the portfolio.
  if (series.items.length === 0 && positions.items.length === 0) {
    return (
      <Notice tone="modelled">
        Nothing to value yet. Import an export, then run{" "}
        <code style={{ fontFamily: mono }}>python -m app.cli fetch-prices</code> and{" "}
        <code style={{ fontFamily: mono }}>python -m app.cli rebuild</code>.
      </Notice>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
        {/* The envelope's method, never the prop. If the two ever disagree the
            response is the truth and the badge has to say so. */}
        <MethodBadge method={positions.method} coverage={positions.coverage} />
        <span style={{ fontSize: 11, color: c.textFaint }}>
          {positions.items.length} open positions
          {positions.as_of ? ` · as of ${shortDate(positions.as_of)}` : ""}
        </span>
        <div
          style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 10 }}
        >
          {/* A refetch no longer blanks the screen, so without this the reader
              has no way to tell a cached answer from one still on the wire. */}
          {inFlight > 0 && (
            <span style={{ fontSize: 10, color: c.textFaint, fontFamily: mono }}>
              UPDATING…
            </span>
          )}
          <SegmentedControl
            options={RANGE_PRESETS}
            value={range}
            onChange={setRange}
            label="RANGE"
            size="sm"
          />
        </div>
      </div>

      <Panel
        title="Portfolio value"
        subtitle="Net of cash — holdings at market plus the cash balance, so a debit balance reduces it"
      >
        <EChart option={option} height={320} />
        <div style={{ marginTop: 10 }}>
          <CoverageStrip
            points={series.items}
            clamped={series.clamped}
            requestedFrom={series.requested_from}
            start={series.start}
          />
        </div>
      </Panel>

      {total === null && (
        <Notice tone="danger">
          One or more positions cannot be priced, so the portfolio total is withheld
          rather than partially summed. Run{" "}
          <code style={{ fontFamily: mono }}>python -m app.cli symbols</code> to see what
          is unanswered.
        </Notice>
      )}

      <TableFrame>
        <Table minWidth={1180}>
          <HeadRow columns={COLUMNS} />
          <tbody>
            {positions.items.map((row, index) => (
              <tr
                key={row.isin}
                style={{
                  background: rowBackground(index),
                  borderBottom: `1px solid ${c.borderSoft}`,
                }}
              >
                <Td>
                  <button
                    type="button"
                    onClick={() => onOpenInstrument(row.isin)}
                    style={{
                      all: "unset",
                      cursor: "pointer",
                      display: "block",
                      color: c.text,
                      fontWeight: 500,
                    }}
                  >
                    {row.product_name || row.isin}
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
                    {row.isin}
                  </span>
                </Td>
                <Td align="right" numeric>{decimal(row.quantity, 0, 4)}</Td>
                <Td align="right" numeric color={c.textMuted}>
                  {decimalEur(row.cost_basis)}
                </Td>
                {/* Two places, like every other money cell (PT-47). QTY above
                    stays at four: a share count is not currency, and a
                    fractional holding is real. */}
                <Td align="right" numeric>{decimal(row.price, 2, 2)}</Td>
                <Td align="right" numeric color={c.textFaint}>
                  {row.price_date ? shortDate(row.price_date) : "—"}
                </Td>
                <Td align="right" numeric color={c.text}>
                  {decimalEur(row.market_value_base)}
                </Td>
                <Td
                  align="right"
                  numeric
                  color={decimalSignColour(row.gross_unrealised_base)}
                >
                  {decimalEur(row.gross_unrealised_base)}
                </Td>
                <Td align="right" numeric color={c.textMuted}>
                  {decimalEur(row.charges_base)}
                </Td>
                <Td align="right" numeric color={decimalSignColour(row.unrealised_base)}>
                  {decimalEur(row.unrealised_base)}
                </Td>
                <Td align="right" numeric color={decimalSignColour(row.unrealised_base)}>
                  {decimalPercent(row.unrealised_pct)}
                </Td>
                <Td align="right" numeric color={c.textMuted}>
                  {decimalPercent(positionWeight(row.market_value_base, total))}
                </Td>
                <Td align="right" numeric color={c.textFaint}>{row.currency}</Td>
                <Td
                  align="right"
                  numeric
                  color={row.coverage === "full" ? c.textFaint : c.modelled}
                >
                  {row.source ?? "—"}
                </Td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr style={{ background: c.panelAlt, borderTop: `1px solid ${c.borderStrong}` }}>
              <Td padding="10px 11px" style={{ fontSize: 11, fontWeight: 600, color: c.text }}>
                Total
              </Td>
              <td />
              <Td padding="10px 11px" align="right" numeric color={c.textMuted}>
                {decimalEur(positions.total_cost_basis)}
              </Td>
              <td />
              <td />
              <Td padding="10px 11px" align="right" numeric color={c.text}>
                {decimalEur(total)}
              </Td>
              <td />
              <td />
              <Td
                padding="10px 11px"
                align="right"
                numeric
                color={decimalSignColour(positions.total_unrealised_base)}
              >
                {decimalEur(positions.total_unrealised_base)}
              </Td>
              <td />
              <td />
              <td />
              <td />
            </tr>
          </tfoot>
        </Table>
      </TableFrame>
    </div>
  );
}
