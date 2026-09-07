/**
 * @vitest-environment jsdom
 */

/** What the Positions screen puts on screen, now that it reads the ledger.
 *
 *  The chart wrapper is mocked: jsdom implements no canvas, and every decision
 *  worth testing about the chart is already covered as a pure function in
 *  `lib/valuation.test.ts`. What is left here is the part only a rendered screen
 *  can be wrong about -- whether an unpriceable position says "no data" or
 *  quietly reads zero, and whether a total that cannot be computed is withheld.
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fetchPositions, fetchValuation } from "../api/client";
import type { LivePosition, PositionsPage, ValuationPoint, ValuationSeries } from "../api/types";
import { Positions } from "./Positions";

vi.mock("../api/client", () => ({
  fetchValuation: vi.fn(),
  fetchPositions: vi.fn(),
}));

vi.mock("../components/charts/EChart", () => ({
  EChart: ({ option }: { option: unknown }) => (
    <div data-testid="chart" data-series={JSON.stringify(option)} />
  ),
}));

const mockValuation = vi.mocked(fetchValuation);
const mockPositions = vi.mocked(fetchPositions);

function point(overrides: Partial<ValuationPoint> = {}): ValuationPoint {
  return {
    date: "2025-03-03",
    holdings_base: "300.00",
    cash_base: "-50.00",
    value_base: "250.00",
    coverage: "full",
    covered_pct: "1",
    ...overrides,
  };
}

function series(overrides: Partial<ValuationSeries> = {}): ValuationSeries {
  return {
    items: [point()],
    start: "2025-03-03",
    end: "2025-03-03",
    requested_from: null,
    clamped: false,
    base_currency: "EUR",
    method: null,
    coverage: "full",
    ...overrides,
  };
}

function position(overrides: Partial<LivePosition> = {}): LivePosition {
  return {
    isin: "NL0000000001",
    product_name: "Example Holdings",
    currency: "EUR",
    quantity: "10",
    cost_basis: "150.00",
    charges_base: "2.00",
    price: "20.00",
    price_date: "2025-03-03",
    source: "yahoo",
    market_value_base: "200.00",
    gross_unrealised_base: "50.00",
    unrealised_base: "48.00",
    unrealised_pct: "0.32",
    coverage: "full",
    ...overrides,
  };
}

function positionsPage(
  items: LivePosition[],
  overrides: Partial<PositionsPage> = {},
): PositionsPage {
  return {
    items,
    as_of: "2025-03-03",
    total_cost_basis: "150.00",
    total_market_value_base: "200.00",
    total_unrealised_base: "48.00",
    base_currency: "EUR",
    method: "FIFO",
    coverage: "full",
    ...overrides,
  };
}

function serve(valuation: ValuationSeries, positions: PositionsPage): void {
  mockValuation.mockResolvedValue(valuation);
  mockPositions.mockResolvedValue(positions);
}

beforeEach(() => {
  mockValuation.mockReset();
  mockPositions.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("the positions table", () => {
  it("shows market value and unrealised P&L", async () => {
    serve(series(), positionsPage([position()]));
    render(<Positions method="FIFO" onOpenInstrument={() => {}} />);

    const row = (await screen.findByText("Example Holdings")).closest("tr");
    const text = (row as HTMLElement).textContent ?? "";
    expect(text).toContain("€ 200,00");
    expect(text).toContain("€ 48,00");
  });

  it("keeps gross, charges and net as three separate figures", async () => {
    // Design doc 6.4. Rolling the commission into one number would hide what
    // the broker charged, which is one of the three answers a position owes.
    serve(series(), positionsPage([position()]));
    render(<Positions method="FIFO" onOpenInstrument={() => {}} />);

    const row = (await screen.findByText("Example Holdings")).closest("tr");
    const text = (row as HTMLElement).textContent ?? "";
    expect(text).toContain("€ 50,00");
    expect(text).toContain("€ 2,00");
  });

  it("says no data rather than € 0 for a position it cannot price", async () => {
    // Design doc 8.1. A zero market value is a claim, and here it is a false one.
    serve(
      series(),
      positionsPage(
        [
          position({
            isin: "US0000000404",
            product_name: "Other Holdings",
            price: null,
            price_date: null,
            source: null,
            market_value_base: null,
            gross_unrealised_base: null,
            unrealised_base: null,
            unrealised_pct: null,
            coverage: "missing",
          }),
        ],
        { total_market_value_base: null, total_unrealised_base: null, coverage: "missing" },
      ),
    );
    render(<Positions method="FIFO" onOpenInstrument={() => {}} />);

    const row = (await screen.findByText("Other Holdings")).closest("tr");
    const text = (row as HTMLElement).textContent ?? "";
    expect(text).not.toContain("€ 0,00");
    expect(text).toContain("—");
  });

  it("withholds the total when a position cannot be priced, and says why", async () => {
    serve(
      series(),
      positionsPage([position(), position({ isin: "US0000000404", coverage: "missing", market_value_base: null, unrealised_base: null })], {
        total_market_value_base: null,
        total_unrealised_base: null,
        coverage: "missing",
      }),
    );
    render(<Positions method="FIFO" onOpenInstrument={() => {}} />);

    expect(await screen.findByText(/cannot be priced/i)).toBeInTheDocument();
  });

  it("shows where a hand-supplied price came from", async () => {
    serve(series(), positionsPage([position({ source: "manual", coverage: "manual" })]));
    render(<Positions method="FIFO" onOpenInstrument={() => {}} />);

    const row = (await screen.findByText("Example Holdings")).closest("tr");
    expect(within(row as HTMLElement).getByText(/manual/i)).toBeInTheDocument();
  });
});

describe("the value chart and its coverage", () => {
  it("renders the chart from the API's own points", async () => {
    serve(series({ items: [point(), point({ date: "2025-03-04" })] }), positionsPage([position()]));
    render(<Positions method="FIFO" onOpenInstrument={() => {}} />);

    const chart = await screen.findByTestId("chart");
    expect(chart.getAttribute("data-series")).toContain("2025-03-04");
  });

  it("counts how many days were fully priced", async () => {
    serve(
      series({
        items: [point(), point({ date: "2025-03-04", coverage: "partial", covered_pct: "0.2" })],
        coverage: "partial",
      }),
      positionsPage([position()]),
    );
    render(<Positions method="FIFO" onOpenInstrument={() => {}} />);

    expect(await screen.findByText(/1 of 2 days fully priced/i)).toBeInTheDocument();
  });

  it("says when the requested window was longer than the ledger", async () => {
    // The five-year case on a younger account. Silently drawing a shorter chart
    // would leave the reader thinking five years is all there ever was.
    serve(
      series({ requested_from: "2021-03-03", clamped: true }),
      positionsPage([position()]),
    );
    render(<Positions method="FIFO" onOpenInstrument={() => {}} />);

    expect(await screen.findByText(/the ledger begins/i)).toBeInTheDocument();
  });
});

describe("the range control", () => {
  it("asks for five years back when the reader selects 5Y", async () => {
    const { default: userEvent } = await import("@testing-library/user-event");
    serve(series(), positionsPage([position()]));
    render(<Positions method="FIFO" onOpenInstrument={() => {}} />);
    await screen.findByText("Example Holdings");

    await userEvent.click(screen.getByRole("button", { name: "5Y" }));

    await waitFor(() =>
      expect(mockValuation).toHaveBeenCalledWith(
        expect.objectContaining({ from: expect.stringMatching(/^\d{4}-\d{2}-\d{2}$/) }),
      ),
    );
  });

  it("sends no start at all for MAX, so the server picks it", async () => {
    serve(series(), positionsPage([position()]));
    render(<Positions method="FIFO" onOpenInstrument={() => {}} />);

    await waitFor(() => expect(mockValuation).toHaveBeenCalledWith({ from: null }));
  });
});

describe("provenance", () => {
  it("reports the method the API said it used, not the one asked for", async () => {
    serve(series(), positionsPage([position()], { method: "HIFO" }));
    render(<Positions method="FIFO" onOpenInstrument={() => {}} />);

    expect(await screen.findByText("HIFO")).toBeInTheDocument();
  });

  it("asks the API for the method it was given", async () => {
    serve(series(), positionsPage([position()]));
    render(<Positions method="LIFO" onOpenInstrument={() => {}} />);

    await waitFor(() => expect(mockPositions).toHaveBeenCalledWith("LIFO"));
  });

  it("does not render a slow response under the method that replaced it", async () => {
    let release: (page: PositionsPage) => void = () => {};
    const slow = new Promise<PositionsPage>((resolve) => {
      release = resolve;
    });
    mockValuation.mockResolvedValue(series());
    mockPositions.mockImplementation((method) =>
      method === "FIFO"
        ? slow
        : Promise.resolve(positionsPage([position({ product_name: "Hifo Holdings" })], { method: "HIFO" })),
    );

    const { rerender } = render(<Positions method="FIFO" onOpenInstrument={() => {}} />);
    rerender(<Positions method="HIFO" onOpenInstrument={() => {}} />);
    expect(await screen.findByText("HIFO")).toBeInTheDocument();

    release(positionsPage([position({ product_name: "Fifo Holdings" })], { method: "FIFO" }));

    await waitFor(() => expect(screen.getByText("HIFO")).toBeInTheDocument());
    expect(screen.queryByText("Fifo Holdings")).not.toBeInTheDocument();
  });
});

describe("what the screen says when there is nothing to show", () => {
  it("distinguishes a dead API from an empty portfolio", async () => {
    mockValuation.mockRejectedValue(new Error("500 Server Error"));
    mockPositions.mockRejectedValue(new Error("500 Server Error"));
    render(<Positions method="FIFO" onOpenInstrument={() => {}} />);

    expect(await screen.findByText(/Could not reach the API/i)).toBeInTheDocument();
  });

  it("tells the reader to fetch prices when the cache is empty", async () => {
    // An empty chart and an unfetched cache look identical. One is a fact about
    // the portfolio and the other is a step nobody has run yet.
    serve(series({ items: [], start: null, end: null }), positionsPage([]));
    render(<Positions method="FIFO" onOpenInstrument={() => {}} />);

    expect(await screen.findByText(/fetch-prices/i)).toBeInTheDocument();
  });
});
