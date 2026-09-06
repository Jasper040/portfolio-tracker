/** Application shell: navigation, the two pieces of state that cross screens, and
 *  the honesty banner.
 *
 *  Only three things live here. `tab` and `selectedIsin` are navigation. `method`
 *  is here because it is genuinely global -- it changes what every realised figure
 *  in the app means, so it cannot belong to any one screen. Everything else
 *  (periods, ranges, sorts, expanded rows, series toggles) is local to the screen
 *  that owns it, which is the main structural departure from the design's single
 *  state bag: that shape is what the dc-runtime template needed, not what React
 *  wants.
 */

import { useMemo, useState } from "react";
import { Header } from "./components/layout/Header";
import { Sidebar } from "./components/layout/Sidebar";
import { ModelledBadge } from "./components/ui/Notice";
import { Benchmarks } from "./screens/Benchmarks";
import { Dashboard } from "./screens/Dashboard";
import { Dividends } from "./screens/Dividends";
import { ImportHealth } from "./screens/ImportHealth";
import { Positions } from "./screens/Positions";
import { Settings } from "./screens/Settings";
import { StockDetail } from "./screens/StockDetail";
import { Transactions } from "./screens/Transactions";
import { WhatIf } from "./screens/WhatIf";
import { aggregate } from "./portfolio/aggregate";
import { loadPortfolio } from "./portfolio/provider";
import { eur } from "./lib/format";
import { shortDate } from "./lib/format";
import type { LotMethod } from "./lib/lots";
import { c } from "./lib/theme";
import { LEDGER_BACKED, tabDef, type TabId } from "./navigation";

export default function App() {
  const [tab, setTab] = useState<TabId>("dash");
  const [method, setMethod] = useState<LotMethod>("FIFO");
  const [selectedIsin, setSelectedIsin] = useState<string>("NL0000000902");

  const data = useMemo(() => loadPortfolio(), []);
  // Re-derived only when the method changes. The provider memoises the lot
  // matching itself, so switching back to a method already seen is free.
  const agg = useMemo(() => aggregate(data, method), [data, method]);

  // Stable palette index per instrument, so a holding keeps its colour across
  // every screen rather than being recoloured by whatever sort order it lands in.
  const instrumentIndex = useMemo(
    () => new Map(data.instruments.map((inst, i) => [inst.isin, i])),
    [data],
  );

  const def = tabDef(tab);
  const isLive = LEDGER_BACKED.has(tab);

  const openInstrument = (isin: string) => {
    setSelectedIsin(isin);
    setTab("detail");
  };

  return (
    <div
      style={{
        minHeight: "100vh",
        background: c.bg,
        display: "grid",
        gridTemplateColumns: "212px minmax(0, 1fr)",
      }}
    >
      <Sidebar
        active={tab}
        onSelect={setTab}
        baseCurrency={data.baseCurrency}
        asOf={shortDate(data.asOf)}
        method={method}
      />

      <main style={{ minWidth: 0 }}>
        <Header
          title={def.title}
          subtitle={def.subtitle}
          method={method}
          onMethodChange={setMethod}
          totalValue={eur(agg.totalValue, 0)}
        />

        <div style={{ padding: "20px 24px 64px", display: "flex", flexDirection: "column", gap: 16 }}>
          {/* Rendered from the data source, not hardcoded per screen: the day a
              screen starts reading the ledger, its badge disappears on its own. */}
          {!isLive && (
            <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
              <ModelledBadge />
              <span style={{ fontSize: 11, color: c.textFaint }}>
                Lots, prices, dividends and benchmarks are computed from a modelled dataset. Only{" "}
                <button
                  type="button"
                  onClick={() => setTab("tx")}
                  style={{ all: "unset", cursor: "pointer", color: c.accent }}
                >
                  Transactions
                </button>{" "}
                reads the live ledger.
              </span>
            </div>
          )}

          {tab === "dash" && (
            <Dashboard data={data} agg={agg} method={method} onOpenWhatIf={() => setTab("whatif")} />
          )}
          {tab === "pos" && (
            <Positions
              agg={agg}
              method={method}
              instrumentIndex={instrumentIndex}
              onOpenInstrument={openInstrument}
            />
          )}
          {tab === "tx" && <Transactions />}
          {tab === "detail" && (
            <StockDetail
              data={data}
              agg={agg}
              method={method}
              isin={selectedIsin}
              onSelect={setSelectedIsin}
            />
          )}
          {tab === "whatif" && <WhatIf data={data} agg={agg} method={method} />}
          {tab === "div" && <Dividends data={data} agg={agg} />}
          {tab === "bm" && <Benchmarks data={data} agg={agg} />}
          {tab === "imp" && <ImportHealth asOf={shortDate(data.asOf)} />}
          {tab === "set" && <Settings data={data} method={method} />}
        </div>
      </main>
    </div>
  );
}
