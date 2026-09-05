import type { TransactionPage } from "./types";

// Falls back to the local dev API when VITE_API_BASE is not set, so the app keeps
// working out of the box while still being deployable against a different backend.
const BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";

export async function fetchTransactions(limit = 500, offset = 0): Promise<TransactionPage> {
  const response = await fetch(`${BASE}/api/transactions?limit=${limit}&offset=${offset}`);
  if (!response.ok) {
    throw new Error(`Failed to load transactions: ${response.status} ${response.statusText}`);
  }
  return (await response.json()) as TransactionPage;
}
