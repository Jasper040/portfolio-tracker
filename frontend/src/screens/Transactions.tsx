/** The raw ledger. The ONE screen reading real data.
 *
 *  Two things make this screen different from the other eight, and both are
 *  deliberate:
 *
 *  1. Every money value here is rendered with `decimal*` formatters, which
 *     re-punctuate the exact string the API sent. Nothing on this screen is ever
 *     passed through `Number()`. See `api/types.ts` for why.
 *  2. Columns the API does not serve show "—" rather than a plausible guess.
 *     `account` and `source` exist on the backend model but not on
 *     `TransactionOut`, so they are genuinely unknown here; inventing "DeGiro"
 *     because it is probably right would make a display bug indistinguishable
 *     from a data bug.
 */

import { Fragment, useEffect, useState } from "react";
import { fetchTransactions } from "../api/client";
import type { Transaction, TransactionPage } from "../api/types";
import { Badge } from "../components/ui/Badge";
import { MethodBadge } from "../components/ui/MethodBadge";
import { Notice } from "../components/ui/Notice";
import { HeadRow, Table, TableFrame, Td, rowBackground, type ColumnDef } from "../components/ui/Table";
import { c, mono } from "../lib/theme";
import { decimal, decimalEur, decimalIsNegative, shortDate } from "../lib/format";

const COLUMNS: readonly ColumnDef[] = [
  { label: "DATE" },
  { label: "TYPE" },
  { label: "INSTRUMENT" },
  { label: "QTY", align: "right" },
  { label: "PRICE", align: "right" },
  { label: "CCY", align: "right" },
  { label: "FX", align: "right" },
  { label: "FEES", align: "right" },
  { label: "NET BASE", align: "right" },
  { label: "ACCOUNT" },
  { label: "SOURCE" },
  { label: "FLAG" },
];

/** Colour a transaction type without a lookup table that would silently fall
 *  through for a type the backend adds later. Anything unrecognised renders
 *  neutral, which reads as "a type this UI has not been taught" rather than as
 *  a miscategorised buy. */
function typeTone(txnType: string): { color: string; background: string } {
  const t = txnType.toUpperCase();
  if (t.includes("BUY")) return { color: c.positive, background: c.positiveBg };
  if (t.includes("SELL") || t.includes("WITHDRAW")) return { color: c.negative, background: c.negativeBg };
  if (t.includes("DIVIDEND") || t.includes("DEPOSIT")) return { color: c.accent, background: c.accentBg };
  return { color: c.textMuted, background: c.neutralBg };
}

/** Surface the two ledger flags the API does send.
 *
 *  `is_economic === false` marks a row that nets to nothing economically (a
 *  bookkeeping pair), and `closure_reason` other than DECISION means the position
 *  was closed by something that was not a choice -- a corporate action, say. Both
 *  change how a row should be read, so neither is hidden. */
function flagFor(txn: Transaction): { label: string; color: string } | null {
  if (!txn.is_economic) return { label: "NON-ECON", color: c.textFaint };
  if (txn.closure_reason && txn.closure_reason !== "DECISION") {
    return { label: txn.closure_reason.toUpperCase(), color: c.modelled };
  }
  return null;
}

export function Transactions() {
  const [page, setPage] = useState<TransactionPage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  useEffect(() => {
    let cancelled = false;
    fetchTransactions()
      .then((p) => {
        if (!cancelled) setPage(p);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    // StrictMode mounts effects twice in development; without this guard the
    // second, discarded fetch can resolve last and overwrite the live state.
    return () => {
      cancelled = true;
    };
  }, []);

  if (loading) {
    return <div style={{ fontSize: 12, color: c.textMuted }}>Loading ledger…</div>;
  }

  if (error) {
    return (
      <Notice tone="danger">
        <span style={{ fontFamily: mono, color: c.negative }}>Could not reach the API</span> — {error}
        <div style={{ marginTop: 8, color: c.textFaint }}>
          The ledger is the only screen backed by a live endpoint. Start the backend with{" "}
          <span style={{ fontFamily: mono }}>uvicorn app.main:create_app --factory</span> and reload.
        </div>
      </Notice>
    );
  }

  const items = page?.items ?? [];
  const total = page?.total ?? 0;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", fontSize: 11 }}>
        {/* Reporting what was fetched AND the total, never just one: a count that
            is quietly short of the truth is the failure the original App.tsx
            heading was written to avoid. */}
        <span
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            border: `1px solid ${c.borderStrong}`,
            borderRadius: 4,
            padding: "4px 9px",
            color: c.textMuted,
          }}
        >
          <span style={{ fontFamily: mono, fontSize: 9.5, color: c.textFaint }}>ROWS</span>
          {items.length < total
            ? `${items.length.toLocaleString("nl-NL")} of ${total.toLocaleString("nl-NL")}`
            : total.toLocaleString("nl-NL")}
        </span>
        <span
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            border: `1px solid ${c.borderStrong}`,
            borderRadius: 4,
            padding: "4px 9px",
            color: c.textMuted,
          }}
        >
          <span style={{ fontFamily: mono, fontSize: 9.5, color: c.textFaint }}>SOURCE</span>
          live API
        </span>
        {/* Provenance straight from the response envelope, never inferred here:
            the point of the required fields is that the screen reports what the
            API said rather than what the screen assumes. */}
        {page && <MethodBadge method={page.method} coverage={page.coverage} />}
      </div>

      <Notice>
        Editing a transaction never mutates history. A correction writes a new offsetting entry
        referencing the original, so every number in this app stays reproducible from the ledger.
      </Notice>

      {items.length === 0 ? (
        <Notice tone="neutral">
          The ledger is empty. Import a DeGiro export with{" "}
          <span style={{ fontFamily: mono, color: c.textSecondary }}>
            python -m app.cli import &lt;file.csv&gt;
          </span>{" "}
          to populate it.
        </Notice>
      ) : (
        <TableFrame>
          <Table minWidth={1120}>
            <HeadRow columns={COLUMNS} />
            <tbody>
              {items.map((txn, i) => {
                const isOpen = expanded[txn.id] ?? false;
                const tone = typeTone(txn.txn_type);
                const flag = flagFor(txn);
                return (
                  <Fragment key={txn.id}>
                    <tr
                      onClick={() => setExpanded((s) => ({ ...s, [txn.id]: !s[txn.id] }))}
                      style={{
                        cursor: "pointer",
                        borderBottom: `1px solid ${c.borderSoft}`,
                        background: rowBackground(i),
                      }}
                    >
                      <Td padding="8px 11px" numeric nowrap>{shortDate(txn.trade_date)}</Td>
                      <Td padding="8px 11px">
                        <Badge color={tone.color} background={tone.background}>
                          {txn.txn_type}
                        </Badge>
                      </Td>
                      <Td padding="8px 11px" color={c.text} nowrap>
                        {txn.product_name ?? txn.isin ?? "—"}
                      </Td>
                      <Td padding="8px 11px" align="right" numeric>{decimal(txn.quantity)}</Td>
                      <Td padding="8px 11px" align="right" numeric>{decimal(txn.price_local, 2)}</Td>
                      <Td padding="8px 11px" align="right" numeric color={c.textFaint}>
                        {txn.currency_local ?? "—"}
                      </Td>
                      <Td padding="8px 11px" align="right" numeric color={c.textFaint}>
                        {decimal(txn.fx_rate, 4)}
                      </Td>
                      <Td padding="8px 11px" align="right" numeric color={c.textMuted}>
                        {decimalEur(txn.fee_base)}
                      </Td>
                      <Td
                        padding="8px 11px"
                        align="right"
                        numeric
                        color={decimalIsNegative(txn.net_base) ? c.negative : c.text}
                      >
                        {decimalEur(txn.net_base)}
                      </Td>
                      {/* Not served by TransactionOut. See the file header. */}
                      <Td padding="8px 11px" color={c.textFaint} nowrap>—</Td>
                      <Td padding="8px 11px" color={c.textFaint} nowrap>—</Td>
                      <Td padding="8px 11px">
                        {flag && (
                          <span style={{ fontFamily: mono, fontSize: 9.5, color: flag.color }}>
                            {flag.label}
                          </span>
                        )}
                      </Td>
                    </tr>

                    {isOpen && (
                      <tr style={{ background: c.sunken, borderBottom: `1px solid ${c.borderSoft}` }}>
                        <td colSpan={COLUMNS.length} style={{ padding: "12px 11px 14px 22px" }}>
                          <div
                            style={{
                              fontFamily: mono,
                              fontSize: 9.5,
                              letterSpacing: "0.05em",
                              color: c.textFaint,
                              marginBottom: 7,
                            }}
                          >
                            SOURCE raw_json
                            {txn.order_ref ? ` — order ${txn.order_ref}` : ""}
                          </div>
                          {/* The original CSV cells, so a parser bug can be
                              diagnosed against the source without re-opening the
                              export. */}
                          <pre
                            style={{
                              margin: 0,
                              fontFamily: mono,
                              fontSize: 10.5,
                              lineHeight: 1.7,
                              color: c.textMuted,
                              background: c.bg,
                              border: `1px solid ${c.border}`,
                              borderRadius: 4,
                              padding: "11px 13px",
                              overflowX: "auto",
                            }}
                          >
                            {JSON.stringify(txn.raw, null, 2)}
                          </pre>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </Table>
        </TableFrame>
      )}
    </div>
  );
}
