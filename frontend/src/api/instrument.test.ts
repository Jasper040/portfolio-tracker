import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchBenchmarks, fetchInstrumentChart } from "./client";

afterEach(() => vi.unstubAllGlobals());

function stub(body: unknown) {
  const json = vi.fn().mockResolvedValue(body);
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, json });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("fetchInstrumentChart", () => {
  it("builds a query string from the range and omits benchmark when null", async () => {
    const fetchMock = stub({
      isin: "XX0000000001",
      points: [],
      intervals: [],
      markers: [],
      comparison: null,
      method: null,
      coverage: "full",
    });
    await fetchInstrumentChart("XX0000000001", { range: "1Y", benchmark: null });

    const url = String(fetchMock.mock.calls[0]?.[0]);
    expect(url).toContain("/api/instruments/XX0000000001/chart");
    expect(url).toContain("range=1Y");
    expect(url).not.toContain("benchmark=");
  });

  it("includes the benchmark key when one is requested", async () => {
    const fetchMock = stub({
      isin: "XX0000000001",
      points: [],
      intervals: [],
      markers: [],
      comparison: null,
      method: null,
      coverage: "full",
    });
    await fetchInstrumentChart("XX0000000001", { range: "max", benchmark: "world" });

    const url = String(fetchMock.mock.calls[0]?.[0]);
    expect(url).toContain("range=max");
    expect(url).toContain("benchmark=world");
  });

  it("surfaces a failed response rather than returning an empty chart", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: false, status: 404, statusText: "Not Found" }),
    );
    await expect(
      fetchInstrumentChart("XX0000000001", { range: "1Y", benchmark: null }),
    ).rejects.toThrow(/404/);
  });
});

describe("fetchBenchmarks", () => {
  it("returns the benchmark list", async () => {
    stub({ items: [{ key: "world", name: "World equities", ter: "0.0020" }] });
    const benchmarks = await fetchBenchmarks();
    expect(benchmarks).toEqual([{ key: "world", name: "World equities", ter: "0.0020" }]);
  });

  it("surfaces a failed response rather than returning an empty list", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: false, status: 500, statusText: "Server Error" }),
    );
    await expect(fetchBenchmarks()).rejects.toThrow(/500/);
  });
});
