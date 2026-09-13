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
}

/** Both responses, or neither. They are fetched together and rendered together,
 *  so "the chart arrived but the table did not" is unrepresentable rather than
 *  merely unlikely -- the same argument `Lots.tsx` makes about its two pages. */
interface LivePages {
  series: ValuationSeries;
  positions: PositionsPage;
}

export function Positions({ method, onOpenInstrument }: PositionsProps) {
  const [range, setRange] = useState<RangePreset>("MAX");
  const [pages, setPages] = useState<LivePages | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // The cancellation guard. A slow FIFO response landing after a fast HIFO one
    // would otherwise put FIFO's numbers under a HIFO badge.
    let cancelled = false;
    setLoading(true);
    setError(null);

    Promise.all([
      fetchValuation({ from: rangeStart(range, new Date()) }),
      fetchPositions(method),
    ])
      .then(([series, positions]) => {
        if (cancelled) return;
        setPages({ series, positions });
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
  }, [method, range]);

  const option = useMemo(
    () => buildValueOption(pages?.series.items ?? [], { label: "Portfolio value" }),
    [pages],
  );

  if (error) {
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

  if (loading || pages === null) {
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
        <div style={{ marginLeft: "auto" }}>
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
                <Td align="right" numeric>{decimal(row.price, 2, 4)}</Td>
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
