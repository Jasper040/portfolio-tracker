/** Open lots and closures, straight from the ledger. The second screen reading
 *  real data.
 *
 *  Every figure here is formatted from the exact decimal string the API sent --
 *  money through `decimalEur`, the return through `decimalPercent`. Nothing on
 *  this screen is passed through `Number()`. See `api/types.ts` for why.
 *
 *  No market value here, and that is deliberate: a position's worth needs a price
 *  series, which is M2. Putting a cost basis under a heading that implied market
 *  value would be exactly the "plausible guess" the Transactions screen refuses
 *  to make.
 *
 *  Charges are three columns, not one. That is the point of Sec 6.4 -- a
 *  commission and an FX cost are different things to have paid.
 */

import { useEffect, useState } from "react";
import { fetchClosures, fetchLots } from "../api/client";
import type { ClosurePage, LotMethodTag, LotPage } from "../api/types";
import { MethodBadge } from "../components/ui/MethodBadge";
import { Notice } from "../components/ui/Notice";
import {
  HeadRow,
  Table,
  TableFrame,
  Td,
  rowBackground,
  type ColumnDef,
} from "../components/ui/Table";
import { c, mono } from "../lib/theme";
import {
  decimal,
  decimalEur,
  decimalIsNegative,
  decimalPercent,
  shortDate,
} from "../lib/format";

const LOT_COLUMNS: readonly ColumnDef[] = [
  { label: "ISIN" },
  { label: "OPENED" },
  { label: "QTY", align: "right" },
  { label: "PRICE", align: "right" },
  { label: "COST BASIS", align: "right" },
  { label: "COMMISSION", align: "right" },
  { label: "FX COST", align: "right" },
  { label: "TAX", align: "right" },
];

const CLOSURE_COLUMNS: readonly ColumnDef[] = [
  { label: "ISIN" },
  { label: "OPENED" },
  { label: "CLOSED" },
  { label: "QTY", align: "right" },
  { label: "OPEN", align: "right" },
  { label: "CLOSE", align: "right" },
  { label: "GROSS P&L", align: "right" },
  { label: "COMMISSION", align: "right" },
  { label: "FX COST", align: "right" },
  { label: "TAX", align: "right" },
  { label: "NET P&L", align: "right" },
  { label: "RETURN", align: "right" },
  { label: "DAYS", align: "right" },
];

export interface LotsProps {
  method: LotMethodTag;
}

/** Both pages, or neither. They are fetched together and rendered together, so
 *  holding them in one state value makes "the lots page arrived but the closures
 *  page did not" unrepresentable rather than merely unlikely. */
interface LedgerPages {
  lots: LotPage;
  closures: ClosurePage;
}

export function Lots({ method }: LotsProps) {
  const [pages, setPages] = useState<LedgerPages | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    Promise.all([fetchLots(method), fetchClosures(method)])
      .then(([lotPage, closurePage]) => {
        if (cancelled) return;
        setPages({ lots: lotPage, closures: closurePage });
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    // Re-fetches whenever the method changes. The guard also stops a slow FIFO
    // response landing after a fast HIFO one and being labelled HIFO.
    return () => {
      cancelled = true;
    };
  }, [method]);

  if (loading) {
    return <div style={{ fontSize: 12, color: c.textMuted }}>Loading lots…</div>;
  }

  if (error) {
    return (
      <Notice tone="danger">
        <span style={{ fontFamily: mono, color: c.negative }}>Could not reach the API</span>{" "}
        — {error}
      </Notice>
    );
  }

  if (!pages) {
    // Not loading, not errored, and nothing set. Unreachable while the effect
    // resolves or rejects, but saying so beats rendering empty tables that would
    // read as "you own nothing".
    return <Notice tone="danger">The API returned no response.</Notice>;
  }

  const { lots, closures } = pages;

  // Both, not just the lots. A portfolio that has been sold down to nothing has
  // zero open lots and a full table of closures; gating on the lots alone would
  // tell its owner there is nothing here while holding every realised trade they
  // made.
  if (!lots.items.length && !closures.items.length) {
    return (
      <Notice tone="neutral">
        No lots or closures yet. Import an export, then run{" "}
        <span style={{ fontFamily: mono }}>
          python -m app.cli rebuild --method {method}
        </span>
        .
      </Notice>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 11 }}>
        {/* Provenance from the response envelope, never from local state: the
            screen reports what the API said it computed. */}
        <MethodBadge method={lots.method} coverage={lots.coverage} />
        <span style={{ color: c.textMuted }}>
          {lots.total} open lots · {closures.total} closures
        </span>
      </div>

      <Notice>
        Cost basis is what the shares cost. Commission, FX cost and tax sit beside it, never
        inside it, so a realised figure separates what the stock did from what the broker took.
        Market value arrives with prices in M2.
      </Notice>

      {/* Each table renders only when it has rows. A header over an empty body
          reads as a fact ("no open lots") in a place the reader expects data,
          and a fully-sold portfolio legitimately has one of these and not the
          other. */}
      {lots.items.length > 0 && (
        <TableFrame>
          <Table>
            <HeadRow columns={LOT_COLUMNS} />
            <tbody>
              {lots.items.map((lot, i) => (
                <tr key={lot.id} style={{ background: rowBackground(i) }}>
                  <Td padding="8px 11px" nowrap>
                    {lot.isin}
                  </Td>
                  <Td padding="8px 11px" nowrap>
                    {shortDate(lot.opened_on)}
                  </Td>
                  <Td padding="8px 11px" align="right" numeric>
                    {decimal(lot.quantity)}
                  </Td>
                  <Td padding="8px 11px" align="right" numeric>
                    {decimal(lot.price, 2, 4)}
                  </Td>
                  <Td padding="8px 11px" align="right" numeric>
                    {decimalEur(lot.cost_basis)}
                  </Td>
                  <Td padding="8px 11px" align="right" numeric color={c.textMuted}>
                    {decimalEur(lot.commission)}
                  </Td>
                  <Td padding="8px 11px" align="right" numeric color={c.textMuted}>
                    {decimalEur(lot.autofx)}
                  </Td>
                  <Td padding="8px 11px" align="right" numeric color={c.textMuted}>
                    {decimalEur(lot.tax)}
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        </TableFrame>
      )}

      {closures.items.length > 0 && (
        <TableFrame>
          <Table>
            <HeadRow columns={CLOSURE_COLUMNS} />
            <tbody>
              {closures.items.map((closure, i) => (
                <tr key={closure.id} style={{ background: rowBackground(i) }}>
                  <Td padding="8px 11px" nowrap>
                    {closure.isin}
                  </Td>
                  <Td padding="8px 11px" nowrap>
                    {shortDate(closure.opened_on)}
                  </Td>
                  <Td padding="8px 11px" nowrap>
                    {shortDate(closure.closed_on)}
                  </Td>
                  <Td padding="8px 11px" align="right" numeric>
                    {decimal(closure.quantity)}
                  </Td>
                  <Td padding="8px 11px" align="right" numeric>
                    {decimal(closure.open_price, 2, 4)}
                  </Td>
                  <Td padding="8px 11px" align="right" numeric>
                    {decimal(closure.close_price, 2, 4)}
                  </Td>
                  <Td
                    padding="8px 11px"
                    align="right"
                    numeric
                    color={decimalIsNegative(closure.gross_pnl) ? c.negative : c.positive}
                  >
                    {decimalEur(closure.gross_pnl)}
                  </Td>
                  <Td padding="8px 11px" align="right" numeric color={c.textMuted}>
                    {decimalEur(closure.commission)}
                  </Td>
                  <Td padding="8px 11px" align="right" numeric color={c.textMuted}>
                    {decimalEur(closure.autofx)}
                  </Td>
                  <Td padding="8px 11px" align="right" numeric color={c.textMuted}>
                    {decimalEur(closure.tax)}
                  </Td>
                  <Td
                    padding="8px 11px"
                    align="right"
                    numeric
                    color={decimalIsNegative(closure.pnl) ? c.negative : c.positive}
                  >
                    {decimalEur(closure.pnl)}
                  </Td>
                  <Td padding="8px 11px" align="right" numeric>
                    {decimalPercent(closure.return_pct)}
                  </Td>
                  <Td padding="8px 11px" align="right" numeric>
                    {closure.holding_days}
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        </TableFrame>
      )}
    </div>
  );
}
