/** Portfolio-level aggregation and the two return measures.
 *
 *  TWR and MWR answer different questions and routinely disagree, which is why
 *  the dashboard shows both rather than picking one:
 *
 *  - TWR (time-weighted) strips out the effect of WHEN money was deposited. It
 *    measures the decisions, not the funding schedule. It is what you compare
 *    against a benchmark.
 *  - MWR (money-weighted, an IRR) keeps that effect in. A large deposit made
 *    just before a rally flatters MWR and leaves TWR unmoved. It is what your
 *    actual euros did.
 */

import { MONTHS } from "./series";
import type { LotTransaction } from "./lots";

export interface InstrumentSeries {
  /** Monthly price marks, length MONTHS. */
  prices: number[];
  /** Quantity actually held at each month end. */
  qty: number[];
  /** Quantity that WOULD be held if no sale had ever happened. Drives the
   *  "never sold" counterfactual line -- see lib/counterfactual.ts. */
  qtyNeverSold: number[];
  transactions: readonly LotTransaction[];
}

export interface PortfolioSeries {
  /** Market value at each month end. */
  value: number[];
  /** Market value under the never-sold counterfactual. */
  valueNeverSold: number[];
  /** Net external cashflow INTO the portfolio in each month.
   *  Positive = money in (a purchase). Negative = money out (a sale).
   *  Fees always add to the cash consumed, on both buys and sells. */
  cashflow: number[];
  /** Value of a portfolio that put every one of those cashflows into the
   *  benchmark instead, at that month's benchmark price. */
  benchmarkReplay: number[];
}

export function portfolioSeries(
  instruments: readonly InstrumentSeries[],
  benchmarkPrices: readonly number[],
): PortfolioSeries {
  const value: number[] = [];
  const valueNeverSold: number[] = [];
  const cashflow: number[] = [];
  const benchmarkReplay: number[] = [];

  // Units of the benchmark accumulated so far. Buying it with the same money on
  // the same days is the only comparison that is actually investable; comparing
  // against the index return alone would silently assume perfect lump-sum timing.
  let benchmarkUnits = 0;

  for (let i = 0; i < MONTHS; i++) {
    let total = 0;
    let totalNeverSold = 0;
    let netCash = 0;

    for (const inst of instruments) {
      total += (inst.qty[i] ?? 0) * (inst.prices[i] ?? 0);
      totalNeverSold += (inst.qtyNeverSold[i] ?? 0) * (inst.prices[i] ?? 0);
      for (const t of inst.transactions) {
        if (t.idx !== i) continue;
        netCash += (t.type === "BUY" ? 1 : -1) * (t.qty * t.price) + t.fees;
      }
    }

    const bp = benchmarkPrices[i] ?? 0;
    if (bp > 0) benchmarkUnits += netCash / bp;

    value.push(total);
    valueNeverSold.push(totalNeverSold);
    cashflow.push(netCash);
    benchmarkReplay.push(benchmarkUnits * bp);
  }

  return { value, valueNeverSold, cashflow, benchmarkReplay };
}

export type Period = "1M" | "3M" | "YTD" | "1Y" | "3Y" | "Max";

export const PERIODS: readonly Period[] = ["1M", "3M", "YTD", "1Y", "3Y", "Max"] as const;

/** First month index included in a period. */
export function periodStartIndex(period: Period): number {
  const last = MONTHS - 1;
  switch (period) {
    case "1M":
      return last - 1;
    case "3M":
      return last - 3;
    // 84 is 2026-01 on a grid that starts at 2019-01.
    case "YTD":
      return 84;
    case "1Y":
      return last - 12;
    case "3Y":
      return last - 36;
    case "Max":
      return 0;
  }
}

/** Time-weighted return: chain-link each month's growth after removing that
 *  month's external cashflow, so deposits and withdrawals do not register as
 *  performance.
 *
 *  Months opening at zero value are skipped rather than treated as infinite
 *  growth -- before the first purchase there is no portfolio to have returned
 *  anything, and dividing by zero there would poison the whole chain.
 */
export function timeWeightedReturn(series: PortfolioSeries, startIndex: number): number {
  let factor = 1;
  for (let i = Math.max(1, startIndex + 1); i < MONTHS; i++) {
    const begin = series.value[i - 1] ?? 0;
    if (begin <= 0) continue;
    factor *= ((series.value[i] ?? 0) - (series.cashflow[i] ?? 0)) / begin;
  }
  return factor - 1;
}

/** Money-weighted return: the annualised IRR of the actual cashflow schedule.
 *
 *  Solved by bisection rather than Newton because the NPV of an irregular
 *  schedule can have a flat or badly-behaved derivative, and bisection cannot
 *  diverge -- it just narrows. 80 iterations over [-90%, +300%] converge far
 *  below display precision.
 *
 *  Sign convention: money leaving your pocket is negative, so the opening value
 *  and every deposit are negative and the closing value is positive.
 */
export function moneyWeightedReturn(series: PortfolioSeries, startIndex: number): number {
  const flows: { t: number; amount: number }[] = [
    { t: 0, amount: -(series.value[startIndex] ?? 0) },
  ];

  for (let i = startIndex + 1; i < MONTHS; i++) {
    const cf = series.cashflow[i] ?? 0;
    if (Math.abs(cf) > 0.01) flows.push({ t: (i - startIndex) / 12, amount: -cf });
  }
  flows.push({
    t: (MONTHS - 1 - startIndex) / 12,
    amount: series.value[MONTHS - 1] ?? 0,
  });

  const npv = (rate: number) =>
    flows.reduce((acc, f) => acc + f.amount / Math.pow(1 + rate, f.t), 0);

  let lo = -0.9;
  let hi = 3;
  for (let k = 0; k < 80; k++) {
    const mid = (lo + hi) / 2;
    if (npv(mid) > 0) lo = mid;
    else hi = mid;
  }
  return (lo + hi) / 2;
}

/** Rebase a series to 100 at `from`, leaving everything before it undefined so a
 *  chart draws nothing there rather than a misleading flat line at 100. */
export function rebase(values: readonly number[], from: number): (number | null)[] {
  const base = values[from];
  if (!base) return values.map(() => null);
  return values.map((v, i) => (i < from ? null : (v / base) * 100));
}
