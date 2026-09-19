import type {
  Benchmark,
  ClosurePage,
  InstrumentChart,
  InstrumentSummary,
  LotMethodTag,
  LotPage,
  PerformanceReport,
  PositionsPage,
  TransactionPage,
  ValuationSeries,
} from "./types";
import { cached } from "./cache";

// Falls back to the local dev API when VITE_API_BASE is not set, so the app keeps
// working out of the box while still being deployable against a different backend.
const BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";

/** Every GET in this module, through the session cache (PT-46).
 *
 *  One function rather than eight copies of the same four lines, which also
 *  means the cache cannot be wired into some endpoints and forgotten on others.
 *  The key is the full URL: two requests that differ in any parameter are
 *  different keys, and two that are identical are the same answer.
 *
 *  `label` survives because the error it produces is the one the reader sees on
 *  a dead backend, and "Failed to load performance" is worth more there than a
 *  generic message with a URL in it.
 *
 *  The caller receives the SAME object on a cache hit, not a copy. That is safe
 *  here only because nothing in this app mutates a response -- see the
 *  immutability rule in CLAUDE.md -- and it is the reason a screen must never
 *  sort or splice an array it got from the client in place.
 */
async function getJson<T>(path: string, label: string): Promise<T> {
  const url = `${BASE}${path}`;
  return cached<T>(url, async () => {
    const response = await fetch(url);
    if (!response.ok) {
      throw new Error(`Failed to load ${label}: ${response.status} ${response.statusText}`);
    }
    return (await response.json()) as T;
  });
}

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

  return getJson<TransactionPage>(`/api/transactions?${params}`, "transactions");
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

  return getJson<T>(`${path}?${params}`, path);
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

  return getJson<ValuationSeries>(`/api/valuation${suffix}`, "valuation");
}

export async function fetchPositions(method: LotMethodTag): Promise<PositionsPage> {
  return getJson<PositionsPage>(`/api/positions?method=${method}`, "positions");
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

  return getJson<InstrumentChart>(
    `/api/instruments/${isin}/chart?${params}`,
    "instrument chart",
  );
}

export async function fetchBenchmarks(): Promise<Benchmark[]> {
  const body = await getJson<{ items: Benchmark[] }>("/api/benchmarks", "benchmarks");
  return body.items;
}

/** Every instrument the ledger has ever recorded an economic trade for --
 *  not only the ones with an open position today. Backs the instrument
 *  picker on `screens/Instrument.tsx`: a fully exited instrument is a real
 *  "out of market" case M3 exists to show, and `/api/positions` cannot
 *  surface it. */
export async function fetchInstruments(): Promise<InstrumentSummary[]> {
  const body = await getJson<{ items: InstrumentSummary[] }>("/api/instruments", "instruments");
  return body.items;
}

export interface PerformanceQuery {
  /** ISO date, or `null` to send none -- the server then measures from the
   *  ledger's first day. The same contract as `ValuationQuery.from`. */
  from: string | null;
  /** `null` omits the parameter: no comparison, not a comparison against "". */
  benchmark: string | null;
}

export async function fetchPerformance(query: PerformanceQuery): Promise<PerformanceReport> {
  const params = new URLSearchParams();
  if (query.from) params.set("from", query.from);
  if (query.benchmark) params.set("benchmark", query.benchmark);
  const suffix = params.toString() ? `?${params}` : "";

  return getJson<PerformanceReport>(`/api/performance${suffix}`, "performance");
}
