/** The benchmark controls two screens share: the selector, the span badge and
 *  the TER label.
 *
 *  Moved out of `screens/Instrument.tsx` in M6a, when Performance became the
 *  second screen that picks a benchmark and reports its span. Two copies of the
 *  selector would be free to disagree about the thing that matters most in it:
 *  a failed benchmark fetch and an empty configured set must never render the
 *  same.
 *
 *  `BenchmarkSpanBadge` stays deliberately separate from `MethodBadge`. Its value
 *  is a SPAN judgement -- how much of the compared stretch the benchmark series
 *  reaches across -- while `MethodBadge`'s COVERAGE is staleness everywhere in
 *  this app. Sharing a component would let an edit to one restyle the other into
 *  looking like the same judgement.
 */

import type { Benchmark, SpanCoverage } from "../../api/types";
import { decimal } from "../../lib/format";
import { c, mono } from "../../lib/theme";
import { Pill } from "./Controls";

const SPAN_TONE: Record<SpanCoverage, { color: string; label: string }> = {
  full: { color: c.textMuted, label: "FULL" },
  partial: { color: c.modelled, label: "PARTIAL" },
  missing: { color: c.negative, label: "MISSING" },
};

/** The benchmark's own span coverage. Deliberately not `MethodBadge` -- see
 *  this file's docstring. */
export function BenchmarkSpanBadge({ span }: { span: SpanCoverage }) {
  const tone = SPAN_TONE[span];
  return (
    <span
      title="How much of the compared stretch the benchmark series itself reaches across -- not how stale it is. A different question from the coverage badge."
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        border: `1px solid ${c.borderStrong}`,
        borderRadius: 4,
        padding: "4px 9px",
        fontSize: 11,
        whiteSpace: "nowrap",
      }}
    >
      <span style={{ fontFamily: mono, fontSize: 9.5, color: c.textFaint }}>BENCHMARK SPAN</span>
      <span style={{ fontFamily: mono, color: tone.color }}>{tone.label}</span>
    </span>
  );
}

/** The proxy's total expense ratio, in the unit the API states: a PERCENTAGE per
 *  year, so it is re-punctuated with `decimal` and never passed through
 *  `decimalPercent` (which shifts the point two places because it takes a
 *  ratio). "0.20" reads as 0,20%/yr. */
export function terLabel(ter: string): string {
  return `TER ${decimal(ter, 2, 2)}%/yr`;
}

export interface BenchmarkSelectorProps {
  benchmarks: readonly Benchmark[];
  /** Non-null when the LIST could not be fetched, which is a different fact
   *  from "none configured" and must not render the same. */
  error: string | null;
  selected: string | null;
  onSelect: (key: string | null) => void;
}

/** The benchmark control, and -- when there is no control to draw -- the reason
 *  why.
 *
 *  `config/benchmarks.yaml` ships with every entry commented out, so the
 *  ordinary first-run state is an empty list. Rendering nothing at all left a
 *  reader unable to tell the comparison feature existed, which is the whole
 *  point of M3 section 4.3. A fetch failure is surfaced separately: collapsing
 *  it into the empty case would make a broken API look like a configuration
 *  choice, and swallowing an error is the one thing this codebase does not do.
 *
 *  Each proxy's TER rides beside its name because it is the proxy's own drag,
 *  and section 8.3 turns that into a reading instruction: a holding that beats
 *  the proxy by less than the TER has not necessarily beaten the market. It sits
 *  OUTSIDE the pill so the button's accessible name stays the benchmark's name. */
export function BenchmarkSelector({ benchmarks, error, selected, onSelect }: BenchmarkSelectorProps) {
  if (error !== null) {
    return (
      <span style={{ fontSize: 11, color: c.negative }}>
        Could not load the benchmark list. {error}.
      </span>
    );
  }

  if (benchmarks.length === 0) {
    return (
      <span style={{ fontSize: 11, color: c.textFaint }}>
        No benchmark configured. Add one to{" "}
        <code style={{ fontFamily: mono }}>config/benchmarks.yaml</code> to overlay a proxy.
      </span>
    );
  }

  return (
    <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
      <span style={{ fontFamily: mono, fontSize: 10, color: c.textFaint }}>BENCHMARK</span>
      <Pill monospace active={selected === null} onClick={() => onSelect(null)}>
        None
      </Pill>
      {benchmarks.map((b) => (
        <span key={b.key} style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
          <Pill monospace active={selected === b.key} onClick={() => onSelect(b.key)}>
            {b.name}
          </Pill>
          <span
            title="The proxy's own annual cost, reported and never subtracted -- adjusting for it would invent a series nobody published."
            style={{ fontFamily: mono, fontSize: 9.5, color: c.textFaint }}
          >
            {terLabel(b.ter)}
          </span>
        </span>
      ))}
    </div>
  );
}
