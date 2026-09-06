/** Interactive chrome: segmented controls and legend-toggle chips.
 *
 *  Every button here starts from `all: unset`, matching the design. That is
 *  deliberate: the design has no default button styling anywhere, and a stray
 *  user-agent border on one control is far more visible than a missing one.
 *  `all: unset` also strips the implicit `button` role reset, so `type="button"`
 *  is set explicitly on each -- without it a button inside a form submits it.
 */

import type { CSSProperties, ReactNode } from "react";
import { c, mono } from "../../lib/theme";

const RESET: CSSProperties = { all: "unset", cursor: "pointer", boxSizing: "border-box" };

/** A row of mutually exclusive options in a single bordered box. */
export interface SegmentedControlProps<T extends string> {
  options: readonly T[];
  value: T;
  onChange: (value: T) => void;
  /** Optional monospace label rendered to the left. */
  label?: string;
  size?: "sm" | "md";
}

export function SegmentedControl<T extends string>({
  options,
  value,
  onChange,
  label,
  size = "md",
}: SegmentedControlProps<T>) {
  const padding = size === "sm" ? "3px 10px" : "4px 11px";

  const group = (
    <div
      style={{
        display: "flex",
        border: `1px solid ${c.borderStrong}`,
        borderRadius: 4,
        overflow: "hidden",
      }}
      role="group"
      aria-label={label}
    >
      {options.map((option) => {
        const active = option === value;
        return (
          <button
            key={option}
            type="button"
            aria-pressed={active}
            onClick={() => onChange(option)}
            style={{
              ...RESET,
              padding,
              fontFamily: mono,
              fontSize: 10.5,
              letterSpacing: "0.03em",
              // Active reverses to a light ground: at these sizes a border or a
              // weight change is not legible, a full inversion always is.
              color: active ? c.bg : c.textMuted,
              background: active ? c.textSecondary : "transparent",
            }}
          >
            {option}
          </button>
        );
      })}
    </div>
  );

  if (!label) return group;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
      <span style={{ fontFamily: mono, fontSize: 10, color: c.textFaint }}>{label}</span>
      {group}
    </div>
  );
}

/** A legend entry that is also the on/off switch for its series.
 *
 *  Merging the two means the legend can never disagree with what is drawn, and
 *  the swatch doubles as the line-style key: solid, dashed or dotted, matching
 *  the stroke used in the chart.
 */
export interface SeriesChipProps {
  label: string;
  color: string;
  lineStyle: "solid" | "dashed" | "dotted";
  active: boolean;
  onToggle?: () => void;
}

export function SeriesChip({ label, color, lineStyle, active, onToggle }: SeriesChipProps) {
  const swatch = active ? color : "#3A4046";
  const body = (
    <>
      <span
        style={{
          width: 14,
          height: 0,
          borderTop: `2px ${lineStyle} ${swatch}`,
          display: "inline-block",
        }}
      />
      {label}
    </>
  );

  const style: CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 6,
    padding: "3px 9px",
    border: `1px solid ${active ? c.chipBorder : c.border}`,
    borderRadius: 3,
    fontSize: 10.5,
    color: active ? c.text : c.textFaint,
    background: active ? c.inset : "transparent",
  };

  // Without a handler this is a legend, not a control, and rendering it as a
  // button would promise interactivity that is not there.
  if (!onToggle) return <span style={style}>{body}</span>;

  return (
    <button type="button" aria-pressed={active} onClick={onToggle} style={{ ...RESET, ...style }}>
      {body}
    </button>
  );
}

/** A standalone pill button: the instrument picker, the aggregate toggle. */
export interface PillProps {
  active?: boolean;
  onClick?: () => void;
  monospace?: boolean;
  children: ReactNode;
}

export function Pill({ active = false, onClick, monospace, children }: PillProps) {
  const style: CSSProperties = {
    padding: "4px 10px",
    border: `1px solid ${active ? c.chipBorder : c.border}`,
    borderRadius: 4,
    fontFamily: monospace ? mono : undefined,
    fontSize: monospace ? 10.5 : 11,
    color: active ? c.text : c.textFaint,
    background: active ? c.inset : "transparent",
  };

  if (!onClick) return <span style={style}>{children}</span>;
  return (
    <button type="button" aria-pressed={active} onClick={onClick} style={{ ...RESET, ...style }}>
      {children}
    </button>
  );
}

/** A plain action button, for the Import screen's commands. */
export function ActionButton({
  onClick,
  tone = "neutral",
  children,
}: {
  onClick?: () => void;
  tone?: "neutral" | "positive";
  children: ReactNode;
}) {
  const positive = tone === "positive";
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        ...RESET,
        padding: "5px 12px",
        border: `1px solid ${positive ? "#1F3B26" : c.chipBorder}`,
        borderRadius: 4,
        fontSize: 11,
        color: positive ? c.positive : c.textSecondary,
        background: positive ? c.positiveBg : c.inset,
      }}
    >
      {children}
    </button>
  );
}
