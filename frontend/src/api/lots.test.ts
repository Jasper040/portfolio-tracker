import { describe, expect, it, vi, afterEach } from "vitest";
import { fetchClosures, fetchLots } from "./client";

afterEach(() => vi.unstubAllGlobals());

function stub(body: unknown) {
  const json = vi.fn().mockResolvedValue(body);
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, json });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("fetchLots", () => {
  it("sends the method the caller asked for", async () => {
    const fetchMock = stub({ items: [], total: 0, method: "HIFO", coverage: "full" });
    await fetchLots("HIFO");
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain("method=HIFO");
  });

  it("surfaces a failed response rather than returning an empty page", async () => {
    // An empty page and a dead API look identical on screen; one is a fact and
    // the other is a bug, so they must not render the same.
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: false, status: 500, statusText: "Server Error" }),
    );
    await expect(fetchLots("FIFO")).rejects.toThrow(/500/);
  });

  it("passes an isin filter through", async () => {
    const fetchMock = stub({ items: [], total: 0, method: "FIFO", coverage: "full" });
    await fetchLots("FIFO", { isin: "US0000000901" });
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain("isin=US0000000901");
  });
});

describe("fetchClosures", () => {
  it("hits the closures endpoint", async () => {
    const fetchMock = stub({ items: [], total: 0, method: "FIFO", coverage: "full" });
    await fetchClosures("FIFO");
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain("/api/closures");
  });
});
