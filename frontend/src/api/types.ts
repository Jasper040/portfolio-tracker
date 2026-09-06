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
