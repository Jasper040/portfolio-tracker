/** Sticky page header: what you are looking at, and the two controls that change
 *  what every number on the page means.
 *
 *  The lot-method switcher lives here rather than in Settings on purpose. Changing
 *  it recomputes every realised figure in the app, so it belongs where its effect
 *  is visible, next to the numbers it rewrites.
 *
 *  The refresh control is here for the same reason and answers a question the app
 *  cannot answer for itself (PT-46). Responses are cached for the life of the
 *  session, which is correct while the ledger is still -- and the ledger only
 *  moves when the operator runs `import`, `fetch-prices` or `rebuild` from a
 *  terminal, which the browser has no way to observe. Rather than guess with a
 *  timer, the app hands the operator the one fact they already have. It is not in
 *  Settings because a screen showing a stale figure is where you realise you want
 *  it.
 */

import { c, mono } from "../../lib/theme";
import { SegmentedControl } from "../ui/Controls";
import { LOT_METHODS, type LotMethod } from "../../lib/lots";

export interface HeaderProps {
  title: string;
  subtitle: string;
  method: LotMethod;
  onMethodChange: (m: LotMethod) => void;
  totalValue: string;
  /** Clears the response cache and makes every live screen refetch. */
  onRefresh: () => void;
}

export function Header({
  title,
  subtitle,
  method,
  onMethodChange,
  totalValue,
  onRefresh,
}: HeaderProps) {
  return (
    <header
      style={{
        display: "flex",
        alignItems: "center",
        gap: 16,
        flexWrap: "wrap",
        padding: "14px 24px",
        borderBottom: `1px solid ${c.border}`,
        background: c.sunken,
        position: "sticky",
        top: 0,
        zIndex: 20,
      }}
    >
      <div style={{ minWidth: 0 }}>
        <div style={{ fontSize: 15, fontWeight: 600, letterSpacing: "-0.01em" }}>{title}</div>
        <div style={{ fontSize: 11, color: c.textFaint, marginTop: 2 }}>{subtitle}</div>
      </div>

      <div
        style={{
          marginLeft: "auto",
          display: "flex",
          alignItems: "center",
          gap: 14,
          flexWrap: "wrap",
        }}
      >
        <SegmentedControl
          label="LOT METHOD"
          options={LOT_METHODS}
          value={method}
          onChange={onMethodChange}
        />
        {/* Titled rather than labelled, because the label is the whole
            explanation: a reader who does not know the app caches has no reason
            to guess that a refresh button would do anything at all. */}
        <button
          type="button"
          onClick={onRefresh}
          title="Refetch everything from the API. Use this after importing, fetching prices or rebuilding."
          aria-label="Refresh data from the API"
          style={{
            all: "unset",
            cursor: "pointer",
            fontFamily: mono,
            fontSize: 11,
            color: c.textMuted,
            border: `1px solid ${c.borderStrong}`,
            borderRadius: 4,
            padding: "4px 10px",
            lineHeight: 1.4,
          }}
        >
          ↻ REFRESH
        </button>
        <div
          style={{
            fontFamily: mono,
            fontSize: 11,
            color: c.textMuted,
            border: `1px solid ${c.borderStrong}`,
            borderRadius: 4,
            padding: "4px 10px",
          }}
        >
          {totalValue}
        </div>
      </div>
    </header>
  );
}
