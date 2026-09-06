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

/** Sign test on a decimal string, again without parsing. Used only to pick a
 *  colour, never to compute -- a wrong answer here miscolours a cell, it does
 *  not corrupt a figure. */
export function decimalIsNegative(value: string | null | undefined): boolean {
  return typeof value === "string" && value.trim().startsWith("-");
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
