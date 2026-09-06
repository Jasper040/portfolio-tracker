import type { ClosurePage, LotMethodTag, LotPage, TransactionPage } from "./types";

// Falls back to the local dev API when VITE_API_BASE is not set, so the app keeps
// working out of the box while still being deployable against a different backend.
const BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";

export interface TransactionQuery {
  limit?: number;
  offset?: number;
  /** Server-side filter. The backend indexes `isin`, so filtering there rather
   *  than in the browser keeps the page count honest -- a client-side filter
   *  would narrow the rows on screen while `total` still counted every row. */
  isin?: string;
}

export async function fetchTransactions(query: TransactionQuery = {}): Promise<TransactionPage> {
  const { limit = 500, offset = 0, isin } = query;

  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (isin) params.set("isin", isin);

  const response = await fetch(`${BASE}/api/transactions?${params}`);
  if (!response.ok) {
    throw new Error(`Failed to load transactions: ${response.status} ${response.statusText}`);
  }
  return (await response.json()) as TransactionPage;
}

export interface LotQuery {
  isin?: string;
  limit?: number;
  offset?: number;
}

async function getPage<T>(path: string, method: LotMethodTag, query: LotQuery): Promise<T> {
  const { isin, limit = 500, offset = 0 } = query;
  const params = new URLSearchParams({
    method,
    limit: String(limit),
    offset: String(offset),
  });
  if (isin) params.set("isin", isin);

  const response = await fetch(`${BASE}${path}?${params}`);
  if (!response.ok) {
    throw new Error(`Failed to load ${path}: ${response.status} ${response.statusText}`);
  }
  return (await response.json()) as T;
}

export function fetchLots(method: LotMethodTag, query: LotQuery = {}): Promise<LotPage> {
  return getPage<LotPage>("/api/lots", method, query);
}

export function fetchClosures(method: LotMethodTag, query: LotQuery = {}): Promise<ClosurePage> {
  return getPage<ClosurePage>("/api/closures", method, query);
}
