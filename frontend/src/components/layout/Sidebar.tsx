/** Fixed left navigation, plus the standing facts every screen is read against.
 *
 *  Base currency, as-of date and lot method live in the footer rather than only
 *  in the header, because they qualify every number on every screen. A realised
 *  P&L means nothing without knowing which matching method produced it, and the
 *  design puts that fact permanently in view rather than a settings page.
 */

import { c, mono } from "../../lib/theme";
import { TABS, type TabId } from "../../navigation";

export interface SidebarProps {
  active: TabId;
  onSelect: (id: TabId) => void;
  baseCurrency: string;
  asOf: string;
  method: string;
}

export function Sidebar({ active, onSelect, baseCurrency, asOf, method }: SidebarProps) {
  return (
    <aside
      style={{
        borderRight: `1px solid ${c.border}`,
        background: c.sunken,
        padding: "0 0 24px",
        display: "flex",
        flexDirection: "column",
        gap: 2,
        position: "sticky",
        top: 0,
        height: "100vh",
        overflowY: "auto",
      }}
    >
      <div
        style={{
          padding: "18px 16px 16px",
          borderBottom: `1px solid ${c.border}`,
          marginBottom: 8,
        }}
      >
        <div style={{ fontSize: 13, fontWeight: 600, letterSpacing: "-0.01em" }}>Ledger</div>
        <div
          style={{
            fontFamily: mono,
            fontSize: 10,
            color: c.textFaint,
            marginTop: 3,
            letterSpacing: "0.04em",
          }}
        >
          LOT-LEVEL PORTFOLIO TRACKER
        </div>
      </div>

      <nav style={{ display: "flex", flexDirection: "column", gap: 2 }}>
        {TABS.map((tab, i) => {
          const isActive = tab.id === active;
          return (
            <button
              key={tab.id}
              type="button"
              aria-current={isActive ? "page" : undefined}
              onClick={() => onSelect(tab.id)}
              style={{
                all: "unset",
                boxSizing: "border-box",
                cursor: "pointer",
                display: "flex",
                alignItems: "center",
                gap: 9,
                padding: "7px 16px",
                fontSize: 12.5,
                color: isActive ? c.text : c.textMuted,
                background: isActive ? c.inset : "transparent",
                // A 2px left rule rather than a full-width highlight: it marks the
                // active row without competing with the content area for weight.
                borderLeft: `2px solid ${isActive ? c.accent : "transparent"}`,
              }}
            >
              <span style={{ fontFamily: mono, fontSize: 10, color: c.textDim, width: 10 }}>
                {i + 1}
              </span>
              <span>{tab.label}</span>
            </button>
          );
        })}
      </nav>

      <div style={{ marginTop: "auto", padding: 16, borderTop: `1px solid ${c.border}` }}>
        <div style={{ fontFamily: mono, fontSize: 10, color: c.textFaint, lineHeight: 1.7 }}>
          <div>
            BASE&nbsp;&nbsp;<span style={{ color: c.textMuted }}>{baseCurrency}</span>
          </div>
          <div>
            AS OF&nbsp;&nbsp;<span style={{ color: c.textMuted }}>{asOf}</span>
          </div>
          <div>
            LOTS&nbsp;&nbsp;<span style={{ color: c.modelled }}>{method}</span>
          </div>
        </div>
      </div>
    </aside>
  );
}
