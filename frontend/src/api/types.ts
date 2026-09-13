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

/** How much of a requested SPAN a series reaches across -- a different question
 *  from `Coverage`'s staleness, and deliberately a different type. Mirrors
 *  `SpanCoverage` in backend/app/models/types.py.
 *
 *  `manual` is absent rather than unused: it is a claim about where a price came
 *  from, and a span has no such claim to make. While the span borrowed
 *  `Coverage`, the badge below had a `MANUAL` arm that could never fire. */
export type SpanCoverage = "missing" | "partial" | "full";

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

/** One day of the instrument's own price line: the unadjusted close, converted
 *  to base. `close_base` is `null`, never zero, on a day that could not be
 *  priced -- a `0` in the plotted series would draw a line to the floor and
 *  read as "this instrument was worthless that day". Mirrors `PricePointOut`. */
export interface PricePoint {
  date: string;
  close_base: string | null;
  coverage: Coverage;
  held: boolean;
}

/** One held-or-flat run of the price line. Mirrors `IntervalOut`. `price_return`
 *  is `null` when either end of the run could not be priced. */
export interface Interval {
  start: string;
  end: string;
  in_market: boolean;
  price_return: string | null;
}

/** One executed trade on the line -- never a corporate-action leg. Mirrors
 *  `MarkerOut`. */
export interface Marker {
  date: string;
  side: string;
  quantity: string;
  price: string;
  fees: string;
  position_after: string;
}

/** One day of a total-return index, rebased to 100 at its interval's start.
 *  Mirrors `IndexPointOut`. */
export interface IndexPoint {
  date: string;
  index: string;
}

/** One in-market interval's excess return against the benchmark. Mirrors
 *  `IntervalExcessOut`. `reason` is present exactly when `excess` is `null`. */
export interface IntervalExcess {
  start: string;
  end: string;
  instrument_return: string | null;
  benchmark_return: string | null;
  /** The ARITHMETIC difference, `instrument_return - benchmark_return`, not the
   *  geometric `(1 + i) / (1 + b) - 1`. Written down because the two answers
   *  diverge as returns grow and a reader comparing this against a figure from
   *  anywhere else has no other way to know which one they are holding.
   *
   *  `null`, never `0`, when either side could not be measured over the whole
   *  interval: a zero excess claims the instrument matched the benchmark
   *  exactly, which is not what "we could not tell" means. */
  excess: string | null;
  reason: string | null;
}

/** The instrument against one benchmark, both as total-return indices. Mirrors
 *  `ComparisonOut`. `span` is the BENCHMARK's span, not the instrument's
 *  staleness -- a different judgement, so a different type. */
export interface Comparison {
  basis: string;
  benchmark_key: string;
  instrument_index: IndexPoint[];
  benchmark_index: IndexPoint[];
  intervals: IntervalExcess[];
  linked_instrument_return: string | null;
  linked_benchmark_return: string | null;
  /** The arithmetic difference of the two CHAIN-LINKED returns, matching
   *  `IntervalExcess.excess`. It has to match: a geometric per-interval excess
   *  does not chain into an arithmetic summary, so mixing the two would make
   *  this figure disagree with the rows it summarises. */
  linked_excess: string | null;
  span: SpanCoverage;
}

/** One instrument's priced line, with an optional benchmark comparison.
 *  Mirrors `InstrumentChartOut`. `method` is `null`: share counts and closes
 *  are method-independent, so the chart is too -- a real answer, not an
 *  omission, exactly as it is for `ValuationSeries`. */
export interface InstrumentChart extends Provenance {
  isin: string;
  points: PricePoint[];
  intervals: Interval[];
  markers: Marker[];
  /** `null` when no benchmark was requested. Comparison is an overlay, not a
   *  precondition -- the chart still draws without one. */
  comparison: Comparison | null;
  /** The left edge the range control asked for, echoed back. Never `null`,
   *  unlike `ValuationSeries.requested_from`: a range always implies a start
   *  date, so there is no "the caller did not ask" case to represent. */
  requested_from: string;
  /** `true` when `requested_from` fell before the instrument's first trade and
   *  the window was clamped to it. The reader asked for a year and the position
   *  is four months old -- the honest reply is everything there is plus a note,
   *  never eight months of out-of-market line for a period they had never heard
   *  of the instrument. */
  clamped: boolean;
}

/** One ISIN this ledger has ever recorded an economic trade for. Mirrors
 *  `InstrumentSummaryOut`. Enough for a picker to list and label it -- price,
 *  coverage and holding state belong to `InstrumentChart`, not here. Returned
 *  for every traded instrument, not only the ones with an open position: a
 *  fully exited instrument is a real "out of market" case, not one to hide
 *  from the picker that opens its own chart. */
export interface InstrumentSummary {
  isin: string;
  product_name: string;
}

/** One configured benchmark. Mirrors `BenchmarkOut`. Never the underlying
 *  provider symbol -- that is provider trivia the screen has no use for. */
export interface Benchmark {
  key: string;
  name: string;
  /** Total expense ratio as a PERCENTAGE PER YEAR: `"0.20"` means 0.20%/yr,
   *  not 20% and not a fraction. Same unit as `config/benchmarks.yaml` and
   *  `BenchmarkOut.ter` -- reading it as a fraction renders the proxy's drag
   *  100x too small, which is why the unit is written down on all three.
   *
   *  Render it with `decimal(ter, 2, 2)`, never `decimalPercent`: that helper
   *  shifts the point two places because it takes a RATIO, and this is already
   *  a percentage. */
  ter: string;
}

/** One day's time-weighted return, measured from the previous valuation day's
 *  close. Mirrors `ReturnLinkOut`. `daily_return` is `null` -- never "0" -- when
 *  either close had no valuation or the day before had no capital, and `reason`
 *  says which. */
export interface ReturnLink {
  date: string;
  since: string;
  flow_base: string;
  daily_return: string | null;
  coverage: Coverage;
  reason: string | null;
}

/** A contiguous stretch of measurable days and its one figure. Mirrors
 *  `ReturnRunOut`. */
export interface ReturnRun {
  start: string;
  end: string;
  days: number;
  linked_return: string;
  coverage: Coverage;
}

/** One run against the benchmark. Mirrors `RunExcessOut`. `excess` is the
 *  ARITHMETIC difference, as on `IntervalExcess`, and `null` with a `reason`
 *  when the benchmark does not span the run. */
export interface RunExcess {
  start: string;
  end: string;
  portfolio_return: string;
  benchmark_return: string | null;
  excess: string | null;
  span: SpanCoverage;
  reason: string | null;
}

/** The portfolio against one benchmark. Mirrors `PortfolioComparisonOut`.
 *  `basis` and `dividends` describe the BENCHMARK side; `PerformanceReport`
 *  carries the portfolio's. */
export interface PortfolioComparison {
  basis: string;
  dividends: string;
  benchmark_key: string;
  benchmark_index: IndexPoint[];
  runs: RunExcess[];
  benchmark_return: string | null;
  excess: string | null;
  span: SpanCoverage;
}

/** The portfolio's time-weighted return over a window. Mirrors `PerformanceOut`.
 *  `linked_return` is `null` whenever the window holds a gap -- no single figure
 *  spans one (M6a-7) -- and `runs` still carries a figure per stretch. */
export interface PerformanceReport extends Provenance {
  basis: string;
  lane: string;
  dividends: string;
  base_currency: string;
  start: string | null;
  end: string | null;
  requested_from: string | null;
  clamped: boolean;
  links: ReturnLink[];
  runs: ReturnRun[];
  portfolio_index: IndexPoint[];
  linked_return: string | null;
  gaps: number;
  reason: string | null;
  comparison: PortfolioComparison | null;
}
