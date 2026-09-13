/** Display formatting. Dutch locale throughout: "€ 1.234,56", matching DeGiro's
 *  own exports so a number on screen can be eyeballed against the source CSV.
 *
 *  There are deliberately TWO families of functions here, and picking the wrong
 *  one is the bug this module exists to prevent:
 *
 *  - `num` / `eur` / `pct` / ... take a JS `number`. Use them for values this app
 *    *computed* -- portfolio series, returns, counterfactuals. Those are float
 *    maths already; formatting them as floats loses nothing that was not already
 *    lost.
 *
 *  - `decimal` takes the `string` the API sends. Ledger money crosses the wire as
 *    a string precisely so it never touches an IEEE double (see `api/types.ts`),
 *    and calling `num(Number(txn.net_base))` would throw that away at the last
 *    possible moment. `decimal` re-punctuates the digits and never parses them.
 */

import { c } from "./theme";

const NL = "nl-NL";

/* ── computed values (floats) ─────────────────────────────────────────────── */

export function num(v: number, digits = 2): string {
  if (!isFinite(v)) return "—";
  return new Intl.NumberFormat(NL, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(v);
}

export function eur(v: number, digits = 2): string {
  return `€ ${num(v, digits)}`;
}

/** Compact euro for KPI tiles: € 1,23M / € 45,6k / € 789.
 *  Uses U+2212 MINUS, not a hyphen -- it aligns with digits in IBM Plex Mono. */
export function eurCompact(v: number): string {
  const a = Math.abs(v);
  const sign = v < 0 ? "−" : "";
  if (a >= 1_000_000) return `${sign}€ ${num(a / 1_000_000, 2)}M`;
  if (a >= 1_000) return `${sign}€ ${num(a / 1_000, 1)}k`;
  return `${sign}€ ${num(a, 0)}`;
}

/** Always-signed euro. A P&L of exactly zero reads "+€ 0" rather than "€ 0",
 *  which keeps a column of numbers aligned on the sign character. */
export function eurSigned(v: number, digits = 0): string {
  return `${v < 0 ? "−€ " : "+€ "}${num(Math.abs(v), digits)}`;
}

/** Always-signed percentage. Takes a ratio (0.153), not a percentage (15.3). */
export function pct(v: number, digits = 1): string {
  if (!isFinite(v)) return "—";
  return `${v < 0 ? "−" : "+"}${num(Math.abs(v * 100), digits)}%`;
}

/** Unsigned percentage, for shares and weights that cannot be negative. */
export function pctPlain(v: number, digits = 1): string {
  if (!isFinite(v)) return "—";
  return `${num(v * 100, digits)}%`;
}

/* ── ledger values (decimal strings — never parsed) ───────────────────────── */

/** Add one to a string of decimal digits, propagating the carry leftward.
 *
 *  A carry that survives the leftmost digit (an all-nines string) grows the
 *  string by one character rather than overflowing -- "999" -> "1000" -- which
 *  is exactly the case that turns a fraction's carry into a new integer digit.
 */
function incrementDigits(digits: string): string {
  const result = digits.split("");
  for (let i = result.length - 1; i >= 0; i--) {
    if (result[i] !== "9") {
      result[i] = String(Number(result[i]) + 1);
      return result.join("");
    }
    result[i] = "0";
  }
  return `1${result.join("")}`;
}

/** Round a magnitude (no sign) to `maxDecimals` fraction digits, half away
 *  from zero, entirely through digit-string manipulation -- never `Number()`,
 *  which would reintroduce the float drift `decimal` exists to avoid.
 *
 *  Handles a carry that ripples through an all-nines fraction ("0.999" ->
 *  "1.00") and one that pushes into a new integer digit ("89.996" -> "90.00").
 *  Only called when `fracDigits` is longer than `maxDecimals`, so `keep`
 *  always has exactly `maxDecimals` digits and the rounding digit always exists.
 */
function roundFraction(
  intDigits: string,
  fracDigits: string,
  maxDecimals: number,
): { intDigits: string; fracDigits: string } {
  const keep = fracDigits.slice(0, maxDecimals);
  const roundUp = fracDigits.charAt(maxDecimals) >= "5";
  if (!roundUp) {
    return { intDigits, fracDigits: keep };
  }

  const combined = incrementDigits(intDigits + keep);
  if (keep.length === 0) {
    return { intDigits: combined, fracDigits: "" };
  }
  return {
    intDigits: combined.slice(0, combined.length - keep.length),
    fracDigits: combined.slice(combined.length - keep.length),
  };
}

/** Re-punctuate an exact decimal string for display: "1234.5" -> "1.234,50".
 *
 *  Never converts to a number, so the value shown is digit-for-digit what the
 *  ledger holds -- unless the caller opts into `maxDecimals`. Left unset (the
 *  default), there is no limit: every pre-existing caller keeps its current
 *  behaviour, because dropping a digit from a broker figure would make the
 *  screen disagree with the source for no reason the reader could see.
 *
 *  Set, `maxDecimals` ROUNDS (half away from zero, via `roundFraction` above)
 *  rather than truncating -- truncating "0.999" to "1.00" would read as if the
 *  stored value were smaller than it is. `minDecimals` still only pads.
 *
 *  Anything that is not a plain decimal is returned untouched rather than
 *  mangled -- if the backend ever sends something unexpected, showing it raw is
 *  far more debuggable than showing a confidently wrong number.
 */
export function decimal(
  value: string | null | undefined,
  minDecimals = 0,
  maxDecimals?: number,
): string {
  if (value == null) return "—";
  const raw = value.trim();
  if (raw === "") return "—";

  const m = /^([+-]?)(\d*)(?:\.(\d*))?$/.exec(raw);
  if (!m) return raw;

  const sign = m[1] ?? "";
  const intRaw = m[2] ?? "";
  const fracRaw = m[3] ?? "";

  const { intDigits, fracDigits } =
    maxDecimals !== undefined && fracRaw.length > maxDecimals
      ? roundFraction(intRaw || "0", fracRaw, maxDecimals)
      : { intDigits: intRaw, fracDigits: fracRaw };

  const int = intDigits.replace(/^0+(?=\d)/, "") || "0";
  const frac = fracDigits.padEnd(minDecimals, "0");

  const grouped = int.replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  const body = frac ? `${grouped},${frac}` : grouped;
  return sign === "-" ? `−${body}` : body;
}

/** Ledger money with the currency mark, rounded to 2 decimals by default --
 *  money is shown to the cent however much precision the stored Decimal
 *  actually carries. Pass `maxDecimals` to override (see `decimal`). */
export function decimalEur(
  value: string | null | undefined,
  minDecimals = 2,
  maxDecimals = 2,
): string {
  const body = decimal(value, minDecimals, maxDecimals);
  return body === "—" ? "—" : `€ ${body}`;
}

/** Move a decimal string's point `places` to the right, in digit space.
 *
 *  Returns `null` for anything that is not a decimal number, so the caller can
 *  decide what to show rather than being handed a mangled one.
 *
 *  Scientific notation is handled here rather than rejected, because the backend
 *  really does emit it: `str(Decimal)` switches to "1E-7" once the adjusted
 *  exponent drops below -6, which is reachable for a tiny `return_pct` (a cent of
 *  P&L against a large basis), and "1E+3" for a positive exponent. Sliding the dot
 *  two characters along such a string would produce nonsense. The exponent is
 *  folded into the shift instead, so both notations take one path.
 *
 *  The single `Number()` reads the EXPONENT -- a count of digit positions, capped
 *  by the pattern at four digits, never money. Integers that small are exact in a
 *  double, and no digit of the value itself is ever parsed.
 */
function shiftPointRight(value: string, places: number): string | null {
  const m = /^([+-]?)(\d*)(?:\.(\d*))?(?:[eE]([+-]?\d{1,4}))?$/.exec(value);
  if (!m) return null;

  const sign = m[1] === "-" ? "-" : "";
  const intRaw = m[2] ?? "";
  const fracRaw = m[3] ?? "";
  if (intRaw === "" && fracRaw === "") return null;

  const digits = intRaw + fracRaw;
  // Where the point lands within `digits` after the shift. Negative means the
  // value is smaller than one and needs leading zeros; past the end means it
  // needs trailing ones.
  const point = intRaw.length + places + (m[4] === undefined ? 0 : Number(m[4]));

  if (point <= 0) return `${sign}0.${"0".repeat(-point)}${digits}`;
  if (point >= digits.length) return `${sign}${digits}${"0".repeat(point - digits.length)}`;
  return `${sign}${digits.slice(0, point)}.${digits.slice(point)}`;
}

/** A ratio held as a decimal string, rendered as a percentage: "0.1234" -> "12,34%".
 *
 *  The point is moved two places in the STRING and the result handed to `decimal`,
 *  so a return lands on screen with the same grouping, comma separator, U+2212
 *  minus and half-away-from-zero rounding as every money cell beside it. Passing
 *  it through `Number(...).toFixed(2)` would be both a float parse of a ledger
 *  figure and an ASCII-hyphen "-12.34%" sitting next to "1.234,56".
 *
 *  `null` renders an em-dash, never "0%": a closure with a zero basis has no
 *  return, and a zero there would claim it broke even. Anything unrecognisable is
 *  returned untouched for the same reason `decimal` does it -- a raw string is far
 *  more debuggable than a confidently wrong number.
 */
export function decimalPercent(value: string | null | undefined): string {
  if (value == null) return "—";
  const raw = value.trim();
  if (raw === "") return "—";

  const shifted = shiftPointRight(raw, 2);
  if (shifted === null) return raw;
  return `${decimal(shifted, 2, 2)}%`;
}

/** The shortest holding an annualised return is reported for.
 *
 *  Below this the arithmetic stops meaning anything: a 3-day 2% gain annualises
 *  to roughly 1.000%, which is correct and useless. The backend computes the
 *  figure for every closure regardless -- design doc Sec 7.1 requires it to be
 *  carried -- so the decision about when it is worth *showing* is a presentation
 *  one, and it lives here rather than in the domain layer.
 *
 *  Exported because Sec 13 requires a methodology choice to be visible next to
 *  the number it governs: the Lots screen reads this constant into the note above
 *  the table, so the threshold on screen cannot drift from the one applied.
 */
export const ANNUALISED_MIN_HOLDING_DAYS = 90;

/** An annualised return, or an em dash when the holding was too short to annualise.
 *
 *  Two different reasons produce the dash and both are honest: the backend gives
 *  `null` where the figure is genuinely undefined (a same-day round trip has no
 *  period; a total loss has no real root), and this adds the case where a figure
 *  exists but would mislead. Neither renders "0%", which would claim the position
 *  broke even.
 */
export function annualisedPercent(
  value: string | null | undefined,
  holdingDays: number,
): string {
  if (holdingDays < ANNUALISED_MIN_HOLDING_DAYS) return "—";
  return decimalPercent(value);
}

/** Sign test on a decimal string, again without parsing. Used only to pick a
 *  colour, never to compute -- a wrong answer here miscolours a cell, it does
 *  not corrupt a figure. */
export function decimalIsNegative(value: string | null | undefined): boolean {
  return typeof value === "string" && value.trim().startsWith("-");
}

/** The colour for a ledger figure's sign: muted for `null`, red for a negative
 *  string, green otherwise. Decided on the string, never a parsed number --
 *  see `decimalIsNegative`. One definition for every screen that colours a
 *  return or a P&L cell. */
export function decimalSignColour(value: string | null | undefined): string {
  if (value == null) return c.textMuted;
  return decimalIsNegative(value) ? c.negative : c.positive;
}

/* ── semantics ────────────────────────────────────────────────────────────── */

/** Green up, red down. The ordinary direction, for P&L and returns. */
export function signColor(v: number): string {
  return v > 0 ? "#3FB950" : v < 0 ? "#E5534B" : "#8A9199";
}

/** INVERTED: red up, green down.
 *
 *  For the counterfactual delta only. There, a positive number means "holding
 *  would have been worth more than selling produced" -- i.e. the sale cost you
 *  money. Painting that green because it is positive would invert the meaning of
 *  the single most important number on the What-If screen. */
export function costColor(v: number): string {
  return v > 0 ? "#E5534B" : v < 0 ? "#3FB950" : "#8A9199";
}

/* ── dates ────────────────────────────────────────────────────────────────── */

/** ISO -> dd-mm-yy, the format DeGiro prints. */
export function shortDate(iso: string): string {
  const [y = "", m = "", d = ""] = iso.split("-");
  return `${d}-${m}-${y.slice(2)}`;
}

/** Whole days between two ISO dates. Both are parsed as UTC midnight, so a DST
 *  boundary between them cannot round the difference to 364 or 366. */
export function daysBetween(fromIso: string, toIso: string): number {
  const ms = Date.parse(`${toIso}T00:00:00Z`) - Date.parse(`${fromIso}T00:00:00Z`);
  return Math.round(ms / 86_400_000);
}
