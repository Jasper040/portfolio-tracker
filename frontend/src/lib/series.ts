/** The monthly time grid, and synthetic price-series generation.
 *
 *  The whole app is indexed by MONTH NUMBER, not by date: index 0 is 2019-01 and
 *  index 92 is 2026-09. Every price array, quantity array and chart x-coordinate
 *  shares that indexing, which is what lets a chart overlay a price line, a
 *  holding band and a transaction marker without any of them carrying dates.
 *
 *  Series generation is a fixture concern (see `portfolio/fixtures.ts`) and produces
 *  floats. It exists so the modelled screens have a plausible shape to draw; it
 *  is not, and must never become, a price source.
 */

/** Months in the grid: 2019-01 .. 2026-09 inclusive. */
export const MONTHS = 93;

/** The date the fixture data is "as of". */
export const TODAY = "2026-09-04";

/** First year on the grid, used to label chart axes. */
export const BASE_YEAR = 2019;

/** ISO date -> fractional month index. The fractional part places a mid-month
 *  transaction between two marks so its marker does not snap onto the wrong one. */
export function monthIndex(iso: string): number {
  const [y = "0", m = "1", d = "1"] = iso.split("-");
  return (Number(y) - BASE_YEAR) * 12 + (Number(m) - 1) + (Number(d) - 1) / 30;
}

/** Month index -> "YYYY-MM". */
export function monthLabel(i: number): string {
  const y = BASE_YEAR + Math.floor(i / 12);
  const m = (i % 12) + 1;
  return y + "-" + String(m).padStart(2, "0");
}

/** Deterministic PRNG seeded from a string (mulberry32 over a cheap string hash).
 *
 *  Deterministic on purpose: the fixture must render identically on every reload,
 *  or a chart would jitter between renders and every screenshot would differ.
 */
export function seededRandom(seed: string): () => number {
  let s = 0;
  for (let i = 0; i < seed.length; i++) s = (s * 31 + seed.charCodeAt(i)) >>> 0;
  return () => {
    s |= 0;
    s = (s + 0x6d2b79f5) | 0;
    let t = Math.imul(s ^ (s >>> 15), 1 | s);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** An anchor: a date at which the price is pinned to a known value. */
export type Anchor = readonly [iso: string, price: number];

/** Build a monthly price series that passes exactly through every anchor.
 *
 *  Between anchors it interpolates GEOMETRICALLY (in log space), because linear
 *  interpolation between 4.0 and 165.0 -- ORN's range here -- would draw a
 *  straight ramp that looks nothing like a price. A mean-reverting random walk is
 *  layered on for texture, then the anchors are re-stamped on top so the exact
 *  values survive the noise.
 */
export function buildSeries(
  anchors: readonly Anchor[],
  volatility: number,
  seed: string,
): number[] {
  const pts = anchors.map((a) => [monthIndex(a[0]), a[1]] as const);
  const first = pts[0];
  const last = pts[pts.length - 1];
  if (!first || !last) return new Array<number>(MONTHS).fill(0);

  const rand = seededRandom(seed);
  const out: number[] = [];
  let drift = 0;

  for (let i = 0; i < MONTHS; i++) {
    let p: number;
    if (i <= first[0]) {
      p = first[1];
    } else if (i >= last[0]) {
      p = last[1];
    } else {
      let k = 0;
      while ((pts[k + 1]?.[0] ?? Infinity) < i) k++;
      const a = pts[k];
      const b = pts[k + 1];
      if (!a || !b) {
        p = last[1];
      } else {
        const t = (i - a[0]) / (b[0] - a[0]);
        p = Math.exp(Math.log(a[1]) * (1 - t) + Math.log(b[1]) * t);
      }
    }
    drift = drift * 0.5 + (rand() * 2 - 1) * volatility;
    out.push(p * (1 + drift));
  }

  // Re-stamp the anchors: they are the facts, the noise is decoration.
  for (const [idx, value] of pts) {
    const i = Math.round(idx);
    if (i >= 0 && i < MONTHS) out[i] = value;
  }
  return out;
}
