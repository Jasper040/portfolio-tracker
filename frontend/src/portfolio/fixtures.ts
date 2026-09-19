/** ─────────────────────────────────────────────────────────────────────────────
 *  MODELLED DATA. NOT LEDGER TRUTH. NOT A PRICE SOURCE.
 *  ─────────────────────────────────────────────────────────────────────────────
 *
 *  Everything in this file is invented. It is the dataset from the Claude Design
 *  project `Portfolio Tracker.dc.html`, ported verbatim so the eight screens that
 *  need lots, prices, dividends and benchmarks have something to render before
 *  the backend can serve them (design doc section 10: those are M1 and later).
 *
 *  It is quarantined in one file, reachable only through `portfolio/provider.ts`, for
 *  one reason: when the real endpoints land, deleting this file and swapping the
 *  provider should be the whole change. Nothing in `screens/` or `components/`
 *  may import from here.
 *
 *  Prices are generated from anchor points by `lib/series.ts`, so they are
 *  plausible in shape, exact at the anchors, and meaningless everywhere else.
 *  Every screen fed from here is badged MODELLED in the UI.
 */

import type { Anchor } from "../lib/series";

export interface FixtureInstrument {
  isin: string;
  symbol: string;
  name: string;
  sector: string;
  industry: string;
  currency: string;
  /** Country of tax residence, which sets the dividend withholding rate. */
  country: string;
  /** DeGiro account type the position sits in. */
  account: string;
  /** Monthly volatility fed to the series generator. */
  volatility: number;
  /** Annual dividend yield. 0 means accumulating or non-paying. */
  dividendYield: number;
  /** Payments per year. 0 means none. */
  dividendFrequency: number;
  /** Dividend withholding rate. */
  withholding: number;
  anchors: readonly Anchor[];
  /** [date, side, quantity, price, fees] */
  transactions: readonly (readonly [string, "BUY" | "SELL", number, number, number])[];
}

export const INSTRUMENTS: readonly FixtureInstrument[] = [
  {
    isin: "NL0000000901", symbol: "DELTA OPTICS", name: "DELTA OPTICS Holding",
    sector: "Technology", industry: "Semiconductors", currency: "EUR",
    country: "NL", account: "Basic", volatility: 0.055,
    dividendYield: 0.011, dividendFrequency: 4, withholding: 0.15,
    anchors: [["2019-01-01", 145], ["2019-04-02", 178], ["2020-03-18", 215], ["2021-06-01", 640], ["2022-10-11", 410], ["2023-07-01", 640], ["2024-07-01", 1020], ["2025-04-01", 620], ["2026-09-04", 880]],
    transactions: [["2019-04-02", "BUY", 12, 178, 3.9], ["2020-03-18", "BUY", 8, 215, 3.9], ["2022-10-11", "BUY", 6, 410, 3.9]],
  },
  {
    isin: "NL0000000902", symbol: "LUMEN", name: "Lumen Semiconductors NV",
    sector: "Technology", industry: "Semiconductors", currency: "EUR",
    country: "NL", account: "Basic", volatility: 0.065,
    dividendYield: 0.022, dividendFrequency: 1, withholding: 0.15,
    anchors: [["2019-01-01", 22], ["2020-05-08", 33], ["2021-09-15", 88], ["2022-10-01", 52], ["2024-03-12", 140], ["2025-06-01", 102], ["2026-09-04", 158]],
    transactions: [["2020-05-08", "BUY", 60, 33.0, 2.6], ["2021-09-15", "SELL", 60, 88.0, 2.6], ["2024-03-12", "BUY", 25, 140.0, 2.6]],
  },
  {
    isin: "US0000000901", symbol: "ORN", name: "ORION Corp",
    sector: "Technology", industry: "Semiconductors", currency: "USD",
    country: "US", account: "Basic", volatility: 0.075,
    dividendYield: 0.002, dividendFrequency: 4, withholding: 0.15,
    anchors: [["2019-01-01", 4.0], ["2020-06-15", 8.9], ["2021-02-10", 12.4], ["2022-10-01", 10.5], ["2023-03-20", 22.1], ["2024-06-01", 115], ["2025-01-01", 130], ["2025-04-01", 88], ["2026-09-04", 165]],
    transactions: [["2020-06-15", "BUY", 40, 8.9, 1.1], ["2021-02-10", "BUY", 30, 12.4, 1.1], ["2023-03-20", "SELL", 50, 22.1, 1.3]],
  },
  {
    isin: "NL0000000903", symbol: "VEGA PAYMENTS", name: "Vega NV",
    sector: "Technology", industry: "Payments", currency: "EUR",
    country: "NL", account: "Basic", volatility: 0.065,
    dividendYield: 0, dividendFrequency: 0, withholding: 0.15,
    anchors: [["2019-01-01", 620], ["2021-09-01", 2900], ["2021-11-15", 2580], ["2022-06-20", 1690], ["2023-08-01", 700], ["2024-08-01", 1400], ["2026-09-04", 1420]],
    transactions: [["2021-11-15", "BUY", 2, 2580, 3.9], ["2022-06-20", "BUY", 2, 1690, 3.9]],
  },
  {
    isin: "GB0000000901", symbol: "CALDERA", name: "Caldera Energy plc",
    sector: "Energy", industry: "Oil & Gas", currency: "EUR",
    country: "NL", account: "Custody", volatility: 0.04,
    dividendYield: 0.038, dividendFrequency: 4, withholding: 0.15,
    anchors: [["2019-01-01", 26], ["2020-10-01", 10.8], ["2021-01-12", 14.2], ["2022-09-05", 23.8], ["2024-05-01", 32.5], ["2026-09-04", 34.1]],
    transactions: [["2021-01-12", "BUY", 200, 14.2, 2.6], ["2022-09-05", "BUY", 100, 23.8, 2.6]],
  },
  {
    isin: "GB0000000902", symbol: "HEARTH", name: "Hearth Goods plc",
    sector: "Consumer Staples", industry: "Household Goods", currency: "EUR",
    country: "GB", account: "Custody", volatility: 0.03,
    dividendYield: 0.035, dividendFrequency: 4, withholding: 0,
    anchors: [["2019-01-01", 48], ["2020-09-01", 48.6], ["2022-07-01", 40.2], ["2024-09-01", 55.4], ["2026-09-04", 56.2]],
    transactions: [["2020-09-01", "BUY", 90, 48.6, 2.6]],
  },
  {
    isin: "IE0000000901", symbol: "VWCE", name: "Northwind All-Country",
    sector: "Diversified", industry: "Global Equity", currency: "EUR",
    country: "IE", account: "Basic", volatility: 0.025,
    dividendYield: 0, dividendFrequency: 0, withholding: 0,
    anchors: [["2019-01-01", 68], ["2021-03-01", 95.4], ["2022-06-01", 92.1], ["2023-09-01", 104.6], ["2025-01-01", 131.2], ["2026-09-04", 158.4]],
    transactions: [["2021-03-01", "BUY", 30, 95.4, 2.0], ["2022-06-01", "BUY", 25, 92.1, 2.0], ["2023-09-01", "BUY", 20, 104.6, 2.0], ["2025-01-01", "BUY", 15, 131.2, 2.0]],
  },
  {
    isin: "NL0000000904", symbol: "HALCYON", name: "Halcyon Bank NV",
    sector: "Financials", industry: "Banks", currency: "EUR",
    country: "NL", account: "Basic", volatility: 0.045,
    dividendYield: 0.062, dividendFrequency: 2, withholding: 0.15,
    anchors: [["2019-01-01", 10.4], ["2020-10-30", 6.15], ["2022-03-01", 10.2], ["2024-05-14", 15.4], ["2025-09-01", 19.6], ["2026-09-04", 22.85]],
    transactions: [["2020-10-30", "BUY", 400, 6.15, 2.6], ["2024-05-14", "SELL", 150, 15.4, 2.6]],
  },
  {
    isin: "NL0000000905", symbol: "AEROSTRAT", name: "Aerostrat SE",
    sector: "Industrials", industry: "Aerospace", currency: "EUR",
    country: "FR", account: "Basic", volatility: 0.045,
    dividendYield: 0.018, dividendFrequency: 1, withholding: 0.128,
    anchors: [["2019-01-01", 98], ["2020-04-01", 55], ["2022-03-08", 98.4], ["2024-01-01", 145], ["2026-09-04", 182.6]],
    transactions: [["2022-03-08", "BUY", 25, 98.4, 2.6]],
  },
  {
    isin: "NL0000000906", symbol: "KESTREL", name: "Kestrel Holdings NV",
    sector: "Technology", industry: "Internet", currency: "EUR",
    country: "NL", account: "Basic", volatility: 0.06,
    dividendYield: 0.003, dividendFrequency: 1, withholding: 0.15,
    anchors: [["2019-01-01", 46], ["2021-08-19", 78.5], ["2022-10-01", 42], ["2024-06-01", 33], ["2026-09-04", 54.3]],
    transactions: [["2021-08-19", "BUY", 60, 78.5, 2.6]],
  },
  {
    isin: "NL0000000907", symbol: "SWIFTBITE", name: "Swiftbite NV",
    sector: "Consumer Discretionary", industry: "Internet Retail", currency: "EUR",
    country: "NL", account: "Basic", volatility: 0.075,
    dividendYield: 0, dividendFrequency: 0, withholding: 0.15,
    anchors: [["2019-01-01", 62], ["2021-04-22", 72.4], ["2022-11-30", 19.8], ["2024-02-01", 13.4], ["2026-09-04", 9.2]],
    transactions: [["2021-04-22", "BUY", 40, 72.4, 2.6], ["2022-11-30", "SELL", 40, 19.8, 2.6]],
  },
  {
    isin: "DK0000000901", symbol: "NORDPHARM", name: "Nordpharm A/S",
    sector: "Healthcare", industry: "Pharmaceuticals", currency: "EUR",
    country: "DK", account: "Basic", volatility: 0.05,
    dividendYield: 0.019, dividendFrequency: 1, withholding: 0.27,
    anchors: [["2019-01-01", 32], ["2023-01-16", 62.4], ["2024-08-01", 125.6], ["2025-06-01", 85], ["2026-09-04", 78.4]],
    transactions: [["2023-01-16", "BUY", 40, 62.4, 2.6], ["2024-08-01", "SELL", 15, 125.6, 2.6]],
  },
];

export interface FixtureBenchmark {
  key: string;
  name: string;
  ticker: string;
  /** Total expense ratio, pre-formatted in the Dutch convention. */
  ter: string;
  color: string;
  volatility: number;
  anchors: readonly Anchor[];
}

/** Investable ETF proxies, deliberately not indices: their TER is embedded in the
 *  return, so excess return is measured against something you could actually have
 *  bought (design doc section 8). */
export const BENCHMARKS: readonly FixtureBenchmark[] = [
  {
    key: "IWDA", name: "Corefund Global Equity", ticker: "IWDA.AS", ter: "0,20%",
    color: "#7D8590", volatility: 0.028,
    anchors: [["2019-01-01", 44.2], ["2020-03-20", 36.4], ["2021-12-01", 72.4], ["2022-10-01", 60.8], ["2024-06-01", 92.6], ["2026-09-04", 118.4]],
  },
  {
    key: "MEUD", name: "Broadstone Europe Large-Cap", ticker: "MEUD.PA", ter: "0,07%",
    color: "#6E7CE0", volatility: 0.026,
    anchors: [["2019-01-01", 148], ["2020-03-20", 108], ["2021-12-01", 196], ["2022-10-01", 168], ["2024-06-01", 234], ["2026-09-04", 262]],
  },
];

/** Industry to [proxy ticker, TER]. Hand-maintained, and expected to be wrong
 *  occasionally; the Settings screen says so rather than implying authority. */
export const INDUSTRY_PROXIES: Readonly<Record<string, readonly [string, string]>> = {
  Semiconductors: ["SMH", "0,35%"],
  Payments: ["XLF", "0,10%"],
  "Oil & Gas": ["XLE", "0,09%"],
  "Household Goods": ["XLP", "0,09%"],
  "Global Equity": ["VWCE", "0,22%"],
  Banks: ["EXV1", "0,46%"],
  Aerospace: ["EXV3", "0,46%"],
  Internet: ["XLC", "0,09%"],
  "Internet Retail": ["XLY", "0,09%"],
  Pharmaceuticals: ["XLV", "0,09%"],
};

/** The FX rate the fixture applies to non-EUR instruments. A single constant
 *  rather than a series: this is scaffolding for the NET BASE column, not a
 *  currency model. Real FX arrives per row on the ledger as `fx_rate`. */
export const FIXTURE_USD_EUR = 0.921;
