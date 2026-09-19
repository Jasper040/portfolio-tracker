/** The twelve screens, in the order the sidebar lists them.
 *
 *  That order is an argument, not an alphabetisation: Dashboard answers "how am I
 *  doing", Positions, Transactions and Lots supply the evidence, and Import &
 *  Health sits second-to-last because it is what makes the other ten
 *  trustworthy. Lots sits right after Transactions and before Stock Detail
 *  because it is the same live ledger read at lot granularity -- the cost basis
 *  and closures behind the raw rows Transactions shows, one level up from the
 *  per-instrument view Stock Detail drills into. Instrument sits right after
 *  Stock Detail for the same reason Lots sits after Transactions: it is the
 *  live counterpart of the screen just before it, the per-instrument view read
 *  from the ledger instead of the modelled dataset (M3 Task 9). The numbering
 *  in the sidebar makes the sequence explicit. Performance sits right after
 *  Benchmarks & Industry for the same reason: it is the live counterpart of the
 *  modelled screen before it (M6a), whose industry sections stay modelled until
 *  M6b.
 *
 *  `subtitle` is the promise each screen makes, shown under its title in the
 *  header. Keeping title and subtitle together here stops the two from drifting
 *  apart across eleven separate components.
 */

export type TabId =
  | "dash"
  | "pos"
  | "tx"
  | "lots"
  | "detail"
  | "instr"
  | "whatif"
  | "div"
  | "bm"
  | "perf"
  | "imp"
  | "set";

export interface TabDef {
  id: TabId;
  label: string;
  title: string;
  subtitle: string;
}

export const TABS: readonly TabDef[] = [
  { id: "dash", label: "Dashboard", title: "Dashboard", subtitle: "The ten-second answer" },
  { id: "pos", label: "Positions", title: "Positions", subtitle: "What I hold right now" },
  { id: "tx", label: "Transactions", title: "Transactions", subtitle: "The raw ledger" },
  { id: "lots", label: "Lots", title: "Lots", subtitle: "Cost basis and realised P&L" },
  { id: "detail", label: "Stock Detail", title: "Stock Detail", subtitle: "Lot tracker" },
  {
    id: "instr",
    label: "Instrument",
    title: "Instrument",
    subtitle: "One priced line, against a benchmark",
  },
  { id: "whatif", label: "What-If", title: "What-If", subtitle: "Counterfactual, both directions" },
  { id: "div", label: "Dividends", title: "Dividends", subtitle: "Received and withheld" },
  {
    id: "bm",
    label: "Benchmarks",
    title: "Benchmarks & Industry",
    subtitle: "Where performance came from",
  },
  {
    id: "perf",
    label: "Performance",
    title: "Performance",
    subtitle: "Time-weighted return, against a benchmark",
  },
  {
    id: "imp",
    label: "Import & Health",
    title: "Import & Data Health",
    subtitle: "What makes the rest trustworthy",
  },
  { id: "set", label: "Settings", title: "Settings", subtitle: "Configuration" },
] as const;

export function tabDef(id: TabId): TabDef {
  // TABS is exhaustive over TabId, so the fallback is unreachable; it exists
  // because `noUncheckedIndexedAccess` cannot prove that from a `find`.
  return TABS.find((t) => t.id === id) ?? TABS[0]!;
}

/** The screens currently backed by a real endpoint. Everything else renders
 *  from `portfolio/provider.ts` and is badged MODELLED. */
export const LEDGER_BACKED: ReadonlySet<TabId> = new Set<TabId>(["pos", "tx", "lots", "instr", "perf"]);

/** Those same screens as definitions, in sidebar order.
 *
 *  Exists so the MODELLED banner can NAME them instead of carrying a
 *  hand-written list. It carried one until PT-44: the sentence said Positions,
 *  Transactions and Lots while `LEDGER_BACKED` had grown to include Instrument
 *  (M3) and Performance (M6a), so the banner understated what was live by two
 *  screens for two milestones. The badge never drifted, because the badge was
 *  already derived. This makes the sentence derived too, which is the only fix
 *  that cannot come undone the next time a screen goes live.
 */
export function liveTabs(): readonly TabDef[] {
  return TABS.filter((tab) => LEDGER_BACKED.has(tab.id));
}
