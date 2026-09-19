/** Application shell: navigation, the two pieces of state that cross screens, and
 *  the honesty banner.
 *
 *  Only four things live here. `tab` and `selectedIsin` are navigation. `method`
 *  is here because it is genuinely global -- it changes what every realised figure
 *  in the app means, so it cannot belong to any one screen. `refreshToken` is
 *  global for the same reason: clearing the response cache invalidates every live
 *  screen at once, not the one that happens to be open (PT-46). Everything else
 *  (periods, ranges, sorts, expanded rows, series toggles) is local to the screen
 *  that owns it, which is the main structural departure from the design's single
 *  state bag: that shape is what the dc-runtime template needed, not what React
 *  wants.
 */

import { Fragment, useMemo, useState } from "react";
import { clearCache } from "./api/cache";
import { Header } from "./components/layout/Header";
import { Sidebar } from "./components/layout/Sidebar";
import { ModelledBadge } from "./components/ui/Notice";
import { Benchmarks } from "./screens/Benchmarks";
import { Dashboard } from "./screens/Dashboard";
import { Dividends } from "./screens/Dividends";
import { ImportHealth } from "./screens/ImportHealth";
import { Instrument } from "./screens/Instrument";
import { Lots } from "./screens/Lots";
import { Performance } from "./screens/Performance";
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
import { LEDGER_BACKED, liveTabs, tabDef, type TabId } from "./navigation";

export default function App() {
  const [tab, setTab] = useState<TabId>("dash");
  const [method, setMethod] = useState<LotMethod>("FIFO");
  const [selectedIsin, setSelectedIsin] = useState<string>("NL0000000902");
  // Incremented, never toggled: every live screen lists it in its effect
  // dependencies, and a boolean would refetch only on alternate presses.
  const [refreshToken, setRefreshToken] = useState(0);

  const data = useMemo(() => loadPortfolio(), []);
  // Re-derived only when the method changes. The provider memoises the lot
  // matching itself, so switching back to a method already seen is free.
  const agg = useMemo(() => aggregate(data, method), [data, method]);

  const def = tabDef(tab);
  const isLive = LEDGER_BACKED.has(tab);

  // Order matters. Emptying the cache first means the refetch the token
  // triggers cannot be answered out of the cache it was meant to discard.
  const refresh = () => {
    clearCache();
    setRefreshToken((n) => n + 1);
  };

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
          onRefresh={refresh}
        />

        <div style={{ padding: "20px 24px 64px", display: "flex", flexDirection: "column", gap: 16 }}>
          {/* Rendered from the data source, not hardcoded per screen: the day a
              screen starts reading the ledger, its badge disappears on its own. */}
          {!isLive && (
            <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
              <ModelledBadge />
              <span style={{ fontSize: 11, color: c.textFaint }}>
                Prices, dividends and benchmarks are computed from a modelled dataset.{" "}
                {/* Built from LEDGER_BACKED, never written out. The hand-written
                    version named three screens while five were live, and stayed
                    wrong for two milestones (PT-44). */}
                {liveTabs().map((live, index, all) => (
                  <Fragment key={live.id}>
                    {index > 0 && (index === all.length - 1 ? " and " : ", ")}
                    <button
                      type="button"
                      onClick={() => setTab(live.id)}
                      style={{ all: "unset", cursor: "pointer", color: c.accent }}
                    >
                      {live.label}
                    </button>
                  </Fragment>
                ))}{" "}
                {liveTabs().length === 1 ? "reads" : "read"} the live ledger.
              </span>
            </div>
          )}

          {tab === "dash" && (
            <Dashboard data={data} agg={agg} method={method} onOpenWhatIf={() => setTab("whatif")} />
          )}
          {tab === "pos" && (
            <Positions
              method={method}
              onOpenInstrument={openInstrument}
              refreshToken={refreshToken}
            />
          )}
          {tab === "tx" && <Transactions refreshToken={refreshToken} />}
          {tab === "lots" && <Lots method={method} refreshToken={refreshToken} />}
          {tab === "detail" && (
            <StockDetail
              data={data}
              agg={agg}
              method={method}
              isin={selectedIsin}
              onSelect={setSelectedIsin}
            />
          )}
          {tab === "instr" && <Instrument refreshToken={refreshToken} />}
          {tab === "whatif" && <WhatIf data={data} agg={agg} method={method} />}
          {tab === "div" && <Dividends data={data} agg={agg} />}
          {tab === "bm" && <Benchmarks data={data} agg={agg} />}
          {tab === "perf" && <Performance refreshToken={refreshToken} />}
          {tab === "imp" && <ImportHealth asOf={shortDate(data.asOf)} />}
          {tab === "set" && <Settings data={data} method={method} />}
        </div>
      </main>
    </div>
  );
}
