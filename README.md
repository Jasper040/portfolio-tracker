# portfolio-tracker

A single-user investment portfolio tracker built over DeGiro CSV exports, because
the broker will not answer four questions:

1. How did each individual **batch of shares** perform?
2. What did **selling** cost me, or save me?
3. What did the stock do **while I was not holding it**?
4. Am I **beating the market**, or riding it?

Everything else here is supporting infrastructure for those four.

> **This is not a tax tool.** The lot model exists for insight, not for a return.
> Withholding tax actually paid is reported as a *fact*; nothing on any screen is
> presented as a tax figure or as advice.

---

## What it does

- **Reads the DeGiro export** — all three CSVs, mapped **by column position**, because
  the header is misaligned against the data and mapping by name silently shifts every
  column. Import is idempotent: re-importing the same file changes nothing.
- **Separates the internal noise from real money.** Roughly a third of the account
  rows are cash sweeps between the investment account and the bank account, plus
  reservation pairs that net to zero. They carry real signed amounts and plausible
  running balances, and only their description distinguishes them. Classify them as
  external flows and money-weighted return becomes meaningless.
- **Tracks lots and closures** under FIFO, LIFO or HIFO, with the method switchable
  on screen because it changes what every realised figure means.
- **Handles corporate actions** — splits and product changes are labelled in the
  *account* file while appearing in the *transactions* file as ordinary offsetting
  trades. They are detected, quarantined and applied as actions, not as P&L.
- **Values the portfolio daily**, holdings at market plus cash, and reports how well
  each day is actually known.
- **Measures time-weighted return** against a benchmark you could have bought, with
  both sides' dividend treatment stated next to the number.

## The idea that shapes the whole thing

**A figure never claims more confidence than it has earned.**

Every valued day carries a coverage verdict: fully priced, carried forward from an
earlier day, hand-supplied, or not priceable at all. A day that cannot be priced has
**no value** — not a value of zero, because a total that quietly drops a position
looks exactly like a total that includes it. `null` renders as an em dash, never as
`0`. A portfolio total is withheld entirely rather than partially summed. Where a
number depends on a methodological choice, that choice is visible beside it.

The same instinct runs through the code: ledger money is a `Decimal` on the server
and a **string** on the wire, so it never touches a float on its way to the screen.
The two or three places a string becomes a number are deliberate, documented, and
all of them are chart coordinates.

## Stack

| | |
|---|---|
| Backend | Python, FastAPI, SQLModel over SQLite |
| Frontend | React, TypeScript (strict), Vite, Apache ECharts |
| Layers | `ingest/` → `models/` → `domain/` → `analytics/` → `api/` |

`domain/` is pure: no I/O, no framework, no database. Broker-specific parsing stays
behind `ingest/degiro/`, so a second broker is a new parser rather than a new app.

## Running it

See **[`docs/RUNBOOK.md`](docs/RUNBOOK.md)** — it carries every command, the import
steps, the quality gates and the troubleshooting. The short version:

```bash
cd backend  && .venv/Scripts/python.exe -m uvicorn app.main:create_app --factory --port 8000
cd frontend && npm run dev
```

The ledger starts empty and the UI says so rather than drawing a flat line at zero.

## Test data

**No real export is in this repository, and none ever will be.** Real exports are
gitignored; CI runs against synthetic golden files that reproduce every structural
quirk — partial fills sharing an order id, byte-identical fill rows, the split pair,
the product-change pair, blank order ids, a one-cent arithmetic mismatch, four
currencies, cash sweeps, reservation pairs, mixed Dutch and English descriptions and
the misaligned header — with invented amounts throughout.

A separate opt-in suite (`pytest -m realdata`) runs against the owner's real files
locally. It states no figure of its own: it reads the export at run time and works
out what to assert, so the repository holds none of it.

One test is a guard rather than a check on behaviour: it derives what "real" means
from the export and fails if any of it has reached a tracked file.

## Design

The full design, including the export's verified quirks and the reasoning behind each
decision, is in
[`docs/superpowers/specs/2026-09-05-portfolio-tracker-design.md`](docs/superpowers/specs/2026-09-05-portfolio-tracker-design.md).

Work is tracked on a private board; `docs/TRACKING.md` explains the convention. That
is why no document here carries a status table — a copied status goes stale, an issue
key does not.

## Licence

MIT — see [`LICENSE`](LICENSE).
