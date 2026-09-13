import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchPerformance } from "./client";

afterEach(() => vi.unstubAllGlobals());

function stub() {
  const json = vi.fn().mockResolvedValue({ links: [], runs: [] });
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, json });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("fetchPerformance", () => {
  it("sends no parameters for the whole ledger with no benchmark", async () => {
    // The server then measures from the ledger's first day, a decision it
    // already knows the answer to.
    const fetchMock = stub();
    await fetchPerformance({ from: null, benchmark: null });
    expect(String(fetchMock.mock.calls[0]?.[0])).toMatch(/\/api\/performance$/);
  });

  it("sends the start and the benchmark when both are given", async () => {
    const fetchMock = stub();
    await fetchPerformance({ from: "2025-09-06", benchmark: "world" });
    const url = String(fetchMock.mock.calls[0]?.[0]);
    expect(url).toContain("from=2025-09-06");
    expect(url).toContain("benchmark=world");
  });

  it("surfaces a failed response rather than returning an empty report", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: false, status: 500, statusText: "Server Error" }),
    );
    await expect(fetchPerformance({ from: null, benchmark: null })).rejects.toThrow(/500/);
  });
});
