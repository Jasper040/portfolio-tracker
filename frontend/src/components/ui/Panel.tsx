/** The card every section of the app sits in.
 *
 *  The design uses exactly one panel treatment -- 1px border, 6px radius, panel
 *  background -- and varies only the padding and what goes in the header. Keeping
 *  that in one component is what stops the twelfth screen from being 5px off.
 */

import type { CSSProperties, ReactNode } from "react";
import { c } from "../../lib/theme";

export interface PanelProps {
  title?: ReactNode;
  /** Muted line beside the title. Explains the panel; never repeats it. */
  subtitle?: ReactNode;
  /** Pushed to the right of the header row: toggles, ranges, legends. */
  actions?: ReactNode;
  /** Footnote below the content, above the panel edge, separated by a rule. */
  footer?: ReactNode;
  children?: ReactNode;
  /** Drops the horizontal padding, for panels that hold a full-bleed table. */
  flush?: boolean;
  style?: CSSProperties;
}

export function Panel({ title, subtitle, actions, footer, children, flush, style }: PanelProps) {
  const hasHeader = title != null || subtitle != null || actions != null;

  return (
    <div
      style={{
        border: `1px solid ${c.border}`,
        borderRadius: 6,
        background: c.panel,
        padding: flush ? 0 : "14px 16px",
        minWidth: 0,
        ...style,
      }}
    >
      {hasHeader && (
        <div
          style={{
            display: "flex",
            alignItems: "baseline",
            gap: 12,
            flexWrap: "wrap",
            marginBottom: 10,
            padding: flush ? "14px 16px 0" : undefined,
          }}
        >
          {title != null && <div style={{ fontSize: 12.5, fontWeight: 600 }}>{title}</div>}
          {subtitle != null && (
            <div style={{ fontSize: 11, color: c.textFaint }}>{subtitle}</div>
          )}
          {actions != null && (
            <div style={{ marginLeft: "auto", display: "flex", gap: 8, flexWrap: "wrap" }}>
              {actions}
            </div>
          )}
        </div>
      )}
      {children}
      {footer != null && (
        <div
          style={{
            fontSize: 10.5,
            color: c.textFaint,
            padding: "8px 0 6px",
            borderTop: `1px solid ${c.border}`,
            marginTop: 8,
            lineHeight: 1.6,
          }}
        >
          {footer}
        </div>
      )}
    </div>
  );
}
