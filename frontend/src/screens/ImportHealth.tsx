/** What makes the rest trustworthy.
 *
 *  This screen is second-to-last in the sidebar and first in importance: every
 *  number on the other eight is only as good as what landed here. It shows the
 *  import preview BEFORE anything is written, the health checks that would
 *  invalidate a computation, the batch history that makes an import undoable,
 *  and any reconciliation conflict between sources.
 *
 *  The actions are inert. Import, rebuild and undo are backend operations that do
 *  not exist yet (design doc section 10, M0b onward); wiring a button to nothing
 *  would be worse than a button that visibly does not claim to work, so the panel
 *  says so.
 */

import { Notice } from "../components/ui/Notice";
import { Panel } from "../components/ui/Panel";
import { ActionButton } from "../components/ui/Controls";
import { Badge } from "../components/ui/Badge";
import { Table, Td } from "../components/ui/Table";
import { c, mono } from "../lib/theme";

interface PreviewRow {
  status: "NEW" | "DUPLICATE" | "UNPARSEABLE";
  date: string;
  instrument: string;
  qty: string;
  price: string;
  note: string;
}

/** A worked example of the three outcomes a parsed row can have. Kept as a
 *  literal rather than derived: it demonstrates the shape of the preview the
 *  importer will produce, including the unparseable case that real data
 *  eventually always contains. */
const PREVIEW: readonly PreviewRow[] = [
  { status: "NEW", date: "02-09-26", instrument: "DELTA OPTICS Holding", qty: "2", price: "871,40", note: "" },
  { status: "NEW", date: "28-08-26", instrument: "ING Groep", qty: "100", price: "22,10", note: "" },
  {
    status: "DUPLICATE",
    date: "14-05-24",
    instrument: "ING Groep",
    qty: "−150",
    price: "15,40",
    note: "matches order ing-2 in batch B1007",
  },
  {
    status: "NEW",
    date: "15-08-26",
    instrument: "Shell plc",
    qty: "—",
    price: "—",
    note: "dividend, gross € 96,40 / wh € 14,46",
  },
  {
    status: "UNPARSEABLE",
    date: "11-08-26",
    instrument: "—",
    qty: "—",
    price: "—",
    note: "row 14: product column empty, no ISIN to resolve",
  },
  {
    status: "NEW",
    date: "01-08-26",
    instrument: "Northwind All-Country",
    qty: "6",
    price: "155,80",
    note: "",
  },
];

const STATUS_TONE: Record<PreviewRow["status"], { color: string; background: string }> = {
  NEW: { color: c.positive, background: c.positiveBg },
  DUPLICATE: { color: c.textMuted, background: c.neutralBg },
  UNPARSEABLE: { color: c.negative, background: c.negativeBg },
};

const HEALTH_CHECKS = [
  { label: "Missing price data on dates held", count: "0", color: c.positive, action: "ok" },
  { label: "Instruments with no industry mapping", count: "1", color: c.modelled, action: "map now" },
  { label: "Transactions missing FX rate (ccy ≠ base)", count: "0", color: c.positive, action: "ok" },
  { label: "Negative computed positions", count: "0", color: c.positive, action: "ok" },
  { label: "Unmatched dividend tax rows", count: "2", color: c.modelled, action: "review" },
  {
    label: "Suspected unhandled corporate actions",
    count: "1",
    color: c.negative,
    action: "ORN 2024-06 gap 9,8×",
  },
] as const;

const BATCHES = [
  { id: "B1014", timestamp: "04-09-26 07:12", source: "SnapTrade", rows: "6" },
  { id: "B1013", timestamp: "01-09-26 21:40", source: "DeGiro CSV", rows: "18" },
  { id: "B1009", timestamp: "12-07-26 09:03", source: "Manual", rows: "1" },
  { id: "B1007", timestamp: "02-06-24 18:22", source: "DeGiro CSV", rows: "214" },
] as const;

export function ImportHealth({ asOf }: { asOf: string }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <Notice tone="neutral">
        The controls on this screen are not wired. Import, rebuild and undo are backend operations
        scheduled for a later milestone; the panels below show the shapes they will produce.
      </Notice>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: 12 }}>
        <div
          style={{
            border: `1px dashed ${c.chipBorder}`,
            borderRadius: 6,
            background: c.sunken,
            padding: "22px 16px",
            textAlign: "center",
          }}
        >
          <div style={{ fontSize: 12.5, fontWeight: 600 }}>Drop a DeGiro CSV</div>
          <div style={{ fontSize: 11, color: c.textFaint, marginTop: 5, lineHeight: 1.6 }}>
            Parsed and previewed. Nothing is written until you commit.
          </div>
        </div>

        <Panel style={{ padding: 16 }}>
          <div style={{ fontSize: 12.5, fontWeight: 600 }}>SnapTrade</div>
          <div style={{ fontSize: 11, color: c.textFaint, margin: "5px 0 10px" }}>
            2 accounts linked · last refresh {asOf} 07:12
          </div>
          <ActionButton>Refresh now</ActionButton>
        </Panel>

        <Panel style={{ padding: 16 }}>
          <div style={{ fontSize: 12.5, fontWeight: 600 }}>Rebuild derived data</div>
          <div style={{ fontSize: 11, color: c.textFaint, margin: "5px 0 10px" }}>
            Last rebuilt {asOf} 07:14 · 41s
          </div>
          <ActionButton>Rebuild</ActionButton>
        </Panel>
      </div>

      <Panel
        title="Preview — Transactions.csv"
        subtitle="18 rows parsed · 12 new · 5 duplicate · 1 unparseable"
        actions={
          <>
            <ActionButton tone="positive">Commit 12 new rows</ActionButton>
            <ActionButton>Cancel</ActionButton>
          </>
        }
      >
        <Table>
          <tbody>
            <tr>
              {(["STATUS", "DATE", "INSTRUMENT", "QTY", "PRICE", "NOTE"] as const).map((label, i) => (
                <th
                  key={label}
                  style={{
                    textAlign: i === 3 || i === 4 ? "right" : "left",
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
            {PREVIEW.map((row, i) => {
              const tone = STATUS_TONE[row.status];
              return (
                <tr key={i} style={{ borderBottom: `1px solid ${c.borderSoft}` }}>
                  <Td padding="7px 9px">
                    <Badge color={tone.color} background={tone.background}>{row.status}</Badge>
                  </Td>
                  <Td padding="7px 9px" numeric>{row.date}</Td>
                  <Td padding="7px 9px" color={c.text}>{row.instrument}</Td>
                  <Td padding="7px 9px" align="right" numeric>{row.qty}</Td>
                  <Td padding="7px 9px" align="right" numeric>{row.price}</Td>
                  <Td padding="7px 9px" color={c.textMuted} style={{ fontSize: 11 }}>{row.note}</Td>
                </tr>
              );
            })}
          </tbody>
        </Table>
      </Panel>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(340px, 1fr))", gap: 12 }}>
        <Panel title="Data health">
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              gap: 1,
              background: c.border,
              border: `1px solid ${c.border}`,
              borderRadius: 4,
              overflow: "hidden",
            }}
          >
            {HEALTH_CHECKS.map((h) => (
              <div
                key={h.label}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 10,
                  background: c.inset,
                  padding: "9px 11px",
                  fontSize: 11.5,
                }}
              >
                <span
                  style={{ width: 6, height: 6, borderRadius: "50%", background: h.color, flex: "0 0 auto" }}
                />
                <span style={{ color: c.textSecondary, minWidth: 0 }}>{h.label}</span>
                <span style={{ marginLeft: "auto", fontFamily: mono, color: h.color, flex: "0 0 auto" }}>
                  {h.count}
                </span>
                <span style={{ fontSize: 10.5, flex: "0 0 auto", color: c.accent }}>{h.action}</span>
              </div>
            ))}
          </div>
        </Panel>

        <Panel title="Batch history">
          <Table>
            <tbody>
              {BATCHES.map((b) => (
                <tr key={b.id} style={{ borderBottom: `1px solid ${c.borderSoft}` }}>
                  <Td padding="8px 9px" numeric color={c.textMuted}>{b.id}</Td>
                  <Td padding="8px 9px" numeric>{b.timestamp}</Td>
                  <Td padding="8px 9px" color={c.textMuted}>{b.source}</Td>
                  <Td padding="8px 9px" align="right" numeric>{b.rows}</Td>
                  <Td padding="8px 9px" align="right" style={{ fontSize: 10.5, color: c.accent }}>
                    undo
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>

          <div style={{ fontSize: 12.5, fontWeight: 600, margin: "18px 0 8px" }}>Reconciliation</div>
          {/* Both sources are retained rather than one overwriting the other:
              the ledger is append-only, so a conflict is resolved by recording a
              preference, never by deleting the losing row. */}
          <Notice tone="danger">
            <span style={{ fontFamily: mono, color: c.negative }}>1 conflict</span> — SnapTrade reports
            252 ING shares, the CSV ledger computes 250. Both sources are kept; pick a winner to
            resolve.
            <div style={{ display: "flex", gap: 8, marginTop: 9 }}>
              <ActionButton>Trust CSV</ActionButton>
              <ActionButton>Trust SnapTrade</ActionButton>
            </div>
          </Notice>
        </Panel>
      </div>
    </div>
  );
}
