import { useState } from "react";
import type { Transaction } from "../api/types";

interface Props {
  transactions: Transaction[];
}

/** One ledger row, plus a collapsible panel showing the original CSV cells.
 *  The raw panel exists so a parser bug can be diagnosed against the source
 *  without re-opening the export. */
function Row({ txn }: { txn: Transaction }) {
  const [open, setOpen] = useState(false);
  // A Fragment (<>...</>), not a <div>: this component renders two sibling <tr>s
  // (the row, and the optional raw-data row below it), but a <tbody> in HTML may
  // only contain <tr> elements directly. Any wrapping element here -- even one
  // React renders correctly -- would be invalid table markup and browsers silently
  // hoist the <tr>s out of it, which is a much stranger bug to track down than
  // just not wrapping them in the first place.
  return (
    <>
      <tr>
        <td>{txn.trade_date}</td>
        <td>{txn.txn_type}</td>
        <td>{txn.product_name ?? "—"}</td>
        <td>{txn.isin ?? "—"}</td>
        <td style={{ textAlign: "right" }}>{txn.quantity ?? "—"}</td>
        <td style={{ textAlign: "right" }}>
          {txn.price_local ?? "—"} {txn.currency_local ?? ""}
        </td>
        <td style={{ textAlign: "right" }}>{txn.fee_base}</td>
        <td style={{ textAlign: "right" }}>{txn.net_base}</td>
        <td>
          <button type="button" onClick={() => setOpen(!open)}>
            {open ? "hide" : "raw"}
          </button>
        </td>
      </tr>
      {open && (
        <tr>
          <td colSpan={9}>
            <pre style={{ margin: 0, fontSize: 12, overflowX: "auto" }}>
              {JSON.stringify(txn.raw, null, 2)}
            </pre>
          </td>
        </tr>
      )}
    </>
  );
}

export function TransactionTable({ transactions }: Props) {
  return (
    <table style={{ borderCollapse: "collapse", width: "100%", fontSize: 14 }}>
      <thead>
        <tr>
          <th>Date</th>
          <th>Type</th>
          <th>Product</th>
          <th>ISIN</th>
          <th>Qty</th>
          <th>Price</th>
          <th>Fee (EUR)</th>
          <th>Net (EUR)</th>
          <th />
        </tr>
      </thead>
      <tbody>
        {transactions.map((txn) => (
          <Row key={txn.id} txn={txn} />
        ))}
      </tbody>
    </table>
  );
}
