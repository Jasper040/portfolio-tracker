/**
 * @vitest-environment jsdom
 */

/** What the Performance screen puts on screen (M6a).
 *
 *  The chart wrapper is mocked as on Positions and Instrument; every drawing
 *  decision is a pure function in `lib/performance.test.ts`. What is left is
 *  what only a rendered screen can get wrong: a window figure across a gap
 *  reading as "0,00%", a comparison shown without saying how each side treats
 *  dividends, and a span badge that reads as the coverage badge.
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fetchBenchmarks, fetchPerformance } from "../api/client";
import type { Benchmark, PerformanceReport, PortfolioComparison } from "../api/types";
import { Performance } from "./Performance";

vi.mock("../api/client", () => ({
  fetchBenchmarks: vi.fn(),
  fetchPerformance: vi.fn(),
}));

vi.mock("../components/charts/EChart", () => ({
  EChart: ({ option }: { option: unknown }) => (
    <div data-testid="chart" data-series={JSON.stringify(option)} />
  ),
}));

const mockBenchmarks = vi.mocked(fetchBenchmarks);
const mockPerformance = vi.mocked(fetchPerformance);

function benchmark(overrides: Partial<Benchmark> = {}): Benchmark {
  return { key: "world", name: "World Equities", ter: "0.20", ...overrides };
}

function comparison(overrides: Partial<PortfolioComparison> = {}): PortfolioComparison {
  return {
    basis: "total_return",
    dividends: "reinvested_gross",
    benchmark_key: "world",
    benchmark_index: [],
    runs: [
      {
        start: "2025-03-03",
        end: "2025-03-04",
        portfolio_return: "0.02",
        benchmark_return: "0.01",
        excess: "0.01",
        span: "full",
        reason: null,
      },
    ],
    benchmark_return: "0.01",
    excess: "0.01",
    span: "full",
    ...overrides,
  };
}

function report(overrides: Partial<PerformanceReport> = {}): PerformanceReport {
  return {
    basis: "time_weighted",
    lane: "unadjusted_close_plus_cash",
    dividends: "held_as_cash_net_of_withholding",
    base_currency: "EUR",
    start: "2025-03-03",
    end: "2025-03-04",
    requested_from: null,
    clamped: false,
    links: [
      {
        date: "2025-03-04",
        since: "2025-03-03",
        flow_base: "0.00",
        daily_return: "0.02",
        coverage: "full",
        reason: null,
      },
    ],
    runs: [{ start: "2025-03-03", end: "2025-03-04", days: 1, linked_return: "0.02", coverage: "full" }],
    portfolio_index: [],
    linked_return: "0.02",
    gaps: 0,
    reason: null,
    comparison: null,
    method: null,
    coverage: "full",
    ...overrides,
  };
}

function gapped(): PerformanceReport {
  return report({
    linked_return: null,
    gaps: 1,
    reason: "1 unmeasurable stretch split this window into 2 runs; no single figure spans a gap",
    runs: [
      { start: "2025-03-03", end: "2025-03-04", days: 1, linked_return: "0.1", coverage: "full" },
      { start: "2025-03-06", end: "2025-03-07", days: 1, linked_return: "0.05", coverage: "full" },
    ],
    coverage: "missing",
  });
}

beforeEach(() => {
  mockBenchmarks.mockReset();
  mockPerformance.mockReset();
  mockBenchmarks.mockResolvedValue([benchmark()]);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("the window figure", () => {
  it("shows the time-weighted return with its basis", async () => {
    mockPerformance.mockResolvedValue(report());
    render(<Performance />);
    expect((await screen.findAllByText("2,00%")).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/time_weighted/).length).toBeGreaterThan(0);
  });

  it("reads a dash and the reason across a gap, never 0,00%", async () => {
    mockPerformance.mockResolvedValue(gapped());
    render(<Performance />);
    expect(await screen.findByText(/no single figure spans a gap/)).toBeInTheDocument();
    expect(screen.queryByText("0,00%")).not.toBeInTheDocument();
  });

  it("still gives every run its own figure", async () => {
    mockPerformance.mockResolvedValue(gapped());
    render(<Performance />);
    const table = await screen.findByRole("table");
    expect(within(table).getByText("10,00%")).toBeInTheDocument();
    expect(within(table).getByText("5,00%")).toBeInTheDocument();
  });
});

describe("the controls", () => {
  it("asks for the whole ledger with no benchmark by default", async () => {
    mockPerformance.mockResolvedValue(report());
    render(<Performance />);
    await waitFor(() =>
      expect(mockPerformance).toHaveBeenCalledWith({ from: null, benchmark: null }),
    );
  });

  it("asks for a start date when a range is picked", async () => {
    mockPerformance.mockResolvedValue(report());
    render(<Performance />);
    const { default: userEvent } = await import("@testing-library/user-event");
    await userEvent.click(await screen.findByRole("button", { name: "1Y" }));
    await waitFor(() =>
      expect(mockPerformance).toHaveBeenCalledWith({
        from: expect.stringMatching(/^\d{4}-\d{2}-\d{2}$/),
        benchmark: null,
      }),
    );
  });

  it("asks for the comparison when a benchmark is picked", async () => {
    mockPerformance.mockResolvedValue(report());
    render(<Performance />);
    const { default: userEvent } = await import("@testing-library/user-event");
    await userEvent.click(await screen.findByRole("button", { name: "World Equities" }));
    await waitFor(() =>
      expect(mockPerformance).toHaveBeenCalledWith({ from: null, benchmark: "world" }),
    );
  });
});

describe("the comparison", () => {
  it("says how each side treats dividends", async () => {
    // M6a-10. Both differences favour the benchmark, and a reader shown only
    // the excess cannot tell how much of it is withholding and reinvestment.
    mockPerformance.mockResolvedValue(report({ comparison: comparison() }));
    render(<Performance />);
    expect(await screen.findByText(/after withholding and stay there/)).toBeInTheDocument();
    expect(screen.getByText(/reinvested, before withholding/)).toBeInTheDocument();
  });

  it("shows the benchmark span as its own badge beside coverage", async () => {
    mockPerformance.mockResolvedValue(
      report({ coverage: "full", comparison: comparison({ span: "partial" }) }),
    );
    render(<Performance />);
    await screen.findByText("FULL");
    expect(screen.getByText(/BENCHMARK SPAN/)).toBeInTheDocument();
    expect(screen.getByText("PARTIAL")).toBeInTheDocument();
  });

  it("renders a dash and the reason when a run's excess could not be computed", async () => {
    mockPerformance.mockResolvedValue(
      report({
        comparison: comparison({
          benchmark_return: null,
          excess: null,
          span: "missing",
          runs: [
            {
              start: "2025-03-03",
              end: "2025-03-04",
              portfolio_return: "0.02",
              benchmark_return: null,
              excess: null,
              span: "missing",
              reason: "no benchmark 'world' data from 2025-03-03 to 2025-03-04",
            },
          ],
        }),
      }),
    );
    render(<Performance />);
    const table = await screen.findByRole("table");
    expect(within(table).getByText(/no benchmark 'world' data/)).toBeInTheDocument();
    expect(within(table).queryByText("0,00%")).not.toBeInTheDocument();
  });
});

describe("what the screen says when there is nothing to show", () => {
  it("distinguishes a dead API from an empty ledger", async () => {
    mockPerformance.mockRejectedValue(new Error("500 Server Error"));
    render(<Performance />);
    expect(await screen.findByText(/Could not reach the API/i)).toBeInTheDocument();
  });

  it("tells the reader what to run when there is nothing to measure", async () => {
    mockPerformance.mockResolvedValue(
      report({ links: [], runs: [], linked_return: null, start: null, end: null, coverage: "missing" }),
    );
    render(<Performance />);
    expect(await screen.findByText(/fetch-prices/)).toBeInTheDocument();
  });

  it("says when the requested window was longer than the ledger", async () => {
    mockPerformance.mockResolvedValue(report({ requested_from: "2020-03-03", clamped: true }));
    render(<Performance />);
    expect(await screen.findByText(/Asked for/)).toBeInTheDocument();
  });
});
