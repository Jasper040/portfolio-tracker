import { useEffect, useState } from "react";
import { fetchTransactions } from "./api/client";
import { TransactionTable } from "./components/TransactionTable";
import type { Transaction } from "./api/types";

export default function App() {
  const [transactions, setTransactions] = useState<Transaction[]>([]);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchTransactions()
      .then((page) => {
        setTransactions(page.items);
        setTotal(page.total);
      })
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <p style={{ padding: 24 }}>Loading…</p>;
  if (error) return <p style={{ padding: 24, color: "crimson" }}>{error}</p>;

  // The ledger can hold more rows than a single fetch returns. Never state a count
  // that is quietly short of the truth: say what was fetched and what the total is.
  const heading =
    transactions.length < total
      ? `Ledger — showing ${transactions.length} of ${total.toLocaleString()} transactions`
      : `Ledger — ${total.toLocaleString()} transactions`;

  return (
    <main style={{ padding: 24, fontFamily: "system-ui, sans-serif" }}>
      <h1 style={{ fontSize: 20 }}>{heading}</h1>
      <TransactionTable transactions={transactions} />
    </main>
  );
}
