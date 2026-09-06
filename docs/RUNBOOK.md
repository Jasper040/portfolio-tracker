# Runbook

Every command needed to run, test and maintain the project locally. Commands are
written for PowerShell; the `bash` equivalents differ only in how environment
variables are set.

Two working directories matter and they are not interchangeable:

| Directory | What runs there |
|---|---|
| `backend/` | the API, the CLI, pytest, ruff, mypy |
| `frontend/` | Vite dev server, the production build, `tsc` |

---

## 1. First-time setup

### Backend

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"

# The SQLite directory must exist first. SQLite does NOT create a missing
# directory — it fails with "unable to open database file".
New-Item -ItemType Directory -Force data

# Settings are read from a .env in the CURRENT directory, so this file belongs
# in backend/, not at the repo root. `database_url` has no default: a missing
# value fails at startup rather than silently opening a second, wrong ledger.
Copy-Item ..\.env.example .env
```

`backend/.env` after copying:

```ini
DATABASE_URL=sqlite:///./data/portfolio.sqlite
BASE_CURRENCY=EUR
LOT_METHOD=FIFO
CORS_ORIGINS=http://localhost:5173
```

### Frontend

```powershell
cd frontend
npm install
```

---

## 2. Running it

Two terminals. **Start the backend first** — the Transactions screen fetches on
mount and will show its error state if the API is not up.

**Terminal 1 — API on :8000**

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
python -m uvicorn app.main:create_app --factory --reload --port 8000
```

`app.main:create_app` is a factory, not a module-level `app`. Uvicorn detects that
on its own, so omitting `--factory` still works — it just warns. Pass it anyway.

**Terminal 2 — UI on :5173**

```powershell
cd frontend
npm run dev
```

Then open <http://localhost:5173>.

> **Watch the port.** The API only accepts browser origins listed in
> `CORS_ORIGINS` (default `http://localhost:5173`). If 5173 is already taken,
> Vite silently falls back to 5174 and every API call fails CORS with a bare
> `Failed to fetch`. Either free the port, or add the fallback:
>
> ```ini
> CORS_ORIGINS=http://localhost:5173,http://localhost:5174
> ```
>
> Comma-separated, not JSON. See [Troubleshooting](#6-troubleshooting).

### Health check

```powershell
curl http://localhost:8000/api/health          # {"status":"ok"}
curl "http://localhost:8000/api/transactions?limit=5"
```

---

## 3. Loading data

The ledger starts empty and the UI says so. Import a DeGiro export **directory** —
both `Transactions.csv` and `Account.csv` are required:

```powershell
cd backend
python -m app.cli import ..\degiro-export
# batch 4bfe79f8-...: parsed 428, inserted 428, skipped 0
```

Both files, because neither is enough alone. `Transactions.csv` is authoritative
for trades; `Account.csv` is authoritative for everything else — dividends,
deposits, fees — and is the only file that names a corporate action.

Imports are idempotent per source row: re-running inserts nothing and reports the
rows as skipped.

### The first import will refuse

A corporate action reaches `Transactions.csv` as an ordinary offsetting buy/sell
pair with a blank Order ID. Booked as trades, the sale realises a profit that never
happened. So the import stops and asks (design doc Sec 6.3):

```
2 corporate action(s) must be answered before this export imports.
Nothing was written. Add each key to ...\config\corporate_actions.yaml and run this again.

  US0000000901:2025-02-18:910.40
      kind:   SPLIT
      amount: 910.40 on 2025-02-18
      label:  SPLIT AANPASSING: 10 ORION Corporation @ 90,666 USD (US0000000901)
  ...

resolutions:
  - key: US0000000901:2025-02-18:910.40
    treatment: corporate_action
```

Copy the `resolutions:` block it prints into `config/corporate_actions.yaml` — do
not retype the keys, a mistyped one parses cleanly and applies to nothing. Then run
the import again.

```powershell
Copy-Item ..\config\corporate_actions.example.yaml ..\config\corporate_actions.yaml
```

`config/corporate_actions.yaml` is gitignored: a key names an instrument, a date
and an amount. The committed `.example.yaml` documents the format. The path is
absolute and anchored on the repo, so it does not matter which directory you run
from; override it with `CORPORATE_ACTIONS_PATH` if you keep answers elsewhere.

Answering `treatment: corporate_action` imports both legs of the event and flags
them `is_economic = false`, which is the `NON-ECON` badge in the Transactions
table. Answering `treatment: trade` is the escape hatch for a false positive — the
rows stay economic.

### CLI reference

| Command | Purpose |
|---|---|
| `python -m app.cli import <export-dir>` | Import a DeGiro export directory. Prints the batch id. Exits 1 while a corporate action is unanswered, 2 when an export file is missing — so it can gate a script. `--resolutions <path>` overrides the answers file. |
| `python -m app.cli review` | Show the corporate-action review queue: what was detected, what is still open. Rebuilt by every import, so it always describes the export as it stands. |
| `python -m app.cli batches` | List import batches, newest first. This is how to find a batch id after the terminal has scrolled. |
| `python -m app.cli undo <batch-id>` | Remove every transaction from one batch. The ledger's only reversal mechanism. |
| `python -m app.cli reconcile <export-dir>` | Check the cross-file invariants between `Transactions.csv`, `Account.csv` and `Portfolio.csv` (design doc Sec 3.6). Exits non-zero on any failure. `Portfolio.csv` is optional; without it the cash invariant is skipped. |

To try the app without touching real data, point it at the synthetic golden files —
same shape and quirks, invented amounts. They live in `backend/tests/golden/` under
their test names, so copy them into a directory first:

```powershell
New-Item -ItemType Directory -Force ..\.scratch\golden-export
Copy-Item tests\golden\degiro_transactions_golden.csv ..\.scratch\golden-export\Transactions.csv
Copy-Item tests\golden\degiro_account_golden.csv ..\.scratch\golden-export\Account.csv
python -m app.cli import ..\.scratch\golden-export --resolutions ..\config\corporate_actions.example.yaml
# batch 6e6605e7-...: parsed 30, inserted 30, skipped 0
```

`--resolutions` is needed here: the example file answers the two *golden* corporate
actions, while the default path holds the answers for your real export. Pointing at
it explicitly keeps the two sets of answers from overwriting each other.

`reconcile` does **not** pass on the golden files, and that is not a bug. The
fixture reproduces the structural quirks of the export — the misaligned header, the
byte-identical fills, the corporate-action pairs — not its cross-file arithmetic; it
carries one commission row against nine order ids. The Sec 3.6 invariants are
covered against purpose-built rows in `tests/unit/test_reconcile.py`, and against
the real export under `pytest -m realdata`.

---

## 4. Tests and quality gates

### Backend

```powershell
cd backend
python -m pytest                 # 227 tests. Excludes the realdata suite by default.
python -m pytest -m realdata     # Opt-in: 24 tests against the gitignored real exports.
python -m ruff check .           # Lint (E, F, I, B).
python -m ruff format .          # Format. See the note below before running.
python -m mypy app               # Strict type check.
```

`pytest` excludes `realdata` via `addopts` in `pyproject.toml`, so a plain run
never depends on whether the owner's gitignored exports happen to be on disk.

> **Note on `ruff format`.** Five pre-existing files (`app/api/schemas.py`,
> `app/domain/money.py`, `app/ingest/importer.py`, `tests/integration/test_api.py`,
> `tests/unit/test_source_ref.py`) are not currently ruff-formatted. Formatting is
> not part of the configured gate — `[tool.ruff.lint] select` covers lint rules
> only — so running `ruff format .` will produce a large unrelated diff. Run it
> deliberately as its own commit, not incidentally.

### Frontend

```powershell
cd frontend
npm test             # 50 Vitest tests over lib/
npm run test:watch   # same, in watch mode
npm run typecheck    # tsc --noEmit, strict + noUncheckedIndexedAccess
npm run build        # tsc -b && vite build -> dist/
```

Tests cover `lib/` only, which is where the logic worth testing lives: lot
matching, TWR/MWR, the counterfactual and the two money-formatting paths. The
components are presentational and are covered by typecheck plus the build.

There is no frontend linter configured. `npm run typecheck` and `npm test` are
the automated gates.

---

## 5. Where things live

```
backend/app/
  api/          FastAPI routes and response schemas
  domain/       Pure logic: money, FX, lot matching, fee apportionment
  ingest/       DeGiro CSV parsing (transactions, account, portfolio), importer,
                cross-file reconciliation
  models/       SQLModel tables (the ledger)
  cli.py        import / batches / undo / reconcile

frontend/src/
  api/          Live client for /api/transactions
  lib/          Pure domain logic: lot matching, TWR/MWR, formatting, tokens
  portfolio/    Data layer. fixtures.ts is MODELLED data; provider.ts is the seam
  components/   Chart primitives, UI primitives, layout
  screens/      The nine screens
```

Only the **Transactions** screen reads the live API. The other eight are computed
from `frontend/src/portfolio/fixtures.ts` and are badged `MODELLED` in the UI.
When the M1 endpoints land, the change is `provider.ts` plus deleting the fixture
— nothing in `screens/` or `components/` imports the fixture directly.

> **Never name a source directory `data/`.** `.gitignore` has a bare `data/` rule
> guarding real broker exports, and it matches at any depth — a
> `frontend/src/data/` folder is silently un-committable. Run
> `git check-ignore -v <path>` after creating any new source directory.

---

## 6. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Could not reach the API — Failed to fetch` on Transactions | The backend is down, **or** the UI's origin is not in `CORS_ORIGINS` | Check the Vite banner for the actual port. If it says 5174, free 5173 or add `http://localhost:5174` to `CORS_ORIGINS` in `backend/.env` and restart the API. |
| `sqlite3.OperationalError: unable to open database file` | `backend/data/` does not exist | `New-Item -ItemType Directory -Force data` from `backend/` |
| `ValidationError: database_url Field required` | No `.env` in the current directory | Copy `.env.example` to `backend/.env`, and run the API/CLI from `backend/` |
| `Error loading ASGI app. Attribute "app" not found` | Pointed at `app.main:app`, which does not exist | Target the factory: `app.main:create_app --factory` |
| `WARNING: ASGI app factory detected` | `--factory` omitted | Harmless — uvicorn detects it and proceeds. Pass `--factory` to silence it. |
| Vite reports "Port 5173 is in use" | A stale dev server from an earlier session | `netstat -ano \| Select-String ":5173"`, then `taskkill /PID <pid> /F` |
| Numbers change when switching FIFO/LIFO/HIFO | Expected | The lot method changes every realised figure. TWR and MWR do **not** change — they are cashflow-based, not lot-based. |

### Freeing port 5173

```powershell
netstat -ano | Select-String ":5173.*LISTENING"
taskkill /PID <pid> /F
```
