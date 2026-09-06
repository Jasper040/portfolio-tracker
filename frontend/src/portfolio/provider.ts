/** The seam between the app and its data.
 *
 *  Screens import from here and never from `fixtures.ts`. Today this derives
 *  everything from the modelled dataset; when M1 ships lot, price and dividend
 *  endpoints, `loadPortfolio()` becomes an async call and the shapes below stay
 *  put. That is the entire point of the indirection.
 *
 *  `source` on the result is not decoration. Screens read it to decide whether to
 *  show the MODELLED badge, so the badge cannot drift out of sync with reality by
 *  someone forgetting to update a screen.
 */

import {
  BENCHMARKS,
  FIXTURE_USD_EUR,
  INDUSTRY_PROXIES,
  INSTRUMENTS,
  type FixtureBenchmark,
  type FixtureInstrument,
} from "./fixtures";
import { buildSeries, monthIndex, monthLabel, MONTHS, TODAY } from "../lib/series";
import { matchLots, type LotMethod, type LotTransaction, type MatchResult } from "../lib/lots";
import { portfolioSeries, type PortfolioSeries } from "../lib/portfolio";

export type DataSource = "modelled" | "ledger";

export interface Dividend {
  /** Month index on the shared grid. */
  idx: number;
  date: string;
  gross: number;
  /** Withholding deducted at source. */
  tax: number;
  net: number;
}

export interface DerivedInstrument {
  isin: string;
  symbol: string;
  name: string;
  sector: string;
  industry: string;
  currency: string;
  country: string;
  account: string;
  withholding: number;
  dividendFrequency: number;
  /** Monthly price marks. */
  prices: number[];
  transactions: LotTransaction[];
  /** Quantity held at each month end. */
  qty: number[];
  /** Quantity if no sale had ever happened. */
  qtyNeverSold: number[];
  dividends: Dividend[];
  /** Latest price mark. */
  current: number;
  /** Previous month's mark, for the day-change column. */
  previous: number;
}

export interface PortfolioData {
  source: DataSource;
  asOf: string;
  baseCurrency: string;
  instruments: DerivedInstrument[];
  benchmarks: FixtureBenchmark[];
  benchmarkPrices: Record<string, number[]>;
  series: PortfolioSeries;
  industryProxies: Readonly<Record<string, readonly [string, string]>>;
  usdEur: number;
  /** Lot matching for every instrument under one method. Memoised per method,
   *  because switching FIFO/LIFO/HIFO re-renders every screen and re-matching
   *  twelve instruments on each keystroke is pure waste. */
  match(method: LotMethod): Record<string, MatchResult>;
}

function toLotTransactions(inst: FixtureInstrument): LotTransaction[] {
  return inst.transactions.map(([date, type, qty, price, fees], i) => ({
    id: `${inst.symbol}-${i + 1}`,
    date,
    type,
    qty,
    price,
    fees,
    idx: Math.round(monthIndex(date)),
  }));
}

/** Walk the transactions forward, producing the two quantity series.
 *
 *  `qtyNeverSold` deliberately ignores SELL rows. It is the counterfactual's
 *  raw material: the position you would hold if you had never sold anything.
 */
function quantitySeries(transactions: readonly LotTransaction[]): {
  qty: number[];
  qtyNeverSold: number[];
} {
  const qty: number[] = [];
  const qtyNeverSold: number[] = [];
  let held = 0;
  let neverSold = 0;
  let cursor = 0;

  for (let i = 0; i < MONTHS; i++) {
    while (cursor < transactions.length && (transactions[cursor]?.idx ?? Infinity) <= i) {
      const t = transactions[cursor];
      if (t) {
        if (t.type === "BUY") {
          held += t.qty;
          neverSold += t.qty;
        } else {
          held -= t.qty;
        }
      }
      cursor++;
    }
    qty.push(held);
    qtyNeverSold.push(neverSold);
  }
  return { qty, qtyNeverSold };
}

/** Synthesise a dividend schedule from a yield and a frequency.
 *
 *  Payments only occur in months the position was actually held, which is what
 *  makes the Stock Detail dividend dots line up with the holding bands rather
 *  than floating over stretches where nothing was owned.
 */
function dividendSeries(
  inst: FixtureInstrument,
  prices: readonly number[],
  qty: readonly number[],
): Dividend[] {
  if (inst.dividendFrequency <= 0) return [];

  const step = 12 / inst.dividendFrequency;
  const out: Dividend[] = [];

  for (let i = 1; i < MONTHS; i++) {
    if ((qty[i] ?? 0) <= 0) continue;
    // Phase offset so payments land on a consistent month of the cycle rather
    // than always on January.
    if (i % step !== 2 % step) continue;

    const gross = (qty[i] ?? 0) * (prices[i] ?? 0) * (inst.dividendYield / inst.dividendFrequency);
    // Sub-euro payments are noise from the generator, not events worth a table row.
    if (gross < 0.5) continue;

    const date = `${monthLabel(i)}-15`;
    if (date > TODAY) continue;

    out.push({
      idx: i,
      date,
      gross,
      tax: gross * inst.withholding,
      net: gross * (1 - inst.withholding),
    });
  }
  return out;
}

function deriveInstrument(inst: FixtureInstrument): DerivedInstrument {
  const prices = buildSeries(inst.anchors, inst.volatility, inst.symbol);
  const transactions = toLotTransactions(inst);
  const { qty, qtyNeverSold } = quantitySeries(transactions);

  return {
    isin: inst.isin,
    symbol: inst.symbol,
    name: inst.name,
    sector: inst.sector,
    industry: inst.industry,
    currency: inst.currency,
    country: inst.country,
    account: inst.account,
    withholding: inst.withholding,
    dividendFrequency: inst.dividendFrequency,
    prices,
    transactions,
    qty,
    qtyNeverSold,
    dividends: dividendSeries(inst, prices, qty),
    current: prices[MONTHS - 1] ?? 0,
    previous: prices[MONTHS - 2] ?? 0,
  };
}

let cached: PortfolioData | null = null;

/** Build (once) the whole derived dataset.
 *
 *  Synchronous today because the fixture is a module import. The signature is
 *  kept trivial so the eventual `async` version is a one-line change at the two
 *  call sites rather than a refactor of every screen.
 */
export function loadPortfolio(): PortfolioData {
  if (cached) return cached;

  const instruments = INSTRUMENTS.map(deriveInstrument);

  const benchmarkPrices: Record<string, number[]> = {};
  for (const b of BENCHMARKS) {
    benchmarkPrices[b.key] = buildSeries(b.anchors, b.volatility, b.key);
  }

  // IWDA is the funding-replay benchmark: the dashboard's grey line is "the same
  // deposits, into a world tracker instead".
  const replayBenchmark = benchmarkPrices["IWDA"] ?? [];
  const series = portfolioSeries(instruments, replayBenchmark);

  const matchCache = new Map<LotMethod, Record<string, MatchResult>>();

  cached = {
    source: "modelled",
    asOf: TODAY,
    baseCurrency: "EUR",
    instruments,
    benchmarks: [...BENCHMARKS],
    benchmarkPrices,
    series,
    industryProxies: INDUSTRY_PROXIES,
    usdEur: FIXTURE_USD_EUR,
    match(method: LotMethod) {
      const hit = matchCache.get(method);
      if (hit) return hit;
      const result: Record<string, MatchResult> = {};
      for (const inst of instruments) {
        result[inst.isin] = matchLots(inst.transactions, method);
      }
      matchCache.set(method, result);
      return result;
    },
  };
  return cached;
}
