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
 *  attached rather than a silent "0,00%", whether the benchmark's own span
 *  coverage stays visibly separate from the instrument's own staleness
 *  coverage instead of reading as the same judgement twice, and -- since the
 *  review fix round -- whether an instrument with no open position is still
 *  reachable from the picker at all.
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fetchBenchmarks, fetchInstrumentChart, fetchInstruments } from "../api/client";
import type {
  Benchmark,
  Comparison,
  InstrumentChart,
  InstrumentSummary,
  IntervalExcess,
} from "../api/types";
import { Instrument } from "./Instrument";

vi.mock("../api/client", () => ({
  fetchInstruments: vi.fn(),
  fetchBenchmarks: vi.fn(),
  fetchInstrumentChart: vi.fn(),
}));

vi.mock("../components/charts/EChart", () => ({
  EChart: ({ option }: { option: unknown }) => (
    <div data-testid="chart" data-series={JSON.stringify(option)} />
  ),
}));

const mockInstruments = vi.mocked(fetchInstruments);
const mockBenchmarks = vi.mocked(fetchBenchmarks);
const mockChart = vi.mocked(fetchInstrumentChart);

function instrument(overrides: Partial<InstrumentSummary> = {}): InstrumentSummary {
  return { isin: "XX0000000001", product_name: "Example Fund", ...overrides };
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
    requested_from: "2025-01-01",
    clamped: false,
    ...overrides,
  };
}

/** Wires all three live endpoints this screen reads. `byIsin` overrides the
 *  default chart fixture for a specific ISIN; anything not listed falls back
 *  to `chart({ isin })` so a test only has to describe what it cares about. */
function serve(
  instruments: InstrumentSummary[],
  benchmarks: Benchmark[],
  byIsin: Record<string, InstrumentChart> = {},
): void {
  mockInstruments.mockResolvedValue(instruments);
  mockBenchmarks.mockResolvedValue(benchmarks);
  mockChart.mockImplementation((isin) => Promise.resolve(byIsin[isin] ?? chart({ isin })));
}

beforeEach(() => {
  mockInstruments.mockReset();
  mockBenchmarks.mockReset();
  mockChart.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("the instrument picker", () => {
  it("loads the default instrument's chart, then refetches when another is picked", async () => {
    serve(
      [
        instrument({ isin: "XX0000000001", product_name: "Example Fund" }),
        instrument({ isin: "XX0000000002", product_name: "Other Fund" }),
      ],
      [],
    );
    render(<Instrument />);

    await waitFor(() =>
      expect(mockChart).toHaveBeenCalledWith("XX0000000001", { range: "1Y", benchmark: null }),
    );

    const { default: userEvent } = await import("@testing-library/user-event");
    await userEvent.click(await screen.findByRole("button", { name: "Other Fund" }));

    await waitFor(() =>
      expect(mockChart).toHaveBeenCalledWith("XX0000000002", { range: "1Y", benchmark: null }),
    );
  });

  it("offers an instrument with no open position, and selecting it fetches its chart", async () => {
    // The review fix: the picker reads `fetchInstruments` (every ISIN ever
    // traded), not `fetchPositions` (only open ones). "Closed Fund" here has
    // no holding at all -- it is the fully-exited case `fetchPositions` could
    // never surface, and the whole reason this screen exists is to show
    // exactly that instrument's out-of-market intervals.
    serve(
      [
        instrument({ isin: "XX0000000001", product_name: "Example Fund" }),
        instrument({ isin: "XX0000000003", product_name: "Closed Fund" }),
      ],
      [],
    );
    render(<Instrument />);
    await waitFor(() =>
      expect(mockChart).toHaveBeenCalledWith("XX0000000001", { range: "1Y", benchmark: null }),
    );

    const { default: userEvent } = await import("@testing-library/user-event");
    await userEvent.click(await screen.findByRole("button", { name: "Closed Fund" }));

    await waitFor(() =>
      expect(mockChart).toHaveBeenCalledWith("XX0000000003", { range: "1Y", benchmark: null }),
    );
  });
});

describe("the range control", () => {
  it("re-requests the chart with the newly selected range", async () => {
    serve([instrument()], []);
    render(<Instrument />);
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
    serve([instrument()], [benchmark({ key: "world", name: "World Equities" })], {
      XX0000000001: chart({ comparison: comparison() }),
    });
    render(<Instrument />);
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
    serve([instrument()], [benchmark()], {
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
    render(<Instrument />);

    expect(await screen.findByText(/does not cover this interval/i)).toBeInTheDocument();
    const table = screen.getByRole("table");
    expect(within(table).getAllByText("—").length).toBeGreaterThan(0);
    expect(within(table).queryByText("0,00%")).not.toBeInTheDocument();
  });
});

describe("a window wider than the instrument's life", () => {
  it("says the window was clamped instead of just starting the chart late", async () => {
    serve([instrument()], [], {
      XX0000000001: chart({ requested_from: "2024-06-01", clamped: true }),
    });
    render(<Instrument />);

    expect(await screen.findByText(/Asked for/i)).toBeInTheDocument();
    expect(screen.getByText(/first traded/i)).toBeInTheDocument();
  });

  it("says nothing when the requested window fits inside the holding", async () => {
    serve([instrument()], [], {
      XX0000000001: chart({ requested_from: "2025-01-01", clamped: false }),
    });
    render(<Instrument />);

    await screen.findByRole("table");
    expect(screen.queryByText(/Asked for/i)).not.toBeInTheDocument();
  });
});

describe("coverage", () => {
  it("shows the benchmark's own span coverage separately from the instrument's own coverage", async () => {
    serve([instrument()], [benchmark()], {
      XX0000000001: chart({ coverage: "full", comparison: comparison({ coverage: "partial" }) }),
    });
    render(<Instrument />);

    await screen.findByText("FULL");
    expect(screen.getByText(/BENCHMARK SPAN/i)).toBeInTheDocument();
    expect(screen.getByText("PARTIAL")).toBeInTheDocument();
  });
});

describe("basis", () => {
  it("labels the excess figure with the basis the API returned", async () => {
    serve([instrument()], [benchmark()], {
      XX0000000001: chart({ comparison: comparison({ basis: "total_return" }) }),
    });
    render(<Instrument />);

    // The basis label appears both in the table (next to each excess figure)
    // and in the panel's linked-total footnote -- both are the right place for
    // it, so this asserts presence rather than a single match.
    await waitFor(() => expect(screen.getAllByText(/total_return/i).length).toBeGreaterThan(0));
  });
});
