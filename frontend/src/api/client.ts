import type { TransactionPage } from "./types";

const BASE = "http://localhost:8000";

export async function fetchTransactions(limit = 500, offset = 0): Promise<TransactionPage> {
  const response = await fetch(`${BASE}/api/transactions?limit=${limit}&offset=${offset}`);
  if (!response.ok) {
    throw new Error(`Failed to load transactions: ${response.status} ${response.statusText}`);
  }
  return (await response.json()) as TransactionPage;
}
