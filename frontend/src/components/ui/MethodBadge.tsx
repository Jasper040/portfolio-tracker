/** Renders a response's provenance: which lot method produced its realised
 *  figures, and how much of the data it could account for.
 *
 *  Design doc Sec 9.2 makes `method` and `coverage` required on every response
 *  envelope so the API cannot ship a number without saying where it came from.
 *  This component is the other half of that: a required field nobody displays is
 *  only half a guarantee.
 *
 *  Two cases are deliberately not silent:
 *
 *  - `method: null` reads NOT LOT-MATCHED rather than being hidden. A blank space
 *    is indistinguishable from a screen that forgot to say, and the whole point
 *    is that a realised figure without a named method is unreadable.
 *  - `coverage` short of "full" is coloured and always shown. Sec 8.1 requires
 *    the UI to render "no data" rather than a zero, so a partial aggregate has to
 *    announce itself before anyone reads a total off it.
 */

import { c, mono } from "../../lib/theme";
import type { Coverage, LotMethodTag } from "../../api/types";

const COVERAGE_TONE: Record<Coverage, { color: string; label: string }> = {
  full: { color: c.textMuted, label: "FULL" },
  // Anything less than full means some figure on screen is incomplete. Amber is
  // the same mark the app uses everywhere else for "modelled, not measured".
  partial: { color: c.modelled, label: "PARTIAL" },
  manual: { color: c.modelled, label: "MANUAL" },
  missing: { color: c.negative, label: "MISSING" },
};

export interface MethodBadgeProps {
  method: LotMethodTag | null;
  coverage: Coverage;
}

function Chip({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <span
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
      <span style={{ fontFamily: mono, fontSize: 9.5, color: c.textFaint }}>{label}</span>
      <span style={{ fontFamily: mono, color }}>{value}</span>
    </span>
  );
}

export function MethodBadge({ method, coverage }: MethodBadgeProps) {
  const tone = COVERAGE_TONE[coverage];

  return (
    <>
      <Chip
        label="LOTS"
        value={method ?? "NOT LOT-MATCHED"}
        color={method ? c.modelled : c.textFaint}
      />
      <Chip label="COVERAGE" value={tone.label} color={tone.color} />
    </>
  );
}
