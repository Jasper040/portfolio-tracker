/** Sticky page header: what you are looking at, and the two controls that change
 *  what every number on the page means.
 *
 *  The lot-method switcher lives here rather than in Settings on purpose. Changing
 *  it recomputes every realised figure in the app, so it belongs where its effect
 *  is visible, next to the numbers it rewrites.
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
}

export function Header({ title, subtitle, method, onMethodChange, totalValue }: HeaderProps) {
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
