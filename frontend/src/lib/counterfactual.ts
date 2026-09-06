/** "What would have happened if I had not sold?"
 *
 *  This is the single most opinionated number in the app, so the assumption is
 *  named in the type system rather than buried in an expression.
 *
 *  ── The modelling choice ────────────────────────────────────────────────────
 *
 *  A closure is a quantity of an instrument sold on a date. Asking what holding
 *  it would be worth today is arithmetic. Asking what the PORTFOLIO would be
 *  worth is not, because the sale produced cash, and that cash did something:
 *
 *    NAIVE_HOLD    The shares are valued at today's price and the proceeds are
 *                  ignored. Implemented here, and what the design specifies.
 *                  It systematically OVERSTATES the counterfactual whenever sale
 *                  proceeds funded a later purchase, because the same euro is
 *                  then counted twice -- once in the unsold position and once in
 *                  whatever it actually bought. The UI says so, in amber,
 *                  wherever one of these numbers appears.
 *
 *    (unbuilt)     Alternatives worth considering when M2 lands:
 *                  - proceeds earn cash/risk-free rate from the sale date;
 *                  - proceeds are traced to the next purchase and that purchase
 *                    is unwound too (the only fully coherent option, and by far
 *                    the most work: it needs a funding graph over the ledger);
 *                  - proceeds buy the benchmark, matching the replay line the
 *                    dashboard already draws.
 *
 *  DECISION PENDING: which of those replaces NAIVE_HOLD as the default is a
 *  portfolio-accounting judgement, not an implementation detail. `Policy` exists
 *  so a second one can be added without touching any caller, and so that a chart
 *  or export can state which policy produced its numbers.
 */

import type { Closure } from "./lots";

export type Policy = "NAIVE_HOLD";

/** Every counterfactual figure the UI can show for one closure. */
export interface CounterfactualDelta {
  /** Value of the sold quantity at today's price. */
  ifHeldValue: number;
  /** ifHeldValue minus what the sale produced.
   *  POSITIVE means holding would have been worth more -- the sale cost money.
   *  This is the sign convention `format.costColor` inverts for. */
  delta: number;
}

export function closureDelta(
  closure: Closure,
  currentPrice: number,
  policy: Policy = "NAIVE_HOLD",
): CounterfactualDelta {
  // A switch on a single-member union looks redundant today. It is the seam: when
  // a second policy lands, TypeScript's exhaustiveness check turns every call
  // site that needs revisiting into a compile error instead of a silent default.
  switch (policy) {
    case "NAIVE_HOLD": {
      const ifHeldValue = closure.qty * currentPrice;
      return { ifHeldValue, delta: closure.qty * (currentPrice - closure.closePrice) };
    }
  }
}

export interface Scorecard {
  /** Net across every closure. Positive = selling cost money overall. */
  net: number;
  /** Sum of the closures that cost money (positive deltas only). */
  costMe: number;
  /** Sum of the closures that saved money, as a POSITIVE number for display. */
  savedMe: number;
  closureCount: number;
}

/** Aggregate the counterfactual across a whole portfolio.
 *
 *  `costMe` and `savedMe` are reported separately rather than only netted,
 *  because a net near zero hides whether nothing much happened or two large
 *  opposite mistakes cancelled out -- and those are very different stories about
 *  how someone trades.
 */
export function scorecard(
  entries: readonly { closure: Closure; currentPrice: number }[],
  policy: Policy = "NAIVE_HOLD",
): Scorecard {
  let costMe = 0;
  let savedMe = 0;

  for (const { closure, currentPrice } of entries) {
    const { delta } = closureDelta(closure, currentPrice, policy);
    if (delta > 0) costMe += delta;
    else savedMe += -delta;
  }

  return { net: costMe - savedMe, costMe, savedMe, closureCount: entries.length };
}
