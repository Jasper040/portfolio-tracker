import { describe, expect, it } from "vitest";
import { matchLots, type LotMethod, type LotTransaction } from "./lots";

function buy(id: string, qty: number, price: number, fees = 0): LotTransaction {
  return { id, date: `2020-01-${id.padStart(2, "0")}`, type: "BUY", qty, price, fees, idx: 0 };
}

function sell(id: string, qty: number, price: number, fees = 0): LotTransaction {
  return { id, date: `2024-01-${id.padStart(2, "0")}`, type: "SELL", qty, price, fees, idx: 48 };
}

describe("matchLots — the three methods disagree", () => {
  /** Three lots at 20, 30 and 10, then one sale of 10 at 40. Chosen so every
   *  method picks a different lot, which is the only way to prove all three are
   *  really implemented rather than two of them aliasing. */
  const transactions = [buy("1", 10, 20), buy("2", 10, 30), buy("3", 10, 10), sell("4", 10, 40)];

  const realisedUnder = (method: LotMethod) => matchLots(transactions, method).realised;

  it("FIFO closes the oldest lot", () => {
    expect(realisedUnder("FIFO")).toBe(10 * (40 - 20));
  });

  it("LIFO closes the newest lot", () => {
    expect(realisedUnder("LIFO")).toBe(10 * (40 - 10));
  });

  it("HIFO closes the most expensive lot, realising the smallest gain", () => {
    expect(realisedUnder("HIFO")).toBe(10 * (40 - 30));
    expect(realisedUnder("HIFO")).toBeLessThan(realisedUnder("FIFO"));
    expect(realisedUnder("HIFO")).toBeLessThan(realisedUnder("LIFO"));
  });

  it("leaves the same total quantity open whichever lot it picked", () => {
    // The method changes WHICH cost basis is consumed, never how much stock is
    // left. A method that changed the quantity would be a bug, not a policy.
    for (const method of ["FIFO", "LIFO", "HIFO"] as const) {
      expect(matchLots(transactions, method).qty).toBe(20);
    }
  });

  it("gives the remaining lots a different cost basis per method", () => {
    // FIFO consumed the 20 lot, so 30 and 10 remain: 400.
    expect(matchLots(transactions, "FIFO").cost).toBe(10 * 30 + 10 * 10);
    // LIFO consumed the 10 lot, so 20 and 30 remain: 500.
    expect(matchLots(transactions, "LIFO").cost).toBe(10 * 20 + 10 * 30);
  });
});

describe("matchLots — fees", () => {
  it("pro-rates both legs by each side's own quantity", () => {
    // Buy 10 @ 10 costing 2 in fees; sell 5 @ 20 costing 4.
    // Sale leg: 4 * (5/5) = 4. Buy leg: 2 * (5/10) = 1. Total 5.
    const result = matchLots([buy("1", 10, 10, 2), sell("2", 5, 20, 4)], "FIFO");
    const closure = result.closures[0];

    expect(result.closures).toHaveLength(1);
    expect(closure?.fees).toBe(5);
    expect(closure?.pnl).toBe(5 * (20 - 10) - 5);
  });

  it("does not charge a purchase fee again on the second tranche", () => {
    // The same lot sold in two halves must pay its 2,00 purchase fee once in
    // total, not once per closure.
    const result = matchLots(
      [buy("1", 10, 10, 2), sell("2", 5, 20, 0), sell("3", 5, 20, 0)],
      "FIFO",
    );
    const buyFeesCharged = result.closures.reduce((a, c) => a + c.fees, 0);
    expect(result.closures).toHaveLength(2);
    expect(buyFeesCharged).toBeCloseTo(2, 10);
  });

  it("leaves the open lot only the fees belonging to its remaining quantity", () => {
    const result = matchLots([buy("1", 10, 10, 2), sell("2", 5, 20, 0)], "FIFO");
    expect(result.open[0]?.fees).toBe(1);
    expect(result.cost).toBe(5 * 10 + 1);
  });
});

describe("matchLots — edges", () => {
  it("splits one sale across as many lots as it consumes", () => {
    const result = matchLots([buy("1", 5, 10), buy("2", 5, 20), sell("3", 8, 30)], "FIFO");
    expect(result.closures.map((c) => c.qty)).toEqual([5, 3]);
    expect(result.qty).toBe(2);
  });

  it("terminates and drops the excess when selling more than is held", () => {
    // Shorts are not modelled. The loop must stop rather than spin or invent a
    // negative lot -- this test exists to catch the infinite loop, so a hang here
    // is the failure.
    const result = matchLots([buy("1", 5, 10), sell("2", 10, 30)], "FIFO");
    expect(result.closures).toHaveLength(1);
    expect(result.closures[0]?.qty).toBe(5);
    expect(result.qty).toBe(0);
  });

  it("reports nothing realised when there are no sales", () => {
    const result = matchLots([buy("1", 10, 10, 1)], "FIFO");
    expect(result.realised).toBe(0);
    expect(result.closures).toHaveLength(0);
    expect(result.cost).toBe(101);
  });

  it("treats a fully consumed lot as closed despite float residue", () => {
    // 0.1 + 0.2 style residue must not leave a lot open at 1e-17.
    const result = matchLots([buy("1", 0.3, 10), sell("2", 0.1, 20), sell("3", 0.2, 20)], "FIFO");
    expect(result.open).toHaveLength(0);
    expect(result.qty).toBe(0);
  });
});
