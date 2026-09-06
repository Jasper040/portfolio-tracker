import { describe, expect, it } from "vitest";
import { closureDelta, scorecard } from "./counterfactual";
import type { Closure } from "./lots";

function closure(qty: number, closePrice: number, pnl = 0): Closure {
  return {
    lotId: "L1",
    openDate: "2020-01-01",
    openPrice: 10,
    openIdx: 12,
    closeDate: "2024-01-01",
    closePrice,
    closeIdx: 60,
    qty,
    fees: 0,
    pnl,
  };
}

describe("closureDelta", () => {
  it("is positive when the price rose after the sale — the sale cost money", () => {
    // The sign convention the whole What-If screen is built on, and the reason
    // `costColor` inverts the usual green-up palette.
    const { delta, ifHeldValue } = closureDelta(closure(10, 20), 30);
    expect(delta).toBe(100);
    expect(ifHeldValue).toBe(300);
  });

  it("is negative when the price fell after the sale — the sale saved money", () => {
    expect(closureDelta(closure(10, 20), 15).delta).toBe(-50);
  });

  it("is zero when the price is unchanged", () => {
    expect(closureDelta(closure(10, 20), 20).delta).toBe(0);
  });

  it("scales with quantity, not just price movement", () => {
    // A one-share mistake and a thousand-share mistake are not the same mistake.
    expect(closureDelta(closure(100, 20), 30).delta).toBe(1000);
  });
});

describe("scorecard", () => {
  const entries = [
    { closure: closure(10, 20, 100), currentPrice: 30 }, // +100, cost money
    { closure: closure(10, 20, 50), currentPrice: 15 }, // -50, saved money
  ];

  it("nets the two directions", () => {
    expect(scorecard(entries).net).toBe(50);
  });

  it("reports each direction separately, so a near-zero net is not mistaken for a quiet year", () => {
    // A net of 50 could be one small error or two large opposite ones. Those are
    // very different stories about how someone trades.
    const result = scorecard(entries);
    expect(result.costMe).toBe(100);
    expect(result.savedMe).toBe(50);
    expect(result.closureCount).toBe(2);
  });

  it("reports savedMe as a positive number for display", () => {
    const result = scorecard([{ closure: closure(10, 20, 0), currentPrice: 10 }]);
    expect(result.savedMe).toBe(100);
    expect(result.net).toBe(-100);
  });

  it("returns zeroes for a portfolio that has never sold anything", () => {
    expect(scorecard([])).toEqual({ net: 0, costMe: 0, savedMe: 0, closureCount: 0 });
  });
});
