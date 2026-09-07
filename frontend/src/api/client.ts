import type {
  Benchmark,
  ClosurePage,
  InstrumentChart,
  InstrumentSummary,
  LotMethodTag,
  LotPage,
  PositionsPage,
  TransactionPage,
  ValuationSeries,
} from "./types";

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

export interface ValuationQuery {
  /** ISO date. Omitted entirely for MAX, so the server answers from the first
   *  day a position existed rather than from a start this client guessed. */
  from?: string | null;
  to?: string | null;
}

export async function fetchValuation(query: ValuationQuery = {}): Promise<ValuationSeries> {
  const params = new URLSearchParams();
  if (query.from) params.set("from", query.from);
  if (query.to) params.set("to", query.to);
  const suffix = params.toString() ? `?${params}` : "";

  const response = await fetch(`${BASE}/api/valuation${suffix}`);
  if (!response.ok) {
    throw new Error(`Failed to load valuation: ${response.status} ${response.statusText}`);
  }
  return (await response.json()) as ValuationSeries;
}

export async function fetchPositions(method: LotMethodTag): Promise<PositionsPage> {
  const response = await fetch(`${BASE}/api/positions?method=${method}`);
  if (!response.ok) {
    throw new Error(`Failed to load positions: ${response.status} ${response.statusText}`);
  }
  return (await response.json()) as PositionsPage;
}

/** What `range` accepts on `GET /api/instruments/{isin}/chart` -- mirrors the
 *  backend's `ChartRange` Literal in `routes_instrument.py` exactly, "max"
 *  included lowercase, so a typo here cannot silently fall through to the
 *  server's own default instead of raising. */
export type Range = "1Y" | "3Y" | "5Y" | "max";

export interface InstrumentChartQuery {
  range: Range;
  /** `null` omits the query parameter entirely, matching the route's own
   *  `benchmark: str | None = Query(default=None)` -- no comparison overlay,
   *  not a comparison against an empty string. */
  benchmark: string | null;
}

export async function fetchInstrumentChart(
  isin: string,
  opts: InstrumentChartQuery,
): Promise<InstrumentChart> {
  const params = new URLSearchParams({ range: opts.range });
  if (opts.benchmark) params.set("benchmark", opts.benchmark);

  const response = await fetch(`${BASE}/api/instruments/${isin}/chart?${params}`);
  if (!response.ok) {
    throw new Error(
      `Failed to load instrument chart: ${response.status} ${response.statusText}`,
    );
  }
  return (await response.json()) as InstrumentChart;
}

export async function fetchBenchmarks(): Promise<Benchmark[]> {
  const response = await fetch(`${BASE}/api/benchmarks`);
  if (!response.ok) {
    throw new Error(`Failed to load benchmarks: ${response.status} ${response.statusText}`);
  }
  const body = (await response.json()) as { items: Benchmark[] };
  return body.items;
}

/** Every instrument the ledger has ever recorded an economic trade for --
 *  not only the ones with an open position today. Backs the instrument
 *  picker on `screens/Instrument.tsx`: a fully exited instrument is a real
 *  "out of market" case M3 exists to show, and `/api/positions` cannot
 *  surface it. */
export async function fetchInstruments(): Promise<InstrumentSummary[]> {
  const response = await fetch(`${BASE}/api/instruments`);
  if (!response.ok) {
    throw new Error(`Failed to load instruments: ${response.status} ${response.statusText}`);
  }
  const body = (await response.json()) as { items: InstrumentSummary[] };
  return body.items;
}
