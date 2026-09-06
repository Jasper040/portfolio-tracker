// Money arrives as a string on purpose: JSON numbers are IEEE doubles and would
// reintroduce the cent drift the backend's Decimal storage prevents. Format these
// for display; never parse them into a float for arithmetic.
export interface Transaction {
  id: string;
  trade_date: string;
  txn_type: string;
  isin: string | null;
  product_name: string | null;
  quantity: string | null;
  price_local: string | null;
  currency_local: string | null;
  fx_rate: string | null;
  fee_base: string;
  tax_base: string;
  net_base: string;
  order_ref: string | null;
  is_economic: boolean;
  closure_reason: string;
  raw: Record<string, string>;
}

/** Which lot-matching method produced the realised figures in a response.
 *  `null` is a real answer: no matching was applied. */
export type LotMethodTag = "FIFO" | "LIFO" | "HIFO";

/** How much of the requested data the response could account for (design doc 8.1).
 *  Anything short of "full" means the UI renders "no data" -- never a zero, and
 *  never a silent omission from an aggregate. */
export type Coverage = "missing" | "partial" | "manual" | "full";

/** Provenance, carried by every response envelope. The backend cannot construct
 *  one without these two fields, so they are always present rather than optional
 *  here -- see `Provenance` in backend/app/api/schemas.py. */
export interface Provenance {
  method: LotMethodTag | null;
  coverage: Coverage;
}

export interface TransactionPage extends Provenance {
  items: Transaction[];
  total: number;
  limit: number;
  offset: number;
}

/** Money is a string for the same reason it is on TransactionOut: a JSON number
 *  is an IEEE double, and rounding a cost basis to one loses the exactness the
 *  backend's Decimal storage exists to keep. */
export interface Lot {
  id: string;
  method: LotMethodTag;
  isin: string;
  source_ref: string;
  opened_on: string;
  quantity: string;
  price: string;
  cost_basis: string;
  commission: string;
  autofx: string;
  tax: string;
}

export interface LotPage extends Provenance {
  items: Lot[];
  total: number;
}

export interface Closure {
  id: string;
  method: LotMethodTag;
  isin: string;
  /** Both halves of the pair. A closure is a (buy, sale) match, and carrying only
   *  the buy would leave half of it untraceable back to the ledger rows it came
   *  from -- see `Closure` in backend/app/domain/lots.py. */
  lot_source_ref: string;
  sale_source_ref: string;
  opened_on: string;
  closed_on: string;
  quantity: string;
  open_price: string;
  close_price: string;
  gross_pnl: string;
  commission: string;
  autofx: string;
  tax: string;
  pnl: string;
  holding_days: number;
  /** `null` where the figure is genuinely undefined -- a zero basis has no return,
   *  a same-day round trip has no annualised one. Render "—", never "0%". */
  return_pct: string | null;
  annualised_return: string | null;
}

/** One day of the net portfolio value: holdings at market plus cash, so a debit
 *  balance reduces it. `value_base` and `holdings_base` are `null` -- never "0"
 *  -- when a held instrument could not be priced (design doc 8.1). */
export interface ValuationPoint {
  date: string;
  holdings_base: string | null;
  cash_base: string;
  value_base: string | null;
  coverage: Coverage;
  /** The share of the day's holdings value that is fresh or hand-supplied.
   *  `null` exactly when coverage is "missing": there is no total, so there is
   *  no denominator. */
  covered_pct: string | null;
}

export interface ValuationSeries extends Provenance {
  items: ValuationPoint[];
  start: string | null;
  end: string | null;
  /** Echoed back with `clamped`, so a window longer than the ledger can say it
   *  was shortened rather than silently drawing a shorter chart. */
  requested_from: string | null;
  clamped: boolean;
  base_currency: string;
}

/** Named `LivePosition` rather than `Position` because `portfolio/aggregate.ts`
 *  already exports a `PositionRow` from the modelled dataset, and a screen that
 *  imported the wrong one would compile and be wrong. */
export interface LivePosition {
  isin: string;
  product_name: string;
  currency: string;
  quantity: string;
  cost_basis: string;
  charges_base: string;
  price: string | null;
  price_date: string | null;
  /** Which provider answered. "manual" belongs beside a figure somebody typed. */
  source: string | null;
  market_value_base: string | null;
  /** Gross, charges and net kept apart all the way to the screen (design doc 6.4). */
  gross_unrealised_base: string | null;
  unrealised_base: string | null;
  unrealised_pct: string | null;
  coverage: Coverage;
}

export interface PositionsPage extends Provenance {
  items: LivePosition[];
  as_of: string | null;
  total_cost_basis: string;
  /** `null` when ANY position is unpriceable. Render "—", never a partial sum. */
  total_market_value_base: string | null;
  total_unrealised_base: string | null;
  base_currency: string;
}

export interface ClosurePage extends Provenance {
  items: Closure[];
  total: number;
}
