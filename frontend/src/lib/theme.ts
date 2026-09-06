/** Design tokens, lifted verbatim from `Portfolio Tracker.dc.html`.
 *
 *  The design is styled entirely with inline styles and literal hex values. Rather
 *  than scatter those literals across forty components -- where a palette change
 *  becomes a find-and-replace and a colour typo is invisible in review -- they are
 *  named once here. The names describe the *role*, not the colour, so a future
 *  light theme is a matter of changing this file rather than every call site.
 */

export const c = {
  /** Page ground. Everything else sits on this. */
  bg: "#0B0C0E",
  /** Standard panel/card surface. */
  panel: "#121417",
  /** Alternate row / table-header surface, one step lighter than `panel`. */
  panelAlt: "#14171B",
  /** Sidebar and expanded-row surface, one step darker than `panel`. */
  sunken: "#0E1013",
  /** Inset tile inside a panel (KPI cells, settings rows). */
  inset: "#171A1E",

  /** Default hairline between regions. */
  border: "#1D2126",
  /** Heavier border: control outlines, table header underline. */
  borderStrong: "#23272E",
  /** Barely-there border between table rows. */
  borderSoft: "#171A1E",
  /** Chart gridlines. Deliberately dimmer than `border` so data reads first. */
  grid: "#1B1F24",
  /** Interactive control border (buttons, chips) when raised. */
  chipBorder: "#2E333A",

  /** Primary reading text. */
  text: "#E8EAED",
  /** Secondary values -- numbers you read but do not act on. */
  textSecondary: "#B8BFC7",
  /** Supporting prose. */
  textMuted: "#8A9199",
  /** Labels, units, captions. */
  textFaint: "#5C646D",
  /** Ordinals and other near-invisible chrome. */
  textDim: "#4A5158",

  /** Selection / focus / links / the "actual portfolio" series. */
  accent: "#4C8DF6",
  accentHover: "#7FB0FF",
  /** Gains, and the "realised" lot state. */
  positive: "#3FB950",
  /** Losses, withholding tax, and hard data-health failures. */
  negative: "#E5534B",
  /** Modelled-not-actual. Every counterfactual figure in the app wears this. */
  modelled: "#D9A54A",
  /** Industry proxy series. */
  violet: "#B072D8",
  /** Benchmark proxy series. */
  neutral: "#7D8590",

  /** Tinted backgrounds for status pills. */
  positiveBg: "#0F1A12",
  negativeBg: "#1A100F",
  accentBg: "#0D1622",
  modelledBg: "#17140A",
  modelledBorder: "#26210F",
  neutralBg: "#191C21",
} as const;

/** Categorical series palette. Order is meaningful: it is assigned by descending
 *  weight, so the largest slice of any donut is always `accent`. */
export const PALETTE = [
  "#4C8DF6", "#3FB950", "#D9A54A", "#B072D8", "#2DB8A8", "#E5534B",
  "#E07A5F", "#6E7CE0", "#9BA84C", "#7D8590", "#C9553D",
] as const;

export const mono = "'IBM Plex Mono', ui-monospace, SFMono-Regular, monospace";
export const sans = "'IBM Plex Sans', system-ui, sans-serif";

/** Cycles the palette so an index beyond its length still gets a stable colour. */
export function paletteAt(i: number): string {
  const n = PALETTE.length;
  // `?? PALETTE[0]` is unreachable after the modulo, but `noUncheckedIndexedAccess`
  // cannot know that and the alternative is a cast that would also hide a real
  // out-of-range bug if this function were ever changed.
  return PALETTE[((i % n) + n) % n] ?? PALETTE[0];
}
