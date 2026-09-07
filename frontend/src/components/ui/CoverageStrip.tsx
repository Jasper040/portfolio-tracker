/** How much of the chart above is actually measured.
 *
 *  Design doc 8.1 makes coverage a first-class result, and a result nobody
 *  displays is only half a guarantee. The strip is one cell per day, coloured by
 *  that day's verdict, so a stale stretch is a visible band rather than a number
 *  in a corner -- and the sentence under it says what the colours mean without
 *  requiring a legend.
 *
 *  `clamped` is here rather than on the chart because it is a statement about
 *  the question, not the answer: the reader asked for a window longer than the
 *  ledger, and the honest reply is everything there is plus a note.
 */

import type { ValuationPoint } from "../../api/types";
import { tallyCoverage } from "../../lib/valuation";
import { c, mono } from "../../lib/theme";
import { shortDate } from "../../lib/format";

const TONE: Record<string, string> = {
  full: c.accent,
  partial: c.modelled,
  manual: c.modelled,
  missing: c.negative,
};

export interface CoverageStripProps {
  points: readonly ValuationPoint[];
  clamped: boolean;
  requestedFrom: string | null;
  start: string | null;
}

export function CoverageStrip({ points, clamped, requestedFrom, start }: CoverageStripProps) {
  const tally = tallyCoverage(points);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <div
        style={{ display: "flex", gap: 1, height: 8, width: "100%" }}
        role="img"
        aria-label={`Coverage across ${tally.total} days`}
      >
        {points.map((point) => (
          <span
            key={point.date}
            title={`${point.date}: ${point.coverage}`}
            style={{ flex: "1 1 0", background: TONE[point.coverage] ?? c.borderSoft }}
          />
        ))}
      </div>
      <div style={{ fontFamily: mono, fontSize: 10, color: c.textFaint }}>
        {tally.full} of {tally.total} days fully priced · {tally.partial} stale ·{" "}
        {tally.manual} hand-supplied · {tally.missing} unpriceable
      </div>
      {clamped && requestedFrom && start && (
        <div style={{ fontSize: 11, color: c.modelled }}>
          Asked for {shortDate(requestedFrom)}; the ledger begins {shortDate(start)}. Showing
          everything there is rather than padding the difference.
        </div>
      )}
    </div>
  );
}
