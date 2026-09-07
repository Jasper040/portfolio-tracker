# M2 follow-ups

Written 2026-09-07, when M2 (prices, FX, valuation and coverage) merged to master.

Everything here was found during M2 and deliberately left. Nothing in this list
blocks the milestone; the first item blocks the *cache-side* half of its
acceptance suite, which is a different thing and is stated as such.

No instrument is named anywhere in this file. Which one is outstanding is a fact
about the gitignored export, so the file tells you how to ask rather than
answering for you — the same rule the `realdata` suite follows.

---

## 1. One symbol is still unanswered — operator action

`fetch-prices` resolves every instrument in the export but one, and refuses to
complete while that one is open. That refusal is the design working, not a bug.

```bash
cd backend
python -m app.cli fetch-prices     # prints the instrument and why, exits 1
```

The report will say the identifier provider offered tickers but none of them has
a price series. That is the known-hard edge between the two providers' ticker
namespaces: OpenFIGI answers in Bloomberg tickers and Yahoo uses its own, and
for some European fund listings the two do not agree. It is not a mapping bug
and there is no general fix for it.

Answer it in `config/instrument_symbols.yaml` (gitignored; copy
`config/instrument_symbols.example.yaml` to start) with either:

* a ticker the price provider actually knows — search Yahoo for the fund and use
  the symbol its own chart page uses; or
* `manual`, which routes the instrument to `config/manual_prices.csv`.

An answer in that file is taken as authoritative and is deliberately **not**
re-validated against the ledger, which is exactly why it has to be a human's.

Then:

```bash
python -m app.cli fetch-prices
python -m app.cli rebuild
python -m pytest -q -m realdata -rs
```

The nine cache-side tests in `tests/integration/test_realdata_prices.py` skip
until the cache is populated. Once it is, they check the five-year backfill
depth, that every day a position was held can be priced, and that no position's
market value differs from the broker's own statement by more than 30%.

## 2. The screen has not been looked at with real data

Task 12 of the plan ends with a browser check that was never run, because the
price cache is empty until item 1 is answered. The component tests cover what
the screen renders; nobody has watched the value chart draw.

After item 1:

```bash
cd backend && python -m uvicorn app.main:create_app --factory
cd frontend && npm run dev
```

Check by eye: the chart starts at the first day a position existed; selecting 5Y
either reaches further back or says the window was clamped; the Positions tab
carries no MODELLED badge. If Vite reports a port other than 5173, add it to
`CORS_ORIGINS` in `backend/.env`, or every request fails as a bare
"Failed to fetch".

## 3. Carried from the final whole-branch review

Each of these was triaged as safe to carry. None is a correctness bug today.

* **`analytics/valuation.py` is 485 lines**, above the project's 200–400 typical
  band. It holds two independent readers — `value_series` and
  `current_positions` — over four shared private helpers. A natural three-way
  split when M3 next touches it.
* **`coverage` is typed `str` on four dataclasses** in that module rather than
  the `Coverage` Literal, so `mypy --strict` does not enforce the four-value
  invariant at that boundary. Bounded: pydantic still validates at the API
  boundary, so a bad value is a 500 rather than a wrong number on screen.
* **The Positions badge reports the positions envelope, not the series.** A
  window containing an unpriceable day, under holdings that are all priced
  today, shows a `full` badge; the coverage strip's count is the only tell.
* **Yahoo's `GBp`/`GBP` distinction is erased** by `.upper()` in the provider.
  `.L` is in the exchange map, so it is reachable. It degrades safely — a pence
  quote against a pound-denominated ledger is off by ~100×, which the symbol
  discriminator rejects, so it becomes a quarantine question rather than a wrong
  number.
* **A provider error during the FX phase exits with prices already committed.**
  `fetch-prices` promises "having written nothing" only for the symbol refusal.
  Idempotent on re-run.
* **A foreign trade bought from a pre-existing foreign balance** would have its
  `Total EUR` counted, deducting euros that never left the account — and the
  foreign cash balance itself is structurally invisible, because `cash_daily`
  holds only `balance_base`. Spec-compliant, and the shape does not occur in the
  current export. Documented in `_moves_euros` in `domain/positions.py`.
* **`manual` prices never age.** A hand-typed price from a year ago counts as
  fully covered and reads on the chart like today's. The reasoning is sound —
  ageing it would make the `manual` coverage value unreachable — but the
  conclusion trades one unreachable value for a figure that cannot say how old it
  is. The per-position `price_date` column is currently the only surface where a
  reader can find out. A separate staleness field would be better than a wider
  threshold.

## 4. `frontend/src/portfolio/fixtures.ts` carries real ISINs

Pre-existing, untouched by M2, and the leak scanner passes because they are not
the owner's holdings — they are large listed companies used as modelled demo
data. Worth replacing with invented instruments before this repo is published,
which the parent design doc leaves open as a possibility.

Not urgent, and not M2's to fix.
