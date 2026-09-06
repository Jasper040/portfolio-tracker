/**
 * @vitest-environment jsdom
 */

/** What the Lots screen actually puts on screen.
 *
 *  Every case here is one a review caught by reading rather than by running,
 *  because until now this project had no way to run a component at all. Three of
 *  them shipped as real defects:
 *
 *  * the closures table showed one of its three charge columns, so 29 of 56 real
 *    rows displayed a GROSS, a CHARGES and a NET that did not add up;
 *  * a fully-sold portfolio rendered "No lots yet" while hiding every closure it
 *    had already loaded;
 *  * the return column parsed a ledger figure through `Number()` and printed it
 *    with an ASCII hyphen beside cells using a typographic minus.
 *
 *  The API client is mocked rather than `fetch`: `api/lots.test.ts` already
 *  covers the URL and the error path, and mocking one layer down keeps these
 *  tests about rendering.
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fetchClosures, fetchLots } from "../api/client";
import type { Closure, ClosurePage, Lot, LotPage } from "../api/types";
import { Lots } from "./Lots";

vi.mock("../api/client", () => ({
  fetchLots: vi.fn(),
  fetchClosures: vi.fn(),
}));

const mockFetchLots = vi.mocked(fetchLots);
const mockFetchClosures = vi.mocked(fetchClosures);

function lot(overrides: Partial<Lot> = {}): Lot {
  return {
    id: "lot-1",
    method: "FIFO",
    isin: "NL0000000001",
    source_ref: "ref-1",
    opened_on: "2025-01-30",
    quantity: "10",
    price: "65.530",
    cost_basis: "655.30",
    commission: "2.00",
    autofx: "2.18",
    tax: "0.00",
    ...overrides,
  };
}

function closure(overrides: Partial<Closure> = {}): Closure {
  return {
    id: "closure-1",
    method: "FIFO",
    isin: "NL0000000002",
    lot_source_ref: "ref-1",
    sale_source_ref: "ref-2",
    opened_on: "2024-01-01",
    closed_on: "2025-06-01",
    quantity: "5",
    open_price: "10.00",
    close_price: "20.00",
    gross_pnl: "50.00",
    commission: "3.11",
    autofx: "1.48",
    tax: "0.25",
    pnl: "45.16",
    holding_days: 517,
    return_pct: "0.9032",
    annualised_return: "0.5921",
    ...overrides,
  };
}

function lotPage(items: Lot[], overrides: Partial<LotPage> = {}): LotPage {
  return { items, total: items.length, method: "FIFO", coverage: "full", ...overrides };
}

function closurePage(items: Closure[], overrides: Partial<ClosurePage> = {}): ClosurePage {
  return { items, total: items.length, method: "FIFO", coverage: "full", ...overrides };
}

function serve(lots: LotPage, closures: ClosurePage): void {
  mockFetchLots.mockResolvedValue(lots);
  mockFetchClosures.mockResolvedValue(closures);
}

beforeEach(() => {
  mockFetchLots.mockReset();
  mockFetchClosures.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

/** The closures table is the second one on screen; the first holds open lots. */
async function closuresTable(): Promise<HTMLElement> {
  const tables = await screen.findAllByRole("table");
  const last = tables[tables.length - 1];
  if (!last) throw new Error("no table rendered");
  return last;
}

describe("charges on a closure", () => {
  it("shows commission, FX cost and tax as three separate figures", async () => {
    // The shipped defect: one column labelled CHARGES, bound to commission
    // alone. `autofx` and `tax` were never read anywhere in the file.
    serve(lotPage([lot()]), closurePage([closure()]));
    render(<Lots method="FIFO" />);

    const table = await closuresTable();
    const row = within(table).getByText("NL0000000002").closest("tr");
    expect(row).not.toBeNull();
    const cells = within(row as HTMLElement).getAllByRole("cell");
    const text = cells.map((cell) => cell.textContent);

    expect(text).toContain("€ 3,11");
    expect(text).toContain("€ 1,48");
    expect(text).toContain("€ 0,25");
  });

  it("shows a row whose gross, charges and net actually reconcile", async () => {
    // The reader's own check: 50.00 - (3.11 + 1.48 + 0.25) = 45.16. When the
    // table showed commission only, this row read 50.00 - 3.11 = 45.16 and the
    // arithmetic was visibly wrong on screen.
    serve(lotPage([lot()]), closurePage([closure()]));
    render(<Lots method="FIFO" />);

    const table = await closuresTable();
    const row = within(table).getByText("NL0000000002").closest("tr");
    const text = (row as HTMLElement).textContent ?? "";

    expect(text).toContain("€ 50,00");
    expect(text).toContain("€ 45,16");
  });

  it("labels all three charge columns in the header", async () => {
    serve(lotPage([lot()]), closurePage([closure()]));
    render(<Lots method="FIFO" />);

    const table = await closuresTable();
    for (const label of ["COMMISSION", "FX COST", "TAX"]) {
      expect(within(table).getAllByText(label).length).toBeGreaterThan(0);
    }
  });
});

describe("the annualised return", () => {
  it("renders under the period return once the holding is long enough", async () => {
    serve(lotPage([lot()]), closurePage([closure()]));
    render(<Lots method="FIFO" />);

    const table = await closuresTable();
    const row = within(table).getByText("NL0000000002").closest("tr");
    const text = (row as HTMLElement).textContent ?? "";

    expect(text).toContain("90,32%");
    expect(text).toContain("59,21%");
  });

  it("declines to annualise a short holding, without hiding the period return", async () => {
    // A 2-day loss annualises to -99.98%, which reads as ruin and is a fact
    // about nothing. The period return is still real and still shown.
    serve(
      lotPage([lot()]),
      closurePage([
        closure({ holding_days: 2, return_pct: "-0.0468", annualised_return: "-0.9998" }),
      ]),
    );
    render(<Lots method="FIFO" />);

    const table = await closuresTable();
    const row = within(table).getByText("NL0000000002").closest("tr");
    const text = (row as HTMLElement).textContent ?? "";

    expect(text).toContain("−4,68%");
    expect(text).not.toContain("99,98%");
    expect(text).toContain("—");
  });

  it("renders an em dash rather than 0% when the backend says null", async () => {
    serve(
      lotPage([lot()]),
      closurePage([closure({ return_pct: null, annualised_return: null })]),
    );
    render(<Lots method="FIFO" />);

    const table = await closuresTable();
    const row = within(table).getByText("NL0000000002").closest("tr");
    const text = (row as HTMLElement).textContent ?? "";

    expect(text).not.toContain("0,00%");
    expect(text).toContain("—");
  });
});

describe("what the screen says when there is nothing to show", () => {
  it("renders the closures table when every position has been sold", async () => {
    // The shipped defect: the empty-state gate read only `lots.items.length`, so
    // a fully-sold portfolio was told to go and run `rebuild` while the closures
    // it had already loaded sat hidden behind the message.
    serve(lotPage([]), closurePage([closure()]));
    render(<Lots method="FIFO" />);

    expect(await screen.findByText("NL0000000002")).toBeInTheDocument();
    expect(screen.queryByText(/No lots or closures yet/i)).not.toBeInTheDocument();
  });

  it("says so plainly when there really is nothing", async () => {
    serve(lotPage([]), closurePage([]));
    render(<Lots method="FIFO" />);

    expect(await screen.findByText(/No lots or closures yet/i)).toBeInTheDocument();
  });

  it("distinguishes a dead API from an empty portfolio", async () => {
    // An empty page and a dead API look identical on screen; one is a fact and
    // the other is a bug, so they must not render the same.
    mockFetchLots.mockRejectedValue(new Error("500 Server Error"));
    mockFetchClosures.mockRejectedValue(new Error("500 Server Error"));
    render(<Lots method="FIFO" />);

    expect(await screen.findByText(/Could not reach the API/i)).toBeInTheDocument();
    expect(screen.queryByText(/No lots or closures yet/i)).not.toBeInTheDocument();
  });
});

describe("provenance", () => {
  it("reports the method the API said it used, not the one that was asked for", async () => {
    // Sec 9.2. If the two ever disagree the response envelope is the truth, and
    // the badge has to say so rather than echoing the prop back.
    serve(
      lotPage([lot({ method: "HIFO" })], { method: "HIFO" }),
      closurePage([closure({ method: "HIFO" })], { method: "HIFO" }),
    );
    render(<Lots method="FIFO" />);

    expect(await screen.findByText("HIFO")).toBeInTheDocument();
  });

  it("states the annualisation threshold on screen, not just in the code", async () => {
    // Sec 13: where a number depends on a methodological choice, the choice is
    // visible next to it.
    serve(lotPage([lot()]), closurePage([closure()]));
    render(<Lots method="FIFO" />);

    expect(await screen.findByText(/90 days or more/i)).toBeInTheDocument();
  });

  it("asks the API for the method it was given", async () => {
    serve(lotPage([lot()]), closurePage([closure()]));
    render(<Lots method="LIFO" />);

    await waitFor(() => expect(mockFetchLots).toHaveBeenCalledWith("LIFO"));
    expect(mockFetchClosures).toHaveBeenCalledWith("LIFO");
  });
});

describe("switching method", () => {
  it("does not render a slow response under the method that replaced it", async () => {
    // The race the cancellation guard exists for: a slow FIFO response landing
    // after a fast HIFO one would put FIFO's numbers under a HIFO badge.
    let releaseFifo: (page: LotPage) => void = () => {};
    const slowFifo = new Promise<LotPage>((resolve) => {
      releaseFifo = resolve;
    });

    mockFetchLots.mockImplementation((method) =>
      method === "FIFO" ? slowFifo : Promise.resolve(lotPage([lot({ method: "HIFO" })], { method: "HIFO" })),
    );
    mockFetchClosures.mockImplementation((method) =>
      Promise.resolve(closurePage([], { method: method === "FIFO" ? "FIFO" : "HIFO" })),
    );

    const { rerender } = render(<Lots method="FIFO" />);
    rerender(<Lots method="HIFO" />);
    expect(await screen.findByText("HIFO")).toBeInTheDocument();

    // FIFO's answer arrives late. It must be discarded, not painted over HIFO's.
    releaseFifo(lotPage([lot({ isin: "NL0000000009" })], { method: "FIFO" }));

    await waitFor(() => expect(screen.getByText("HIFO")).toBeInTheDocument());
    expect(screen.queryByText("NL0000000009")).not.toBeInTheDocument();
  });
});
