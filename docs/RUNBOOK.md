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

The ledger starts empty and the UI says so. Import a DeGiro export:

```powershell
cd backend
python -m app.cli import ..\degiro-export\Transactions.csv
# batch 28d4533c-...: parsed 13, inserted 13, skipped 0
```

Imports are idempotent per source row — re-importing the same file inserts
nothing and reports the rows as skipped.

### CLI reference

| Command | Purpose |
|---|---|
| `python -m app.cli import <path>` | Import a DeGiro `Transactions.csv`. Prints the batch id. |
| `python -m app.cli batches` | List import batches, newest first. This is how to find a batch id after the terminal has scrolled. |
| `python -m app.cli undo <batch-id>` | Remove every transaction from one batch. The ledger's only reversal mechanism. |

To try the app without touching real data, import the synthetic golden file
instead — same shape, invented amounts:

```powershell
python -m app.cli import tests\golden\degiro_transactions_golden.csv
```

---

## 4. Tests and quality gates

### Backend

```powershell
cd backend
python -m pytest                 # 78 tests. Excludes the realdata suite by default.
python -m pytest -m realdata     # Opt-in: runs against the gitignored real exports.
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
  domain/       Money and FX value objects
  ingest/       DeGiro CSV parsing and the importer
  models/       SQLModel tables (the ledger)
  cli.py        import / batches / undo

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
