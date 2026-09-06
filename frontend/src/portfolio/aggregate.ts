/** Portfolio-wide aggregates, computed once per (data, method) pair.
 *
 *  Every screen needs some of this and no screen needs all of it, but computing
 *  it in one place matters more than trimming it: the Positions footer, the
 *  Dashboard KPI row and the What-If summary all quote total value, and the one
 *  thing worse than computing it three times is computing it three times
 *  slightly differently.
 */

import { scorecard, closureDelta, type Scorecard } from "../lib/counterfactual";
import type { Closure, LotMethod, MatchResult } from "../lib/lots";
import type { DerivedInstrument, PortfolioData } from "./provider";

export interface PositionRow {
  instrument: DerivedInstrument;
  match: MatchResult;
  /** Market value of the open quantity. */
  value: number;
  /** Unrealised P&L against cost basis. */
  pnl: number;
  pnlPct: number;
  /** Latest month-on-month price change. The design calls this "Day"; on a
   *  monthly grid it is the most recent mark-to-mark move. */
  dayChange: number;
}

export interface ClosureEntry {
  instrument: DerivedInstrument;
  closure: Closure;
  /** Counterfactual delta. Positive = the sale cost money. */
  delta: number;
}

export interface Aggregates {
  rows: PositionRow[];
  /** Rows with a non-zero open position. */
  held: PositionRow[];
  totalValue: number;
  totalCost: number;
  totalUnrealised: number;
  totalRealised: number;
  totalDividendNet: number;
  totalDividendGross: number;
  totalFees: number;
  transactionCount: number;
  closures: ClosureEntry[];
  scorecard: Scorecard;
  /** Value-weighted day change across held positions. */
  dayChange: number;
}

export function aggregate(data: PortfolioData, method: LotMethod): Aggregates {
  const matches = data.match(method);

  const rows: PositionRow[] = data.instruments.map((instrument) => {
    const match = matches[instrument.isin] ?? {
      closures: [],
      open: [],
      qty: 0,
      cost: 0,
      realised: 0,
    };
    const value = match.qty * instrument.current;
    const pnl = value - match.cost;
    return {
      instrument,
      match,
      value,
      pnl,
      pnlPct: match.cost > 0 ? pnl / match.cost : 0,
      // Guard the denominator: a series that starts at zero would otherwise
      // report an infinite day change on its first mark.
      dayChange: instrument.previous > 0 ? instrument.current / instrument.previous - 1 : 0,
    };
  });

  const held = rows.filter((r) => r.match.qty > 0);
  const totalValue = held.reduce((a, r) => a + r.value, 0);
  const totalCost = held.reduce((a, r) => a + r.match.cost, 0);

  const closures: ClosureEntry[] = [];
  for (const row of rows) {
    for (const closure of row.match.closures) {
      closures.push({
        instrument: row.instrument,
        closure,
        delta: closureDelta(closure, row.instrument.current).delta,
      });
    }
  }

  const totalDividendNet = data.instruments.reduce(
    (a, i) => a + i.dividends.reduce((b, d) => b + d.net, 0),
    0,
  );
  const totalDividendGross = data.instruments.reduce(
    (a, i) => a + i.dividends.reduce((b, d) => b + d.gross, 0),
    0,
  );

  return {
    rows,
    held,
    totalValue,
    totalCost,
    totalUnrealised: totalValue - totalCost,
    totalRealised: rows.reduce((a, r) => a + r.match.realised, 0),
    totalDividendNet,
    totalDividendGross,
    totalFees: data.instruments.reduce(
      (a, i) => a + i.transactions.reduce((b, t) => b + t.fees, 0),
      0,
    ),
    transactionCount: data.instruments.reduce((a, i) => a + i.transactions.length, 0),
    closures,
    scorecard: scorecard(
      closures.map((e) => ({ closure: e.closure, currentPrice: e.instrument.current })),
    ),
    dayChange:
      totalValue > 0 ? held.reduce((a, r) => a + r.value * r.dayChange, 0) / totalValue : 0,
  };
}

/** Group held positions by some key and return slices sorted largest-first.
 *
 *  Sorted descending so palette index 0 -- the accent blue -- always lands on the
 *  biggest slice, making the four allocation donuts visually consistent with each
 *  other even though they group by different things.
 */
export function groupByValue(
  held: readonly PositionRow[],
  key: (row: PositionRow) => string,
): { label: string; value: number }[] {
  const totals = new Map<string, number>();
  for (const row of held) {
    const k = key(row);
    totals.set(k, (totals.get(k) ?? 0) + row.value);
  }
  return [...totals.entries()]
    .map(([label, value]) => ({ label, value }))
    .sort((a, b) => b.value - a.value);
}
