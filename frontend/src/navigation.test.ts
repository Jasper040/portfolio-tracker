/** The navigation table's two invariants.
 *
 *  Both exist because of PT-44, where the MODELLED banner named three live
 *  screens while five read the ledger. The badge was right the whole time
 *  because it was derived from `LEDGER_BACKED`; the sentence beside it was
 *  hand-written and had been wrong since M3. What follows pins the derivation
 *  so re-hardcoding the list fails a test rather than shipping.
 */

import { describe, expect, it } from "vitest";

import { LEDGER_BACKED, TABS, liveTabs, tabDef } from "./navigation";

describe("liveTabs", () => {
  it("names every ledger-backed screen and nothing else", () => {
    expect(new Set(liveTabs().map((tab) => tab.id))).toEqual(LEDGER_BACKED);
  });

  it("returns them in sidebar order, so the banner reads in the order they are listed", () => {
    const order = TABS.map((tab) => tab.id);
    const positions = liveTabs().map((tab) => order.indexOf(tab.id));
    expect(positions).toEqual([...positions].sort((a, b) => a - b));
  });

  /** A guard on the set itself, not on the derivation: a typo'd id would make
   *  `liveTabs` quietly drop a screen and the banner would understate again,
   *  which is precisely the failure PT-44 describes. */
  it("contains only ids the tab table defines", () => {
    for (const id of LEDGER_BACKED) {
      expect(TABS.some((tab) => tab.id === id)).toBe(true);
    }
  });

  it("gives every live screen a label the banner can print", () => {
    for (const tab of liveTabs()) {
      expect(tabDef(tab.id).label).not.toBe("");
    }
  });
});
