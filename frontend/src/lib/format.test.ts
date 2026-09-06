import { describe, expect, it } from "vitest";
import {
  ANNUALISED_MIN_HOLDING_DAYS,
  annualisedPercent,
  costColor,
  daysBetween,
  decimal,
  decimalEur,
  decimalIsNegative,
  decimalPercent,
  eur,
  eurCompact,
  eurSigned,
  num,
  pct,
  pctPlain,
  shortDate,
  signColor,
} from "./format";

/** U+2212 MINUS, not a hyphen. Spelled out so a failure message shows which one
 *  actually came back rather than two visually identical strings. */
const MINUS = "−";

describe("decimal — the ledger path", () => {
  it("never rounds, however many decimals are asked for", () => {
    // The whole reason this function exists. Rounding here would make the screen
    // disagree with the ledger for a reason the reader could not see.
    expect(decimal("1.239", 2)).toBe("1,239");
    expect(decimal("0.005", 2)).toBe("0,005");
    expect(decimal("99.999999", 2)).toBe("99,999999");
  });

  it("preserves trailing zeros exactly as stored", () => {
    expect(decimal("12.3400")).toBe("12,3400");
    expect(decimal("1.0000")).toBe("1,0000");
  });

  it("pads up to minDecimals, which is lossless", () => {
    expect(decimal("5", 2)).toBe("5,00");
    expect(decimal("0.5", 2)).toBe("0,50");
  });

  it("groups thousands with a dot and separates decimals with a comma", () => {
    expect(decimal("1234.5", 2)).toBe("1.234,50");
    expect(decimal("1000000", 0)).toBe("1.000.000");
    expect(decimal("999", 0)).toBe("999");
  });

  it("renders a negative with a typographic minus", () => {
    expect(decimal("-93.14", 2)).toBe(`${MINUS}93,14`);
  });

  it("strips leading zeros without eating the last one", () => {
    expect(decimal("007", 0)).toBe("7");
    expect(decimal("0", 2)).toBe("0,00");
  });

  it("renders absent values as an em dash", () => {
    expect(decimal(null)).toBe("—");
    expect(decimal(undefined)).toBe("—");
    expect(decimal("")).toBe("—");
    expect(decimal("   ")).toBe("—");
  });

  it("returns anything that is not a plain decimal untouched", () => {
    // Showing the raw value is far more debuggable than showing a confidently
    // wrong number if the backend ever sends something unexpected.
    expect(decimal("1.2.3")).toBe("1.2.3");
    expect(decimal("1e5")).toBe("1e5");
    expect(decimal("abc")).toBe("abc");
  });

  it("adds the currency mark without touching the digits", () => {
    expect(decimalEur("1234.5")).toBe("€ 1.234,50");
    expect(decimalEur(null)).toBe("—");
  });

  it("detects sign without parsing", () => {
    expect(decimalIsNegative("-0.01")).toBe(true);
    expect(decimalIsNegative("0.01")).toBe(false);
    expect(decimalIsNegative(null)).toBe(false);
  });
});

describe("decimal — rounding via maxDecimals (string arithmetic, never Number())", () => {
  it("leaves the fraction untouched when maxDecimals is not given", () => {
    // The default: every pre-existing caller keeps its current behaviour.
    expect(decimal("41.80333333333333333333333332")).toBe("41,80333333333333333333333332");
  });

  it("rounds half away from zero rather than truncating", () => {
    // 0.005 at 2 decimals rounds up, it does not just drop the trailing digit.
    expect(decimal("0.005", 2, 2)).toBe("0,01");
    expect(decimal("0.004", 2, 2)).toBe("0,00");
  });

  it("ripples a carry through an all-nines fraction", () => {
    expect(decimal("0.999", 2, 2)).toBe("1,00");
  });

  it("carries into a new integer digit", () => {
    expect(decimal("89.996", 2, 2)).toBe("90,00");
  });

  it("rounds a negative by magnitude, then reapplies the sign", () => {
    expect(decimal("-1.005", 2, 2)).toBe(`${MINUS}1,01`);
  });

  it("does nothing when the value already has fewer decimals than the cap", () => {
    expect(decimal("5", 2, 4)).toBe("5,00");
    expect(decimal("12.3", 0, 4)).toBe("12,3");
  });

  it("does nothing to a value with no fraction at all", () => {
    expect(decimal("42", 0, 2)).toBe("42");
  });

  it("caps the two real figures that motivated this fix", () => {
    // Real gross_pnl / pnl values from the owner's export: price = |value_base|
    // / |quantity| is a non-terminating Decimal whenever quantity does not
    // divide the euro value evenly, and that precision propagates into pnl.
    // The stored value keeps its full precision; only the display caps it.
    expect(decimal("41.80333333333333333333333332", 2, 2)).toBe("41,80");
    expect(decimal("-0.883333333333333333333333350", 2, 2)).toBe(`${MINUS}0,88`);
  });

  it("decimalEur caps at 2 decimals by default", () => {
    expect(decimalEur("41.80333333333333333333333332")).toBe("€ 41,80");
    expect(decimalEur("-0.883333333333333333333333350")).toBe(`€ ${MINUS}0,88`);
  });

  it("decimal at 4 decimals keeps DeGiro's own price precision intact", () => {
    // The acceptance figure for the ORION lot: 65,530 must survive untouched.
    expect(decimal("65.530", 2, 4)).toBe("65,530");
    expect(decimal("153.70", 2, 4)).toBe("153,70");
  });
});

describe("decimalPercent — a ratio string as a percentage, never through Number()", () => {
  it("moves the point two places and formats like every other cell", () => {
    expect(decimalPercent("0.1234")).toBe("12,34%");
    expect(decimalPercent("0.5")).toBe("50,00%");
  });

  it("renders a negative return with a typographic minus, not a hyphen", () => {
    // The bug this replaced: `Number(v).toFixed(2)` emitted "-12.34%" beside a
    // "1.234,56" money column -- ASCII hyphen, ASCII point, no grouping.
    expect(decimalPercent("-0.1234")).toBe(`${MINUS}12,34%`);
    expect(decimalPercent("-0.0525")).toBe(`${MINUS}5,25%`);
  });

  it("handles a value with no fraction at all", () => {
    expect(decimalPercent("1")).toBe("100,00%");
    expect(decimalPercent("0")).toBe("0,00%");
    expect(decimalPercent("-2")).toBe(`${MINUS}200,00%`);
  });

  it("handles a fraction shorter than two digits", () => {
    expect(decimalPercent("0.5")).toBe("50,00%");
    expect(decimalPercent("0.05")).toBe("5,00%");
    expect(decimalPercent(".5")).toBe("50,00%");
  });

  it("renders null as an em dash, never 0%", () => {
    // A closure whose basis was zero has no return. "0%" would claim it broke
    // even, which is a different statement from "there is no such figure".
    expect(decimalPercent(null)).toBe("—");
    expect(decimalPercent(undefined)).toBe("—");
    expect(decimalPercent("")).toBe("—");
    expect(decimalPercent("   ")).toBe("—");
  });

  it("expands scientific notation rather than mangling it", () => {
    // `str(Decimal)` emits "1E-7" once the adjusted exponent drops below -6,
    // which a tiny return_pct reaches. Sliding the dot two characters along that
    // string would print something that is not a number at all.
    expect(decimalPercent("1E-7")).toBe("0,00%");
    expect(decimalPercent("1.5E-3")).toBe("0,15%");
    expect(decimalPercent("-1.5E-3")).toBe(`${MINUS}0,15%`);
    expect(decimalPercent("1e-2")).toBe("1,00%");
    expect(decimalPercent("1E+2")).toBe("10.000,00%");
    expect(decimalPercent("1.234E2")).toBe("12.340,00%");
  });

  it("rounds half away from zero at two decimals, in digit space", () => {
    expect(decimalPercent("0.123456")).toBe("12,35%");
    expect(decimalPercent("0.0099999")).toBe("1,00%");
    expect(decimalPercent("-0.123456")).toBe(`${MINUS}12,35%`);
  });

  it("groups a return large enough to need it", () => {
    expect(decimalPercent("12.345")).toBe("1.234,50%");
  });

  it("keeps full precision inputs exact all the way to the rounding step", () => {
    // The stored return_pct is a full-precision Decimal quotient; only the
    // display caps it, and it must cap by digits rather than by float.
    expect(decimalPercent("0.056666666666666666666666667")).toBe("5,67%");
  });

  it("returns anything that is not a decimal untouched", () => {
    expect(decimalPercent("abc")).toBe("abc");
    expect(decimalPercent("1.2.3")).toBe("1.2.3");
  });
});

describe("computed-value formatting", () => {
  it("formats euros in the Dutch convention", () => {
    expect(eur(1234.5)).toBe("€ 1.234,50");
    expect(eur(1234.5, 0)).toBe("€ 1.235");
    expect(num(0.5, 1)).toBe("0,5");
  });

  it("compacts at thousand and million boundaries", () => {
    expect(eurCompact(999)).toBe("€ 999");
    expect(eurCompact(1234)).toBe("€ 1,2k");
    expect(eurCompact(1_500_000)).toBe("€ 1,50M");
    expect(eurCompact(-1234)).toBe(`${MINUS}€ 1,2k`);
  });

  it("always shows a sign, so a column stays aligned on it", () => {
    expect(eurSigned(0)).toBe("+€ 0");
    expect(eurSigned(-45.6, 2)).toBe(`${MINUS}€ 45,60`);
    expect(pct(0.153)).toBe("+15,3%");
    expect(pct(-0.05)).toBe(`${MINUS}5,0%`);
  });

  it("leaves shares and weights unsigned", () => {
    expect(pctPlain(0.153)).toBe("15,3%");
  });

  it("returns an em dash for non-finite input rather than NaN", () => {
    expect(num(NaN)).toBe("—");
    expect(pct(Infinity)).toBe("—");
  });
});

describe("sign colours", () => {
  it("paints gains green and losses red", () => {
    expect(signColor(1)).toBe("#3FB950");
    expect(signColor(-1)).toBe("#E5534B");
    expect(signColor(0)).toBe("#8A9199");
  });

  it("inverts for counterfactual deltas, where positive means the sale cost money", () => {
    // Painting a positive delta green because it is positive would invert the
    // meaning of the most important number on the What-If screen.
    expect(costColor(1)).toBe("#E5534B");
    expect(costColor(-1)).toBe("#3FB950");
    expect(costColor(1)).not.toBe(signColor(1));
  });
});

describe("annualisedPercent", () => {
  it("renders the figure once the holding period is long enough to mean something", () => {
    expect(annualisedPercent("0.1834", ANNUALISED_MIN_HOLDING_DAYS)).toBe("18,34%");
  });

  it("declines to annualise a holding shorter than the threshold", () => {
    // A 3-day 2% gain annualises to roughly 1.000%. Arithmetically correct and
    // useless -- so the cell says nothing rather than saying something absurd.
    expect(annualisedPercent("10.9876", 3)).toBe("—");
  });

  it("draws the line exactly at the threshold, not near it", () => {
    expect(annualisedPercent("0.5", ANNUALISED_MIN_HOLDING_DAYS - 1)).toBe("—");
    expect(annualisedPercent("0.5", ANNUALISED_MIN_HOLDING_DAYS)).toBe("50,00%");
  });

  it("renders an em-dash for a null figure however long the holding", () => {
    // `Closure.annualised_return` is null for a same-day round trip and for a
    // total loss. Length of holding cannot rescue either.
    expect(annualisedPercent(null, 4000)).toBe("—");
  });

  it("uses the same separator and sign glyph as every other cell", () => {
    // Not toFixed: that would emit "-12.34%" beside neighbours reading "1.234,56".
    expect(annualisedPercent("-0.1234", 365)).toBe("−12,34%");
  });

  it("never parses the figure through a float", () => {
    // 28 significant digits survive intact, which Number() could not carry.
    expect(annualisedPercent("0.1234567890123456789012345678", 365)).toBe("12,35%");
  });
});

describe("dates", () => {
  it("renders ISO as the dd-mm-yy DeGiro prints", () => {
    expect(shortDate("2026-09-04")).toBe("04-09-26");
  });

  it("counts whole days across a DST boundary", () => {
    // Europe/Amsterdam springs forward on 2026-03-29. Local-time parsing would
    // make this 1 day and 23 hours, and round to 1.
    expect(daysBetween("2026-03-28", "2026-03-30")).toBe(2);
    expect(daysBetween("2025-01-01", "2026-01-01")).toBe(365);
  });
});
