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

import { act, render, screen, waitFor, within } from "@testing-library/react";
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
  /** The operator's complaint and the fix for it. Narrowing happens in the
   *  browser, so picking a preset is a `filter` over data already here rather
   *  than a round trip -- as instant as dragging the chart's own zoom slider,
   *  which is what made the difference obvious in the first place. */
  it("does not touch the API for a window inside the series it holds", async () => {
    const { default: userEvent } = await import("@testing-library/user-event");
    serve(
      series({ items: [point({ date: "2020-01-02" }), point({ date: "2026-09-01" })], start: "2020-01-02", end: "2026-09-01" }),
      positionsPage([position()]),
    );
    render(<Positions method="FIFO" onOpenInstrument={() => {}} />);
    await screen.findByText("Example Holdings");
    const before = mockValuation.mock.calls.length;

    await userEvent.click(screen.getByRole("button", { name: "1M" }));
    await userEvent.click(screen.getByRole("button", { name: "1Y" }));
    await userEvent.click(screen.getByRole("button", { name: "3M" }));

    expect(mockValuation.mock.calls.length).toBe(before);
  });

  /** The case narrowing must refuse. `value_series` starts a whole-ledger
   *  request at the first day a POSITION existed but floors a windowed one at
   *  the first day a CASH ROW existed, and the response never reveals the
   *  second -- so a window reaching back further may contain days we were never
   *  sent. Asking is the only correct answer. */
  it("asks the API for a window starting before the series it holds", async () => {
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

  it("fetches the whole ledger with no start, so the server picks it", async () => {
    serve(series(), positionsPage([position()]));
    render(<Positions method="FIFO" onOpenInstrument={() => {}} />);

    await waitFor(() => expect(mockValuation).toHaveBeenCalledWith({}));
  });

  /** The narrowed envelope has to say what the API would have said, not what
   *  the fetched one said. A window of fully-priced days inheriting a
   *  `missing` from a day outside it is PT-14's mistake in the other
   *  direction. */
  it("recomputes the coverage strip over the narrowed window", async () => {
    const { default: userEvent } = await import("@testing-library/user-event");
    serve(
      series({
        items: [
          point({ date: "2020-01-02", coverage: "missing", value_base: null, holdings_base: null, covered_pct: null }),
          point({ date: "2026-09-01" }),
        ],
        start: "2020-01-02",
        end: "2026-09-01",
        coverage: "missing",
      }),
      positionsPage([position()]),
    );
    render(<Positions method="FIFO" onOpenInstrument={() => {}} />);
    await screen.findByText(/1 unpriceable/);

    await userEvent.click(screen.getByRole("button", { name: "1M" }));

    // The unpriceable day is outside the month; the strip must stop counting it.
    await waitFor(() => expect(screen.getByText(/0 unpriceable/)).toBeInTheDocument());
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

describe("when one of the two fetches fails", () => {
  /** Regression, found in review before merge. Splitting one effect into three
   *  (PT-46) left them sharing a single `error` slot, and each cleared it on
   *  its own success -- so whichever settled LAST won. A positions success
   *  arriving after a valuation failure wiped the failure, and the screen sat
   *  on "Loading…" for ever with nothing saying anything had gone wrong.
   *
   *  The ORDER is the whole test. Both mocks settling in one tick does not
   *  reproduce it -- the first version of these tests passed against the bug.
   *  The success has to land after the failure is already on screen, so the
   *  slow half is held open deliberately and released once the notice is up.
   */
  it("still reports a valuation failure when the positions call then succeeds", async () => {
    let release: (page: PositionsPage) => void = () => {};
    mockValuation.mockRejectedValue(new Error("500 Server Error"));
    mockPositions.mockImplementation(
      () => new Promise<PositionsPage>((resolve) => { release = resolve; }),
    );
    render(<Positions method="FIFO" onOpenInstrument={() => {}} />);

    expect(await screen.findByText(/Could not reach the API/i)).toBeInTheDocument();

    await act(async () => {
      release(positionsPage([position()]));
      await Promise.resolve();
    });

    expect(screen.getByText(/Could not reach the API/i)).toBeInTheDocument();
  });

  it("still reports a positions failure when the valuation call then succeeds", async () => {
    let release: (s: ValuationSeries) => void = () => {};
    mockPositions.mockRejectedValue(new Error("500 Server Error"));
    mockValuation.mockImplementation(
      () => new Promise<ValuationSeries>((resolve) => { release = resolve; }),
    );
    render(<Positions method="FIFO" onOpenInstrument={() => {}} />);

    expect(await screen.findByText(/Could not reach the API/i)).toBeInTheDocument();

    await act(async () => {
      release(series());
      await Promise.resolve();
    });

    expect(screen.getByText(/Could not reach the API/i)).toBeInTheDocument();
  });
});
