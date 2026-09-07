# Tracking

Where work is recorded, and why it is split the way it is.

Adopted 2026-09-07, after M2 merged and before M3 started.

---

## 1. The rule

**Jira holds state. Git holds prose. Nothing lives in both.**

A design document explains *why* a decision was made. It is reviewed alongside the diff,
versioned with the code, and diffable — git is the right home for it.

An item like "`coverage` is typed `str` on four dataclasses" has a *lifecycle*: open,
triaged, fixed. A markdown bullet cannot represent that, and going stale is its default
behaviour. Jira is the right home for it.

The split is not "docs versus tickets". It is **prose versus state**, and the test is
whether the thing has a status.

This is the same discipline the rest of the repo already runs on: real holdings live only
in the gitignored export, the `LEDGER_BACKED` set drives the `MODELLED` badge so a screen
cannot lie about its own provenance, and the `realdata` suite derives its expectations
rather than hardcoding them. One fact, one home.

### What that means in practice

A document may state **what** a milestone is, or **what** a question asks. It may never
state whether either is finished.

So `docs/superpowers/specs/2026-09-05-portfolio-tracker-design.md` §10 keeps the M0–M8
scope table — that table is prose, describing what each milestone delivers — and gains an
Epic-key column. It gains no status column. §12 keeps the five open questions in full, each
carrying its issue key. Neither section can drift from the board, because neither makes a
claim the board could contradict.

---

## 2. The project

| | |
|---|---|
| **Project** | `PT` — Portfolio Tracker |
| **Type** | Team-managed software (Kanban) |
| **Statuses** | `To Do` → `In Progress` → `Done` |

Three statuses, deliberately. A solo board with six columns is a board whose columns stop
being moved. The one genuinely distinct state this project has — *blocked on a human
editing a gitignored answers file* — is carried by the `operator-action` label instead,
because it is a property of the issue rather than a stage of it.

### Issue types

| Type | Use for |
|---|---|
| **Epic** | A milestone, and nothing else. Nine exist; there will not be more until the roadmap grows |
| **Bug** | A defect — something that produces, or would produce, a wrong result |
| **Task** | Everything else: refactors, chores, verifications, decisions |
| **Subtask** | A step within a task, when a task genuinely has separable steps |

### Labels

| Label | Meaning |
|---|---|
| `carried` | Deliberately deferred out of a milestone that shipped. Triaged as safe at the time. **Not a regression** — the distinction matters, because a carried item was a decision and a regression is a surprise |
| `operator-action` | Blocked on a human doing something a machine must not. Usually: answering a question in a gitignored config file, where the answer is taken as authoritative and is deliberately not re-validated |
| `open-question` | A decision, not work. Carries **no epic parent** — deciding it is what creates the work item, under whichever epic then owns it |
| `pre-publish` | A gate on making this repo public. Not attached to any milestone, because no milestone will ever close it |

### Epics

| Milestone | Epic |
|---|---|
| M0 — Ledger, both DeGiro parsers and quarantine | PT-1 |
| M1 — Lots, closures, FIFO/LIFO/HIFO and splits | PT-2 |
| M2 — Prices, FX, valuation and coverage | PT-3 |
| M3 — Instrument chart, benchmark and holding intervals | PT-4 |
| M4 — Counterfactuals | PT-5 |
| M5 — Dividends | PT-6 |
| M6 — TWR, MWR and attribution | PT-7 |
| M7 — SnapTrade sync and reconciliation | PT-8 |
| M8 — News on demand | PT-9 |

**A `Done` epic can have open children.** PT-3 (M2) is done and still has seven open issues
under it. That is the `carried` semantics working as intended: the milestone shipped, and
these were consciously left. The parent link records *which milestone found the problem*,
which is worth more than a tidy board.

---

## 3. Nothing goes into a Jira issue that could not go into a tracked file

**No ISIN, ticker, instrument name, holding size, or account figure. Ever.**

`backend/tests/integration/test_no_real_data_committed.py` reads the gitignored export,
derives what "real" means from it, and fails if any of it appears in a tracked file. It is
the guard that lets this repo hold documentation about a real portfolio at all.

**It cannot see a Jira issue.** Jira is an external surface with no scanner behind it, so
the same discipline has to be kept by hand there — exactly as `M2-FOLLOW-UPS.md` kept it
("No instrument is named anywhere in this file") and the M3 design keeps it, extending the
rule to benchmark proxies.

Describe the shape of the problem and give the command that names the specifics:

> Which instrument is outstanding is a fact about the gitignored export. Run
> `python -m app.cli fetch-prices` to find out — it is deliberately not named here.

Without this rule, moving the backlog to a cloud service quietly becomes the exact leak
the scanner was built to prevent.

---

## 4. Queries worth keeping

```jql
-- Everything still open, oldest first
project = PT AND statusCategory != Done ORDER BY created ASC

-- Open in the current milestone
project = PT AND parent = PT-4 AND statusCategory != Done

-- Debt carried out of shipped milestones
project = PT AND labels = carried AND statusCategory != Done

-- Waiting on me, not on code
project = PT AND labels = "operator-action" AND statusCategory != Done

-- Decisions nobody has made yet
project = PT AND labels = "open-question" AND statusCategory != Done

-- Gates on publishing the repo
project = PT AND labels = "pre-publish" AND statusCategory != Done
```

---

## 5. Referencing between the two

**From a doc to an issue:** cite the key, never the status.

> M3's real-data acceptance will skip exactly as M2's nine do until the outstanding symbol
> question in **PT-10** is answered.

**From an issue to a doc:** cite the repo path. Paths are stable and the issue is read by
someone who has the repo open.

**From a commit to an issue:** the key at the end of the subject line.

```
fix(analytics): split valuation three ways (PT-12)
```

---

## 6. What moved, and where it went

The record of the 2026-09-07 migration. `docs/M2-FOLLOW-UPS.md` was deleted after this; git
history holds the original.

| Was | Now |
|---|---|
| M2 follow-ups §1 — unanswered symbol | PT-10 |
| M2 follow-ups §2 — screen unseen with real data | PT-11 |
| M2 follow-ups §3 — `valuation.py` is 485 lines | PT-12 |
| M2 follow-ups §3 — `coverage` typed `str` | PT-13 |
| M2 follow-ups §3 — Positions badge reports the envelope | PT-14 |
| M2 follow-ups §3 — `GBp`/`GBP` erased by `.upper()` | PT-15 |
| M2 follow-ups §3 — FX-phase error exits with prices committed | PT-16 |
| M2 follow-ups §3 — foreign trade from a pre-existing foreign balance | PT-17 |
| M2 follow-ups §3 — `manual` prices never age | PT-18 |
| M2 follow-ups §4 — `fixtures.ts` carries real ISINs | PT-19 |
| Parent spec §12.1 — M7 ahead of M4? | PT-20 |
| Parent spec §12.2 — B2 wording | PT-21 |
| Parent spec §12.3 — HKD in FX coverage | PT-22 |
| Parent spec §12.4 — securities lending income | PT-23 |
| Parent spec §12.5 — exchange code to MIC mapping | PT-24 |

PT-12 and PT-13 were re-parented from M2 to **M3** on the way across: decision M3-5 commits
to fixing both in the milestone that opens `analytics/valuation.py` anyway.

---

## 7. Operator steps

Two things cannot be done from a session and need a human in the Jira UI.

**Creating a Jira project.** The Atlassian MCP catalogue has no create-project operation —
`createJiraBoard` builds a board inside a company-managed project from an existing filter,
and `createProject` belongs to a different product. Creating `PT` was done by hand.

**Adding issue types, statuses or a workflow.** Same reason. `PT` was created with a `Bug`
type already present; a project cloned from a different template may not have one.

Everything else — creating issues, editing them, transitioning, commenting, querying —
works through the MCP server.
