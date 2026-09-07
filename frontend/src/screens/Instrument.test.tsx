/**
 * @vitest-environment jsdom
 */

/** What the Instrument screen puts on screen, now that Task 9 wires it to the
 *  live chart endpoint.
 *
 *  The chart wrapper is mocked exactly as in `Positions.test.tsx`: jsdom has no
 *  canvas, and every drawing decision is already covered as a pure function in
 *  `lib/instrument.test.ts`. What is left here is what only a rendered screen
 *  can be wrong about: whether a `null` excess reads as a dash with its reason
 *  attached rather than a silent "0,00%", and whether the benchmark's own span
 *  coverage stays visibly separate from the instrument's own staleness
 *  coverage instead of reading as the same judgement twice.
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fetchBenchmarks, fetchInstrumentChart, fetchPositions } from "../api/client";
import type {
  Benchmark,
  Comparison,
  InstrumentChart,
  IntervalExcess,
  LivePosition,
  PositionsPage,
} from "../api/types";
import { Instrument } from "./Instrument";

vi.mock("../api/client", () => ({
  fetchPositions: vi.fn(),
  fetchBenchmarks: vi.fn(),
  fetchInstrumentChart: vi.fn(),
}));

vi.mock("../components/charts/EChart", () => ({
  EChart: ({ option }: { option: unknown }) => (
    <div data-testid="chart" data-series={JSON.stringify(option)} />
  ),
}));

const mockPositions = vi.mocked(fetchPositions);
const mockBenchmarks = vi.mocked(fetchBenchmarks);
const mockChart = vi.mocked(fetchInstrumentChart);

function position(overrides: Partial<LivePosition> = {}): LivePosition {
  return {
    isin: "XX0000000001",
    product_name: "Example Fund",
    currency: "EUR",
    quantity: "10",
    cost_basis: "100.00",
    charges_base: "1.00",
    price: "12.00",
    price_date: "2025-03-03",
    source: "yahoo",
    market_value_base: "120.00",
    gross_unrealised_base: "20.00",
    unrealised_base: "19.00",
    unrealised_pct: "0.19",
    coverage: "full",
    ...overrides,
  };
}

function positionsPage(items: LivePosition[]): PositionsPage {
  return {
    items,
    as_of: "2025-03-03",
    total_cost_basis: "100.00",
    total_market_value_base: "120.00",
    total_unrealised_base: "19.00",
    base_currency: "EUR",
    method: "FIFO",
    coverage: "full",
  };
}

function benchmark(overrides: Partial<Benchmark> = {}): Benchmark {
  return { key: "world", name: "World Equities", ter: "0.0020", ...overrides };
}

function intervalExcess(overrides: Partial<IntervalExcess> = {}): IntervalExcess {
  return {
    start: "2025-01-01",
    end: "2025-02-01",
    instrument_return: "0.05",
    benchmark_return: "0.03",
    excess: "0.02",
    reason: null,
    ...overrides,
  };
}

function comparison(overrides: Partial<Comparison> = {}): Comparison {
  return {
    basis: "total_return",
    benchmark_key: "world",
    instrument_index: [],
    benchmark_index: [],
    intervals: [intervalExcess()],
    linked_instrument_return: "0.05",
    linked_benchmark_return: "0.03",
    linked_excess: "0.02",
    coverage: "full",
    ...overrides,
  };
}

function chart(overrides: Partial<InstrumentChart> = {}): InstrumentChart {
  return {
    isin: "XX0000000001",
    method: null,
    coverage: "full",
    points: [
      { date: "2025-01-01", close_base: "10.00", coverage: "full", held: true },
      { date: "2025-02-01", close_base: "10.50", coverage: "full", held: true },
    ],
    intervals: [{ start: "2025-01-01", end: "2025-02-01", in_market: true, price_return: "0.05" }],
    markers: [],
    comparison: null,
    ...overrides,
  };
}

/** Wires all three live endpoints this screen reads. `byIsin` overrides the
 *  default chart fixture for a specific ISIN; anything not listed falls back
 *  to `chart({ isin })` so a test only has to describe what it cares about. */
function serve(
  positions: PositionsPage,
  benchmarks: Benchmark[],
  byIsin: Record<string, InstrumentChart> = {},
): void {
  mockPositions.mockResolvedValue(positions);
  mockBenchmarks.mockResolvedValue(benchmarks);
  mockChart.mockImplementation((isin) => Promise.resolve(byIsin[isin] ?? chart({ isin })));
}

beforeEach(() => {
  mockPositions.mockReset();
  mockBenchmarks.mockReset();
  mockChart.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("the instrument picker", () => {
  it("loads the default instrument's chart, then refetches when another is picked", async () => {
    serve(
      positionsPage([
        position({ isin: "XX0000000001", product_name: "Example Fund" }),
        position({ isin: "XX0000000002", product_name: "Other Fund" }),
      ]),
      [],
    );
    render(<Instrument method="FIFO" />);

    await waitFor(() =>
      expect(mockChart).toHaveBeenCalledWith("XX0000000001", { range: "1Y", benchmark: null }),
    );

    const { default: userEvent } = await import("@testing-library/user-event");
    await userEvent.click(await screen.findByRole("button", { name: "Other Fund" }));

    await waitFor(() =>
      expect(mockChart).toHaveBeenCalledWith("XX0000000002", { range: "1Y", benchmark: null }),
    );
  });
});

describe("the range control", () => {
  it("re-requests the chart with the newly selected range", async () => {
    serve(positionsPage([position()]), []);
    render(<Instrument method="FIFO" />);
    await waitFor(() =>
      expect(mockChart).toHaveBeenCalledWith("XX0000000001", { range: "1Y", benchmark: null }),
    );

    const { default: userEvent } = await import("@testing-library/user-event");
    await userEvent.click(await screen.findByRole("button", { name: "3Y" }));

    await waitFor(() =>
      expect(mockChart).toHaveBeenCalledWith("XX0000000001", { range: "3Y", benchmark: null }),
    );
  });
});

describe("the benchmark selector", () => {
  it("adds the overlay when a benchmark is picked and removes it when cleared", async () => {
    serve(positionsPage([position()]), [benchmark({ key: "world", name: "World Equities" })], {
      XX0000000001: chart({ comparison: comparison() }),
    });
    render(<Instrument method="FIFO" />);
    await waitFor(() =>
      expect(mockChart).toHaveBeenCalledWith("XX0000000001", { range: "1Y", benchmark: null }),
    );

    const { default: userEvent } = await import("@testing-library/user-event");
    await userEvent.click(await screen.findByRole("button", { name: "World Equities" }));

    await waitFor(() =>
      expect(mockChart).toHaveBeenCalledWith("XX0000000001", { range: "1Y", benchmark: "world" }),
    );

    await userEvent.click(screen.getByRole("button", { name: "None" }));

    await waitFor(() =>
      expect(mockChart).toHaveBeenLastCalledWith("XX0000000001", { range: "1Y", benchmark: null }),
    );
  });
});

describe("excess returns", () => {
  it("renders a dash and the reason, never 0,00%, when excess could not be computed", async () => {
    serve(positionsPage([position()]), [benchmark()], {
      XX0000000001: chart({
        comparison: comparison({
          intervals: [
            intervalExcess({
              excess: null,
              reason: "the instrument's own span does not cover this interval",
            }),
          ],
        }),
      }),
    });
    render(<Instrument method="FIFO" />);

    expect(await screen.findByText(/does not cover this interval/i)).toBeInTheDocument();
    const table = screen.getByRole("table");
    expect(within(table).getAllByText("—").length).toBeGreaterThan(0);
    expect(within(table).queryByText("0,00%")).not.toBeInTheDocument();
  });
});

describe("coverage", () => {
  it("shows the benchmark's own span coverage separately from the instrument's own coverage", async () => {
    serve(positionsPage([position()]), [benchmark()], {
      XX0000000001: chart({ coverage: "full", comparison: comparison({ coverage: "partial" }) }),
    });
    render(<Instrument method="FIFO" />);

    await screen.findByText("FULL");
    expect(screen.getByText(/BENCHMARK SPAN/i)).toBeInTheDocument();
    expect(screen.getByText("PARTIAL")).toBeInTheDocument();
  });
});

describe("basis", () => {
  it("labels the excess figure with the basis the API returned", async () => {
    serve(positionsPage([position()]), [benchmark()], {
      XX0000000001: chart({ comparison: comparison({ basis: "total_return" }) }),
    });
    render(<Instrument method="FIFO" />);

    // The basis label appears both in the table (next to each excess figure)
    // and in the panel's linked-total footnote -- both are the right place for
    // it, so this asserts presence rather than a single match.
    await waitFor(() => expect(screen.getAllByText(/total_return/i).length).toBeGreaterThan(0));
  });
});
