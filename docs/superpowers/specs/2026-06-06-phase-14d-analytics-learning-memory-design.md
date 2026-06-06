# Phase 14D — Analytics & Learning Memory (our own, not Hermes's)

> **Status:** design (2026-06-06). Replaces the ephemeral Phase-13C learn job with a **persistent,
> analyzable** decision history + a readable weekly learning log + a stats view. This is the substrate
> for a future dashboard. Engine for the weekly summary: NIM llama (direct call, scheduled by Hermes).

## 1. Why NOT Hermes's "grows with the user" memory

Hermes's agent-memory feature only accumulates from its **agent loop** (interactive chat). Our discovery
+ apply pipeline runs in `--no-agent` mode and the gate/automation are deterministic cvflow code — none
of it passes through Hermes's brain, so Hermes never observes the decisions or results. Hermes memory is
also opaque (not queryable) and not auditable. Therefore the learning/analytics layer is **our own**,
built on the SQLite store we already maintain — queryable, auditable, exportable, dashboard-ready.

## 2. What we record (decision history)

Today `applications` stores status transitions but not the *why*. Add a thin, **additive** decisions
log capturing enough to learn from and to power stats:

```sql
CREATE TABLE IF NOT EXISTS decisions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      TEXT NOT NULL,
    decision    TEXT NOT NULL,         -- 'apply' | 'skip'
    decided_at  TEXT NOT NULL,
    cohort      TEXT,                  -- 'M' | 'N'
    benchmark   INTEGER,               -- score at decision time
    fit_score   INTEGER,
    role_family TEXT,
    company     TEXT,
    company_type TEXT,                 -- product/service/staffing (from crux)
    ctc_lpa     REAL,                  -- null for N
    concerns    TEXT                   -- json array of concern codes
);
```
Written by the gate (`handle_gate_command`) when an apply/skip resolves — it already has the job_id;
it looks up the cached crux + last digest benchmark to fill the row. Additive, never touches the gate's
approval logic (invariant 1 intact).

## 3. Stats view (the dashboard data)

A pure read-side aggregator `cvflow.analytics.summarize(store)` → a dict the digest/dashboard can render:
- counts by status (discovered/applied/skipped/approved/failed) — already derivable.
- apply-rate by `role_family`, by `company_type`, by `cohort`.
- applied vs skipped median `fit_score` / `benchmark` / `ctc_lpa`.
- top skipped companies / role families (what you keep rejecting).
- 7-day and all-time rollups.

No new model calls — pure SQL/Python over `decisions` + `applications`. This is the dashboard payload
(rendered to Telegram text now; a web/JSON view later is just a second renderer over the same dict).

## 4. Weekly learning log (persistent, readable)

Upgrade the Phase-13C `learn` cron from "ephemeral suggestion" to "append to a durable log":
1. Build the §3 stats + pull recent `decisions`.
2. One NIM call: "given these stats + recent apply/skip decisions, propose concrete, optional edits to
   preferences (exclude_when rules / prefer_roles weights / thresholds). Propose only — never apply."
3. **Append** a dated entry to `data/learning/YYYY-MM-DD.md` (gitignored runtime data) AND notify the
   summary to Telegram. The file is the growing, analyzable memory you asked for — readable any time,
   diffable week to week.

Still **propose-only** (human applies edits to `preferences.md`) — same discipline as the gate. The log
records both the stats snapshot and the suggestion, so you can see how your behaviour shifts over time.

## 5. Module shape
- `src/cvflow/analytics.py` — `summarize(store) -> dict`; `record_decision(store, job_id, decision)`.
- `storage`: `decisions` table + `add_decision(...)` / `recent_decisions(n)` (additive).
- `gate.handle_gate_command`: after a successful apply/skip, call `record_decision` (best-effort; a
  logging failure never blocks the gate).
- `cron learn`: stats → NIM summary → append `data/learning/<date>.md` + notify.
- `cron stats` (new, optional): post the §3 summary on demand / weekly.

## 6. Scope guard (YAGNI)
- **In scope now:** the `decisions` table, `analytics.summarize`, the persistent weekly log. These are
  cheap, reuse existing storage, and directly serve "analyze what it learns" + future dashboard.
- **Out of scope:** a web dashboard UI, multi-user accounts, per-user data isolation. The stats
  aggregator returns a plain dict, so a web/JSON renderer later needs no backend rework.
- **Not "training":** nothing fine-tunes or self-modifies. "Learning" = recorded history + LLM-proposed,
  human-approved preference edits. Auditable by construction.

## 7. Test plan (TDD, mocked)
- `record_decision` writes a row with cohort/benchmark/role_family from the cached crux + last digest.
- `summarize`: canned applications+decisions → expected apply-rate-by-role / medians / top-skipped.
- gate still approves/skips correctly and a failing `record_decision` does NOT block the gate.
- `learn` appends a dated file and notifies; propose-only (no preference file is mutated).
