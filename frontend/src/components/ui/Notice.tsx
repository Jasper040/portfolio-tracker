/** Callouts. Three tones, each with a job.
 *
 *  `modelled` (amber) is the most important one in this app and appears wherever
 *  a number is computed under an assumption rather than read from the ledger --
 *  counterfactuals, forward dividend estimates, benchmark replays. The design
 *  doc's whole position is that a modelled figure shown without that mark is a
 *  lie of omission, so this component exists to make marking one cheap.
 */

import type { ReactNode } from "react";
import { c } from "../../lib/theme";

export type NoticeTone = "modelled" | "danger" | "neutral";

const TONES: Record<NoticeTone, { border: string; background: string; color: string }> = {
  modelled: { border: c.modelledBorder, background: c.modelledBg, color: c.modelled },
  danger: { border: "#2A1A18", background: "#170F0E", color: c.textSecondary },
  neutral: { border: c.border, background: c.sunken, color: c.textMuted },
};

export interface NoticeProps {
  tone?: NoticeTone;
  children: ReactNode;
}

export function Notice({ tone = "modelled", children }: NoticeProps) {
  const t = TONES[tone];
  return (
    <div
      style={{
        border: `1px solid ${t.border}`,
        background: t.background,
        borderRadius: 6,
        padding: "10px 13px",
        fontSize: 11,
        color: t.color,
        lineHeight: 1.6,
      }}
    >
      {children}
    </div>
  );
}

/** The inline "this screen is not ledger truth" marker.
 *
 *  Rendered from `PortfolioData.source` rather than hardcoded per screen, so it
 *  disappears by itself the day a screen starts reading real data. */
export function ModelledBadge({ note }: { note?: string }) {
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        fontFamily: "'IBM Plex Mono', monospace",
        fontSize: 9.5,
        letterSpacing: "0.05em",
        color: c.modelled,
        border: `1px solid ${c.modelledBorder}`,
        background: c.modelledBg,
        borderRadius: 3,
        padding: "2px 7px",
        whiteSpace: "nowrap",
      }}
      title={note ?? "Computed from modelled data, not the transaction ledger."}
    >
      MODELLED
    </span>
  );
}
