import { useEffect, useState } from "react";
import { fetchTransactions } from "./api/client";
import { TransactionTable } from "./components/TransactionTable";
import type { Transaction } from "./api/types";

export default function App() {
  const [transactions, setTransactions] = useState<Transaction[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchTransactions()
      .then((page) => setTransactions(page.items))
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <p style={{ padding: 24 }}>Loading…</p>;
  if (error) return <p style={{ padding: 24, color: "crimson" }}>{error}</p>;

  return (
    <main style={{ padding: 24, fontFamily: "system-ui, sans-serif" }}>
      <h1 style={{ fontSize: 20 }}>Ledger — {transactions.length} transactions</h1>
      <TransactionTable transactions={transactions} />
    </main>
  );
}
