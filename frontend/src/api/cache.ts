/** A session-long response cache, keyed on the resolved request (PT-46).
 *
 *  Before this, nothing the app fetched was ever reused. `App.tsx` renders each
 *  screen as `{tab === "pos" && <Positions/>}`, so switching tabs unmounts the
 *  component and destroys its state -- every return to a screen was a cold
 *  start, and every range or method the reader had already looked at was
 *  fetched again from scratch.
 *
 *  ## Why a module-level Map rather than component state
 *
 *  The data has to outlive the component that asked for it, which rules out
 *  `useState`. Lifting it into `App` would work and was rejected: `App.tsx`
 *  deliberately holds only navigation and `method`, and turning it into a state
 *  bag for every screen's responses is the shape that file says it is avoiding.
 *  A module-level cache behind the API client leaves every screen exactly as it
 *  was and is testable without a DOM.
 *
 *  ## Why it holds promises, not values
 *
 *  Two screens mounting at once ask for the same series. Storing the in-flight
 *  promise means the second caller joins the first request instead of opening a
 *  second one, so deduplication falls out of the same Map that does the caching.
 *
 *  ## Why a failure is never remembered
 *
 *  A rejected promise left in the Map would turn one dropped request into a key
 *  that can never succeed again for the life of the session, and the screen
 *  would show the original error forever with no way back short of a page
 *  reload. So the entry is evicted on rejection and the next caller genuinely
 *  retries. This is the one case where the cache does LESS than it could, and it
 *  is deliberate.
 *
 *  ## Why there is no expiry
 *
 *  Nothing the app serves changes on its own. The ledger moves when the operator
 *  runs `import`, `fetch-prices` or `rebuild` from a terminal, and the browser
 *  cannot observe any of them. A TTL would therefore be guessing at something
 *  the operator knows exactly -- and it would guess wrong in both directions,
 *  refetching unchanged data all afternoon and still serving a stale answer for
 *  the minutes right after a rebuild.
 *
 *  `clearCache` is the honest alternative: the header's refresh control calls
 *  it, so the reader decides when the answer is old, and the app never quietly
 *  changes a figure that is already on screen. That is the same instinct as the
 *  MODELLED badge and the withheld total -- design doc section 8.1 asks the app
 *  never to present a number with more confidence than it has earned, and a
 *  silently-expiring cache would be doing exactly that.
 */

/** Keyed on the resolved request string, which for every caller here is the
 *  full URL including its query. Two requests that differ only in parameter
 *  ORDER would key separately; the client builds them through `URLSearchParams`
 *  in a fixed order, so that cannot arise from this codebase. */
const entries = new Map<string, Promise<unknown>>();

/** The cached answer for `key`, or `load()`'s, remembered for next time.
 *
 *  The cast on the hit is unavoidable and safe in the one way that matters: a
 *  key is a URL, and a URL has exactly one response shape. The client module is
 *  the only caller, and it passes a literal type argument per endpoint.
 */
export function cached<T>(key: string, load: () => Promise<T>): Promise<T> {
  const hit = entries.get(key);
  if (hit !== undefined) return hit as Promise<T>;

  const pending = load().catch((cause: unknown) => {
    // Evict before rethrowing, so the retry is real. Deleting by identity
    // rather than by key alone means a `clearCache` that landed mid-flight
    // cannot have its eviction undone by this one arriving late.
    if (entries.get(key) === pending) entries.delete(key);
    throw cause;
  });

  entries.set(key, pending);
  return pending;
}

/** Drop everything. Called by the header's refresh control after the operator
 *  has changed the ledger from a terminal. */
export function clearCache(): void {
  entries.clear();
}

/** How many responses are held. Exported for the tests and for a future
 *  diagnostic; nothing on screen reads it. */
export function cacheSize(): number {
  return entries.size;
}
