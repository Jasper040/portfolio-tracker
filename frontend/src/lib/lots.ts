/** Lot matching: FIFO, LIFO and HIFO.
 *
 *  A sale does not close "a position", it closes specific PURCHASE LOTS, and
 *  which lots it picks decides the realised P&L. Selling 50 ORN bought at 8,90
 *  and 12,40 realises a different number under FIFO than under HIFO from
 *  identical ledger rows. That is why the method is a first-class input
 *  everywhere in this app rather than a hidden default -- design doc, section 9.2.
 *
 *  This module is pure: transactions in, lots and closures out. No dates parsed,
 *  no I/O, no formatting. It is the piece most worth porting to the backend
 *  unchanged when M1 lands.
 */

export type LotMethod = "FIFO" | "LIFO" | "HIFO";

export const LOT_METHODS: readonly LotMethod[] = ["FIFO", "LIFO", "HIFO"] as const;

export interface LotTransaction {
  id: string;
  date: string;
  type: "BUY" | "SELL";
  qty: number;
  price: number;
  /** Transaction cost in base currency, as a positive number. */
  fees: number;
  /** Month index on the shared grid (see lib/series.ts). */
  idx: number;
}

/** A purchase lot with quantity still open. */
export interface OpenLot {
  id: string;
  date: string;
  qty: number;
  price: number;
  /** Fees apportioned to the still-open quantity only. */
  fees: number;
  idx: number;
}

/** One matched (buy lot, sale) pair. A single sale spanning three lots produces
 *  three closures; a single lot sold in three tranches also produces three. */
export interface Closure {
  lotId: string;
  openDate: string;
  openPrice: number;
  openIdx: number;
  closeDate: string;
  closePrice: number;
  closeIdx: number;
  qty: number;
  /** Pro-rata fees from BOTH legs of the round trip. */
  fees: number;
  /** Realised P&L, net of those fees. */
  pnl: number;
}

export interface MatchResult {
  closures: Closure[];
  open: OpenLot[];
  /** Total quantity still held. */
  qty: number;
  /** Cost basis of the open quantity, fees included. */
  cost: number;
  /** Lifetime realised P&L under this method. */
  realised: number;
}

/** Quantities are floats, so an exhausted lot lands on 1e-17 rather than 0.
 *  Anything below this counts as closed. */
const EPSILON = 1e-9;

/** An open lot mid-match: it still remembers the quantity it was bought with, so
 *  fees can be pro-rated against the original rather than the dwindling balance. */
type WorkingLot = OpenLot & { remaining: number; originalQty: number };

function selectLot(available: WorkingLot[], method: LotMethod): WorkingLot | undefined {
  if (method === "FIFO") return available[0];
  if (method === "LIFO") return available[available.length - 1];
  // HIFO: highest cost first, which realises the smallest gain or the largest loss.
  return available.reduce<WorkingLot | undefined>(
    (best, lot) => (best === undefined || lot.price > best.price ? lot : best),
    undefined,
  );
}

/** Match one instrument's transactions into open lots and closures.
 *
 *  Transactions are consumed in the order given, so the caller must pass them in
 *  chronological order -- a SELL cannot match a BUY it has not seen yet.
 */
export function matchLots(
  transactions: readonly LotTransaction[],
  method: LotMethod,
): MatchResult {
  const open: WorkingLot[] = [];
  const closures: Closure[] = [];

  for (const txn of transactions) {
    if (txn.type === "BUY") {
      open.push({ ...txn, remaining: txn.qty, originalQty: txn.qty });
      continue;
    }

    let need = txn.qty;
    // The `available.length` guard is not redundant with `need`: a sale larger
    // than everything held would otherwise spin forever. Selling more than you
    // hold is a short, which this ledger does not model, so the excess quantity
    // is dropped rather than invented into a negative lot.
    for (;;) {
      if (need <= EPSILON) break;
      const available = open.filter((l) => l.remaining > EPSILON);
      if (available.length === 0) break;

      const pick = selectLot(available, method);
      if (!pick) break;

      const take = Math.min(pick.remaining, need);
      pick.remaining -= take;
      need -= take;

      // Both legs contribute fees, each pro-rated by the fraction of its OWN
      // quantity involved. A lot bought once and sold in three tranches must not
      // be charged its full purchase fee three times over.
      const fees = txn.fees * (take / txn.qty) + pick.fees * (take / pick.originalQty);

      closures.push({
        lotId: pick.id,
        openDate: pick.date,
        openPrice: pick.price,
        openIdx: pick.idx,
        closeDate: txn.date,
        closePrice: txn.price,
        closeIdx: txn.idx,
        qty: take,
        fees,
        pnl: take * (txn.price - pick.price) - fees,
      });
    }
  }

  const remaining: OpenLot[] = open
    .filter((l) => l.remaining > EPSILON)
    .map((l) => ({
      id: l.id,
      date: l.date,
      qty: l.remaining,
      price: l.price,
      fees: l.fees * (l.remaining / l.originalQty),
      idx: l.idx,
    }));

  return {
    closures,
    open: remaining,
    qty: remaining.reduce((a, l) => a + l.qty, 0),
    cost: remaining.reduce((a, l) => a + l.qty * l.price + l.fees, 0),
    realised: closures.reduce((a, c) => a + c.pnl, 0),
  };
}
