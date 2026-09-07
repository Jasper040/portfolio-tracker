# portfolio-tracker

A personal portfolio tracker over DeGiro exports: FastAPI + SQLModel backend, React +
Vite frontend. `docs/RUNBOOK.md` has every command. The parent design is
`docs/superpowers/specs/2026-09-05-portfolio-tracker-design.md`.

## Work tracking — Jira project `PT`

**Jira holds state. Git holds prose. Nothing lives in both.**

| Goes in Jira | Stays in git |
|---|---|
| Bugs, follow-ups, backlog items, chores | Design docs and specs |
| Milestones M0–M8, one Epic each (PT-1 … PT-9) | Implementation plans |
| Open questions awaiting a decision | The runbook |
| Anything with a lifecycle: open → doing → done | The *statement* of a question or a scope |

**Never write a `*-FOLLOW-UPS.md`, a TODO list, or a status table in a doc.** That is what
the board is for. If a doc needs to reference work, it cites the issue key — the key never
goes stale, a copied status always does.

A doc may state *what* a milestone is, or *what* a question asks. It may never state
whether either is finished.

### Before you put anything in a Jira issue

**No ISIN, ticker, instrument name, holding size, or account figure — ever.** Describe the
shape of the problem, not the holding. `test_no_real_data_committed.py` derives what "real"
means from the gitignored export and fails if it reaches a tracked file; it cannot see a
Jira issue. Jira is an external surface with no scanner behind it, so the discipline is
manual there. Say "the outstanding instrument" and give the command that names it.

### Opening an issue

Type `Bug` for a defect, `Task` for everything else, `Epic` only for a milestone. Parent it
to the milestone epic that owns the work. Labels:

| Label | Meaning |
|---|---|
| `carried` | Deliberately deferred out of a milestone that shipped. Not a regression |
| `operator-action` | Blocked on a human doing something a machine must not — usually editing a gitignored answers file |
| `open-question` | A decision, not work. No epic parent; deciding it creates the work item |
| `pre-publish` | A gate on making this repo public, not on any milestone |

Statuses are `To Do` → `In Progress` → `Done`. There are deliberately only three.

## Commits

`<type>: <description> (PT-n)` — the key goes at the end of the subject line.
Types: `feat` `fix` `refactor` `docs` `test` `chore` `perf` `ci`.

```
fix(analytics): split valuation three ways (PT-12)
```

Milestone work happens on a branch and merges to `master` with a merge commit.

## Ground rules that bite

- **Never name a source directory `data/`.** The bare `data/` rule in `.gitignore` matches
  at any depth, so the folder is silently un-committable, *and* setuptools auto-discovery
  then refuses to guess the package. Run `git check-ignore -v <path>` after creating any
  new source directory.
- **There is one backend virtualenv and it is `backend/.venv`.** A root `.venv` resolves
  nothing and reads as a broken backend.
- **No migrations.** `SQLModel.metadata.create_all` never alters an existing table, so a
  new column strands old local sqlite files. Delete `backend/data/portfolio.sqlite` and
  re-import.
- Real exports are gitignored. `pytest` excludes the `realdata` suite by default; opt in
  with `-m realdata`.
- Immutable data structures, no in-place mutation. Files 200–400 lines typical, 800 max.

Full convention: `docs/TRACKING.md`.
