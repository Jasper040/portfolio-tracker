/** The small monospace status pill: BUY / SELL / OPEN / REALISED / DUPLICATE.
 *
 *  Colour is passed in rather than derived from the label, because the same word
 *  means different things in different tables -- "OPEN" is informational on a lot
 *  row and would be alarming on an import row -- and a lookup table keyed by
 *  string would quietly mis-colour any label it had not been taught.
 */

import type { ReactNode } from "react";
import { mono } from "../../lib/theme";

export interface BadgeProps {
  color: string;
  background: string;
  children: ReactNode;
}

export function Badge({ color, background, children }: BadgeProps) {
  return (
    <span
      style={{
        fontFamily: mono,
        fontSize: 9.5,
        padding: "2px 6px",
        borderRadius: 3,
        color,
        background,
        whiteSpace: "nowrap",
      }}
    >
      {children}
    </span>
  );
}
