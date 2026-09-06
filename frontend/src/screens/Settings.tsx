/** Configuration, and the consequences of changing it.
 *
 *  The lot-matching card carries a warning rather than just a control, because
 *  changing that setting silently rewrites every realised figure the app has ever
 *  shown -- including numbers already exported and quoted elsewhere. A settings
 *  screen that presents a destructive choice as an equal option to a cosmetic one
 *  is misleading by layout alone.
 */

import { Notice } from "../components/ui/Notice";
import { Panel } from "../components/ui/Panel";
import { c, mono } from "../lib/theme";
import type { LotMethod } from "../lib/lots";
import type { PortfolioData } from "../portfolio/provider";

interface SettingRow {
  key: string;
  value: string;
  color?: string;
}

interface SettingCard {
  title: string;
  note: string;
  rows: SettingRow[];
  warn?: string;
}

export interface SettingsProps {
  data: PortfolioData;
  method: LotMethod;
}

export function Settings({ data, method }: SettingsProps) {
  const cards: SettingCard[] = [
    {
      title: "Base currency",
      note: "All primary amounts are shown in this currency; local amounts appear on hover.",
      rows: [
        { key: "Base", value: data.baseCurrency, color: c.text },
        { key: "FX source", value: "ECB daily" },
        { key: "Fallback", value: "exchangerate.host" },
      ],
    },
    {
      title: "Lot matching",
      note: "Applies to every realised figure in the app.",
      rows: [
        { key: "Default method", value: method, color: c.modelled },
        { key: "Fee treatment", value: "added to cost basis" },
        { key: "Partial closures", value: "pro-rata fees" },
      ],
      warn:
        "Changing this changes every realised P&L, annualised return and scorecard figure shown anywhere in the app. Historical exports will not match.",
    },
    {
      title: "Benchmark proxies",
      note: "Investable ETFs, not indices. Their TER is embedded in the comparison.",
      rows: data.benchmarks.map((b) => ({ key: b.name, value: `${b.ticker} · ${b.ter}` })),
    },
    {
      title: "Industry proxies",
      note: "Mappings are hand-maintained. Expect to correct them.",
      rows: Object.entries(data.industryProxies)
        .slice(0, 6)
        .map(([key, [ticker, ter]]) => ({ key, value: `${ticker} · ${ter}` })),
    },
    {
      title: "Providers",
      note: "Keys are stored locally, never sent anywhere but the provider.",
      rows: [
        { key: "Prices", value: "EOD Historical" },
        { key: "News", value: "Marketaux · 68/100 today", color: c.modelled },
        { key: "Brokerage", value: "SnapTrade · 2 linked" },
      ],
    },
    {
      title: "Data export",
      note: "No lock-in. The full ledger leaves in the same shape it went in.",
      rows: [
        { key: "Ledger", value: "CSV · JSON", color: c.accent },
        { key: "Derived lots", value: "CSV", color: c.accent },
        { key: "Price cache", value: "JSON", color: c.accent },
      ],
    },
  ];

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(330px, 1fr))",
        gap: 12,
        alignItems: "start",
      }}
    >
      {cards.map((card) => (
        <Panel key={card.title} title={card.title} subtitle={card.note}>
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              gap: 1,
              background: c.border,
              border: `1px solid ${c.border}`,
              borderRadius: 4,
              overflow: "hidden",
            }}
          >
            {card.rows.map((row) => (
              <div
                key={row.key}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 12,
                  background: c.inset,
                  padding: "9px 11px",
                  fontSize: 11.5,
                }}
              >
                <span style={{ color: c.textMuted, minWidth: 0 }}>{row.key}</span>
                <span
                  style={{
                    marginLeft: "auto",
                    fontFamily: mono,
                    color: row.color ?? c.textMuted,
                    flex: "0 0 auto",
                  }}
                >
                  {row.value}
                </span>
              </div>
            ))}
          </div>
          {card.warn && (
            <div style={{ marginTop: 10 }}>
              <Notice>{card.warn}</Notice>
            </div>
          )}
        </Panel>
      ))}
    </div>
  );
}
