/** What the response cache promises, and the two things it must refuse to do.
 *
 *  The cache exists because nothing the app fetched was ever reused: switching
 *  tabs unmounts a screen and destroys its state, so every return was a cold
 *  start (PT-46). These tests pin the behaviour that makes reuse safe rather
 *  than merely fast -- a remembered failure and a remembered stale answer are
 *  both worse than the round trip they saved.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

import { cacheSize, cached, clearCache } from "./cache";

beforeEach(() => {
  clearCache();
});

describe("cached", () => {
  it("calls the loader once for a repeated key and returns the same answer", async () => {
    const load = vi.fn().mockResolvedValue({ items: [1, 2, 3] });

    const first = await cached("/api/valuation", load);
    const second = await cached("/api/valuation", load);

    expect(load).toHaveBeenCalledTimes(1);
    expect(second).toBe(first);
  });

  it("keeps different keys apart", async () => {
    const load = vi.fn().mockResolvedValueOnce("max").mockResolvedValueOnce("one year");

    expect(await cached("/api/valuation", load)).toBe("max");
    expect(await cached("/api/valuation?from=2025-01-01", load)).toBe("one year");
    expect(load).toHaveBeenCalledTimes(2);
  });

  /** The reason the cache holds promises rather than resolved values. Two
   *  screens mounting at once ask for the same series; without this they open
   *  two sockets and one answer is thrown away. */
  it("shares one in-flight request between concurrent callers", async () => {
    let settle: (value: string) => void = () => {};
    const load = vi.fn(
      () =>
        new Promise<string>((resolve) => {
          settle = resolve;
        }),
    );

    const a = cached("/api/positions?method=FIFO", load);
    const b = cached("/api/positions?method=FIFO", load);
    settle("one answer");

    expect(load).toHaveBeenCalledTimes(1);
    expect(await a).toBe("one answer");
    expect(await b).toBe("one answer");
  });

  /** The subtle one. A cache that remembers a rejection turns one dropped
   *  request into a key that can never succeed again for the life of the
   *  session -- and the reader has no way to tell that from a backend that is
   *  genuinely down. */
  it("does not remember a failure", async () => {
    const load = vi
      .fn()
      .mockRejectedValueOnce(new Error("Failed to fetch"))
      .mockResolvedValueOnce("recovered");

    await expect(cached("/api/performance", load)).rejects.toThrow("Failed to fetch");
    expect(cacheSize()).toBe(0);

    await expect(cached("/api/performance", load)).resolves.toBe("recovered");
    expect(load).toHaveBeenCalledTimes(2);
  });

  it("rejects every concurrent caller when the shared request fails", async () => {
    const load = vi.fn().mockRejectedValue(new Error("Failed to fetch"));

    const a = cached("/api/performance", load);
    const b = cached("/api/performance", load);

    await expect(a).rejects.toThrow("Failed to fetch");
    await expect(b).rejects.toThrow("Failed to fetch");
    expect(load).toHaveBeenCalledTimes(1);
  });
});

describe("clearCache", () => {
  /** The operator rebuilds the database from a terminal while the browser is
   *  open. Nothing in the app can observe that, so the refresh control is the
   *  only thing standing between a cached answer and a screen that lies about
   *  the ledger. */
  it("makes the next call reach the loader again", async () => {
    const load = vi.fn().mockResolvedValueOnce("before rebuild").mockResolvedValueOnce("after");

    expect(await cached("/api/positions", load)).toBe("before rebuild");
    clearCache();
    expect(await cached("/api/positions", load)).toBe("after");
    expect(load).toHaveBeenCalledTimes(2);
  });

  it("empties every key, not only the one last read", async () => {
    await cached("a", () => Promise.resolve(1));
    await cached("b", () => Promise.resolve(2));
    expect(cacheSize()).toBe(2);

    clearCache();

    expect(cacheSize()).toBe(0);
  });
});
